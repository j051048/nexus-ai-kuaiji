import type { ReviewPolicyMode } from './review-policy';
export interface ManualReviewLine {
  account_id: string;
  account_version: number;
  debit: string;
  credit: string;
  auxiliary_values: Record<string, string>;
  source_ref: string;
}
export interface ManualReviewContent {
  command_key: string;
  book_id: string;
  voucher_date: string;
  voucher_type: '记' | '收' | '付' | '转';
  description: string;
  reason: string;
  lines: ManualReviewLine[];
}
export interface ManualReviewRequest {
  id: string;
  book_id: string;
  version: number;
  status: 'pending' | 'approved';
  prepared_by: string;
  prepared_name: string | null;
  policy_version: number;
  mode: ReviewPolicyMode;
  independent: boolean | null;
  content: ManualReviewContent;
  reviewed_by: string | null;
  reviewed_name: string | null;
  review_reason: string | null;
  posted_voucher_id?: string | null;
  blocked_reason?: string | null;
  withdrawn?: boolean;
  withdrawal_reason?: string | null;
}
export interface ManualReviewList {
  scope: { book_id: string; company_id: string; firm_id: string; period: string };
  requests: ManualReviewRequest[];
  accounts: { id: string; version: number; code: string; name: string }[];
  can_prepare: boolean;
  can_review: boolean;
}
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const positive = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
export function parseManualReviewList(
  value: unknown,
  expected: { bookId: string; firmId: string | null; companyId?: string },
): ManualReviewList {
  const invalid = () => new Error('手工凭证范围、版本或内容不一致，请重新读取当前账套。');
  if (
    !record(value) ||
    !record(value.scope) ||
    typeof value.scope.company_id !== 'string' ||
    !value.scope.company_id ||
    typeof value.scope.firm_id !== 'string' ||
    !value.scope.firm_id ||
    value.scope.book_id !== expected.bookId ||
    (expected.firmId && value.scope.firm_id !== expected.firmId) ||
    (expected.companyId && value.scope.company_id !== expected.companyId) ||
    typeof value.scope.period !== 'string' ||
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(value.scope.period) ||
    typeof value.can_prepare !== 'boolean' ||
    typeof value.can_review !== 'boolean' ||
    !Array.isArray(value.requests) ||
    !Array.isArray(value.accounts)
  )
    throw invalid();
  for (const item of value.requests) {
    if (
      !record(item) ||
      typeof item.id !== 'string' ||
      item.book_id !== expected.bookId ||
      !positive(item.version) ||
      !(item.policy_version === 0 || positive(item.policy_version)) ||
      !['pending', 'approved'].includes(String(item.status)) ||
      !['independent', 'single_operator'].includes(String(item.mode)) ||
      typeof item.prepared_by !== 'string' ||
      !item.prepared_by ||
      (item.withdrawn !== undefined && typeof item.withdrawn !== 'boolean') ||
      (item.withdrawal_reason !== undefined &&
        item.withdrawal_reason !== null &&
        typeof item.withdrawal_reason !== 'string') ||
      !(item.independent === null || typeof item.independent === 'boolean') ||
      !record(item.content) ||
      item.content.book_id !== expected.bookId ||
      typeof item.content.command_key !== 'string' ||
      item.content.command_key.length < 8 ||
      typeof item.content.description !== 'string' ||
      typeof item.content.voucher_date !== 'string' ||
      !Array.isArray(item.content.lines)
    )
      throw invalid();
    if (
      item.independent === true &&
      (item.status !== 'approved' ||
        item.mode !== 'independent' ||
        !item.reviewed_by ||
        item.reviewed_by === item.prepared_by)
    )
      throw invalid();
    if (
      item.status === 'approved' &&
      (typeof item.reviewed_by !== 'string' ||
        !item.reviewed_by ||
        item.independent !== (item.mode === 'independent'))
    )
      throw invalid();
    for (const line of item.content.lines)
      if (
        !record(line) ||
        typeof line.account_id !== 'string' ||
        !positive(line.account_version) ||
        typeof line.debit !== 'string' ||
        typeof line.credit !== 'string' ||
        !record(line.auxiliary_values) ||
        !Object.values(line.auxiliary_values).every((v) => typeof v === 'string')
      )
        throw invalid();
  }
  for (const item of value.accounts)
    if (
      !record(item) ||
      typeof item.id !== 'string' ||
      !positive(item.version) ||
      typeof item.code !== 'string' ||
      typeof item.name !== 'string'
    )
      throw invalid();
  return value as unknown as ManualReviewList;
}
export function manualCanReview(
  list: ManualReviewList,
  item: ManualReviewRequest,
  userId: string,
): boolean {
  return (
    !!userId &&
    list.can_review &&
    item.status === 'pending' &&
    !item.withdrawn &&
    !item.blocked_reason &&
    (item.mode === 'single_operator' || item.prepared_by !== userId)
  );
}
