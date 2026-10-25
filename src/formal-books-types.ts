/** Read-only display contracts. All balances come from the formal accounting API. */
export type FormalBookKind = 'trial_balance' | 'account_ledger' | 'daily_journal' | 'aux_ledger';
export interface FormalBookScope {
  book_id: string;
  firm_id: string;
  company_id: string;
  period: string;
}
export interface FormalExpectedScope {
  bookId: string;
  firmId: string | null;
  companyId?: string;
  period?: string;
}
export interface FormalAmounts {
  opening_cents: number;
  debit_cents: number;
  credit_cents: number;
  closing_cents: number;
}
export interface FormalBalanceRow extends FormalAmounts {
  account_id: string;
  code: string;
  name: string;
  is_leaf: boolean;
}
export interface FormalEntry {
  voucher_id: string;
  voucher_date: string;
  voucher_no: number;
  voucher_type: string;
  description: string;
  line_no: number;
  debit_cents: number;
  credit_cents: number;
  balance_cents?: number;
  account_id?: string;
  code_snapshot?: string;
  name_snapshot?: string;
}
export interface FormalOpening {
  account_id: string;
  opening_cents: number;
  code_snapshot?: string;
  name_snapshot?: string;
}
export type FormalBookData =
  | {
      kind: 'trial_balance';
      scope: FormalBookScope;
      rows: FormalBalanceRow[];
      totals: FormalAmounts;
    }
  | {
      kind: 'account_ledger';
      scope: FormalBookScope;
      opening_cents: number;
      closing_cents: number;
      entries: FormalEntry[];
    }
  | { kind: 'daily_journal'; scope: FormalBookScope; entries: FormalEntry[] }
  | {
      kind: 'aux_ledger';
      scope: FormalBookScope;
      opening: FormalOpening[];
      vouchers: FormalEntry[];
    };
export interface FormalVoucher {
  voucher: {
    id: string;
    book_id: string;
    firm_id: string;
    company_id: string;
    voucher_date: string;
    voucher_no: number;
    voucher_type: string;
    description: string;
  };
  lines: {
    account_id: string;
    code_snapshot: string;
    name_snapshot: string;
    line_no: number;
    debit_cents: number;
    credit_cents: number;
  }[];
  posting: 'posted';
}
export interface FormalBookFilters {
  accountId?: string;
  key?: string;
  value?: string;
  asOf?: string;
}

const invalid = () => new Error('账簿数据或范围无法确认，请重新读取；仍失败时联系本账套会计。');
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, empty = false): string {
  if (typeof value !== 'string' || (!empty && !value.trim())) throw invalid();
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) throw invalid();
  return value;
}
function positive(value: unknown): number {
  const n = integer(value);
  if (n < 1) throw invalid();
  return n;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw invalid();
  return value;
}
function amounts(value: unknown): FormalAmounts {
  const row = record(value);
  return {
    opening_cents: integer(row.opening_cents),
    debit_cents: integer(row.debit_cents),
    credit_cents: integer(row.credit_cents),
    closing_cents: integer(row.closing_cents),
  };
}
function validDate(value: unknown): string {
  const date = text(value);
  if (!/^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/.test(date)) throw invalid();
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date)
    throw invalid();
  return date;
}
function scopeOf(value: unknown, expected: FormalExpectedScope): FormalBookScope {
  const row = record(value);
  const result = {
    book_id: text(row.book_id),
    firm_id: text(row.firm_id),
    company_id: text(row.company_id),
    period: text(row.period),
  };
  if (
    !expected.firmId ||
    result.book_id !== expected.bookId ||
    result.firm_id !== expected.firmId ||
    (expected.companyId !== undefined && result.company_id !== expected.companyId) ||
    (expected.period !== undefined && result.period !== expected.period) ||
    !/^20\d{2}-(0[1-9]|1[0-2])$/.test(result.period)
  )
    throw invalid();
  return result;
}
function entryOf(value: unknown, kind: FormalBookKind): FormalEntry {
  const row = record(value);
  const result: FormalEntry = {
    voucher_id: text(row.voucher_id),
    voucher_date: validDate(row.voucher_date),
    voucher_no: positive(row.voucher_no),
    voucher_type: text(row.voucher_type),
    description: text(row.description, true),
    line_no: positive(row.line_no),
    debit_cents: integer(row.debit_cents),
    credit_cents: integer(row.credit_cents),
  };
  if (kind !== 'aux_ledger') result.balance_cents = integer(row.balance_cents);
  if (kind !== 'account_ledger') {
    result.account_id = text(row.account_id);
    result.code_snapshot = text(row.code_snapshot);
    result.name_snapshot = text(row.name_snapshot);
  }
  return result;
}

export function parseFormalBooks(
  kind: FormalBookKind,
  value: unknown,
  expected: FormalExpectedScope,
): FormalBookData {
  const payload = record(value);
  const scope = scopeOf(payload.scope, expected);
  if (kind === 'trial_balance') {
    const seen = new Set<string>();
    const rows = list(payload.rows).map((value) => {
      const row = record(value),
        account_id = text(row.account_id);
      if (seen.has(account_id) || typeof row.is_leaf !== 'boolean') throw invalid();
      seen.add(account_id);
      return {
        ...amounts(row),
        account_id,
        code: text(row.code),
        name: text(row.name),
        is_leaf: row.is_leaf,
      };
    });
    return { kind, scope, rows, totals: amounts(payload.totals) };
  }
  if (kind === 'account_ledger')
    return {
      kind,
      scope,
      opening_cents: integer(payload.opening_cents),
      closing_cents: integer(payload.closing_cents),
      entries: list(payload.entries).map((row) => entryOf(row, kind)),
    };
  if (kind === 'daily_journal')
    return { kind, scope, entries: list(payload.entries).map((row) => entryOf(row, kind)) };
  return {
    kind,
    scope,
    opening: list(payload.opening).map((value) => {
      const row = record(value);
      return {
        account_id: text(row.account_id),
        opening_cents: integer(row.opening_cents),
        ...(typeof row.code_snapshot === 'string' ? { code_snapshot: row.code_snapshot } : {}),
        ...(typeof row.name_snapshot === 'string' ? { name_snapshot: row.name_snapshot } : {}),
      };
    }),
    vouchers: list(payload.vouchers).map((row) => entryOf(row, kind)),
  };
}

export function parseFormalVoucher(
  value: unknown,
  expected: FormalExpectedScope,
  voucherId: string,
): FormalVoucher {
  const payload = record(value),
    voucher = record(payload.voucher);
  if (
    payload.posting !== 'posted' ||
    voucher.id !== voucherId ||
    voucher.book_id !== expected.bookId ||
    !expected.firmId ||
    voucher.firm_id !== expected.firmId ||
    (expected.companyId !== undefined && voucher.company_id !== expected.companyId)
  )
    throw invalid();
  return {
    posting: 'posted',
    voucher: {
      id: text(voucher.id),
      book_id: text(voucher.book_id),
      firm_id: text(voucher.firm_id),
      company_id: text(voucher.company_id),
      voucher_date: validDate(voucher.voucher_date),
      voucher_no: positive(voucher.voucher_no),
      voucher_type: text(voucher.voucher_type),
      description: text(voucher.description, true),
    },
    lines: list(payload.lines).map((value) => {
      const row = record(value);
      return {
        account_id: text(row.account_id),
        code_snapshot: text(row.code_snapshot),
        name_snapshot: text(row.name_snapshot),
        line_no: positive(row.line_no),
        debit_cents: integer(row.debit_cents),
        credit_cents: integer(row.credit_cents),
      };
    }),
  };
}

export function formatFormalCents(value: number): string {
  integer(value);
  const amount = BigInt(value),
    absolute = amount < 0n ? -amount : amount;
  return `${amount < 0n ? '-' : ''}${(absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

export function formalBooksPath(
  kind: FormalBookKind,
  bookId: string,
  filters: FormalBookFilters = {},
): string {
  const base = `/books/${encodeURIComponent(text(bookId))}`;
  if (kind === 'aux_ledger') {
    const query = new URLSearchParams({
      key: text(filters.key).trim(),
      value: text(filters.value).trim(),
    });
    if (query.get('key')!.length > 40 || query.get('value')!.length > 200) throw invalid();
    return `${base}/aux-ledger?${query}`;
  }
  const suffix =
    kind === 'trial_balance'
      ? '/trial-balance'
      : kind === 'daily_journal'
        ? '/daily-journal'
        : `/ledger/${encodeURIComponent(text(filters.accountId))}`;
  return `${base}${suffix}${kind !== 'daily_journal' && filters.asOf ? `?as_of=${validDate(filters.asOf)}` : ''}`;
}

export interface FormalRequestTicket {
  scope: string;
  sequence: number;
  signal: AbortSignal;
}
export class FormalBooksRequestGuard {
  private sequence = 0;
  private controller: AbortController | undefined;
  begin(scope: string): FormalRequestTicket {
    this.controller?.abort();
    this.controller = new AbortController();
    return { scope, sequence: ++this.sequence, signal: this.controller.signal };
  }
  accepts(ticket: FormalRequestTicket, scope: string): boolean {
    return ticket.scope === scope && ticket.sequence === this.sequence && !ticket.signal.aborted;
  }
  cancel(): void {
    this.sequence++;
    this.controller?.abort();
  }
}
