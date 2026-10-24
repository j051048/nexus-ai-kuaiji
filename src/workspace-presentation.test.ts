import { describe, expect, it } from 'vitest';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import { workspaceViewLabels } from './workspace-labels';
import {
  canSeeSurface,
  clientPortalTabs,
  clientPortalNav,
  contextMode,
  contextTitle,
  presentationRole,
  primaryNav,
  roleHome,
  selectBriefing,
  workSurface,
} from './workspace-presentation';

function profile(changes: Partial<WorkspaceProfile> = {}): WorkspaceProfile {
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

const allRoles: PresentationRole[] = [
  'employee',
  'approver',
  'finance',
  'manager',
  'accountant',
  'reviewer',
  'firm_owner',
  'admin',
  'viewer',
];

describe('UX01 role presentation contract', () => {
  it('professional identities cannot project accounting routes into the customer tab icon contract', () => {
    for (const display_role of ['accountant', 'reviewer', 'firm_owner', 'admin'] as const) {
      expect(clientPortalNav(profile({ display_role }))).toEqual([]);
    }
  });
  it('uses the same plain management labels in navigation and page breadcrumbs', () => {
    expect(workspaceViewLabels.firm).toBe('全所概览');
    expect(workspaceViewLabels.monthly).toBe('月度交付');
    expect(workspaceViewLabels.ingestion).toBe('数据接入');
  });
  it('defines a default surface for all nine presentation roles', () => {
    const surfaces = allRoles.map((role) => workSurface(role));
    expect(surfaces).toEqual([
      'client',
      'client',
      'client',
      'client',
      'accounting',
      'accounting',
      'firm',
      'admin',
      'client',
    ]);
    for (const role of allRoles) {
      expect(roleHome(role).name).toBeTruthy();
      expect(primaryNav(role).length).toBeGreaterThan(0);
      expect(primaryNav(role).length).toBeLessThanOrEqual(5);
    }
  });

  it('keeps employee navigation focused on own work and materials', () => {
    expect(primaryNav('employee').map((item) => item.id)).toEqual([
      'home',
      'business',
      'materials',
      'messages',
    ]);
  });

  it('does not turn a manager or finance member into firm-wide access', () => {
    expect(presentationRole(profile({ role: 'manager', can_manage: true }))).toBe('manager');
    expect(presentationRole(profile({ role: 'finance', can_finance: true }))).toBe('finance');
    expect(canSeeSurface(profile({ role: 'finance', can_finance: true }), 'firm')).toBe(false);
  });

  it('uses server display_role when available but keeps capabilities as fallback', () => {
    expect(presentationRole(profile({ display_role: 'reviewer', can_accounting: true }))).toBe(
      'reviewer',
    );
    expect(presentationRole(profile({ can_firm_operations: true }))).toBe('firm_owner');
    expect(
      presentationRole(profile({ role: 'accountant', can_accounting: true, can_review: true })),
    ).toBe('reviewer');
    expect(presentationRole(profile({ role: 'viewer' }))).toBe('viewer');
    expect(presentationRole(null)).toBe('viewer');
  });

  it.each(['employee', 'approver', 'finance', 'manager', 'viewer'] as const)(
    'preserves an explicit legacy %s job when accounting capabilities are added',
    (role) => {
      const current = profile({
        role,
        can_accounting: true,
        can_review: true,
        can_finance: true,
        can_approve: true,
        can_submit: true,
        can_manage: true,
      });
      expect(presentationRole(current)).toBe(role);
      expect(workSurface(presentationRole(current))).toBe('client');
    },
  );

  it('uses the current server display role for navigation, home and surface together', () => {
    const current = profile({
      role: 'employee',
      display_role: 'finance',
      can_submit: true,
      can_accounting: true,
      can_review: true,
    });
    const role = presentationRole(current);
    expect(roleHome(role).name).toBe('财务核验');
    expect(roleHome(role).subtitle).toBe(roleHome(role).focus);
    expect(primaryNav(role)[0].label).toBe('待核验');
    expect(workSurface(role)).toBe('client');
  });

  it('labels customer navigation by the current job without adding permission', () => {
    const nav = (role: WorkspaceProfile['role']) =>
      clientPortalNav(profile({ role, can_submit: true }));
    expect(nav('employee').find((item) => item.id === 'submit')?.label).toBe('我的事项');
    expect(nav('approver').map((item) => item.label)).toEqual([
      '待审批',
      '部门事项',
      '资料清单',
      '消息',
    ]);
    expect(
      nav('finance')
        .slice(0, 2)
        .map((item) => item.label),
    ).toEqual(['待核验', '业务事项']);
    expect(nav('manager')[0].label).toBe('企业首页');
    expect(nav('manager').some((item) => item.id === 'submit')).toBe(false);
  });

  it.each(['employee', 'approver', 'finance', 'manager', 'viewer'] as const)(
    'V04 %s 客户门户与角色配置使用同一入口及顺序',
    (role) => {
      const current = profile({ role });
      expect(
        clientPortalNav(current).map((item) => (item.id === 'submit' ? 'business' : item.id)),
      ).toEqual(primaryNav(role).map((item) => item.id));
    },
  );

  it.each(['employee', 'approver', 'finance', 'manager', 'viewer'] as const)(
    'uses one navigation vocabulary across the %s customer shell and workspace',
    (role) => {
      const current = profile({ role });
      const primary = primaryNav(role);
      for (const item of clientPortalNav(current)) {
        const source = primary.find(
          (entry) => entry.id === (item.id === 'submit' ? 'business' : item.id),
        );
        if (source) expect(item.label).toBe(source.label);
      }
    },
  );

  it('describes single, multi-company, firm and admin context without applying permissions', () => {
    expect(contextMode(profile({ role: 'employee', companies: [{ id: 'c1', name: 'A' }] }))).toBe(
      'single_company',
    );
    expect(
      contextMode(
        profile({
          role: 'employee',
          companies: [
            { id: 'c1', name: 'A' },
            { id: 'c2', name: 'B' },
          ],
        }),
      ),
    ).toBe('multi_company');
    expect(contextMode(profile({ display_role: 'firm_owner' }))).toBe('firm');
    expect(contextMode(profile({ display_role: 'admin' }))).toBe('system');
    expect(contextTitle(profile({ display_role: 'admin' }))).toBe('系统管理');
  });

  it('filters primary navigation with server capabilities, not role labels alone', () => {
    const accountant = profile({ can_accounting: true, can_review: false });
    expect(primaryNav('accountant', accountant).some((item) => item.id === 'review')).toBe(false);
    const reviewer = profile({ can_accounting: true, can_review: true });
    expect(primaryNav('accountant', reviewer).some((item) => item.id === 'review')).toBe(true);
    const owner = profile({
      display_role: 'firm_owner',
      can_firm_operations: true,
      can_manage: true,
      can_review: false,
    });
    expect(primaryNav('firm_owner', owner).map((item) => item.id)).toEqual([
      'firm',
      'accounting',
      'monthly',
      'cases',
    ]);
  });

  it('keeps company administration in settings and restricts main task entries by capability', () => {
    expect(
      primaryNav('admin', profile({ display_role: 'admin', can_manage: true })).map(
        (item) => item.id,
      ),
    ).toEqual(['accounting', 'monthly', 'cases']);
    expect(
      primaryNav('admin', profile({ display_role: 'admin', can_manage: false })).map(
        (item) => item.id,
      ),
    ).toEqual(['cases']);
    expect(
      primaryNav('manager', profile({ role: 'manager', can_manage: true })).some(
        (item) => item.id === 'admin',
      ),
    ).toBe(false);
  });

  it('defines role default landing without changing deep links in App', () => {
    expect(roleHome('reviewer').defaultView).toBe('review');
    expect(roleHome('firm_owner').defaultView).toBe('firm');
    expect(roleHome('admin').defaultView).toBe('admin');
    expect(roleHome('accountant').defaultView).toBe('home');
  });

  it('selects a briefing by current company/book/period, never array order', () => {
    const reports = [
      { book_id: 'b-b', book_name: 'B', company_id: 'c-b', company_name: 'B', period: '2026-10' },
      { book_id: 'b-a', book_name: 'A', company_id: 'c-a', company_name: 'A', period: '2026-10' },
    ];
    expect(selectBriefing(reports, { companyId: 'c-a' })?.book_id).toBe('b-a');
    expect(selectBriefing(reports, { companyId: 'c-a', period: '2026-09' })).toBeUndefined();
    expect(selectBriefing(reports, {})).toBeUndefined();
    expect(selectBriefing([reports[1]], {})).toBeUndefined();
    expect(selectBriefing(reports, { companyId: 'c-a', bookId: 'b-b' })).toBeUndefined();
  });

  it('filters customer tabs by presentation role rather than one fixed menu', () => {
    expect(clientPortalTabs(profile({ role: 'manager' }))).toEqual([
      'home',
      'materials',
      'messages',
      'briefing',
    ]);
    expect(clientPortalTabs(profile({ role: 'viewer' }))).toEqual([
      'home',
      'materials',
      'briefing',
    ]);
    expect(clientPortalTabs(profile({ role: 'employee' }))).toContain('submit');
  });
});
