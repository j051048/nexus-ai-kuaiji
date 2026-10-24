import { describe, expect, it } from 'vitest';
import { taskStateLabel } from './workspace-types';
import { taskPresentation } from './task-presentation';
import type { TaskKind, WorkspaceProfile, WorkspaceTask } from './workspace-types';

const employee: WorkspaceProfile = {
  role: 'employee',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: false,
  companies: [{ id: 'company-a', name: '虚构企业甲' }],
  book_count: 0,
};
const allGrants: WorkspaceProfile = {
  ...employee,
  can_approve: true,
  can_finance: true,
  can_review: true,
  can_accounting: true,
  can_manage: true,
  can_firm_operations: true,
};
function task(extra: Partial<WorkspaceTask> = {}): WorkspaceTask {
  return {
    key: 'business:a',
    kind: 'business',
    ref: 'a',
    company_id: 'company-a',
    company_name: '虚构企业甲',
    book_id: null,
    event_id: 'event-a',
    draft_id: null,
    batch_id: null,
    title: '差旅报销',
    state: 'submitted',
    priority: 2,
    due_on: null,
    updated_at: '2026-10-24T00:00:00Z',
    bucket: 'waiting',
    action_label: '查看进度',
    responsible: '企业财务',
    overdue: false,
    ...extra,
  };
}

describe('V05 任务类型必须决定相同状态的业务含义', () => {
  it.each([
    ['handoff', 'submitted', '待会计接收'],
    ['finance', 'submitted', '待财务核验'],
    ['review', 'exported', '已导出，待回执核验'],
    ['delivery', 'unknown', '导入结果待核实'],
    ['ocr', 'succeeded', '识别完成，仍需核对'],
    ['proposal', 'succeeded', '草稿已生成，待会计复核'],
    ['material', 'accepted', '已验收'],
    ['migration', 'accepted', '待接管'],
  ] as const)('%s / %s', (kind, state, label) => {
    expect(taskStateLabel({ kind, state })).toBe(label);
  });
});

describe('V05 待办摘要按服务端分派与当前能力收窄', () => {
  it('同一事项对员工是等待，对获授权财务是待我处理', () => {
    const submitted = task({ state: 'department_approved' });
    expect(taskPresentation(submitted, employee)).toMatchObject({
      key: 'waiting',
      label: '等待处理',
      detail: '待财务核验',
      actionable: false,
      actionLabel: '查看进度',
    });
    const finance = taskPresentation(
      task({
        kind: 'finance',
        state: 'department_approved',
        bucket: 'action',
        action_label: '核验资料',
      }),
      { ...employee, can_finance: true },
    );
    expect(finance).toMatchObject({
      key: 'mine',
      label: '待我处理',
      actionable: true,
      actionLabel: '核验资料',
    });
    expect(finance.next).toContain('核对');
  });

  it('负责人名字含“我”或管理员头衔都不能把 waiting 变成本人待办', () => {
    for (const responsible of ['我', '超级管理员', '我负责的部门审批人']) {
      const value = taskPresentation(task({ responsible, action_label: '批准并导出' }), allGrants);
      expect(value.actionable).toBe(false);
      expect(value.actionLabel).toBe('查看进度');
      expect(value.next).not.toContain('批准并导出');
    }
  });

  it('同状态也不能以其他岗位的能力替代当前步骤权限', () => {
    const value = taskPresentation(
      task({ kind: 'finance', state: 'department_approved', bucket: 'action' }),
      {
        ...employee,
        can_review: true,
        can_manage: true,
      },
    );
    expect(value.actionable).toBe(false);
    expect(value.actionLabel).toBe('查看详情');
    expect(value.next).toContain('授权');
  });

  it('未读到 profile 时不推定任何当前操作资格', () => {
    const value = taskPresentation(
      task({ kind: 'review', state: 'draft', bucket: 'action', action_label: '确认审核' }),
      null,
    );
    expect(value).toMatchObject({ actionable: false, actionLabel: '查看详情' });
    expect(value.next).toContain('权限');
  });

  it('角色名不授权，附加能力只在服务端 action 内生效', () => {
    const row = task({
      kind: 'approval',
      state: 'submitted',
      bucket: 'action',
      action_label: '审批事项',
    });
    expect(taskPresentation(row, { ...employee, role: 'approver' }).actionable).toBe(false);
    expect(
      taskPresentation(row, { ...employee, display_role: 'employee', can_approve: true })
        .actionable,
    ).toBe(true);
    expect(taskPresentation({ ...row, bucket: 'waiting' }, allGrants).actionable).toBe(false);
  });

  it.each([
    ['intake', 'draft', 'can_submit'],
    ['business', 'returned', 'can_submit'],
    ['approval', 'submitted', 'can_approve'],
    ['finance', 'department_approved', 'can_finance'],
    ['handoff', 'submitted', 'can_accounting'],
    ['case', 'response_submitted', 'can_review'],
    ['review', 'draft', 'can_review'],
    ['ocr', 'failed', 'can_submit'],
    ['proposal', 'needs_attention', 'can_accounting'],
    ['export', 'approved', 'can_review'],
    ['delivery', 'unverified', 'can_review'],
    ['alert', 'open', 'can_manage'],
    ['migration', 'ready', 'can_review'],
    ['material', 'pending', 'can_submit'],
  ] as const)('全部14类任务 %s 根据自己的能力生成下一步', (kind, state, grant) => {
    const profile = { ...employee, can_submit: false, [grant]: true };
    const value = taskPresentation(
      task({ kind, state, bucket: 'action', action_label: '打开办理' }),
      profile,
    );
    expect(value.actionable).toBe(true);
    expect(value.actionLabel).toBe('打开办理');
    expect(value.next.length).toBeGreaterThan(8);
  });

  it('材料提交后的独立验收需要复核能力，不能靠本人提交能力', () => {
    const row = task({ kind: 'material', state: 'submitted', bucket: 'action' });
    expect(taskPresentation(row, employee).actionable).toBe(false);
    expect(taskPresentation(row, { ...employee, can_review: true }).actionable).toBe(true);
  });

  it.each(['approved', 'exported'] as const)('凭证 %s 不显示整件业务完成', (state) => {
    const value = taskPresentation(task({ kind: 'review', state, bucket: 'action' }), allGrants);
    expect(value.key).not.toBe('done');
    expect(value.detail).toContain(state === 'approved' ? '待导出' : '待回执核验');
    expect(value.next).toContain(state === 'approved' ? '导出' : '回执');
  });

  it('已移交只说明交接，不暗示凭证已审核或已导出', () => {
    const value = taskPresentation(task({ state: 'handed_off' }), employee);
    expect(value.key).toBe('waiting');
    expect(value.next).toContain('不代表');
  });

  it.each(['unknown', 'unverified', 'exported'] as const)(
    '回执 %s 先核实已有结果，不建议重复发送',
    (state) => {
      const value = taskPresentation(
        task({ kind: 'delivery', state, bucket: 'action', action_label: '核验导入回执' }),
        allGrants,
      );
      expect(value.key).not.toBe('done');
      expect(value.next).toContain('不要重复');
      expect(value.next).toContain('回执');
      expect(value.next).not.toContain('点击重试');
    },
  );

  it.each(['queued', 'running', 'retry_wait'] as const)(
    '后台 %s 保留具体阶段并禁止主动重发提示',
    (state) => {
      const value = taskPresentation(task({ kind: 'ocr', state, bucket: 'action' }), allGrants);
      expect(value).toMatchObject({ key: 'running', actionable: false, actionLabel: '查看进度' });
      expect(value.next).toContain('不要重复');
    },
  );

  it('取消是已结束，不是处理成功，也不暗示费用为零', () => {
    const value = taskPresentation(task({ kind: 'ocr', state: 'cancelled' }), allGrants);
    expect(value).toMatchObject({
      key: 'done',
      label: '已结束',
      detail: '已取消',
      actionable: false,
    });
    expect(value.next).not.toContain('免费');
    expect(value.next).not.toContain('重试');
  });

  it.each(['failed', 'interrupted', 'stale', 'needs_attention'] as const)(
    '异常 %s 有核对与人工处理的下一步',
    (state) => {
      const value = taskPresentation(
        task({ kind: 'proposal', state, bucket: 'action' }),
        allGrants,
      );
      expect(value.key).toBe('blocked');
      expect(value.next).toMatch(/核对|人工/);
      expect(value.next).not.toContain('自动批准');
    },
  );

  it('同一 accepted 状态按材料验收与迁移接管分别解释', () => {
    expect(taskPresentation(task({ kind: 'material', state: 'accepted' }), employee)).toMatchObject(
      { key: 'done', detail: '已验收' },
    );
    expect(
      taskPresentation(task({ kind: 'migration', state: 'accepted' }), allGrants),
    ).toMatchObject({ key: 'waiting', detail: '待接管' });
  });

  it('未来状态和错误 kind/state 组合只能核实，不能从 action_label 指示写入', () => {
    for (const [kind, state] of [
      ['ocr', 'provider_new_state'],
      ['finance', 'accepted'],
      ['material', 'approved'],
      ['case', 'toString'],
      ['ocr', '__proto__'],
    ] as const) {
      const value = taskPresentation(
        task({ kind, state, bucket: 'action', action_label: '直接批准并导出' }),
        allGrants,
      );
      expect(value).toMatchObject({
        key: 'blocked',
        actionable: false,
        actionLabel: '查看详情',
        detail: '状态待核实',
      });
      expect(value.next).toContain('重新读取');
      expect(value.next).not.toContain('直接批准并导出');
    }
  });

  it('回执未核验对获授权交付会计保留核验入口，对等待者只显示查看', () => {
    const row = task({
      kind: 'delivery',
      state: 'unverified',
      bucket: 'action',
      action_label: '核验导入回执',
    });
    expect(taskPresentation(row, { ...employee, can_review: true })).toMatchObject({
      key: 'mine',
      actionable: true,
      actionLabel: '核验导入回执',
    });
    expect(taskPresentation({ ...row, bucket: 'waiting' }, allGrants)).toMatchObject({
      key: 'waiting',
      actionable: false,
      actionLabel: '查看回执',
    });
  });

  it('缺失负责人明确提醒确认，长名字完整保留且不改变资格', () => {
    const missing = taskPresentation(task({ responsible: '  ' }), employee);
    expect(missing.responsible).toBe('负责人待确认');
    expect(missing.next).toContain('联系');
    const long = '甲企业项目资料核验负责人'.repeat(12);
    expect(taskPresentation(task({ responsible: long }), employee).responsible).toBe(long);
  });

  it('纯映射不改变任务、企业范围或传入权限', () => {
    const row = task({ kind: 'review', state: 'draft', bucket: 'action' });
    const before = JSON.stringify([row, allGrants]);
    taskPresentation(Object.freeze(row), Object.freeze(allGrants));
    expect(JSON.stringify([row, allGrants])).toBe(before);
  });

  it.each([
    ['approval', 'department_approved'],
    ['finance', 'finance_approved'],
    ['handoff', 'handed_off'],
    ['ocr', 'succeeded'],
    ['proposal', 'succeeded'],
  ] as const)('%s / %s 旧action分组也不能建议重复处理已越过的阶段', (kind, state) => {
    const value = taskPresentation(
      task({ kind, state, bucket: 'action', action_label: '再次执行' }),
      allGrants,
    );
    expect(value.actionable).toBe(false);
    expect(value.actionLabel).not.toBe('再次执行');
  });

  it.each([
    'intake',
    'business',
    'approval',
    'finance',
    'handoff',
    'case',
    'review',
    'ocr',
    'proposal',
    'export',
    'delivery',
    'alert',
    'migration',
    'material',
  ] as TaskKind[])('缺权限的 %s 即使服务端action也不鼓励写操作', (kind) => {
    const none = { ...employee, can_submit: false };
    const value = taskPresentation(
      task({ kind, state: kind === 'intake' ? 'draft' : 'submitted', bucket: 'action' }),
      none,
    );
    expect(value.actionable).toBe(false);
    expect(value.actionLabel).toBe('查看详情');
  });
});
