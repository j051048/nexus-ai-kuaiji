/** Display/command contracts only. The server rechecks authorization and policy at write time. */
export type ReviewPolicyMode = 'independent' | 'single_operator';
export interface ReviewResponsibilityData {
  state: 'unattributed' | 'awaiting_review' | 'reviewed' | 'legacy_unverified';
  draft_version: number;
  maker_user_id: string | null;
  maker_name: string | null;
  content_revision: number | null;
  policy_version: number;
  mode: ReviewPolicyMode;
  label: string;
  independent: boolean | null;
  can_review: boolean;
  blocked_reason: string | null;
  reviewer_user_id: string | null;
  reviewer_name: string | null;
  reviewed_version: number | null;
}
export interface ReviewPolicyData {
  book_id: string;
  ledger_id: string;
  policy: {
    version: number;
    mode: ReviewPolicyMode;
    label: string;
    reason: string;
    confirmed_by: string | null;
    created_at: string | null;
  };
  can_manage: boolean;
  review_summary?: {
    total_reviewed: number;
    independent: number;
    single_operator: number;
    unverified: number;
    single_operator_ratio: string | null;
  };
}
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const positive = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const policyVersion = (value: unknown): value is number => value === 0 || positive(value);
const nullableText = (value: unknown) => value === null || typeof value === 'string';
const nullablePositive = (value: unknown) => value === null || positive(value);
const mode = (value: unknown): value is ReviewPolicyMode =>
  value === 'independent' || value === 'single_operator';

export function reviewModeLabel(value: ReviewPolicyMode): string {
  return value === 'single_operator' ? '单人办理（非独立复核）' : '独立复核';
}
export function boundReviewReason(sent: string | null, editing: string): string {
  return sent ?? (editing.trim() || '已逐项核对原件、金额和会计处理');
}
export function parseReviewResponsibility(
  value: unknown,
  version: number,
): ReviewResponsibilityData | null {
  if (
    !record(value) ||
    !positive(version) ||
    value.draft_version !== version ||
    !['unattributed', 'awaiting_review', 'reviewed', 'legacy_unverified'].includes(
      String(value.state),
    ) ||
    !policyVersion(value.policy_version) ||
    !mode(value.mode) ||
    typeof value.label !== 'string' ||
    typeof value.can_review !== 'boolean' ||
    !(value.independent === null || typeof value.independent === 'boolean') ||
    !nullablePositive(value.content_revision) ||
    !nullablePositive(value.reviewed_version) ||
    !['maker_user_id', 'maker_name', 'blocked_reason', 'reviewer_user_id', 'reviewer_name'].every(
      (key) => nullableText(value[key]),
    )
  )
    return null;
  if (
    value.independent === true &&
    (value.state !== 'reviewed' ||
      value.mode !== 'independent' ||
      !value.maker_user_id ||
      !value.reviewer_user_id ||
      value.maker_user_id === value.reviewer_user_id ||
      !positive(value.reviewed_version))
  )
    return null;
  return value as unknown as ReviewResponsibilityData;
}
export function canApproveResponsibility(
  value: ReviewResponsibilityData | null,
  userId: string,
): boolean {
  return (
    !!value &&
    value.state === 'awaiting_review' &&
    value.can_review &&
    !!value.maker_user_id &&
    !!userId &&
    (value.mode === 'single_operator' || value.maker_user_id !== userId)
  );
}
export function approvalBody(value: unknown, version: number, userId: string, reason: string) {
  const review = parseReviewResponsibility(value, version);
  if (!canApproveResponsibility(review, userId) || !review)
    throw new Error('当前版本的制单责任或复核资格尚未通过。请重新读取，并交由符合政策的会计复核。');
  const explanation = reason.trim();
  if (review.mode === 'single_operator' && explanation.length < 3)
    throw new Error('请填写至少 3 个字的单人办理说明；该办理不属于独立复核。');
  return {
    expected_version: version,
    expected_policy_version: review.policy_version,
    review_reason: explanation || '已核对原件、金额和会计处理',
  };
}
export function parseReviewPolicy(value: unknown, bookId: string): ReviewPolicyData {
  if (
    !record(value) ||
    value.book_id !== bookId ||
    typeof value.ledger_id !== 'string' ||
    !value.ledger_id ||
    typeof value.can_manage !== 'boolean' ||
    !record(value.policy) ||
    !policyVersion(value.policy.version) ||
    !mode(value.policy.mode) ||
    typeof value.policy.reason !== 'string' ||
    typeof value.policy.label !== 'string' ||
    !nullableText(value.policy.confirmed_by) ||
    !nullableText(value.policy.created_at)
  )
    throw new Error('复核政策范围或版本无效，请重新读取当前账套。');
  const s = value.review_summary;
  if (s !== undefined) {
    if (
      !record(s) ||
      !['total_reviewed', 'independent', 'single_operator', 'unverified'].every((k) =>
        policyVersion(s[k]),
      ) ||
      s.total_reviewed !==
        (s.independent as number) + (s.single_operator as number) + (s.unverified as number) ||
      (s.total_reviewed === 0
        ? s.single_operator_ratio !== null
        : typeof s.single_operator_ratio !== 'string' ||
          !/^0\.\d{4}$|^1\.0000$/.test(s.single_operator_ratio) ||
          Math.abs(
            Number(s.single_operator_ratio) -
              (s.single_operator as number) / (s.total_reviewed as number),
          ) > 0.000051)
    )
      throw new Error('复核分类统计不完整，请重新读取；未知不能计为独立复核。');
  }
  return value as unknown as ReviewPolicyData;
}
