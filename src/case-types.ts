export type CaseState = 'open' | 'awaiting_info' | 'response_submitted' | 'in_review' | 'closed';
export interface CaseAttachment { id: string; filename: string; media_type: string; size_bytes: number; sha256: string }
export interface CaseResponse { id: string; state: 'draft' | 'submitted' | 'accepted' | 'returned'; message: string; fields: Record<string, string>; created_at: string; submitted_at: string | null; review_reason: string; attachments: CaseAttachment[] }
export interface CaseTask {
  id: string; company_id: string; company_name: string; event_id: string | null; book_id?: string | null; draft_id?: string | null;
  kind: 'exception' | 'supplement'; title: string; request_reason: string; required_fields: string[]; attachment_required: boolean | number;
  recipient_id: string | null; assignee_id?: string | null; priority: 'normal' | 'high' | 'urgent'; due_on: string | null; state: CaseState;
  version: number; evidence_revision: number; overdue: boolean; updated_at: string;
  permissions: { manage: boolean; respond: boolean; upload: boolean };
  history?: { action: string; audience: 'internal' | 'requester'; reason: string; case_version: number; created_at: string; created_by: string }[];
  responses?: CaseResponse[];
  replacement_choices?: { id: string; filename: string }[];
  collection_request?: boolean;
  source_summary?: { date: string; description: string; counterparty: string; amount: string; direction: 'expense' | 'income' };
}
export const caseStateLabels: Record<CaseState, string> = { open: '待处理', awaiting_info: '待补件', response_submitted: '已提交 · 待核验', in_review: '核验后处理中', closed: '已关闭' };
export function caseCanClose(task: CaseTask): boolean { return task.permissions.manage && ['open', 'in_review'].includes(task.state); }
export function caseReasonError(value: string): string { return value.trim() ? value.trim().length > 2000 ? '处理依据最多 2000 字' : '' : '请填写处理依据'; }
export function caseMissingFields(task: Pick<CaseTask, 'required_fields'>, fields: Record<string, string>): string[] { return task.required_fields.filter(field => !fields[field]?.trim()); }
