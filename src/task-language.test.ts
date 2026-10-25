import { describe, expect, it } from 'vitest';
import { taskPresentation } from './task-presentation';
import type { WorkspaceProfile, WorkspaceTask } from './workspace-types';

const employee: WorkspaceProfile = {
  role: 'employee',
  display_role: 'employee',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_accounting: false,
  can_review: false,
  can_manage: false,
  companies: [{ id: 'a', name: '虚构甲企业' }],
  book_count: 0,
};
const row = (patch: Partial<WorkspaceTask> = {}): WorkspaceTask => ({
  key: 'business:a',
  kind: 'business',
  ref: 'a',
  company_id: 'a',
  company_name: '虚构甲企业',
  book_id: null,
  event_id: 'a',
  draft_id: null,
  batch_id: null,
  title: '虚构事项',
  state: 'returned',
  priority: 1,
  due_on: null,
  updated_at: '2026-10-24',
  bucket: 'action',
  action_label: '修改并重提',
  responsible: '会计',
  overdue: false,
  ...patch,
});

describe('V08 岗位文案与授权行为分开', () => {
  it.each([
    ['material', 'submitted'],
    ['review', 'draft'],
    ['approval', 'submitted'],
  ] as const)('兼任授权的 %s/%s 本人处理时不显示等别人', (kind, state) => {
    const value = taskPresentation(row({ kind, state }), {
      ...employee,
      can_review: true,
      can_approve: true,
    });
    expect(value.actionable).toBe(true);
    expect(value.detail).not.toContain('等待');
  });
  it.each(['constructor', '__proto__', 'toString'])('损坏类型%s保留未知态保护', (kind) => {
    const value = taskPresentation(
      row({ kind: kind as WorkspaceTask['kind'], state: 'prototype' }),
      employee,
    );
    expect(value).toMatchObject({
      key: 'blocked',
      actionable: false,
      detail: '状态待核实',
      kindLabel: '工作事项',
    });
  });
  it('资料下一步以实际负责人为准，不假定一定由会计处理', () => {
    const value = taskPresentation(
      row({ kind: 'material', state: 'submitted', bucket: 'waiting', responsible: '企业财务' }),
      employee,
    );
    expect(value.detail).not.toContain('等待会计');
    expect(value.next).toContain('企业财务');
  });
  it.each(['employee', 'approver', 'manager', 'viewer'] as const)(
    '%s 看到准确的资料提交阶段',
    (role) => {
      const value = taskPresentation(
        row({ kind: 'material', state: 'submitted', bucket: 'waiting' }),
        { ...employee, display_role: role },
      );
      expect(value).toMatchObject({
        key: 'waiting',
        actionable: false,
        kindLabel: '需要提供的资料',
        detail: '资料已提交，等待核对',
        actionLabel: '查看进度',
      });
      expect(value.next).toContain('会计');
      expect(value.next).not.toContain('独立验收');
    },
  );
  it('员工兼任复核仍用员工说明，但不增加任何操作资格', () => {
    const value = taskPresentation(
      row({ kind: 'material', state: 'submitted', bucket: 'waiting' }),
      { ...employee, role: 'accountant', can_review: true, can_accounting: true },
    );
    expect(value.detail).toBe('资料已提交，等待核对');
    expect(value.actionable).toBe(false);
  });
  it('同一财务阶段，员工等待，财务保留核验术语', () => {
    const task = row({ kind: 'finance', state: 'department_approved', bucket: 'waiting' });
    expect(taskPresentation(task, employee).detail).toBe('等待财务核对资料');
    expect(
      taskPresentation(
        { ...task, bucket: 'action' },
        { ...employee, role: 'finance', display_role: 'finance', can_finance: true },
      ),
    ).toMatchObject({
      key: 'mine',
      detail: '待财务核验',
      kindLabel: '财务核验',
      actionable: true,
    });
  });
  it('会计保持专业阶段，资料完成不等同于整月交付', () => {
    const accountant = {
      ...employee,
      role: 'accountant' as const,
      display_role: 'accountant' as const,
      can_review: true,
    };
    expect(taskPresentation(row({ kind: 'review', state: 'draft' }), accountant).detail).toBe(
      '待会计复核',
    );
    expect(taskPresentation(row({ kind: 'material', state: 'accepted' }), employee).next).toContain(
      '不代表',
    );
  });
  it.each(['business', 'material'] as const)('%s 本人退回任务是待我处理', (kind) => {
    expect(taskPresentation(row({ kind }), employee)).toMatchObject({
      key: 'mine',
      label: '待我处理',
      detail: '需要修改后重交',
      actionable: true,
      actionLabel: '修改并重提',
    });
  });
  it.each(['business', 'material'] as const)(
    '%s 不能仅凭returned或负责人名字判为本人待办',
    (kind) => {
      for (const profile of [employee, { ...employee, can_submit: false }, null]) {
        const value = taskPresentation(
          row({ kind, bucket: 'waiting', responsible: '我' }),
          profile,
        );
        expect(value.key).not.toBe('mine');
        expect(value.actionable).toBe(false);
        expect(value.actionLabel).not.toBe('修改并重提');
      }
      for (const profile of [{ ...employee, can_submit: false }, null]) {
        const value = taskPresentation(row({ kind }), profile);
        expect(value.key).not.toBe('mine');
        expect(value.next).toMatch(/权限|授权/);
        expect(value.actionable).toBe(false);
      }
    },
  );
  it.each(['failed', 'unknown', 'surprise'] as const)(
    '%s 仍是需关注，不能建议重复提交',
    (state) => {
      const value = taskPresentation(row({ kind: 'ocr', state }), employee);
      expect(value.key).toBe('blocked');
      expect(value.next).not.toContain('修改同一事项');
    },
  );
  it.each(['queued', 'succeeded'] as const)('票据%s 不冒充已完成做账', (state) => {
    const value = taskPresentation(row({ kind: 'ocr', state, bucket: 'waiting' }), employee);
    expect(value.key).not.toBe('done');
    expect(value.kindLabel).toBe('票据处理');
    expect(value.detail).not.toContain('已记账');
    if (state === 'queued') expect(value.detail).toContain('排队');
    else expect(value.next).toContain('核对');
  });
  it('客户只读的已导出记录仍等待接收，不能变成完成', () => {
    const value = taskPresentation(
      row({ kind: 'delivery', state: 'exported', bucket: 'waiting' }),
      employee,
    );
    expect(value).toMatchObject({
      key: 'waiting',
      detail: '文件已导出，等待接收确认',
      actionable: false,
    });
    expect(value.next).toContain('不要重复');
  });
  it('不修改任何企业、任务、能力或原始状态', () => {
    const task = row();
    const before = JSON.stringify([task, employee]);
    taskPresentation(Object.freeze(task), Object.freeze(employee));
    expect(JSON.stringify([task, employee])).toBe(before);
  });
});
