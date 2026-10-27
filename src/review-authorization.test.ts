import { describe, expect, it } from 'vitest';
import {
  previewExpired,
  responsibilitySummary,
  reviewAuthorization,
  singleOperatorNote,
} from './review-authorization';

const base = {
  state: 'awaiting_review' as const,
  draft_version: 3,
  maker_user_id: 'maker-1',
  maker_name: '制单会计',
  content_revision: 4,
  policy_version: 1,
  mode: 'independent' as const,
  label: '独立复核',
  independent: null,
  can_review: true,
  blocked_reason: null,
  reviewer_user_id: null,
  reviewer_name: null,
  reviewed_version: null,
};

const item = (patch: Record<string, unknown> = {}) => ({
  ready: true,
  expected_version: 3,
  review_responsibility: { ...base, ...patch },
});

describe('reviewAuthorization', () => {
  it('服务端明确说不能复核时判 denied，而不是从列表读取推断', () => {
    expect(
      reviewAuthorization(
        [item({ can_review: false, blocked_reason: '当前账号没有复核权限' })],
        'user-1',
      ),
    ).toBe('denied');
  });

  it('本人制单在独立复核下被服务端拒绝时判 denied', () => {
    expect(
      reviewAuthorization(
        [
          item({
            can_review: false,
            maker_user_id: 'user-1',
            blocked_reason: '制单责任人不能独立复核本人待审版本，请交另一位有权限的会计',
          }),
        ],
        'user-1',
      ),
    ).toBe('denied');
  });

  it('制单责任人未落定时判 denied', () => {
    expect(
      reviewAuthorization([item({ state: 'unattributed', can_review: false })], 'user-1'),
    ).toBe('denied');
  });

  it('缺少制单责任数据时判 unknown，不当作有权限', () => {
    expect(reviewAuthorization([{ ready: true, expected_version: 3 }], 'user-1')).toBe('unknown');
    expect(reviewAuthorization([], 'user-1')).toBe('unknown');
    expect(reviewAuthorization([item()], '')).toBe('unknown');
  });

  it('只有全部可审项都返回 can_review=true 才是 granted', () => {
    expect(reviewAuthorization([item(), item()], 'user-1')).toBe('granted');
    expect(reviewAuthorization([item(), item({ can_review: false })], 'user-1')).toBe('denied');
  });

  it('未通过检查的项不参与授权判定', () => {
    expect(
      reviewAuthorization([item(), { ...item({ can_review: false }), ready: false }], 'x'),
    ).toBe('granted');
  });
});

describe('previewExpired', () => {
  it('缺少或无法解析的失效时间按已过期处理', () => {
    expect(previewExpired(null, Date.now())).toBe(true);
    expect(previewExpired('not-a-date', Date.now())).toBe(true);
    expect(previewExpired('', Date.now())).toBe(true);
  });

  it('到点即过期，未到点仍有效', () => {
    const now = Date.parse('2026-10-27T10:00:00+08:00');
    expect(previewExpired('2026-10-27T09:59:59+08:00', now)).toBe(true);
    expect(previewExpired('2026-10-27T10:00:00+08:00', now)).toBe(true);
    expect(previewExpired('2026-10-27T10:05:00+08:00', now)).toBe(false);
  });
});

describe('singleOperatorNote', () => {
  it('存在单人办理时要求填写说明并提示非独立复核', () => {
    const value = singleOperatorNote([
      item({ mode: 'single_operator', label: '单人办理，非独立复核' }),
    ]);
    expect(value.required).toBe(true);
    expect(value.note).toContain('不属于独立复核');
  });

  it('独立复核时不要求说明', () => {
    expect(singleOperatorNote([item()])).toEqual({ required: false, note: '' });
  });
});

describe('responsibilitySummary', () => {
  it('区分未落定、独立复核与单人办理', () => {
    expect(responsibilitySummary({ ...base, state: 'unattributed', can_review: false })).toBe(
      '尚未确认制单责任人',
    );
    expect(
      responsibilitySummary({
        ...base,
        state: 'reviewed',
        independent: true,
        reviewer_user_id: 'r-1',
        reviewed_version: 2,
      }),
    ).toBe('已独立复核');
    expect(
      responsibilitySummary({
        ...base,
        state: 'reviewed',
        independent: false,
        reviewer_user_id: 'r-1',
        reviewed_version: 2,
      }),
    ).toBe('已复核（单人办理，非独立复核）');
  });
});
