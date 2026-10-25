import { describe, expect, it } from 'vitest';
import {
  navigationAllowed,
  navigationCurrent,
  navigationParents,
  roleNavigation,
  workspaceNavigation,
  type NavigationCapabilities,
} from './workspace-navigation';
import { primaryNav } from './workspace-presentation';
import { projectNavigationCapabilities, type NavigationContext } from './navigation-capabilities';
import { workspaceViews } from './workspace-route';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';

const managerOnly: WorkspaceProfile = {
  role: 'accountant',
  display_role: 'firm_owner',
  can_submit: false,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: true,
  can_firm_operations: true,
  companies: [],
  book_count: 0,
};
const context: NavigationContext = {
  accounting: false,
  processing: false,
  operations: false,
  hasBook: false,
  manageAdmin: false,
  manageFirm: false,
};
const roles: PresentationRole[] = [
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

describe('V09 navigation regression', () => {
  it('does not promote management capability into accounting navigation', () => {
    const caps = projectNavigationCapabilities(managerOnly, { ...context, operations: true });
    expect(primaryNav('firm_owner', caps).map((item) => item.id)).toEqual([
      'firm',
      'monthly',
      'cases',
    ]);
    expect(workspaceNavigation(primaryNav('firm_owner', caps), caps).accounting).toEqual([]);
  });
  it('unknown routes have no invented accounting parent', () => {
    expect(navigationCurrent('future_unknown_route')).toBeNull();
    expect(navigationCurrent('__proto__')).toBeNull();
  });
  it('book detail ownership remains accounting regardless of the entry source', () => {
    for (const view of [
      'learning',
      'reconciliation',
      'delivery',
      'ledger',
      'integration',
      'efficiency',
      'closing',
      'settlement',
      'migration',
      'catalog',
      'balances',
      'recurring',
    ]) {
      expect(navigationCurrent(view)).toBe('accounting');
    }
    expect(navigationCurrent('portfolio')).toBe('portfolio');
    expect(navigationCurrent('service')).toBe('monthly');
    expect(navigationCurrent('accounting', true)).toBe('business');
  });
  it('requires an explicit parent for every workspace route with no extra or omitted routes', () => {
    expect(Object.keys(navigationParents).sort()).toEqual([...workspaceViews].sort());
    for (const view of workspaceViews)
      expect(navigationCurrent(view)).toBe(navigationParents[view]);
    for (const view of ['constructor', 'toString', '', '/workspace/ledger']) {
      expect(navigationCurrent(view)).toBeNull();
    }
  });
  it('preserves already-resolved legacy fallbacks without reading role names as capabilities', () => {
    const caps = projectNavigationCapabilities(null, {
      ...context,
      accounting: true,
      processing: true,
      operations: true,
      manageFirm: true,
    });
    expect(caps).toEqual({
      accounting: true,
      monthly: true,
      review: false,
      operations: true,
      quality: false,
      processing: true,
      business: false,
      admin: true,
      access: true,
    });
    expect(primaryNav('firm_owner', caps).map((item) => item.id)).toEqual([
      'firm',
      'accounting',
      'monthly',
      'cases',
    ]);
  });
  it('quality needs both review permission and a selected book; processing uses the resolved surface', () => {
    const reviewer = { ...managerOnly, can_review: true, can_accounting: true };
    expect(projectNavigationCapabilities(reviewer, context)).toMatchObject({
      quality: false,
      processing: false,
    });
    expect(projectNavigationCapabilities(reviewer, { ...context, hasBook: true })).toMatchObject({
      quality: true,
    });
    expect(projectNavigationCapabilities(managerOnly, { ...context, hasBook: true })).toMatchObject(
      { quality: false },
    );
    expect(
      projectNavigationCapabilities(managerOnly, { ...context, processing: true }),
    ).toMatchObject({ processing: true });
  });
  it('admin and member settings depend on existing management permissions, never callback presence or display role', () => {
    for (const role of roles) {
      const profile = { ...managerOnly, display_role: role };
      expect(projectNavigationCapabilities(profile, context)).toMatchObject({
        admin: false,
        access: false,
      });
      expect(
        projectNavigationCapabilities(profile, { ...context, manageAdmin: true }),
      ).toMatchObject({ admin: true, access: false });
      expect(
        projectNavigationCapabilities(profile, { ...context, manageFirm: true }),
      ).toMatchObject({ admin: true, access: true });
    }
  });
  it.each(roles)(
    '%s primary and grouped navigation use exactly the same projection for every capability combination',
    (role) => {
      const keys: (keyof NavigationCapabilities)[] = [
        'accounting',
        'monthly',
        'review',
        'operations',
        'quality',
        'processing',
        'business',
        'admin',
        'access',
      ];
      for (let bits = 0; bits < 2 ** keys.length; bits++) {
        const caps = Object.fromEntries(
          keys.map((key, index) => [key, !!(bits & (1 << index))]),
        ) as NavigationCapabilities;
        const primary = primaryNav(role, caps);
        expect(primary).toEqual(
          roleNavigation(role).filter((item) => navigationAllowed(item, caps)),
        );
        const grouped = workspaceNavigation(primary, caps);
        expect(grouped.primary).toEqual(primary);
        const rendered = [
          ...grouped.primary,
          ...grouped.daily,
          ...grouped.accounting,
          ...grouped.settings.flatMap((group) => group.items),
        ];
        expect(new Set(rendered.map((item) => item.id)).size).toBe(rendered.length);
        expect(rendered.every((item) => navigationAllowed(item, caps))).toBe(true);
      }
    },
  );
});
