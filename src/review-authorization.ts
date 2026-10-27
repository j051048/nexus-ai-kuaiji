import type { ReviewResponsibilityData } from './review-policy';
import { parseReviewResponsibility } from './review-policy';

/**
 * 复核资格的来源：只认服务端在待审列表/预览里返回的 `review_responsibility`。
 * 「列表能读到」不是授权——读取成功只说明能看到这本账套。
 */
export type ReviewAuthorization = 'granted' | 'denied' | 'unknown';

type Item = {
  ready?: boolean;
  expected_version?: number;
  review_responsibility?: unknown;
};

/** 把每条待审版本的制单责任与复核资格汇总成一个授权状态。 */
export function reviewAuthorization(items: readonly Item[], userId: string): ReviewAuthorization {
  if (!items.length || !userId) return 'unknown';
  let seen = false;
  for (const item of items) {
    if (!item.ready) continue;
    const value = parseReviewResponsibility(item.review_responsibility, item.expected_version ?? 0);
    if (!value) continue;
    seen = true;
    // 服务端明确说「不能复核」：权限、制单责任缺失、本人独立复核、锁期都在这一类。
    if (!value.can_review) return 'denied';
    // 制单责任人未落定时，服务端会返回 can_review=false 并带原因；再看状态兜底。
    if (value.state === 'unattributed') return 'denied';
  }
  return seen ? 'granted' : 'unknown';
}

/** 单人办理（非独立复核）需要填写理由，且必须显式提示。 */
export function singleOperatorNote(items: readonly Item[]): { required: boolean; note: string } {
  const single = items.some(
    (item) =>
      parseReviewResponsibility(item.review_responsibility, item.expected_version ?? 0)?.mode ===
      'single_operator',
  );
  return single
    ? {
        required: true,
        note: '本次由制单责任人本人办理，属于单人办理，不属于独立复核；请填写办理说明。',
      }
    : { required: false, note: '' };
}

/** 预览是否已过服务端给的失效时间。 */
export function previewExpired(expiresAt: string | null | undefined, now: number): boolean {
  if (!expiresAt) return true;
  const at = Date.parse(expiresAt);
  return !Number.isFinite(at) || at <= now;
}

/** 供展示用的制单责任摘要（不参与授权判定）。 */
export function responsibilitySummary(value: unknown, version?: number): string {
  const own =
    value && typeof value === 'object' && 'draft_version' in value
      ? Number((value as { draft_version?: unknown }).draft_version)
      : NaN;
  const parsed: ReviewResponsibilityData | null = parseReviewResponsibility(
    value,
    version ?? (Number.isSafeInteger(own) ? own : 0),
  );
  if (!parsed) return '制单责任未知';
  if (parsed.state === 'unattributed') return '尚未确认制单责任人';
  if (parsed.state === 'reviewed')
    return parsed.independent === false ? '已复核（单人办理，非独立复核）' : '已独立复核';
  return `${parsed.maker_name || '制单责任人'} 制单 · ${parsed.label || '待复核'}`;
}
