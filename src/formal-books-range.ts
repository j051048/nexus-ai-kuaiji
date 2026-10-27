/** Continuous-period read contracts. The server decides completeness; the page never infers it. */
import { formatFormalCents } from './formal-books-types';
import type { FormalAmounts, FormalEntry } from './formal-books-types';

export type RangePeriodStatus = 'ok' | 'missing' | 'unauthorized' | 'unbound';
export interface RangeScope {
  firm_id: string;
  company_id: string;
  ledger_id: string;
  book_id: string;
  start: string;
  end: string;
}
export interface RangePeriod {
  period: string;
  status: RangePeriodStatus;
  book_id: string | null;
  opening_cents: number;
  debit_cents: number;
  credit_cents: number;
  closing_cents: number;
}
export interface RangeBlocker {
  code: string;
  period: string;
  message: string;
}
export interface RangeBalanceRow extends FormalAmounts {
  account_id: string;
  code: string;
  name: string;
  is_leaf: boolean;
  names: string[];
  renamed: boolean;
}
export interface RangeTrialBalance {
  kind: 'range_trial_balance';
  scope: RangeScope;
  start: string;
  end: string;
  rows: RangeBalanceRow[];
  totals: FormalAmounts;
  complete: boolean;
  coverage: RangePeriod[];
  blockers: RangeBlocker[];
  fingerprint: string;
}
export interface RangeLedgerEntry extends Omit<FormalEntry, 'balance_cents'> {
  balance_cents: number;
  period: string;
}
export interface RangeAccountLedger {
  kind: 'range_account_ledger';
  account: { id: string; code: string; name: string };
  scope: RangeScope;
  start: string;
  end: string;
  opening_cents: number;
  closing_cents: number;
  entries: RangeLedgerEntry[];
  complete: boolean;
  coverage: RangePeriod[];
  blockers: RangeBlocker[];
  fingerprint: string;
}

const invalid = () => new Error('连续期间账簿数据无法确认，请重新读取；仍失败时联系本账套会计。');
const statuses: RangePeriodStatus[] = ['ok', 'missing', 'unauthorized', 'unbound'];
const blockerLabels: Record<string, string> = {
  missing_period: '该月未建账套',
  unauthorized: '当前账号无权访问该月',
  opening_missing: '该月未绑定正式期初',
  period_open: '该月尚未关账',
  opening_mismatch: '该月期初与上月期末不一致',
};

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
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw invalid();
  return value;
}
function month(value: unknown): string {
  const period = text(value);
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(period)) throw invalid();
  return period;
}
function amountsOf(value: unknown): FormalAmounts {
  const row = record(value);
  return {
    opening_cents: integer(row.opening_cents),
    debit_cents: integer(row.debit_cents),
    credit_cents: integer(row.credit_cents),
    closing_cents: integer(row.closing_cents),
  };
}
function fingerprint(value: unknown): string {
  const digest = text(value);
  if (!/^[0-9a-f]{64}$/.test(digest)) throw invalid();
  return digest;
}
function scopeOf(value: unknown, expected: RangeExpected): RangeScope {
  const row = record(value);
  const scope = {
    firm_id: text(row.firm_id),
    company_id: text(row.company_id),
    ledger_id: text(row.ledger_id),
    book_id: text(row.book_id),
    start: month(row.start),
    end: month(row.end),
  };
  if (
    !expected.firmId ||
    scope.ledger_id !== expected.ledgerId ||
    scope.firm_id !== expected.firmId ||
    (expected.companyId !== undefined && scope.company_id !== expected.companyId) ||
    scope.start !== expected.start ||
    scope.end !== expected.end
  )
    throw invalid();
  return scope;
}
function coverageOf(value: unknown, scope: RangeScope): RangePeriod[] {
  const periods = list(value).map((raw) => {
    const row = record(raw);
    const status = text(row.status) as RangePeriodStatus;
    if (!statuses.includes(status)) throw invalid();
    const bound = row.book_id;
    if (bound !== null && typeof bound !== 'string') throw invalid();
    const period = month(row.period);
    if (period < scope.start || period > scope.end) throw invalid();
    const amounts = amountsOf(row);
    if (
      amounts.closing_cents !==
      amounts.opening_cents + amounts.debit_cents - amounts.credit_cents
    )
      throw invalid();
    return { period, status, book_id: bound, ...amounts };
  });
  if (!periods.length) throw invalid();
  return periods;
}
function blockersOf(value: unknown): RangeBlocker[] {
  return list(value).map((raw) => {
    const row = record(raw);
    return { code: text(row.code), period: month(row.period), message: text(row.message) };
  });
}
function entryOf(value: unknown, scope: RangeScope): RangeLedgerEntry {
  const row = record(value);
  const period = month(row.period);
  if (period < scope.start || period > scope.end) throw invalid();
  return {
    voucher_id: text(row.voucher_id),
    voucher_date: text(row.voucher_date),
    voucher_no: integer(row.voucher_no),
    voucher_type: text(row.voucher_type),
    description: text(row.description, true),
    line_no: integer(row.line_no),
    debit_cents: integer(row.debit_cents),
    credit_cents: integer(row.credit_cents),
    balance_cents: integer(row.balance_cents),
    period,
  };
}

export interface RangeExpected {
  ledgerId: string;
  firmId: string | null;
  companyId?: string;
  start: string;
  end: string;
}

export function parseRangeTrialBalance(value: unknown, expected: RangeExpected): RangeTrialBalance {
  const payload = record(value);
  const scope = scopeOf(payload.scope, expected);
  const coverage = coverageOf(payload.coverage, scope);
  const seen = new Set<string>();
  const rows = list(payload.rows).map((raw) => {
    const row = record(raw);
    const account_id = text(row.account_id);
    if (seen.has(account_id) || typeof row.is_leaf !== 'boolean') throw invalid();
    seen.add(account_id);
    const names = list(row.names).map((name) => text(name));
    if (!names.length || typeof row.renamed !== 'boolean') throw invalid();
    return {
      ...amountsOf(row),
      account_id,
      code: text(row.code),
      name: text(row.name),
      is_leaf: row.is_leaf,
      names,
      renamed: row.renamed,
    };
  });
  return {
    kind: 'range_trial_balance',
    scope,
    start: month(payload.start),
    end: month(payload.end),
    rows,
    totals: amountsOf(payload.totals),
    complete: payload.complete === true,
    coverage,
    blockers: blockersOf(payload.blockers),
    fingerprint: fingerprint(payload.fingerprint),
  };
}

export function parseRangeAccountLedger(
  value: unknown,
  expected: RangeExpected & { accountId: string },
): RangeAccountLedger {
  const payload = record(value);
  const scope = scopeOf(payload.scope, expected);
  const account = record(payload.account);
  if (text(account.id) !== expected.accountId) throw invalid();
  const opening_cents = integer(payload.opening_cents);
  const closing_cents = integer(payload.closing_cents);
  const entries = list(payload.entries).map((row) => entryOf(row, scope));
  if (entries.length) {
    const first = entries[0]!;
    if (first.balance_cents !== opening_cents + first.debit_cents - first.credit_cents)
      throw invalid();
    for (let index = 1; index < entries.length; index++) {
      const previous = entries[index - 1]!;
      const current = entries[index]!;
      if (
        current.balance_cents !==
        previous.balance_cents + current.debit_cents - current.credit_cents
      )
        throw invalid();
    }
    if (entries[entries.length - 1]!.balance_cents !== closing_cents) throw invalid();
  } else if (closing_cents !== opening_cents) throw invalid();
  return {
    kind: 'range_account_ledger',
    account: { id: expected.accountId, code: text(account.code), name: text(account.name) },
    scope,
    start: month(payload.start),
    end: month(payload.end),
    opening_cents,
    closing_cents,
    entries,
    complete: payload.complete === true,
    coverage: coverageOf(payload.coverage, scope),
    blockers: blockersOf(payload.blockers),
    fingerprint: fingerprint(payload.fingerprint),
  };
}

export function rangeTrialBalancePath(ledgerId: string, start: string, end: string): string {
  const query = new URLSearchParams({ start: month(start), end: month(end) });
  return `/ledgers/${encodeURIComponent(text(ledgerId))}/trial-balance?${query}`;
}

export function rangeAccountLedgerPath(
  ledgerId: string,
  accountId: string,
  start: string,
  end: string,
): string {
  const query = new URLSearchParams({ start: month(start), end: month(end) });
  return `/ledgers/${encodeURIComponent(text(ledgerId))}/ledger/${encodeURIComponent(text(accountId))}?${query}`;
}

export function rangeExportPath(
  ledgerId: string,
  kind: 'trial_balance' | 'account_ledger',
  start: string,
  end: string,
  accountId?: string,
): string {
  const query = new URLSearchParams({ kind, start: month(start), end: month(end) });
  if (kind === 'account_ledger') query.set('account_id', text(accountId));
  return `/ledgers/${encodeURIComponent(text(ledgerId))}/books/export?${query}`;
}

/**
 * The CSV opens with a label row and then the range header; its last column is the
 * fingerprint the page shows. Scan the leading rows so a label change cannot silently
 * disable the check.
 */
export function csvFingerprint(content: string): string | null {
  for (const line of content
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)
    .slice(0, 3)) {
    const cells: string[] = [];
    let cell = '';
    let quoted = false;
    for (let index = 0; index < line.length; index++) {
      const char = line[index];
      if (char === '"') {
        if (quoted && line[index + 1] === '"') {
          cell += '"';
          index++;
        } else quoted = !quoted;
      } else if (char === ',' && !quoted) {
        cells.push(cell);
        cell = '';
      } else cell += char;
    }
    cells.push(cell);
    const digest = cells[cells.length - 1]?.trim() ?? '';
    if (/^[0-9a-f]{64}$/.test(digest)) return digest;
  }
  return null;
}

export function describeBlocker(code: string): string {
  return blockerLabels[code] ?? '该期间不可用，请人工核对';
}

export function blockerLines(blockers: RangeBlocker[]): string[] {
  return blockers.map(
    (blocker) => `${blocker.period} ${describeBlocker(blocker.code)}：${blocker.message}`,
  );
}

export function rangeSummary(data: { complete: boolean; coverage: RangePeriod[] }): string {
  const months = data.coverage.length;
  if (data.complete) return `${months} 个月连续完整，期初与期末衔接已核验`;
  const unusable = data.coverage.filter((item) => item.status !== 'ok').length;
  if (unusable === 0) return `${months} 个月均可查，但尚未全部关账，合计不能当作已结账账簿`;
  return `${months} 个月内有 ${unusable} 个月不可用，合计不代表完整账簿`;
}

export function formatRangeCents(value: number): string {
  return formatFormalCents(value);
}
