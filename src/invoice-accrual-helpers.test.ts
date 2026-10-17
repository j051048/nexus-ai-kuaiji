import { describe, expect, it } from 'vitest';
import type { CatalogAccount } from './catalog-types';
import {
  accrualAccountCandidates,
  defaultInvoiceAccrualAccounts,
  invoiceAccrualEligible,
} from './invoice-accrual-helpers';

const account = (
  code: string,
  name: string,
  category: CatalogAccount['category'],
): CatalogAccount => ({
  code,
  name,
  category,
  normal_balance: category === 'asset' || category === 'expense' ? 'debit' : 'credit',
  active: true,
  auxiliary_requirements: [],
  version: 1,
});

const accounts = [
  account('1122', '应收账款', 'asset'),
  account('2202', '应付账款', 'liability'),
  account('6001', '主营业务收入', 'income'),
  account('6602.99', '管理费用—其他', 'expense'),
  account('2221.01', '销项税额', 'liability'),
  account('2221.02', '进项税额', 'asset'),
];

describe('invoice accrual account mapping', () => {
  it('uses direction-specific categories and standard defaults', () => {
    expect(accrualAccountCandidates(accounts, 'settlement', 'output').map((x) => x.code)).toEqual([
      '1122',
      '2221.02',
    ]);
    expect(defaultInvoiceAccrualAccounts(accounts, 'output')).toEqual({
      settlement_account: '1122',
      profit_account: '6001',
      tax_account: '2221.01',
    });
    expect(defaultInvoiceAccrualAccounts(accounts, 'input')).toEqual({
      settlement_account: '2202',
      profit_account: '6602.99',
      tax_account: '2221.02',
    });
  });
});

describe('invoice accrual eligibility', () => {
  const ready = {
    void: false,
    red: false,
    duplicate: false,
    direction: 'output' as const,
    date: '2026-10-05',
    amount_cents: 100_000,
    tax_cents: 13_000,
    total_cents: 113_000,
    match: null,
    accrual: null,
  };

  it('accepts a complete unmatched invoice', () => {
    expect(invoiceAccrualEligible(ready)).toBe(true);
  });

  it.each([
    { match: { source_id: 'bank' } },
    { duplicate: true },
    { void: true },
    { red: true },
    { total_cents: 112_999 },
    { accrual: { draft_id: 'draft' } },
  ])('rejects an ineligible invoice: %o', (changes) => {
    expect(invoiceAccrualEligible({ ...ready, ...changes })).toBe(false);
  });
});
