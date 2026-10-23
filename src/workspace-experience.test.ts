import { describe, expect, it } from 'vitest';
import { workspaceExperience } from './workspace-experience';
import type { WorkspaceProfile } from './workspace-types';

const employee: WorkspaceProfile = {
  role: 'employee',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: false,
  companies: [{ id: 'one', name: '虚构企业' }],
  book_count: 0,
};

describe('角色工作入口', () => {
  it('未读到当前角色时不推测拥有后台能力', () => {
    expect(workspaceExperience(null).processing).toBe(false);
  });
  it('员工与审批人聚焦自己的事项，不显示技术处理入口', () => {
    expect(workspaceExperience(employee)).toMatchObject({
      processing: false,
      simple: true,
      businessLabel: '我的事项',
      kinds: ['intake', 'business', 'case', 'material'],
    });
    expect(workspaceExperience({ ...employee, role: 'approver', can_approve: true })).toMatchObject(
      { processing: false, businessLabel: '部门事项' },
    );
  });
  it('兼任权限不把客户岗位变成专业主界面', () => {
    for (const role of ['employee', 'approver', 'finance', 'manager', 'viewer'] as const) {
      expect(
        workspaceExperience({
          ...employee,
          role,
          can_finance: true,
          can_accounting: true,
          can_review: true,
          can_manage: true,
        }).processing,
      ).toBe(false);
    }
    expect(workspaceExperience({ ...employee, role: 'accountant' }).processing).toBe(true);
  });
  it('工作体验跟随服务端主身份，不跟随旧角色或附加权限', () => {
    expect(
      workspaceExperience({ ...employee, display_role: 'finance', can_accounting: true }),
    ).toMatchObject({
      processing: false,
      businessLabel: '业务事项',
      actionHint: '资料核验 · 移交会计',
    });
    expect(workspaceExperience({ ...employee, display_role: 'reviewer' })).toMatchObject({
      processing: true,
      simple: false,
      kinds: null,
    });
    expect(
      workspaceExperience({
        ...employee,
        role: 'accountant',
        display_role: 'employee',
        can_accounting: true,
      }),
    ).toMatchObject({ processing: false, businessLabel: '我的事项' });
  });
  it('保留当前授权的财务核验、移交和兼任复核筛选入口', () => {
    const finance = workspaceExperience({ ...employee, role: 'finance', can_finance: true });
    expect(finance.processing).toBe(false);
    expect(finance.kinds).toEqual(expect.arrayContaining(['finance', 'handoff']));
    const reviewer = workspaceExperience({ ...employee, can_review: true });
    expect(reviewer.processing).toBe(false);
    expect(reviewer.kinds).toEqual(expect.arrayContaining(['review', 'export', 'delivery']));
    const accounting = workspaceExperience({ ...employee, can_accounting: true });
    expect(accounting.kinds).toEqual(
      expect.arrayContaining(['handoff', 'ocr', 'proposal', 'migration']),
    );
    expect(workspaceExperience(employee).kinds).not.toContain('review');
  });
});
