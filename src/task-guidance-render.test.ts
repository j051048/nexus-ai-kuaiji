import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WorkspaceHome } from './WorkspaceHome';
import type { Session } from './types';
import type { WorkspaceProfile, WorkspaceTask } from './workspace-types';

const session: Session = {
  token: 'offline-only',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'employee-a',
  user_name: '虚构员工',
  scope_key: 'offline-a',
  role: 'client_employee',
};
const profile: WorkspaceProfile = {
  role: 'employee',
  display_role: 'employee',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: false,
  companies: [{ id: 'company-a', name: '虚构企业A' }],
  book_count: 0,
};
const row: WorkspaceTask = {
  key: 'material:fixture',
  kind: 'material',
  ref: 'fixture',
  company_id: 'company-a',
  company_name: '虚构企业A',
  book_id: null,
  event_id: null,
  draft_id: null,
  batch_id: null,
  title: '本月资料',
  state: 'submitted',
  priority: 20,
  due_on: null,
  updated_at: '2026-10-24T00:00:00Z',
  bucket: 'waiting',
  action_label: '查看进度',
  responsible: '企业财务',
  overdue: false,
};

function render(task: WorkspaceTask, actor: WorkspaceProfile | null = profile) {
  const html = renderToStaticMarkup(
    createElement(WorkspaceHome, {
      session,
      profile: actor,
      profileError: '',
      revision: 0,
      initialTasks: {
        tasks: [task],
        counts: { action: 1, waiting: 1 },
        pagination: { total: 1, limit: 20, offset: 0, has_more: false },
      },
      onRetryProfile: () => {},
      onOpen: () => {},
      onNew: () => {},
      onAccounting: () => {},
      onReview: () => {},
    }),
  );
  return html.match(/<article[^>]*data-task-key="material:fixture"[\s\S]*?<\/article>/)?.[0] ?? '';
}

describe('V05 待办将身份摘要、业务事实与下一步同屏呈现', () => {
  it('员工已提交资料显示等财务处理，不能被概括为已完成', () => {
    const html = render(row);
    expect(html).toContain('data-summary="waiting"');
    expect(html).toContain('资料已提交，等待核对');
    expect(html).not.toContain('待独立验收');
    expect(html).toContain('下一步');
    expect(html).toContain('企业财务');
    expect(html).not.toContain('data-summary="done"');
  });
  it('相同资料在获授权财务的action清单中突出本人处理', () => {
    const html = render(
      { ...row, bucket: 'action' },
      {
        ...profile,
        role: 'finance',
        display_role: 'finance',
        can_finance: true,
        can_review: true,
      },
    );
    expect(html).toContain('data-summary="mine"');
    expect(html).toContain('待我处理');
    expect(html).toContain('下一步');
  });
  it('未知交付结果提示核对已有回执，不鼓励重复提交', () => {
    const html = render(
      { ...row, kind: 'delivery', state: 'unknown' },
      {
        ...profile,
        role: 'accountant',
        display_role: 'reviewer',
        can_review: true,
        can_accounting: true,
      },
    );
    expect(html).toContain('data-summary="blocked"');
    expect(html).toContain('回执');
    expect(html).toContain('不要重复');
    expect(html).not.toContain('data-summary="done"');
  });
  it('审核通过依然提示会计导出与回执核验，不宣称交付完成', () => {
    const html = render(
      { ...row, kind: 'export', state: 'approved', bucket: 'action' },
      {
        ...profile,
        role: 'accountant',
        can_review: true,
        can_accounting: true,
      },
    );
    expect(html).toContain('已审核');
    expect(html).toContain('下一步');
    expect(html).not.toContain('data-summary="done"');
  });
  it('权限未就绪时降为查看提示，不能依据任务自报按钮鼓励批准', () => {
    const html = render(
      { ...row, kind: 'review', state: 'draft', bucket: 'action', action_label: '立即审核并导出' },
      null,
    );
    expect(html).toContain('下一步');
    expect(html).not.toContain('立即审核并导出');
    expect(html).not.toContain('data-summary="mine"');
  });
});
