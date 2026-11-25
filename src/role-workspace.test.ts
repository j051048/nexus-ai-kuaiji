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
    expect(html).toContain(`<span class="sr-only">${roleHome(role).name}</span>`);
    expect(html).toContain('岗位与权限');
  });

  it.each([
    ['employee', '我的进度', '我要报销'],
    ['approver', '待我放行', '部门单据'],
    ['finance', '待核验移交', '收支核验'],
    ['manager', '公司首页', '经营简报'],
  ] as const)('%s 子公司导航反映当前岗位职责', (role, home, action) => {
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

  it('企业负责人首页无已发布简报时诚实提示会计核算中，有简报时直接提供入口', () => {
    const pendingHtml = renderToStaticMarkup(
      createElement(WorkspaceHome, {
        session,
        profile: profile('manager'),
        profileError: '',
        onRetryProfile: noop,
        onOpen: noop,
        onNew: noop,
        onAccounting: noop,
        onReview: noop,
        onBriefing: noop,
        revision: 0,
      }),
    );
    expect(pendingHtml).toContain('本月经营与财务简报');
    expect(pendingHtml).toContain('会计核算中');

    const readyProfile: WorkspaceProfile = {
      ...profile('manager'),
      briefings: [
        {
          book_id: 'book-1',
          book_name: '2026 年总账',
          period: '2026-10',
          company_id: 'company-a',
          company_name: '虚构企业 A',
        },
      ],
    };
    const readyHtml = renderToStaticMarkup(
      createElement(WorkspaceHome, {
        session,
        profile: readyProfile,
        profileError: '',
        onRetryProfile: noop,
        onOpen: noop,
        onNew: noop,
        onAccounting: noop,
        onReview: noop,
        onBriefing: noop,
        revision: 0,
      }),
    );
    expect(readyHtml).toContain('1 期已发布');
    expect(readyHtml).toContain('查看 2026-10 财务简报');
  });
});
