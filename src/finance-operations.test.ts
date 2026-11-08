import { expect, it } from 'vitest';
import { parseAging, parsePeriodic } from './finance-operations';

const scope = { firm_id: 'f', company_id: 'c', book_id: 'b', period: '2026-10' };
const expected = { firmId: 'f', companyId: 'c', bookId: 'b', period: '2026-10' };
const zero = { current: 0, '1_30': 0, '31_60': 0, '61_90': 0, over_90: 0 };
const aging = () => ({
  scope,
  as_of: '2026-10-31',
  buckets: zero,
  by_kind: Object.fromEntries(
    ['receivable', 'payable', 'advance_paid', 'advance_received'].map((k) => [k, zero]),
  ),
  items: [],
});
const periodic = () => ({
  scope,
  locked: false,
  can_generate: true,
  accounts: [],
  assets: [],
  drafts: [],
});
it('preserves separate kinds and explicit empty data', () => {
  expect(Object.keys(parseAging(aging(), expected, '2026-10-31').by_kind)).toHaveLength(4);
  expect(parsePeriodic(periodic(), expected).assets).toEqual([]);
});
it.each(['company_id', 'book_id', 'firm_id', 'period'])(
  'rejects a mismatched %s atomically',
  (key) => {
    expect(() =>
      parseAging({ ...aging(), scope: { ...scope, [key]: 'foreign' } }, expected, '2026-10-31'),
    ).toThrow();
    expect(() =>
      parsePeriodic({ ...periodic(), scope: { ...scope, [key]: 'foreign' } }, expected),
    ).toThrow();
  },
);
it('rejects an old cutoff and inconsistent totals', () => {
  expect(() => parseAging(aging(), expected, '2026-09-30')).toThrow();
  expect(() =>
    parseAging({ ...aging(), buckets: { ...zero, current: 2 } }, expected, '2026-10-31'),
  ).toThrow();
});
it.each([null, true, -1, 0.1, Number.MAX_SAFE_INTEGER + 1])(
  'rejects malformed amount %s',
  (amount) => {
    expect(() =>
      parseAging(
        { ...aging(), by_kind: { ...aging().by_kind, payable: { ...zero, current: amount } } },
        expected,
        '2026-10-31',
      ),
    ).toThrow();
  },
);
it('rejects inferred permissions and missing collections', () => {
  expect(() => parsePeriodic({ ...periodic(), can_generate: 'true' }, expected)).toThrow();
  expect(() => parsePeriodic({ ...periodic(), assets: null }, expected)).toThrow();
  expect(() => parsePeriodic({ ...periodic(), locked: true }, expected)).toThrow();
});
it('rejects foreign draft children and unbalanced lines before exposing actions', () => {
  const draft = {
    id: 'd',
    book_id: 'b',
    period: '2026-10',
    description: '折旧',
    status: 'draft',
    version: 1,
    created_by: 'maker',
  };
  const line = {
    id: 'l',
    draft_id: 'foreign',
    account_id: 'a',
    account_version: 1,
    debit_cents: 1,
    credit_cents: 0,
  };
  expect(() =>
    parsePeriodic(
      { ...periodic(), drafts: [{ draft, lines: [line], can_approve: true, can_post: false }] },
      expected,
    ),
  ).toThrow();
  expect(() =>
    parsePeriodic(
      {
        ...periodic(),
        drafts: [
          { draft, lines: [{ ...line, draft_id: 'd' }], can_approve: true, can_post: false },
        ],
      },
      expected,
    ),
  ).toThrow();
});
