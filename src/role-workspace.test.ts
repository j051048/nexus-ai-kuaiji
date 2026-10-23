import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClientPortalShell } from './ClientPortalShell';
import { WorkspaceHome } from './WorkspaceHome';
import { roleHome } from './workspace-presentation';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import type { Session } from './types';

const noop = () => {};
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构事务所',
  user_name: '测试用户',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'client_member',
  scope_key: 'offline-a',
};
function profile(display_role: PresentationRole): WorkspaceProfile {
  return {
    role: 'accountant',
    display_role,
    can_submit: false,
    can_approve: false,
    can_finance: false,
    can_review: false,
    can_accounting: false,
    can_manage: false,
    companies: [{ id: 'company-a', name: '虚构企业 A' }],
    book_count: 0,
  };
}

describe('同一展示身份贯通首页和客户门户', () => {
  it('简单首页保留可主动展开的列表选项，不丢失已保存视图能力', () => {
    const html = renderToStaticMarkup(
      createElement(WorkspaceHome, {
        session,
        profile: profile('manager'),
        profileError: '',
        onRetryProfile: noop,
        onOpen: noop,
        onNew: noop,
        onAccounting: noop,
        onReview: noop,
        revision: 0,
      }),
    );
    expect(html).toContain('显示列表选项');
    expect(html).not.toContain('aria-label="紧凑表格视图"');
  });
  it.each<PresentationRole>([
    'employee',
    'approver',
    'finance',
    'manager',
    'accountant',
    'reviewer',
    'firm_owner',
    'admin',
    'viewer',
  ])('%s 首页与身份说明使用服务端展示岗位', (role) => {
    const html = renderToStaticMarkup(
      createElement(WorkspaceHome, {
        session,
        profile: profile(role),
        profileError: '',
        onRetryProfile: noop,
        onOpen: noop,
        onNew: noop,
        onAccounting: noop,
        onReview: noop,
        revision: 0,
      }),
    );
    expect(html).toContain(`<h1>${roleHome(role).name}</h1>`);
    expect(html).toContain(`<strong>${roleHome(role).name}</strong>`);
  });

  it.each([
    ['employee', '首页', '我的事项'],
    ['approver', '待审批', '部门事项'],
    ['finance', '待核验', '业务事项'],
    ['manager', '企业首页', '报告'],
  ] as const)('%s 客户导航反映当前岗位职责', (role, home, action) => {
    const html = renderToStaticMarkup(
      createElement(ClientPortalShell, {
        session,
        profile: profile(role),
        current: 'home',
        onNavigate: noop,
        onBriefing: noop,
        onLogout: noop,
        children: null,
      }),
    );
    expect(html).toContain(`<span>${home}</span>`);
    expect(html).toContain(`<span>${action}</span>`);
  });
});
