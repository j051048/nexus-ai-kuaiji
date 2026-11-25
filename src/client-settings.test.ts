import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClientPortalShell } from './ClientPortalShell';
import type { Session } from './types';
import type { WorkspaceProfile } from './workspace-types';

const noop = () => {};
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'finance-a',
  user_name: '企业财务',
  role: 'client_member',
  scope_key: 'scope-a',
};
function render(changes: Partial<WorkspaceProfile> = {}) {
  const profile: WorkspaceProfile = {
    role: 'finance',
    display_role: 'finance',
    can_submit: true,
    can_approve: false,
    can_finance: true,
    can_accounting: true,
    can_review: false,
    can_manage: false,
    companies: [{ id: 'company-a', name: '虚构企业' }],
    book_count: 1,
    ...changes,
  };
  return renderToStaticMarkup(
    createElement(ClientPortalShell, {
      session,
      profile,
      current: 'home',
      onNavigate: noop,
      onBriefing: noop,
      onLogout: noop,
      onAccounting: noop,
      onAdmin: noop,
      children: null,
    }),
  );
}

describe('子公司门户按需设置', () => {
  it('把附加共享财务中台和子公司管理入口放到默认折叠的设置', () => {
    const html = render({ can_manage: true });
    expect(html).toMatch(/<details[^>]*class="client-portal-settings"[^>]*>/);
    expect(html).not.toMatch(/<details[^>]*class="client-portal-settings"[^>]*\bopen=/);
    expect(html).toContain('共享财务中台');
    expect(html).toContain('子公司与人员');
  });
  it('缺少能力时不因回调存在就提供管理或专业入口', () => {
    const html = render({ can_manage: false, can_accounting: false });
    expect(html).not.toContain('共享财务中台');
    expect(html).not.toContain('子公司与人员');
    expect(html).not.toContain('client-portal-settings');
  });
  it('子公司财务不出现共享中心技术和运维监控入口', () => {
    const html = render();
    expect(html).not.toContain('系统运行诊断');
    expect(html).not.toContain('智能用量监控');
    expect(html).not.toContain('外部数据接入');
  });
});
