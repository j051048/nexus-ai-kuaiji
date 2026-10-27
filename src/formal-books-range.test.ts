import { describe, expect, it } from 'vitest';
import {
  blockerLines,
  csvFingerprint,
  parseRangeAccountLedger,
  parseRangeTrialBalance,
  rangeAccountLedgerPath,
  rangeExportPath,
  rangeSummary,
  rangeTrialBalancePath,
} from './formal-books-range';

const digest = 'a'.repeat(64);
const scope = {
  firm_id: 'firm-a',
  company_id: 'company-a',
  ledger_id: 'ledger-a',
  book_id: 'book-a',
  start: '2026-10',
  end: '2026-11',
};
const expected = {
  ledgerId: 'ledger-a',
  firmId: 'firm-a',
  companyId: 'company-a',
  start: '2026-10',
  end: '2026-11',
};
const coverage = [
  {
    period: '2026-10',
    status: 'ok',
    book_id: 'book-a',
    opening_cents: 0,
    debit_cents: 1000000,
    credit_cents: 0,
    closing_cents: 1000000,
  },
  {
    period: '2026-11',
    status: 'ok',
    book_id: 'book-b',
    opening_cents: 1000000,
    debit_cents: 0,
    credit_cents: 0,
    closing_cents: 1000000,
  },
];
const balance = {
  kind: 'range_trial_balance',
  scope: { ...scope },
  start: '2026-10',
  end: '2026-11',
  rows: [
    {
      account_id: 'account-a',
      code: '1002',
      name: '银行存款',
      is_leaf: true,
      opening_cents: 0,
      debit_cents: 1000000,
      credit_cents: 0,
      closing_cents: 1000000,
      names: ['银行存款'],
      renamed: false,
    },
  ],
  totals: { opening_cents: 0, debit_cents: 1000000, credit_cents: 0, closing_cents: 1000000 },
  complete: true,
  coverage: coverage.map((item) => ({ ...item })),
  blockers: [],
  fingerprint: digest,
};
const entry = (no: number, debit: number, credit: number, balanceCents: number) => ({
  voucher_id: `voucher-${no}`,
  voucher_date: `2026-10-0${no}`,
  voucher_no: no,
  voucher_type: '记',
  description: '虚构款项',
  line_no: 1,
  debit_cents: debit,
  credit_cents: credit,
  balance_cents: balanceCents,
  period: '2026-10',
});
const ledger = {
  kind: 'range_account_ledger',
  account: { id: 'account-a', code: '1002', name: '银行存款' },
  scope: { ...scope },
  start: '2026-10',
  end: '2026-11',
  opening_cents: 0,
  closing_cents: 1000000,
  entries: [entry(1, 1000000, 0, 1000000)],
  complete: true,
  coverage: coverage.map((item) => ({ ...item })),
  blockers: [],
  fingerprint: digest,
};

describe('continuous period read contracts', () => {
  it('keeps the server completeness verdict and per-month coverage', () => {
    const result = parseRangeTrialBalance(balance, expected);
    expect(result.complete).toBe(true);
    expect(result.coverage.map((item) => item.period)).toEqual(['2026-10', '2026-11']);
    expect(result.rows[0].closing_cents).toBe(1000000);
  });

  it('rejects a scope that does not match the selected ledger or range', () => {
    expect(() =>
      parseRangeTrialBalance({ ...balance, scope: { ...scope, ledger_id: 'ledger-b' } }, expected),
    ).toThrow();
    expect(() =>
      parseRangeTrialBalance({ ...balance, scope: { ...scope, start: '2026-09' } }, expected),
    ).toThrow();
  });

  it('rejects a fingerprint that is not a sha256 digest', () => {
    expect(() => parseRangeTrialBalance({ ...balance, fingerprint: 'short' }, expected)).toThrow();
  });

  it('rejects coverage months whose closing does not equal opening plus movement', () => {
    const broken = {
      ...balance,
      coverage: [{ ...coverage[0], closing_cents: 999 }, { ...coverage[1] }],
    };
    expect(() => parseRangeTrialBalance(broken, expected)).toThrow();
  });

  it('refuses a range that would look complete while a month is missing', () => {
    const gapped = {
      ...balance,
      complete: false,
      coverage: [
        { ...coverage[0] },
        {
          ...coverage[1],
          status: 'missing',
          book_id: null,
          opening_cents: 0,
          debit_cents: 0,
          credit_cents: 0,
          closing_cents: 0,
        },
      ],
      blockers: [{ code: 'missing_period', period: '2026-11', message: '该期间没有可用账套' }],
    };
    const result = parseRangeTrialBalance(gapped, expected);
    expect(result.complete).toBe(false);
    expect(rangeSummary(result)).toContain('不代表完整账簿');
    expect(blockerLines(result.blockers)).toEqual(['2026-11 该月未建账套：该期间没有可用账套']);
  });

  it('rejects a ledger whose running balance does not chain', () => {
    const broken = {
      ...ledger,
      entries: [entry(1, 1000000, 0, 900000)],
    };
    expect(() =>
      parseRangeAccountLedger(broken, { ...expected, accountId: 'account-a' }),
    ).toThrow();
  });

  it('rejects a ledger for another account', () => {
    expect(() =>
      parseRangeAccountLedger(ledger, { ...expected, accountId: 'account-b' }),
    ).toThrow();
  });

  it('reads the fingerprint from the first CSV row it shows on the page', () => {
    const csv = `\ufeffledger-a,company-a,2026-10,2026-11,CNY,完整,${digest}\r\n科目编码,科目名称\r\n`;
    expect(csvFingerprint(csv)).toBe(digest);
    expect(csvFingerprint('科目编码,科目名称')).toBeNull();
    expect(csvFingerprint(`"a,b",${digest}`)).toBe(digest);
  });

  it('skips the human label row that precedes the range header', () => {
    const csv = `\ufeff长期账套ID,企业ID,起始期间,结束期间,币种,完整性,来源指纹\r\nledger-a,company-a,2026-10,2026-11,CNY,完整,${digest}\r\n科目编码,科目名称\r\n`;
    expect(csvFingerprint(csv)).toBe(digest);
  });

  it('builds paths from validated months only', () => {
    expect(rangeTrialBalancePath('ledger-a', '2026-10', '2026-11')).toBe(
      '/ledgers/ledger-a/trial-balance?start=2026-10&end=2026-11',
    );
    expect(rangeAccountLedgerPath('ledger-a', 'account-a', '2026-10', '2026-11')).toBe(
      '/ledgers/ledger-a/ledger/account-a?start=2026-10&end=2026-11',
    );
    expect(rangeExportPath('ledger-a', 'account_ledger', '2026-10', '2026-11', 'account-a')).toBe(
      '/ledgers/ledger-a/books/export?kind=account_ledger&start=2026-10&end=2026-11&account_id=account-a',
    );
    expect(() => rangeTrialBalancePath('ledger-a', '2026-13', '2026-11')).toThrow();
    expect(() => rangeExportPath('ledger-a', 'account_ledger', '2026-10', '2026-11')).toThrow();
  });
});
