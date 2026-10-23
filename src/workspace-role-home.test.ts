import { describe, expect, it } from 'vitest';
import { roleHomeGuide } from './workspace-role-home';
import type { WorkspaceProfile } from './workspace-types';
import { presentationRole, roleHome } from './workspace-presentation';

function profile(changes: Partial<WorkspaceProfile>): WorkspaceProfile {
  return {
    role: 'employee',
    can_submit: false,
    can_approve: false,
    can_finance: false,
    can_review: false,
    can_accounting: false,
    can_manage: false,
    companies: [],
    book_count: 0,
    ...changes,
  };
}

describe('roleHomeGuide', () => {
  it('derives actions from server capabilities, not only role label', () => {
    const guide = roleHomeGuide(
      profile({ role: 'employee', can_submit: true, can_review: true, can_accounting: true }),
    );
    expect(guide.label).toBe('我的工作台');
    expect(guide.actions).toEqual(
      expect.arrayContaining(['提交本人事项与补件', '凭证复核与审核后导出', '进入当前授权账套']),
    );
  });

  it('keeps a viewer without capabilities read-only', () => {
    const guide = roleHomeGuide(profile({ role: 'viewer' }));
    expect(guide.actions).toEqual(['读取当前授权范围']);
  });

  it('shows the service-side authorization boundary', () => {
    expect(roleHomeGuide(profile({ role: 'accountant', can_review: true })).boundary).toContain(
      '服务端逐次校验',
    );
  });

  it('uses the same server identity and focus as the navigation and home', () => {
    for (const display_role of [
      'employee',
      'approver',
      'finance',
      'manager',
      'accountant',
      'reviewer',
      'firm_owner',
      'admin',
      'viewer',
    ] as const) {
      const current = profile({ role: 'employee', display_role });
      const guide = roleHomeGuide(current);
      const home = roleHome(presentationRole(current));
      expect(guide.label).toBe(home.name);
      expect(guide.focus).toBe(home.focus);
    }
  });

  it('does not expose firm operations hints through the deprecated can_operations flag', () => {
    expect(
      roleHomeGuide(profile({ can_operations: true, can_firm_operations: false })).actions,
    ).not.toContain('事务所运营视图');
    expect(roleHomeGuide(profile({ can_firm_operations: true })).actions).toContain(
      '事务所运营视图',
    );
  });
});
