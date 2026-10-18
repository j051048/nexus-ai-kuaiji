import { describe, expect, it } from 'vitest';
import { roleHomeGuide } from './workspace-role-home';
import type { WorkspaceProfile } from './workspace-types';

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
});
