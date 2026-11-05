import type { CompanyRole, Me, Session } from './types';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';
import { presentationRole } from './workspace-presentation';

export interface AccountIdentityOptions {
  session: Session;
  profile?: WorkspaceProfile | null;
  me?: Me | null;
  administration?: boolean;
  unavailable?: boolean;
}

const primaryRoles: Record<PresentationRole, string> = {
  employee: '企业员工',
  approver: '部门审批人',
  finance: '企业财务',
  manager: '企业管理人员',
  accountant: '经办会计',
  reviewer: '复核会计',
  firm_owner: '事务所负责人',
  admin: '事务所管理员',
  viewer: '只读成员',
};

const membershipRoles: Record<NonNullable<Session['role']>, string> = {
  owner: '事务所负责人',
  admin: '事务所管理员',
  operator: '经办会计',
  reviewer: '复核会计',
  viewer: '只读成员',
  client_employee: '企业成员',
  client_member: '企业成员',
};

const limitedScope = '仅限获授权的企业、部门及账套';
const firmScope = '本事务所全部企业（不含其他事务所）';
const managementCapability = '管理获授权企业与人员';
const companyRoleLabels: Record<CompanyRole, string> = {
  owner: '企业负责人',
  admin: '企业管理员',
  finance: '企业财务',
  employee: '企业员工',
};

function memberCompanies(value: Me['company_memberships']) {
  if (!Array.isArray(value)) return null;
  const seen = new Set<string>();
  for (const row of value) {
    if (
      !row ||
      typeof row.id !== 'string' ||
      !row.id.trim() ||
      typeof row.name !== 'string' ||
      !row.name.trim() ||
      !Object.prototype.hasOwnProperty.call(companyRoleLabels, row.role) ||
      seen.has(row.id)
    )
      return null;
    seen.add(row.id);
  }
  return value.map((row) => ({ ...row, role: companyRoleLabels[row.role] }));
}

function knownProfile(profile: WorkspaceProfile | null | undefined): profile is WorkspaceProfile {
  return (
    !!profile &&
    (Object.prototype.hasOwnProperty.call(primaryRoles, profile.display_role ?? '') ||
      ['accountant', 'finance', 'approver', 'employee', 'manager', 'viewer'].includes(profile.role))
  );
}

/** Read-only labels. These summaries must never authorize a page or a request. */
export function accountIdentity({
  session,
  profile,
  me,
  administration = false,
  unavailable = false,
}: AccountIdentityOptions): {
  name: string;
  role: string;
  firm: string;
  scope: string;
  capabilities: string[];
  confirmed: boolean;
  unitLabel: string;
  unitName: string;
  clientIdentity: boolean;
  companyMemberships: { id: string; name: string; role: string }[];
} {
  const base = {
    name: session.user_name || '当前账号',
    firm: session.firm_name || '尚未选择事务所',
  };
  const unresolved = (role: string) => ({
    ...base,
    role,
    scope: '授权范围待确认',
    capabilities: [] as string[],
    confirmed: false,
    unitLabel: '所属单位',
    unitName: '所属单位待确认',
    clientIdentity: false,
    companyMemberships: [],
  });
  if (unavailable) return unresolved('权限待重新确认');
  if (!session.firm_id) return unresolved('身份确认中');
  const currentFirms =
    me?.user?.id === session.user_id &&
    me.current_firm_id === session.firm_id &&
    Array.isArray(me.firms)
      ? me.firms.filter((firm) => firm.id === session.firm_id)
      : [];
  const currentFirm = currentFirms.length === 1 ? currentFirms[0] : undefined;
  if (me && !currentFirm) return unresolved('身份确认中');
  base.firm = currentFirm?.name || base.firm;
  const allFirm = !!currentFirm && me?.permissions?.manage_firm === true;
  const affiliation = (clientIdentity: boolean) => {
    const companies = currentFirm ? memberCompanies(me?.company_memberships) : null;
    return {
      clientIdentity,
      unitLabel: clientIdentity ? '所属企业' : '所属事务所',
      unitName: !clientIdentity
        ? base.firm
        : companies === null
          ? '所属企业待确认'
          : companies.length === 0
            ? '尚未加入企业'
            : companies
                .slice(0, 2)
                .map((row) => row.name)
                .join('、') + (companies.length > 2 ? `等 ${companies.length} 家企业` : ''),
      companyMemberships: companies ?? [],
    };
  };

  if (administration) {
    if (!currentFirm || !Object.prototype.hasOwnProperty.call(membershipRoles, currentFirm.role))
      return unresolved('身份确认中');
    const canManage = allFirm || me?.permissions?.manage_admin === true;
    const clientMember = ['client_employee', 'client_member', 'viewer'].includes(currentFirm.role);
    return {
      ...base,
      ...affiliation(
        ['client_employee', 'client_member'].includes(currentFirm.role) ||
          (currentFirm.role === 'viewer' && canManage && !allFirm),
      ),
      role: clientMember && canManage ? '企业管理人员' : membershipRoles[currentFirm.role],
      scope: !canManage ? '当前账号没有管理后台权限' : allFirm ? firmScope : limitedScope,
      capabilities: canManage ? [managementCapability] : [],
      confirmed: true,
    };
  }

  if (!knownProfile(profile)) return unresolved('身份确认中');
  const primary = presentationRole(profile);
  // Older API projections merge both firm-manager roles. The authenticated
  // membership distinguishes an administrator from the firm's owner.
  const legacyAdmin = !profile.display_role && primary === 'firm_owner' && session.role === 'admin';
  const capabilities: string[] = [];
  if (profile.can_submit === true) capabilities.push('提交本人业务');
  if (profile.can_approve === true) capabilities.push('审批获授权部门事项');
  if (profile.can_finance === true) capabilities.push('核验获授权企业资料');
  if (profile.can_accounting === true) capabilities.push('查看获授权账套');
  if (profile.can_review === true) capabilities.push('复核获授权账套');
  if (profile.can_manage === true) capabilities.push(managementCapability);
  return {
    ...base,
    ...affiliation(
      ['employee', 'approver', 'finance', 'manager'].includes(primary) ||
        (primary === 'viewer' &&
          ['client_employee', 'client_member'].includes(currentFirm?.role ?? session.role ?? '')),
    ),
    role: legacyAdmin ? primaryRoles.admin : primaryRoles[primary],
    scope: allFirm ? firmScope : limitedScope,
    capabilities,
    confirmed: true,
  };
}
