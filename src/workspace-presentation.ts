import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import type { ClientPortalTab } from './ClientPortalShell';

export type WorkSurface = 'client' | 'accounting' | 'firm' | 'admin';
export type PresentationNavItem = {
  id: string;
  label: string;
  hint: string;
};

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
 * Presentation-only. Derive a role from already-authorized server facts when
 * the server does not supply display_role. This never grants an API permission.
 */
export function presentationRole(profile: WorkspaceProfile | null): PresentationRole {
  if (!profile) return 'viewer';
  if (profile.display_role && roleOrder.includes(profile.display_role)) return profile.display_role;
  if (profile.can_firm_operations) return 'firm_owner';
  if (profile.role === 'manager') return 'manager';
  if (profile.can_finance) return 'finance';
  if (profile.can_approve) return 'approver';
  if (profile.can_accounting) return profile.can_review ? 'reviewer' : 'accountant';
  if (profile.can_review) return 'reviewer';
  if (profile.role === 'employee' || profile.can_submit) return 'employee';
  if (profile.role === 'finance') return 'finance';
  if (profile.role === 'approver') return 'approver';
  if (profile.can_manage) return 'admin';
  if (profile.role === 'accountant') return 'accountant';
  return 'viewer';
}

export function workSurface(role: PresentationRole): WorkSurface {
  if (role === 'admin') return 'admin';
  if (role === 'firm_owner') return 'firm';
  if (role === 'accountant' || role === 'reviewer') return 'accounting';
  return 'client';
}

const labels: Record<PresentationRole, { name: string; focus: string; action: string }> = {
  employee: { name: '我的工作台', focus: '先处理本人被退回、待补件和待提交事项。', action: '提交业务' },
  approver: { name: '部门审批', focus: '先处理本部门获授权待审批和超期事项。', action: '处理审批' },
  finance: { name: '财务核验', focus: '核验资料完整性，再移交会计处理。', action: '核验并移交' },
  manager: { name: '企业工作台', focus: '关注获授权财务结果、服务进度和待确认问题。', action: '查看报告' },
  accountant: { name: '会计作业', focus: '处理本人负责的异常、草稿和客户期限。', action: '继续处理' },
  reviewer: { name: '会计复核', focus: '先处理待复核凭证、重大差异和退回重提。', action: '开始复核' },
  firm_owner: { name: '事务所管理', focus: '关注交付风险、人员负载和未分派事项。', action: '协调和分派' },
  admin: { name: '系统管理', focus: '管理企业开通、人员权限和运行异常。', action: '管理企业与账号' },
  viewer: { name: '只读工作台', focus: '查看当前授权范围内的事项和报告。', action: '查看' },
};

export function roleHome(role: PresentationRole) {
  return { ...labels[role], surface: workSurface(role) };
}

export function primaryNav(role: PresentationRole): PresentationNavItem[] {
  switch (role) {
    case 'employee':
      return [
        { id: 'home', label: '首页', hint: '我的退回与进度' },
        { id: 'business', label: '我的事项', hint: '提交与补件' },
        { id: 'materials', label: '资料', hint: '周期资料' },
        { id: 'messages', label: '消息', hint: '通知与沟通' },
        { id: 'briefing', label: '报告', hint: '获授权结果' },
      ];
    case 'approver':
      return [
        { id: 'home', label: '待审批', hint: '本部门事项' },
        { id: 'business', label: '部门事项', hint: '审批与退回' },
        { id: 'materials', label: '资料', hint: '部门资料' },
        { id: 'messages', label: '消息', hint: '通知与沟通' },
      ];
    case 'finance':
      return [
        { id: 'home', label: '待核验', hint: '资料完整性' },
        { id: 'business', label: '业务事项', hint: '核验与移交' },
        { id: 'materials', label: '资料', hint: '周期资料' },
        { id: 'messages', label: '消息', hint: '客户沟通' },
      ];
    case 'manager':
      return [
        { id: 'home', label: '企业首页', hint: '进度与待确认' },
        { id: 'briefing', label: '报告', hint: '获授权结果' },
        { id: 'materials', label: '资料', hint: '服务资料' },
        { id: 'messages', label: '消息', hint: '沟通记录' },
      ];
    case 'accountant':
      return [
        { id: 'home', label: '今日工作', hint: '我的异常与草稿' },
        { id: 'review', label: '跨企业批量复核', hint: '待复核凭证与差异' },
        { id: 'business', label: '客户事项', hint: '待处理业务' },
        { id: 'cases', label: '异常与补件', hint: '阻塞与退回' },
        { id: 'monthly', label: '月度交付', hint: '客户期间进度' },
        { id: 'materials', label: '资料', hint: '周期资料' },
      ];
    case 'reviewer':
      return [
        { id: 'review', label: '跨企业批量复核', hint: '待复核凭证与差异' },
        { id: 'cases', label: '异常与补件', hint: '退回重提' },
        { id: 'monthly', label: '月度交付', hint: '复核与交付' },
        { id: 'materials', label: '资料', hint: '证据查看' },
      ];
    case 'firm_owner':
      return [
        { id: 'firm', label: '全所概览', hint: '交付风险' },
        { id: 'review', label: '跨企业批量复核', hint: '获授权复核' },
        { id: 'portfolio', label: '客户', hint: '客户组合' },
        { id: 'operations', label: '团队与效率', hint: '负载与投入' },
        { id: 'monthly', label: '交付', hint: '月度进度' },
      ];
    case 'admin':
      return [{ id: 'admin', label: '管理后台', hint: '企业、人员与运行' }];
    default:
      return [
        { id: 'home', label: '首页', hint: '获授权事项' },
        { id: 'materials', label: '资料', hint: '获授权资料' },
        { id: 'briefing', label: '报告', hint: '获授权结果' },
      ];
  }
}

export function clientPortalTabs(profile: WorkspaceProfile): ClientPortalTab[] {
  const role = presentationRole(profile);
  if (role === 'manager') return ['home', 'materials', 'messages', 'briefing'];
  if (role === 'viewer') return ['home', 'materials', 'briefing'];
  if (role === 'employee') return ['home', 'submit', 'materials', 'messages'];
  if (role === 'approver') return ['home', 'submit', 'materials', 'messages'];
  return ['home', 'submit', 'materials', 'messages', 'briefing'];
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
