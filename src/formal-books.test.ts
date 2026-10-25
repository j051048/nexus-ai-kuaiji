import { describe, expect, it } from 'vitest';
import {
  FormalBooksRequestGuard,
  formalBooksPath,
  formatFormalCents,
  parseFormalBooks,
  parseFormalVoucher,
} from './formal-books-types';

const scope = { book_id: 'book-a', firm_id: 'firm-a', company_id: 'company-a', period: '2026-10' };
const expected = { bookId: 'book-a', firmId: 'firm-a', companyId: 'company-a', period: '2026-10' };
const amounts = {
  opening_cents: 10000,
  debit_cents: 2500,
  credit_cents: 500,
  closing_cents: 12000,
};
const row = { account_id: 'account-a', code: '1002', name: '银行存款', is_leaf: true, ...amounts };
const trial = () => ({ scope: { ...scope }, rows: [{ ...row }], totals: { ...amounts } });
const entry = {
  voucher_id: 'voucher-a',
  voucher_date: '2026-10-01',
  voucher_no: 1,
  voucher_type: '记',
  description: '虚构款项',
  debit_cents: 2500,
  credit_cents: 500,
  balance_cents: 12000,
  line_no: 1,
};

describe('formal accounting book read contracts', () => {
  it('reads amounts from the formal read model without recomputing or mixing draft rows', () => {
    const result = parseFormalBooks(
      'trial_balance',
      { ...trial(), drafts: [{ amount: 99999 }] },
      expected,
    );
    expect(result.kind).toBe('trial_balance');
    if (result.kind !== 'trial_balance') throw new Error('wrong kind');
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].closing_cents).toBe(12000);
    expect(result.totals.closing_cents).toBe(12000);
  });

  it.each([
    undefined,
    { ...scope, book_id: 'book-b' },
    { ...scope, firm_id: 'firm-b' },
    { ...scope, company_id: 'company-b' },
    { ...scope, period: '2026-11' },
    { ...scope, period: '2026-99' },
  ])('rejects missing or mismatched response identity', (badScope) => {
    expect(() =>
      parseFormalBooks('trial_balance', { ...trial(), scope: badScope }, expected),
    ).toThrow();
  });

  it.each([
    true,
    null,
    '12000',
    1.1,
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.MAX_SAFE_INTEGER + 1,
  ])('rejects unsafe JSON cents rather than silently rounding them (%s)', (closing) =>
    expect(() =>
      parseFormalBooks(
        'trial_balance',
        { ...trial(), totals: { ...amounts, closing_cents: closing } },
        expected,
      ),
    ).toThrow(),
  );

  it('rejects duplicate account rows and non-boolean leaf flags', () => {
    expect(() =>
      parseFormalBooks('trial_balance', { ...trial(), rows: [row, row] }, expected),
    ).toThrow();
    expect(() =>
      parseFormalBooks(
        'trial_balance',
        { ...trial(), rows: [{ ...row, is_leaf: 'false' }] },
        expected,
      ),
    ).toThrow();
  });

  it('supports a confirmed empty formal balance without presenting drafts as posted', () => {
    expect(
      parseFormalBooks(
        'trial_balance',
        {
          scope,
          rows: [],
          totals: { opening_cents: 0, debit_cents: 0, credit_cents: 0, closing_cents: 0 },
        },
        expected,
      ),
    ).toMatchObject({ rows: [] });
  });

  it('keeps account entries, per-account daily balances, and auxiliary opening separate', () => {
    expect(
      parseFormalBooks(
        'account_ledger',
        { scope, opening_cents: 10000, closing_cents: 12000, entries: [entry] },
        expected,
      ),
    ).toMatchObject({ entries: [{ voucher_id: 'voucher-a', balance_cents: 12000 }] });
    expect(
      parseFormalBooks(
        'daily_journal',
        {
          scope,
          entries: [
            { ...entry, account_id: 'account-a', code_snapshot: '1002', name_snapshot: '银行存款' },
          ],
        },
        expected,
      ),
    ).toMatchObject({ entries: [{ account_id: 'account-a' }] });
    expect(
      parseFormalBooks(
        'aux_ledger',
        {
          scope,
          opening: [{ account_id: 'account-a', opening_cents: 10000 }],
          vouchers: [
            { ...entry, account_id: 'account-a', code_snapshot: '1002', name_snapshot: '银行存款' },
          ],
        },
        expected,
      ),
    ).toMatchObject({
      opening: [{ opening_cents: 10000 }],
      vouchers: [{ voucher_id: 'voucher-a' }],
    });
  });

  it('never manufactures a voucher reference from a voucher number', () => {
    const { voucher_id: _id, ...unlinked } = entry;
    expect(() =>
      parseFormalBooks(
        'account_ledger',
        { scope, opening_cents: 0, closing_cents: 0, entries: [unlinked] },
        expected,
      ),
    ).toThrow();
  });

  it('binds voucher drill-down to both selected ID and current firm/company/book', () => {
    const detail = {
      posting: 'posted',
      voucher: {
        ...scope,
        id: 'voucher-a',
        voucher_date: '2026-10-01',
        voucher_no: 1,
        voucher_type: '记',
        description: '已记账',
      },
      lines: [
        {
          account_id: 'account-a',
          code_snapshot: '1002',
          name_snapshot: '银行存款',
          debit_cents: 0,
          credit_cents: 0,
          line_no: 1,
        },
      ],
      relations: [],
    };
    expect(parseFormalVoucher(detail, expected, 'voucher-a').voucher.id).toBe('voucher-a');
    expect(() => parseFormalVoucher(detail, expected, 'voucher-b')).toThrow();
    for (const [field, value] of [
      ['book_id', 'book-b'],
      ['firm_id', 'firm-b'],
      ['company_id', 'company-b'],
    ]) {
      expect(() =>
        parseFormalVoucher(
          { ...detail, voucher: { ...detail.voucher, [field]: value } },
          expected,
          'voucher-a',
        ),
      ).toThrow();
    }
    expect(() =>
      parseFormalVoucher({ ...detail, posting: 'draft' }, expected, 'voucher-a'),
    ).toThrow();
  });

  it.each([
    [0, '0.00'],
    [1, '0.01'],
    [-1, '-0.01'],
    [123456, '1,234.56'],
    [-123456, '-1,234.56'],
    [9007199254740991, '90,071,992,547,409.91'],
  ] as const)('formats integer cents exactly (%s)', (input, formatted) => {
    expect(formatFormalCents(input)).toBe(formatted);
  });

  it('encodes identifiers and auxiliary filters without constructing another route', () => {
    expect(formalBooksPath('account_ledger', 'a/b', { accountId: 'x/y', asOf: '2026-10-31' })).toBe(
      '/books/a%2Fb/ledger/x%2Fy?as_of=2026-10-31',
    );
    expect(
      formalBooksPath('aux_ledger', 'book-a', { key: 'vendor', value: 'a&company_id=other' }),
    ).toBe('/books/book-a/aux-ledger?key=vendor&value=a%26company_id%3Dother');
    expect(() => formalBooksPath('account_ledger', 'book-a', {})).toThrow();
    expect(() => formalBooksPath('aux_ledger', 'book-a', { key: 'vendor', value: ' ' })).toThrow();
    expect(() => formalBooksPath('trial_balance', 'book-a', { asOf: '2026-02-30' })).toThrow();
  });
});

describe('formal book request ownership', () => {
  it('ignores A arriving after B even when the transport ignores abort', async () => {
    const guard = new FormalBooksRequestGuard();
    let resolveA!: (value: string) => void;
    const delayed = new Promise<string>((resolve) => {
      resolveA = resolve;
    });
    const a = guard.begin('firm-a/book-a');
    const received: string[] = [];
    const waiting = delayed.then((value) => {
      if (guard.accepts(a, 'firm-a/book-b')) received.push(value);
    });
    const b = guard.begin('firm-a/book-b');
    expect(a.signal.aborted).toBe(true);
    if (guard.accepts(b, 'firm-a/book-b')) received.push('B');
    resolveA('A');
    await waiting;
    expect(received).toEqual(['B']);
  });
  it('rejects older refreshes, revoked requests and cross-session reuse', () => {
    const guard = new FormalBooksRequestGuard();
    const first = guard.begin('session-a/book-a');
    const second = guard.begin('session-a/book-a');
    expect(guard.accepts(first, 'session-a/book-a')).toBe(false);
    expect(guard.accepts(second, 'session-b/book-a')).toBe(false);
    guard.cancel();
    expect(second.signal.aborted).toBe(true);
    expect(guard.accepts(second, 'session-a/book-a')).toBe(false);
  });
});
