import type { AccountCategory, CatalogAccount } from './catalog-types';

export type InvoiceDirection = 'input' | 'output';
export type AccrualAccountRole = 'settlement' | 'profit' | 'tax';

const CATEGORIES: Record<
  InvoiceDirection,
  Record<AccrualAccountRole, readonly AccountCategory[]>
> = {
  output: {
    settlement: ['asset'],
    profit: ['income'],
    tax: ['liability'],
  },
  input: {
    settlement: ['liability'],
    profit: ['expense', 'cost', 'asset'],
    tax: ['asset'],
  },
};

const PREFERRED: Record<InvoiceDirection, Record<AccrualAccountRole, readonly string[]>> = {
  output: {
    settlement: ['1122'],
    profit: ['6001'],
    tax: ['2221.01', '2221'],
  },
  input: {
    settlement: ['2202'],
    profit: ['6602.99'],
    tax: ['2221.02', '2221'],
  },
};

export function accrualAccountCandidates(
  accounts: CatalogAccount[],
  role: AccrualAccountRole,
  direction: InvoiceDirection,
) {
  const categories = CATEGORIES[direction][role];
  return accounts.filter((account) => account.active && categories.includes(account.category));
}

export function defaultInvoiceAccrualAccounts(
  accounts: CatalogAccount[],
  direction: InvoiceDirection,
) {
  const used = new Set<string>();
  const choose = (role: AccrualAccountRole) => {
    const candidates = accrualAccountCandidates(accounts, role, direction).filter(
      (account) => !used.has(account.code),
    );
    const preferred = PREFERRED[direction][role]
      .map((code) => candidates.find((account) => account.code === code))
      .find(Boolean);
    const selected = preferred ?? (candidates.length === 1 ? candidates[0] : undefined);
    if (selected) used.add(selected.code);
    return selected?.code ?? '';
  };
  return {
    settlement_account: choose('settlement'),
    profit_account: choose('profit'),
    tax_account: choose('tax'),
  };
}

export function invoiceAccrualEligible(row: {
  void: boolean;
  red: boolean;
  duplicate: boolean;
  direction: 'input' | 'output' | 'unknown';
  date: string | null;
  amount_cents: number | null;
  tax_cents: number | null;
  total_cents: number | null;
  match: unknown;
  accrual?: unknown;
}) {
  return (
    !row.void &&
    !row.red &&
    !row.duplicate &&
    (row.direction === 'input' || row.direction === 'output') &&
    Boolean(row.date) &&
    row.match === null &&
    !row.accrual &&
    Number.isInteger(row.amount_cents) &&
    Number.isInteger(row.tax_cents) &&
    Number.isInteger(row.total_cents) &&
    (row.amount_cents ?? -1) >= 0 &&
    (row.tax_cents ?? -1) >= 0 &&
    (row.total_cents ?? 0) > 0 &&
    (row.amount_cents ?? 0) + (row.tax_cents ?? 0) === row.total_cents
  );
}
