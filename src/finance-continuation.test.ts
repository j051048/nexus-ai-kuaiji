import { expect, it } from 'vitest';
import * as finance from './finance-operations';

const expected = { firmId: 'f', companyId: 'c', bookId: 'b', period: '2026-10' };
const view = () => ({
  scope: { firm_id: 'f', company_id: 'c', book_id: 'b', period: '2026-10' },
  fingerprint: 'a'.repeat(64),
  locked: false,
  can_allocate: true,
  item: {
    id: 'i',
    book_id: 'b',
    kind: 'payable',
    counterparty_name: '供方',
    amount_cents: 10000,
    remaining_cents: 10000,
    allocated_cents: 0,
    original_date: '2026-10-01',
  },
  source: { kind: 'manual', id: null, version: null, verified: false },
  vouchers: [
    {
      id: 'v',
      book_id: 'b',
      voucher_date: '2026-10-10',
      description: '已复核付款',
      settlement_cents: 3000,
      available_cents: 3000,
    },
  ],
  allocations: [],
});
it('accepts only an exact human settlement preview', () => {
  const result = finance.parseSettlement(view(), expected, 'i');
  expect(result.item.remaining_cents).toBe(10000);
  expect(result.vouchers[0].available_cents).toBe(3000);
});
it.each(['firm_id', 'company_id', 'book_id', 'period'])(
  'rejects foreign %s and item before showing actions',
  (key) => {
    const raw = view();
    expect(() =>
      finance.parseSettlement({ ...raw, scope: { ...raw.scope, [key]: 'foreign' } }, expected, 'i'),
    ).toThrow();
    expect(() => finance.parseSettlement(raw, expected, 'other')).toThrow();
  },
);
it('rejects inconsistent balance, malformed permissions and foreign vouchers', () => {
  const raw = view();
  expect(() =>
    finance.parseSettlement(
      { ...raw, item: { ...raw.item, remaining_cents: 9999 } },
      expected,
      'i',
    ),
  ).toThrow();
  expect(() => finance.parseSettlement({ ...raw, can_allocate: 'true' }, expected, 'i')).toThrow();
  expect(() => finance.parseSettlement({ ...raw, locked: true }, expected, 'i')).toThrow();
  expect(() =>
    finance.parseSettlement(
      { ...raw, vouchers: [{ ...raw.vouchers[0], book_id: 'other' }] },
      expected,
      'i',
    ),
  ).toThrow();
});

it('keeps a finance subpage only for its exact monthly scope', () => {
  expect(
    finance.financeSection('?book=b&company=c&period=2026-10&finance=periodic', expected),
  ).toBe('periodic');
  for (const search of [
    '?book=other&company=c&period=2026-10&finance=periodic',
    '?book=b&company=c&period=2026-10&finance=unknown',
    '?book=b&company=c&period=2026-10&finance=aging&finance=periodic',
    '?book=b&book=other&company=c&period=2026-10&finance=periodic',
  ]) {
    expect(finance.financeSection(search, expected)).toBeUndefined();
  }
});
