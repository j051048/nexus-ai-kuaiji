export type ReviewActionTarget = { scopeKey: string; bookId: string; draftId: string };
export type ReviewFeedbackNotice = {
  kind: 'success' | 'error';
  text: string;
  reviewTarget?: ReviewActionTarget;
} | null;

export function reviewFailureNotice(
  text: string,
  target: ReviewActionTarget,
): ReviewFeedbackNotice {
  return { kind: 'error', text, reviewTarget: { ...target } };
}

/** A general notice or another selection's failure is not this review's result. */
export function currentReviewError(
  notice: ReviewFeedbackNotice,
  target: ReviewActionTarget,
): string {
  if (notice?.kind !== 'error' || !notice.reviewTarget) return '';
  return (['scopeKey', 'bookId', 'draftId'] as const).every(
    (key) => !!target[key] && notice.reviewTarget![key] === target[key],
  )
    ? notice.text
    : '';
}
