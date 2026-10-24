import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import type { ClientPortalTab } from './ClientPortalShell';
import {
  navigationAllowed,
  roleNavigation,
  type PresentationNavItem,
} from './workspace-navigation';

export type WorkSurface = 'client' | 'accounting' | 'firm' | 'admin';
export type { PresentationNavItem } from './workspace-navigation';

const roleOrder: PresentationRole[] = [
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

/**
 * Presentation-only. The server chooses the primary job. Older profiles retain
 * their explicit role, so an additional grant cannot change the main workspace.
 * This never grants an API permission.
 */
export function presentationRole(profile: WorkspaceProfile | null): PresentationRole {
  if (!profile) return 'viewer';
  if (profile.display_role && roleOrder.includes(profile.display_role)) return profile.display_role;
  if (profile.can_firm_operations) return 'firm_owner';
  if (profile.role === 'accountant') return profile.can_review ? 'reviewer' : 'accountant';
  if (roleOrder.includes(profile.role)) return profile.role;
  return 'viewer';
}

export function workSurface(role: PresentationRole): WorkSurface {
  if (role === 'admin') return 'admin';
  if (role === 'firm_owner') return 'firm';
  if (role === 'accountant' || role === 'reviewer') return 'accounting';
  return 'client';
}

const labels: Record<PresentationRole, { name: string; focus: string; action: string }> = {
  employee: {
    name: '我的工作台',
    focus: '先处理本人被退回、待补件和待提交事项。',
    action: '提交业务',
  },
  approver: { name: '部门审批', focus: '先处理本部门获授权待审批和超期事项。', action: '处理审批' },
  finance: { name: '财务核验', focus: '核验资料完整性，再移交会计处理。', action: '核验并移交' },
  manager: {
    name: '企业工作台',
    focus: '关注获授权财务结果、服务进度和待确认问题。',
    action: '查看报告',
  },
  accountant: {
    name: '会计作业',
    focus: '处理本人负责的异常、草稿和客户期限。',
    action: '继续处理',
  },
  reviewer: {
    name: '会计复核',
    focus: '先处理待复核凭证、重大差异和退回重提。',
    action: '开始复核',
  },
  firm_owner: {
    name: '事务所管理',
    focus: '关注交付风险、人员负载和未分派事项。',
    action: '协调和分派',
  },
  admin: {
    name: '系统管理',
    focus: '管理企业开通、人员权限和运行异常。',
    action: '管理企业与账号',
  },
  viewer: { name: '只读工作台', focus: '查看当前授权范围内的事项和报告。', action: '查看' },
};

const defaultViews: Record<PresentationRole, string> = {
  employee: 'home',
  approver: 'home',
  finance: 'home',
  manager: 'home',
  accountant: 'home',
  reviewer: 'review',
  firm_owner: 'firm',
  admin: 'admin',
  viewer: 'home',
};

export function roleHome(role: PresentationRole, profile?: WorkspaceProfile | null) {
  const defaultView =
    role === 'admin' && profile?.can_firm_operations ? 'firm' : defaultViews[role];
  return { ...labels[role], subtitle: labels[role].focus, defaultView };
}

export function primaryNav(
  role: PresentationRole,
  profile?: WorkspaceProfile | null,
): PresentationNavItem[] {
  const items = roleNavigation(role);
  if (!profile) return items;
  return items.filter((item) =>
    navigationAllowed(item, {
      accounting: profile.can_accounting || profile.can_manage,
      monthly: profile.can_accounting || profile.can_manage,
      review: profile.can_review,
      operations: Boolean(profile.can_firm_operations),
      quality: profile.can_review,
      processing: profile.can_accounting,
      business: Boolean(
        profile.can_submit ||
        profile.can_approve ||
        profile.can_finance ||
        profile.can_accounting ||
        profile.can_manage,
      ),
      admin: role === 'admin' && profile.can_manage,
      access: profile.can_manage,
    }),
  );
}

export function clientPortalTabs(profile: WorkspaceProfile): ClientPortalTab[] {
  return clientPortalNav(profile).map((item) => item.id);
}

/** Customer shells and professional shells read the same menu vocabulary. */
export function clientPortalNav(
  profile: WorkspaceProfile,
): (Omit<PresentationNavItem, 'id'> & { id: ClientPortalTab })[] {
  const role = presentationRole(profile);
  if (workSurface(role) !== 'client') return [];
  return roleNavigation(role).map((item) => ({
    ...item,
    id: (item.id === 'business' ? 'submit' : item.id) as ClientPortalTab,
  }));
}

export type BriefingTarget = NonNullable<WorkspaceProfile['briefings']>[number];

/**
 * Never select by array position. A report is usable only when the current
 * company/period is known and exactly one book matches (or the book is known).
 */
export function selectBriefing(
  briefings: BriefingTarget[] | undefined,
  scope: { companyId?: string; bookId?: string; period?: string },
): BriefingTarget | undefined {
  const rows = briefings ?? [];
  const companyId = scope.companyId ?? '';
  const period = scope.period ?? '';
  const bookId = scope.bookId ?? '';
  if (!companyId && !period && !bookId) return undefined;
  const candidates = rows.filter(
    (item) =>
      (!companyId || item.company_id === companyId) &&
      (!period || item.period === period) &&
      (!bookId || item.book_id === bookId),
  );
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function contextMode(profile: WorkspaceProfile | null) {
  const role = presentationRole(profile);
  const count = profile?.companies.length ?? 0;
  if (role === 'admin') return 'system' as const;
  if (role === 'firm_owner') return 'firm' as const;
  return count <= 1 ? ('single_company' as const) : ('multi_company' as const);
}

export function contextTitle(profile: WorkspaceProfile | null, companyName = '') {
  const role = presentationRole(profile);
  if (role === 'firm_owner') return '全所范围';
  if (role === 'admin') return '系统管理';
  if (contextMode(profile) === 'single_company' && companyName) return companyName;
  return companyName ? `${companyName}（获授权企业）` : '当前获授权企业';
}

export function canSeeSurface(profile: WorkspaceProfile | null, surface: WorkSurface) {
  const role = presentationRole(profile);
  if (surface === 'client') return workSurface(role) === 'client';
  if (surface === 'accounting') return workSurface(role) === 'accounting';
  if (surface === 'firm') return workSurface(role) === 'firm';
  return workSurface(role) === 'admin';
}
