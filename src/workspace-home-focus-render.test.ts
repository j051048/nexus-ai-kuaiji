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
  user_name: '测试用户',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'client_member',
  scope_key: 'offline-a',
};
const financeTask: WorkspaceTask = {
  key: 'finance:a',
  kind: 'finance',
  ref: 'a',
  company_id: 'company-a',
  company_name: '虚构企业 A',
  book_id: null,
  event_id: 'a',
  draft_id: null,
  batch_id: null,
  title: '办公用品资料待核验',
  state: 'department_approved',
  priority: 10,
  due_on: '2026-10-25',
  updated_at: '2026-10-23T08:00:00Z',
  bucket: 'action',
  action_label: '核验资料',
  responsible: '本企业财务',
  overdue: false,
  amount: '120.00',
};
function profile(role: 'employee' | 'finance'): WorkspaceProfile {
  return {
    role,
    display_role: role,
    can_submit: true,
    can_approve: false,
    can_finance: role === 'finance',
    can_review: true,
    can_accounting: true,
    can_manage: false,
    book_count: 17,
    companies: [{ id: 'company-a', name: '虚构企业 A' }],
    briefings: [
      {
        book_id: 'book-a',
        book_name: 'A 账套',
        period: '2026-09',
        company_id: 'company-a',
        company_name: '虚构企业 A',
      },
    ],
  };
}
function render(
  role: 'employee' | 'finance',
  tasks?: WorkspaceTasks,
  profileError = '',
  changes: Partial<WorkspaceProfile> = {},
) {
  return renderToStaticMarkup(
    createElement(WorkspaceHome, {
      session,
      profile: { ...profile(role), ...changes },
      profileError,
      onRetryProfile: noop,
      onOpen: noop,
      onNew: noop,
      onAccounting: noop,
      onBusiness: noop,
      onMaterials: noop,
      onReview: noop,
      onBriefing: noop,
      revision: 0,
      initialTasks: tasks,
    }),
  );
}

describe('岗位首页围绕主任务收敛', () => {
  it('员工先看到三种业务提交，再看到本人待办；附加会计权限不抢占主操作', () => {
    const html = render('employee');
    expect(html).toContain('今天要提交什么？');
    expect(html).toContain('提交报销');
    expect(html).toContain('采购 / 费用');
    expect(html).toContain('日常收支');
    expect(html.indexOf('今天要提交什么？')).toBeLessThan(html.indexOf('我的工作概览'));
    expect(html).toContain('我的提交与补件');
    expect(html).toContain('我的办理进度');
    expect(html).not.toContain('class="workspace-accounting-shortcuts"');
    expect(html).not.toContain('class="workspace-runtime-link"');
    expect(html.indexOf('开始批量复核')).toBeGreaterThan(html.indexOf('workspace-inbox'));
  });
  it('授权说明默认收折，身份仍在可访问区域内', () => {
    const html = render('employee');
    expect(html).toMatch(/<section[^>]*aria-label="当前职责与授权范围"[^>]*>\s*<details/);
    expect(html).toContain('<strong>我的工作台</strong>');
    expect(html).toContain('查看岗位与操作说明');
    expect(html).not.toMatch(/<details[^>]*\bopen(?:="")?[^>]*>/);
  });
  it('企业财务先核验并移交，下一项来自真实清单，报告和本人提交降为辅助', () => {
    const html = render('finance', {
      tasks: [financeTask],
      counts: { action: 9, waiting: 4 },
      pagination: { total: 9, limit: 20, offset: 0, has_more: false },
    });
    expect(html).toContain('核验资料并移交会计');
    expect(html).toContain('当前清单下一项');
    expect(html.indexOf('核验资料并移交会计')).toBeLessThan(html.indexOf('我的工作概览'));
    expect(html.indexOf('本月财税简报入口')).toBeGreaterThan(html.indexOf('workspace-inbox'));
    expect(html.indexOf('记录本人业务')).toBeGreaterThan(html.indexOf('workspace-inbox'));
    expect(html).not.toContain('当前授权工作范围');
    expect(html).not.toContain('全部完成');
  });
  it('当前页空、权限读取失败时不伪造下一项或宣称本月已办完', () => {
    const tasks: WorkspaceTasks = {
      tasks: [],
      counts: { action: 24 },
      pagination: { total: 24, limit: 20, offset: 20, has_more: false },
    };
    expect(render('finance', tasks)).not.toContain('当前清单下一项');
    expect(render('finance', { ...tasks, tasks: [financeTask] }, '权限读取失败')).not.toContain(
      '当前清单下一项',
    );
    expect(render('finance', tasks)).not.toContain('本月已完成');
  });
  it('财务展示身份保留但实时核验权限撤销时不提供可执行核验入口', () => {
    const html = render(
      'finance',
      {
        tasks: [financeTask],
        counts: { action: 1, waiting: 0 },
        pagination: { total: 1, limit: 20, offset: 0, has_more: false },
      },
      '',
      { can_finance: false },
    );
    expect(html).toContain('财务权限已更新，当前无法核验或移交');
    expect(html).not.toContain('核验下一项');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>查看待核验事项/);
    expect(html).toContain('查看资料清单');
  });
});
