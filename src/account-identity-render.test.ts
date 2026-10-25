import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AccountIdentity } from './AccountIdentity';
import type { Session } from './types';
import type { WorkspaceProfile } from './workspace-types';

const session: Session = {
  token: 'must-not-render-secret',
  mode: 'local',
  firm_id: 'f1',
  firm_name: '虚构事务所',
  user_id: 'u1',
  user_name: '虚构员工',
  role: 'client_member',
  scope_key: 'scope-a',
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
  companies: [],
  book_count: 0,
};

describe('shared signed-in identity', () => {
  it('shows an actual job name and account, with keyboard-native disclosure and no secrets', () => {
    const html = renderToStaticMarkup(createElement(AccountIdentity, { session, profile }));
    expect(html).toContain('aria-label="当前登录身份"');
    expect(html).toContain('企业员工');
    expect(html).toContain('虚构员工');
    expect(html).toContain('<summary');
    expect(html).toContain('授权范围');
    expect(html).not.toContain(session.token);
    expect(html).not.toContain('我的工作台');
    expect(html).not.toContain('超级管理员');
  });
  it('does not keep prior capability claims when authorization needs confirmation', () => {
    const html = renderToStaticMarkup(
      createElement(AccountIdentity, {
        session,
        profile: { ...profile, can_review: true },
        unavailable: true,
      }),
    );
    expect(html).toContain('权限待重新确认');
    expect(html).not.toContain('复核获授权账套');
  });
  it('escapes names and keeps the read-only disclosure free from business commands', () => {
    const html = renderToStaticMarkup(
      createElement(AccountIdentity, {
        session: { ...session, user_name: '<script>wrong()</script>' },
        profile,
      }),
    );
    expect(html).not.toContain('<script>');
    expect(html).not.toMatch(/<(form|input|button)\b/);
    expect(html).toContain('&lt;script&gt;');
  });
});
