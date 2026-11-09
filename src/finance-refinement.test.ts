import { describe, expect, it } from 'vitest';
import * as finance from './finance-operations';

const scope = { firm_id: 'f', company_id: 'c', book_id: 'b', period: '2026-11' };
const expected = { firmId: 'f', companyId: 'c', bookId: 'b', period: '2026-11' };
const value = () => ({
  scope,
  asset_id: 'a',
  asset_name: '设备',
  fingerprint: 'a'.repeat(64),
  prior_posted_cents: 3333,
  net_posted_cents: 0,
  amount_cents: 6666,
  cumulative_cents: 6666,
  can_generate: true,
  blockers: [],
  replaces_draft_id: null,
  history: [
    {
      id: 'v',
      book_id: 'old',
      period: '2026-10',
      fingerprint: 'b'.repeat(64),
      net_cents: -3333,
      accum_cents: -3333,
      unsupported: 0,
    },
    {
      id: 'original',
      book_id: 'old',
      period: '2026-10',
      fingerprint: 'c'.repeat(64),
      net_cents: 3333,
      accum_cents: 3333,
      unsupported: 0,
    },
  ],
  accounts: [{ id: 'expense', name: '费用', version: 2, original_version: 1 }],
});
describe('资产人工核对契约', () => {
  it('binds signed history and current difference to asset and scope', () => {
    expect(finance.parseAssetReconciliation(value(), expected, 'a').amount_cents).toBe(6666);
  });
  it.each(['firm_id', 'company_id', 'book_id', 'period'])('rejects wrong %s', (key) => {
    expect(() =>
      finance.parseAssetReconciliation(
        { ...value(), scope: { ...scope, [key]: 'wrong' } },
        expected,
        'a',
      ),
    ).toThrow();
  });
  it('rejects forged actionable totals, booleans and duplicate evidence', () => {
    for (const patch of [
      { amount_cents: 9999 },
      { can_generate: 'true' },
      { blockers: ['先核对'] },
      { history: [value().history[0], value().history[0]] },
    ])
      expect(() =>
        finance.parseAssetReconciliation({ ...value(), ...patch }, expected, 'a'),
      ).toThrow();
  });
});
