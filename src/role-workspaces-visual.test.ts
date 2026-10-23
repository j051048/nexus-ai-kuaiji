import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WorkspaceHome } from './WorkspaceHome';
import type { Session } from './types';
import type { WorkspaceProfile, WorkspaceTask, WorkspaceTasks } from './workspace-types';

const noop = () => {};
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构所',
  user_name: '虚构用户',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'client_member',
  scope_key: 'offline-visual-a',
};
const item: WorkspaceTask = {
  key: 'finance:a',
  kind: 'finance',
  ref: 'a',
  company_id: 'company-a',
  company_name: '虚构企业 A',
  book_id: null,
  event_id: 'a',
  draft_id: null,
  batch_id: null,
  title: '待核验采购资料',
  state: 'department_approved',
  priority: 10,
  due_on: '2026-10-01',
  updated_at: '2026-10-01T00:00:00Z',
  bucket: 'action',
  action_label: '核验资料',
  responsible: '本企业财务',
  overdue: true,
  amount: '123456789.01',
};
function render(
  role: 'employee' | 'finance',
  counts: WorkspaceTasks['counts'] = {},
  tasks: WorkspaceTask[] = [],
  changes: Partial<WorkspaceProfile> = {},
) {
  const profile: WorkspaceProfile = {
    role,
    display_role: role,
    can_submit: true,
    can_approve: false,
    can_finance: role === 'finance',
    can_review: false,
    can_accounting: false,
    can_manage: false,
    book_count: 0,
    companies: [{ id: 'company-a', name: '虚构企业 A' }],
    ...changes,
  };
  return renderToStaticMarkup(
    createElement(WorkspaceHome, {
      session,
      profile,
      profileError: '',
      onRetryProfile: noop,
      onOpen: noop,
      onNew: noop,
      onAccounting: noop,
      onBusiness: noop,
      onMaterials: noop,
      onReview: noop,
      revision: 0,
      initialTasks: {
        tasks,
        counts,
        pagination: { total: tasks.length, limit: 20, offset: 0, has_more: false },
      },
    }),
  );
}

describe('V02 employee and finance workspaces', () => {
  it('scopes visual roles without adding homepage sections or changing business permissions', () => {
    for (const role of ['employee', 'finance'] as const) {
      const html = render(role);
      expect(html).toMatch(new RegExp(`<main[^>]*workspace-${role}`));
      expect(html.match(/<section\b/g)).toHaveLength(4);
      expect(html).not.toContain('全所运营概览');
      expect(html).not.toContain('开始批量复核');
    }
  });

  it('keeps three existing employee templates with decorative icons and unchanged names', () => {
    const html = render('employee');
    const primary =
      html.match(/<section[^>]*aria-label="提交与进度"[\s\S]*?<\/section>/)?.[0] ?? '';
    expect(primary.match(/<button\b/g)).toHaveLength(3);
    expect(primary.match(/class="workspace-template-icon" aria-hidden="true"/g)).toHaveLength(3);
    for (const label of ['提交报销', '采购 / 费用', '日常收支']) expect(primary).toContain(label);
  });

  it('explains unknown action counts while preserving known zero and selected filter semantics', () => {
    const unknown = render('employee');
    expect(unknown).toContain('数量暂未汇总，以当前清单为准');
    expect(unknown).toContain('<strong>—</strong>');
    const known = render('employee', { action: 0, waiting: 0 });
    expect(known).not.toContain('数量暂未汇总');
    expect(known).toContain('<strong>0</strong>');
    const summary =
      known.match(/<section[^>]*aria-label="我的工作概览"[\s\S]*?<\/section>/)?.[0] ?? '';
    expect(summary.match(/aria-pressed="true"/g)).toHaveLength(1);
    expect(summary.match(/aria-pressed="false"/g)).toHaveLength(1);
  });

  it('uses real overdue facts for the next task and retains one primary finance action', () => {
    const html = render('finance', { action: 1, waiting: 0 }, [item]);
    const primary =
      html.match(/<section[^>]*aria-label="资料核验与移交"[\s\S]*?<\/section>/)?.[0] ?? '';
    expect(primary).toContain('workspace-next-task overdue');
    expect(primary).toContain('已逾期');
    expect(primary.match(/class="button primary"/g)).toHaveLength(1);
    expect(html.match(/class="button primary(?: small)?"/g)).toHaveLength(1);
    expect(primary).toContain('待核验采购资料');
    expect(html).toContain('data-state="department_approved"');
    expect(html).not.toContain('已导出');
  });

  it('does not promote stale finance presentation into an executable review action after withdrawal', () => {
    const html = render('finance', { action: 1 }, [item], { can_finance: false });
    expect(html).not.toContain('核验下一项');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>查看待核验事项/);
    expect(html).toContain('财务权限已更新');
  });
});
