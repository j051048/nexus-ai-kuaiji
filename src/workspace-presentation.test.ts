import { describe, expect, it } from 'vitest';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import {
  canSeeSurface,
  clientPortalTabs,
  contextMode,
  contextTitle,
  presentationRole,
  primaryNav,
  roleHome,
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
      expect(primaryNav(role).length).toBeLessThanOrEqual(6);
    }
  });

  it('keeps employee navigation focused on own work and materials', () => {
    expect(primaryNav('employee').map((item) => item.id)).toEqual([
      'home',
      'business',
      'materials',
      'messages',
      'briefing',
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
    expect(presentationRole(profile({ can_accounting: true, can_review: true }))).toBe('reviewer');
    expect(presentationRole(profile({ role: 'viewer' }))).toBe('viewer');
    expect(presentationRole(null)).toBe('viewer');
  });

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
    const owner = profile({ display_role: 'firm_owner', can_firm_operations: true, can_manage: true, can_review: false });
    expect(primaryNav('firm_owner', owner).map((item) => item.id)).toEqual([
      'firm',
      'cases',
      'accounting',
      'operations',
      'monthly',
    ]);
  });

  it('exposes admin entry only for an admin display role with manage capability', () => {
    expect(primaryNav('admin', profile({ display_role: 'admin', can_manage: true })).map((item) => item.id)).toEqual([
      'admin',
    ]);
    expect(primaryNav('admin', profile({ display_role: 'admin', can_manage: false }))).toEqual([]);
    expect(primaryNav('manager', profile({ role: 'manager', can_manage: true })).some((item) => item.id === 'admin')).toBe(false);
  });

  it('defines role default landing without changing deep links in App', () => {
    expect(roleHome('reviewer').defaultView).toBe('review');
    expect(roleHome('firm_owner').defaultView).toBe('firm');
    expect(roleHome('admin').defaultView).toBe('admin');
    expect(roleHome('accountant').defaultView).toBe('home');
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
