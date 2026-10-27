import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  approvalBody,
  boundReviewReason,
  canApproveResponsibility,
  parseReviewPolicy,
  parseReviewResponsibility,
  reviewModeLabel,
} from './review-policy';
import { ReviewResponsibility } from './ReviewResponsibility';

const responsibility = {
  state: 'awaiting_review',
  draft_version: 4,
  maker_user_id: 'maker-a',
  maker_name: '制单甲',
  content_revision: 2,
  policy_version: 3,
  mode: 'independent',
  label: '独立复核',
  independent: null,
  can_review: true,
  blocked_reason: null,
  reviewer_user_id: null,
  reviewer_name: null,
  reviewed_version: null,
};
const policy = {
  book_id: 'book-a',
  ledger_id: 'ledger-a',
  can_manage: true,
  policy: {
    version: 3,
    mode: 'independent',
    label: '独立复核',
    reason: '正常分工',
    confirmed_by: 'admin-a',
    created_at: '2026-10-25T10:00:00Z',
  },
};

describe('version-bound review policy presentation and commands', () => {
  it('retries preserve the first sent audit reason, including after the editable text changes', () => {
    expect(boundReviewReason('已发出的本次审核理由', '后来修改的文字')).toBe(
      '已发出的本次审核理由',
    );
    expect(boundReviewReason(null, '  本次真实审核依据  ')).toBe('本次真实审核依据');
    expect(boundReviewReason(null, '')).toBe('已逐项核对原件、金额和会计处理');
  });
  it('rejects missing, malformed, stale and permissive-looking responsibility records', () => {
    for (const value of [
      undefined,
      {},
      { ...responsibility, draft_version: 3 },
      { ...responsibility, policy_version: true },
      { ...responsibility, can_review: 'true' },
      { ...responsibility, mode: 'unknown' },
      { ...responsibility, content_revision: -1 },
    ])
      expect(parseReviewResponsibility(value, 4)).toBeNull();
  });
  it('never treats missing attribution or the maker as independent reviewer', () => {
    const value = parseReviewResponsibility(responsibility, 4)!;
    expect(canApproveResponsibility(value, 'maker-a')).toBe(false);
    expect(canApproveResponsibility(value, 'reviewer-b')).toBe(true);
    expect(
      canApproveResponsibility(
        { ...value, state: 'unattributed', maker_user_id: null },
        'reviewer-b',
      ),
    ).toBe(false);
    expect(canApproveResponsibility(null, 'reviewer-b')).toBe(false);
  });
  it('binds approval to the displayed draft and policy versions', () => {
    expect(approvalBody(responsibility, 4, 'reviewer-b', '')).toEqual({
      expected_version: 4,
      expected_policy_version: 3,
      review_reason: '已核对原件、金额和会计处理',
    });
    expect(() => approvalBody(responsibility, 5, 'reviewer-b', '')).toThrow();
    expect(() => approvalBody(responsibility, 4, 'maker-a', '')).toThrow();
  });
  it('requires an explicit reason for single operator review and preserves its classification', () => {
    const single = { ...responsibility, mode: 'single_operator' };
    expect(() => approvalBody(single, 4, 'maker-a', '  ')).toThrow();
    expect(() => approvalBody(single, 4, 'maker-a', '是')).toThrow();
    expect(approvalBody(single, 4, 'maker-a', ' 本人已核对所有资料 ')).toEqual({
      expected_version: 4,
      expected_policy_version: 3,
      review_reason: '本人已核对所有资料',
    });
    expect(reviewModeLabel('single_operator')).toBe('单人办理（非独立复核）');
  });
  it('rejects policy from another book and invalid optimistic concurrency versions', () => {
    expect(parseReviewPolicy(policy, 'book-a').policy.version).toBe(3);
    expect(() => parseReviewPolicy(policy, 'book-b')).toThrow();
    expect(
      parseReviewPolicy({ ...policy, policy: { ...policy.policy, version: 0 } }, 'book-a').policy
        .version,
    ).toBe(0);
    expect(
      approvalBody({ ...responsibility, policy_version: 0 }, 4, 'reviewer-b', '')
        .expected_policy_version,
    ).toBe(0);
    expect(() =>
      parseReviewPolicy({ ...policy, policy: { ...policy.policy, version: -1 } }, 'book-a'),
    ).toThrow();
    expect(() => parseReviewPolicy({ ...policy, can_manage: 1 }, 'book-a')).toThrow();
  });
  it('explains missing attribution without inventing a maker or approval', () => {
    const html = renderToStaticMarkup(
      createElement(ReviewResponsibility, {
        value: {
          ...responsibility,
          state: 'unattributed',
          maker_user_id: null,
          maker_name: null,
          can_review: false,
        },
        version: 4,
        userId: 'reviewer-b',
      }),
    );
    expect(html).toContain('制单责任待确认');
    expect(html).toContain('认领');
    expect(html).not.toContain('已独立复核');
  });
  it('shows the version maker and a concrete next step when independent self-review is blocked', () => {
    const html = renderToStaticMarkup(
      createElement(ReviewResponsibility, {
        value: responsibility,
        version: 4,
        userId: 'maker-a',
      }),
    );
    expect(html).toContain('制单甲');
    expect(html).toContain('版本 4');
    expect(html).toContain('另一名');
    expect(html).not.toContain('重新登录');
  });
  it('renders historical single-operator results as non-independent even if label is misleading', () => {
    const html = renderToStaticMarkup(
      createElement(ReviewResponsibility, {
        value: {
          ...responsibility,
          state: 'reviewed',
          mode: 'single_operator',
          independent: false,
          reviewer_user_id: 'maker-a',
          reviewer_name: '制单甲',
          reviewed_version: 4,
          label: '独立复核',
          can_review: false,
        },
        version: 4,
        userId: 'maker-a',
      }),
    );
    expect(html).toContain('单人办理（非独立复核）');
    expect(html).not.toContain('已独立复核');
  });
  it('does not label contradictory historical identities or single policy as independently reviewed', () => {
    for (const value of [
      {
        ...responsibility,
        state: 'reviewed',
        independent: true,
        reviewer_user_id: 'maker-a',
        reviewed_version: 4,
      },
      {
        ...responsibility,
        state: 'reviewed',
        mode: 'single_operator',
        independent: true,
        reviewer_user_id: 'reviewer-b',
        reviewed_version: 4,
      },
    ]) {
      const html = renderToStaticMarkup(
        createElement(ReviewResponsibility, { value, version: 4, userId: 'maker-a' }),
      );
      expect(html).not.toContain('已独立复核');
      expect(parseReviewResponsibility(value, 4)).toBeNull();
    }
  });
});
