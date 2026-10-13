import type { BusinessPayload } from './business-types';

export type FactRevisionState = 'pending' | 'rejected' | 'replaced' | 'adjustment_proposed';
export type FactRevision = {
  id: string; event_id: string; old_book_id: string; target_book_id: string; old_source_id: string; old_draft_id: string;
  state: FactRevisionState; version: number; proposed_by: string; reason: string; created_at: string;
  expected_event_version: number; expected_fact_version: number; expected_draft_version: number;
  proposed_payload: BusinessPayload;
  old_snapshot: { payload: BusinessPayload; event_version: number; fact_version: number; draft_version: number; draft_status: string;
    attachments: { id: string; filename: string; sha256: string }[] };
  permissions: { review: boolean };
  decisions: { id: string; decision: string; result_state: string; reason: string; reviewed_by: string; created_at: string }[];
  lineage: { old_book_id: string; old_draft_id: string; new_book_id: string; new_source_id: string; new_draft_id: string; new_fact_version: number } | null;
};
export type RevisionPage = { items: FactRevision[]; total: number; limit: number; offset: number; has_more: boolean };
export type RevisionContext = { event_id: string; event_version: number; fact_version: number; draft_version: number;
  draft_status: string; template_id: string; payload: BusinessPayload; mapping: { book_id: string; draft_id: string };
  books: { id: string; name: string; period: string }[]; permissions: { submit: boolean }; pending: boolean };
export const revisionStateLabel: Record<FactRevisionState, string> = {
  pending: '等待独立复核', rejected: '修订已拒绝', replaced: '未审草稿已替代', adjustment_proposed: '待处理调整建议',
};
export const revisionFields = [
  ['occurred_on', '发生日期'], ['description', '业务说明'], ['counterparty', '交易对方'], ['amount', '含税金额'],
  ['direction', '收支方向'], ['payment_status', '付款状态'], ['category', '费用类别'], ['purpose', '业务用途'], ['project', '项目'],
] as const;
export function revisionChanges(before: BusinessPayload, after: BusinessPayload) {
  return revisionFields.filter(([key]) => (before[key] ?? '') !== (after[key] ?? '')).map(([key, label]) => ({ key, label, before: before[key] ?? '', after: after[key] ?? '' }));
}
export function revisionSubmitCommand(context: RevisionContext, payload: BusinessPayload, targetBookId: string, reason: string, key: string) {
  return { expected_event_version: context.event_version, expected_fact_version: context.fact_version, expected_draft_version: context.draft_version,
    target_book_id: targetBookId, payload, reason: reason.trim(), idempotency_key: key };
}
