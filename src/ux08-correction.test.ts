import { expect, it } from 'vitest';
import { correctionSeed } from './voucher-correction';
const original = {
  posting: 'posted',
  voucher: {
    id: 'v1',
    book_id: 'a',
    company_id: 'co',
    firm_id: 'f',
    voucher_date: '2026-10-12',
    voucher_type: '记',
    voucher_no: 1,
    description: '原件',
  },
  lines: [
    {
      account_id: 'expense',
      account_version: 2,
      debit_cents: 12850,
      credit_cents: 0,
      aux_json: '{"department":"sales"}',
      source_ref: '原件A',
    },
    {
      account_id: 'cash',
      account_version: 1,
      debit_cents: 0,
      credit_cents: 12850,
      aux_json: '{}',
      source_ref: '原件A',
    },
  ],
};
it('从原记录冲销保留科目版本、辅助维度和原件，交换借贷，不修改原记录', () => {
  const seed = correctionSeed(original, { bookId: 'a', companyId: 'co', firmId: 'f' }, 'reversal');
  expect(seed.original_voucher_id).toBe('v1');
  expect(seed.lines[0]).toEqual({
    account_id: 'expense',
    account_version: 2,
    debit: '0.00',
    credit: '128.50',
    auxiliary_values: { department: 'sales' },
    source_ref: '原件A',
  });
  expect(original.lines[0].debit_cents).toBe(12850);
});
it('调整只带原记录关联，必须由人重新填写差额，不能复制原凭证重复入账', () => {
  expect(
    correctionSeed(
      original,
      { bookId: 'a', companyId: 'co', firmId: 'f' },
      'adjustment',
    ).lines.every((l) => l.debit === '0.00' && l.credit === '0.00'),
  ).toBe(true);
});
it('拒绝跨企业、错账套和缺失会计版本，不能猜测科目', () => {
  expect(() =>
    correctionSeed(original, { bookId: 'b', companyId: 'co', firmId: 'f' }, 'reversal'),
  ).toThrow();
  expect(() =>
    correctionSeed(original, { bookId: 'a', companyId: 'other', firmId: 'f' }, 'reversal'),
  ).toThrow();
  expect(() =>
    correctionSeed(
      { ...original, lines: [{ ...original.lines[0], account_version: null }] },
      { bookId: 'a', companyId: 'co', firmId: 'f' },
      'reversal',
    ),
  ).toThrow();
});
