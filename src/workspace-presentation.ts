import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import type { ClientPortalTab } from './ClientPortalShell';
import {
  navigationAllowed,
  roleNavigation,
  type NavigationCapabilities,
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
    name: '子公司报销门户',
    focus: '拍照传票附上原办公审批单号即可提交，优先处理被退回和待补件单据。',
    action: '我要报销',
  },
  approver: {
    name: '部门极简放行',
    focus: '核对原办公审批结果一键放行，优先处理本部门待确认和超期事项。',
    action: '一键放行',
  },
  finance: {
    name: '子公司财务核验',
    focus: '核对原办公审批单与票据原件完整性，一键移交集团共享财务中心入账。',
    action: '核验并移交',
  },
  manager: {
    name: '子公司管理门户',
    focus: '一键放行已过原办公审批单据，随时查看本公司本月收支简报与资料进度。',
    action: '查看经营简报',
  },
  accountant: {
    name: '分管核算会计台',
    focus: '包干处理所分管子公司的票据核对、分录草稿、银企对账与报税月结。',
    action: '进入待办收件箱',
  },
  reviewer: {
    name: '总账复核主管台',
    focus: '批量放行绿通合规单，重点严审高风险红线拦截单与月结总账把关。',
    action: '开始复核',
  },
  firm_owner: {
    name: '集团共享中心总览',
    focus: '总览集团各子公司收支盈亏、结账锁期进度、内部抵销与人员负荷。',
    action: '协调与分派',
  },
  admin: {
    name: '共享中心配置台',
    focus: '管理旗下子公司建档、共享中心分管权限矩阵与智能审核规则。',
    action: '管理子公司与人员',
  },
  viewer: {
    name: '只读查阅工作台',
    focus: '查看当前授权范围内的单据进度、资料清单和已发布经营简报。',
    action: '查看',
  },
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
  capabilities?: NavigationCapabilities,
): PresentationNavItem[] {
  const items = roleNavigation(role);
  if (!capabilities) return items;
  return items.filter((item) => navigationAllowed(item, capabilities));
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
  if (role === 'firm_owner') return '全集团汇总';
  if (role === 'admin') return '共享中心管理';
  if (contextMode(profile) === 'single_company' && companyName) return companyName;
  return companyName || '当前子公司';
}

export function canSeeSurface(profile: WorkspaceProfile | null, surface: WorkSurface) {
  const role = presentationRole(profile);
  if (surface === 'client') return workSurface(role) === 'client';
  if (surface === 'accounting') return workSurface(role) === 'accounting';
  if (surface === 'firm') return workSurface(role) === 'firm';
  return workSurface(role) === 'admin';
}
