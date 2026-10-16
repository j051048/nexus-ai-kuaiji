import type { Pagination } from './types';

export type MonthlyDestination = {
  view:
    | 'business'
    | 'accounting'
    | 'review'
    | 'cases'
    | 'delivery'
    | 'closing'
    | 'efficiency'
    | 'balances'
    | 'recurring';
  companyId: string;
  period: string;
  bookId?: string;
  filter?: 'all' | 'pending' | 'exception' | 'approved';
};
export interface MonthlyClientRow {
  row_key: string;
  company_id: string;
  company_name: string;
  book_id: string | null;
  book_name: string | null;
  ledger_id: string | null;
  period: string;
  status: 'open' | 'closed' | 'no_book';
  materials: {
    sources: number | null;
    business_pending: number | null;
    intake_pending: number | null;
    missing: number | null;
  };
  drafts: { pending: number | null; approved: number | null; exported: number | null };
  exceptions: { open: number | null; awaiting_info: number | null; overdue: number | null };
  delivery: {
    unverified: number | null;
    unknown: number | null;
    differences: number | null;
    verified: number | null;
  };
  closing: {
    state: 'not_checked' | 'recheck' | 'closed' | 'restricted';
    blockers: number | null;
    snapshot_id: string | null;
    last_check?: { passed: boolean; issue_count: number; created_at: string } | null;
  };
  efficiency: {
    reviews: number | null;
    manual_actions: number | null;
    handling_seconds: number | null;
  };
  permissions: { edit: boolean; review: boolean; export: boolean; manage: boolean };
  service_plan?: {
    version: number;
    lead_user_id: string | null;
    reviewer_user_id: string | null;
    backup_user_id: string | null;
    contact_user_id: string | null;
    lead_name?: string | null;
    reviewer_name?: string | null;
    due_on: string;
  } | null;
  material_requests?: {
    total: number | null;
    pending: number | null;
    submitted: number | null;
    accepted: number | null;
    required_pending: number | null;
  } | null;
  warnings: string[];
}
export interface MonthlyClients {
  period: string;
  rows: MonthlyClientRow[];
  pagination: Pagination;
  updated_at: string;
  notes: string[];
}

export function monthlyCount(value: number | null | undefined): string {
  return value == null || !Number.isSafeInteger(value) || value < 0
    ? '未授权 / 待核实'
    : String(value);
}
export function monthlyClosingLabel(state: string): string {
  return (
    (
      {
        not_checked: '尚未预检',
        recheck: '需要当前预检',
        closed: '已锁定期间',
        restricted: '未授权预检',
      } as Record<string, string>
    )[state] ?? '状态待核实'
  );
}
export function monthlyRowDestination(
  row: MonthlyClientRow,
  view: MonthlyDestination['view'],
  filter?: MonthlyDestination['filter'],
): MonthlyDestination | null {
  if (view === 'business' && row.materials.business_pending == null) return null;
  if (view !== 'business' && !row.book_id) return null;
  if (['review', 'closing'].includes(view) && !row.permissions.review) return null;
  if (view === 'cases' && row.exceptions.open == null) return null;
  if (view === 'delivery' && row.delivery.unverified == null) return null;
  return {
    view,
    companyId: row.company_id,
    period: row.period,
    ...(row.book_id ? { bookId: row.book_id } : {}),
    ...(filter ? { filter } : {}),
  };
}
