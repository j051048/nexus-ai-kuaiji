import { describe, expect, it } from 'vitest';
import { canReviewAlias, nextPeriod } from './ledger-types';

describe('长期账套边界', () => {
  it('跨年期间不会错误复用旧月份', () => {
    expect(nextPeriod('2026-12')).toBe('2027-01');
    expect(nextPeriod('2026-09')).toBe('2026-10');
    expect(nextPeriod('2099-12')).toBe('');
    expect(nextPeriod('2026-13')).toBe('');
  });
  it('别名审核必须是另一名当前复核人', () => {
    const alias = {
      id: 'a',
      kind: 'counterparty' as const,
      entity_id: 'e',
      original: '原名',
      status: 'pending' as const,
      version: 1,
      created_by: 'one',
      reason: '名称依据',
    };
    expect(canReviewAlias(alias, 'one', true)).toBe(false);
    expect(canReviewAlias(alias, 'two', false)).toBe(false);
    expect(canReviewAlias(alias, 'two', true)).toBe(true);
    expect(canReviewAlias({ ...alias, status: 'retired' }, 'two', true)).toBe(false);
  });
});
