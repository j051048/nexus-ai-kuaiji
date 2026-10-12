export type BusinessTemplate = 'reimbursement' | 'procurement_expense' | 'daily_cashflow';
export type BusinessScope = 'own' | 'department' | 'company' | 'accounting';
export type BusinessStatus = 'draft' | 'submitted' | 'department_approved' | 'finance_approved' | 'handed_off' | 'returned';
export interface Department { id: string; name: string }
export interface BusinessCompany {
  id: string; name: string; role: string | null; department_id: string | null; departments: Department[];
  can_submit: boolean; can_finance: boolean; can_manage_approvers: boolean;
  approver_department_ids: string[];
}
export interface BusinessContext {
  companies: BusinessCompany[];
  templates: { id: BusinessTemplate; name: string; version: number; description: string }[];
  scopes: Record<BusinessScope, boolean>;
}
export interface BusinessPayload {
  occurred_on: string; description: string; counterparty: string; amount: string;
  direction: 'expense' | 'income'; payment_status: 'paid' | 'unpaid';
  category: 'office' | 'travel' | 'rent' | 'communication' | 'other'; purpose?: string; project?: string;
}
export interface BusinessAttachment { id: string; filename: string; media_type: string; sha256: string; size_bytes: number }
export interface BusinessAction { stage: string; decision: string; reason?: string; created_by?: string; created_at?: string; submitted_version?: number }
export interface BusinessVersion { event_version: number; payload: BusinessPayload; attachment_snapshot: BusinessAttachment[]; created_at: string; created_by: string }
export interface BusinessEvent {
  id: string; firm_id: string; company_id: string; company_name?: string; department_id: string | null; department_name?: string;
  applicant_id: string; applicant_name?: string; template_id: BusinessTemplate; template_version: number; version: number;
  status: BusinessStatus; payload: BusinessPayload; attachments: BusinessAttachment[]; actions: BusinessAction[];
  versions: BusinessVersion[];
  mapping?: { book_id: string; source_id: string; draft_id: string } | null;
  permissions: { edit: boolean; submit: boolean; department_approve: boolean; finance_approve: boolean; return: boolean; handoff: boolean; process_intelligence?: boolean };
}
