import type { BankCandidate, ReconciliationEvent } from './reconciliation-types';

export function reconciliationCents(amount: string): bigint {
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(amount)) throw new Error('金额不是有效的精确十进制字符串');
  const [whole, fraction = ''] = amount.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
}
export function reconciliationMoney(amount: bigint): string { return `${amount / 100n}.${String(amount % 100n).padStart(2, '0')}`; }
export function reconciliationSelectionTotal(rows: BankCandidate[]): string { return reconciliationMoney(rows.reduce((sum, row) => sum + reconciliationCents(row.amount), 0n)); }
export function reconciliationSelectionIssue(event: ReconciliationEvent, rows: BankCandidate[], reason: string): string {
  if (!event.permissions.confirm) return '当前账套或事项不能建立新关联';
  if (!rows.length) return '请选择实际对应的银行流水';
  if (rows.length > 20) return '一次事项最多关联 20 笔流水';
  if (rows.some(row => !row.selectable || row.draft_version === null)) return '所选流水尚有待处理事项，请先核对来源';
  if (new Set(rows.map(row => row.source_id)).size !== rows.length) return '流水选择不能重复';
  if (reconciliationCents(event.amount) !== reconciliationCents(reconciliationSelectionTotal(rows))) return '所选流水合计与业务金额不一致';
  if (!reason.trim() || reason.trim().length > 1000) return '请填写 1–1000 字核对依据';
  return '';
}
export function reconciliationMatchItem(event: ReconciliationEvent, rows: BankCandidate[]) {
  return { book_id: event.book_id, event_id: event.event_id, event_version: event.event_version, fact_version: event.fact_version,
    business_draft_version: event.business_draft_version, business_source_fingerprint: event.business_source_fingerprint,
    banks: rows.map(row => ({ source_id: row.source_id, source_fingerprint: row.source_fingerprint, draft_version: row.draft_version })).sort((a, b) => a.source_id.localeCompare(b.source_id)) };
}
