import { describe, expect, it } from 'vitest';
import { parseEfficiencyPortfolio } from './efficiency-portfolio-contract';

const request = { period: '2026-11', limit: 20, offset: 0 };
const sample = () => ({
  period: request.period,
  notice: '仅确认计时',
  measurement: 'explicit_book_sessions',
  pagination: { limit: 20, offset: 0, total: 1, has_more: false },
  items: [
    {
      company_id: 'c',
      company_name: '虚构企业',
      period: request.period,
      credited_seconds: null as number | null,
      session_count: 0,
      reviewed_drafts: 1,
      draft_count: 2,
      books: [
        {
          book_id: 'b',
          name: '本月',
          credited_seconds: null as number | null,
          session_count: 0,
          reviewed_drafts: 1,
          draft_count: 2,
        },
      ],
    },
  ],
});
describe('实际投入范围与分母', () => {
  it('preserves missing measurements and explicit zero', () => {
    expect(parseEfficiencyPortfolio(sample(), request).items[0].credited_seconds).toBeNull();
    const v = sample();
    v.items[0] = { ...v.items[0], credited_seconds: 0, session_count: 1 };
    v.items[0].books[0] = { ...v.items[0].books[0], credited_seconds: 0, session_count: 1 };
    expect(parseEfficiencyPortfolio(v, request).items[0].credited_seconds).toBe(0);
  });
  it.each(['period', 'pagination', 'measurement', 'items'])(
    'rejects bad %s before displaying',
    (key) => {
      expect(() => parseEfficiencyPortfolio({ ...sample(), [key]: null }, request)).toThrow();
    },
  );
  it('rejects wrong range, duplicate identities and self-reported totals', () => {
    const base = sample();
    for (const value of [
      { ...base, period: '2026-10' },
      { ...base, pagination: { ...base.pagination, offset: 20 } },
      { ...base, items: [base.items[0], base.items[0]] },
      { ...base, items: [{ ...base.items[0], reviewed_drafts: 2 }] },
      { ...base, items: [{ ...base.items[0], session_count: true }] },
      { ...base, items: [{ ...base.items[0], credited_seconds: 0 }] },
    ])
      expect(() => parseEfficiencyPortfolio(value, request)).toThrow();
  });
});
