import type { Book, CompanyRole, Member } from './types';
import type { Department } from './business-types';

export type CompanyStatus = 'active' | 'suspended' | 'archived';
export interface AdminCompany {
  id: string;
  name: string;
  tax_id: string;
  status: CompanyStatus;
  version: number;
  can_manage: boolean;
  can_change_status?: boolean;
  member_count: number;
  book_count: number;
}
export interface AdminCompanyDetail {
  company: AdminCompany;
  members: Member<CompanyRole>[];
  departments: Department[];
  books: Book[];
  pending_counts: Record<string, number>;
}
export interface AdminPage<T> {
  total: number;
  limit: number;
  offset: number;
  companies?: T[];
}
export interface AdminAuditEvent {
  id: string;
  user_id: string;
  user_name: string;
  action: string;
  object_id: string;
  company_id?: string;
  book_id?: string;
  details: unknown;
  created_at: string;
}
export interface AccountRequest {
  activation_expired?: boolean;
  expires_at?: number;
  id: string;
  email: string;
  name: string;
  status: 'pending' | 'awaiting_activation' | 'completed' | 'failed' | 'cancelled';
  provisioning_mode?: 'activation' | 'password';
  company_id?: string | null;
  department_id?: string | null;
  company_role?: string;
  firm_role?: string;
  error?: string;
  last_error?: string;
  created_at?: string;
}
export const companyStatusLabels: Record<CompanyStatus, string> = {
  active: '正常服务',
  suspended: '已暂停',
  archived: '已归档',
};
export const adminRoleLabels: Record<string, string> = {
  owner: '负责人',
  admin: '管理员',
  finance: '企业财务',
  employee: '员工',
  operator: '经办会计',
  reviewer: '复核会计',
  viewer: '只读成员',
  editor: '经办员',
};
export function canOpenAdmin(permissions: {
  manage_admin?: boolean;
  manage_firm?: boolean;
}): boolean {
  return permissions.manage_admin === true || permissions.manage_firm === true;
}
