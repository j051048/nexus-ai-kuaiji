import type { JournalLine } from './types';

export interface MonthlyAccount {
  code: string;
  name: string;
  active: boolean;
  auxiliary_requirements: string[];
}
export interface MonthlyBalanceLine {
  account_code: string;
  auxiliary_values: Record<string, string>;
  balance: string;
}
export interface MonthlyBaseline {
  id: string;
  book_id: string;
  kind: 'opening' | 'closing';
  version: number;
  period: string;
  external_reference: string;
  evidence_ref: string;
  reason: string;
  source_kind: 'manual' | 'legacy_cutover' | 'bridge_import';
  source_id: string | null;
  created_by: string;
  created_at: string;
  confirmed: boolean;
  confirmed_name: string | null;
  lines: MonthlyBalanceLine[];
  original?: {
    format: 'json' | 'csv' | 'structured_entry' | 'legacy_snapshot' | 'bridge_snapshot';
    filename: string;
    sha256: string;
    download_path: string;
  };
}
export interface MonthlyMovement {
  debit: string;
  credit: string;
  net: string;
}
export interface MonthlyReferenceLine {
  account_code: string;
  account_name: string;
  auxiliary_values: Record<string, string>;
  baseline: string | null;
  baseline_state: string;
  reference_closing: string | null;
  actual: string | null;
  difference: string | null;
  verified_delivery: MonthlyMovement;
  approved_pending_delivery: MonthlyMovement;
  exported_pending_receipt: MonthlyMovement;
  unreviewed: MonthlyMovement;
}
export interface MonthlyReference {
  lines: MonthlyReferenceLine[];
  opening_id: string | null;
  closing_id: string | null;
  unknown_count: number;
  difference_count: number;
  pending_delivery_count: number;
  unreviewed_count: number;
  official_report: false;
  input_fingerprint: string;
}
export interface MonthlyCheck {
  id: string;
  version: number;
  current: boolean;
  created_at: string;
  report: MonthlyReference;
  state: string;
  closure: { closed_name: string; reason: string; created_at: string } | null;
}
export interface RecurringPlan {
  id: string;
  plan_id: string;
  version: number;
  name: string;
  start_period: string;
  end_period: string | null;
  day: number;
  description: string;
  evidence_ref: string;
  reason: string;
  created_by: string;
  created_at: string;
  lines: JournalLine[];
  current: boolean;
  approved: boolean;
  approved_name: string | null;
  applicable: boolean;
  can_generate: boolean;
  can_approve: boolean;
  issues: string[];
  generated_current: {
    draft_id: string;
    book_id: string;
    status: string;
    plan_version: number;
  } | null;
}
export interface MonthlyOverview {
  book_id: string;
  company_id: string;
  ledger_id: string;
  period: string;
  period_status: 'open' | 'closed';
  can_edit: boolean;
  can_review: boolean;
  baselines: MonthlyBaseline[];
  checks: MonthlyCheck[];
  plans: RecurringPlan[];
  reference: MonthlyReference;
  accounts: MonthlyAccount[];
  live_tplus_connected: false;
}

export const monthlyAuxiliaryLabels: Record<string, string> = {
  department: '部门',
  counterparty: '交易对方',
  employee: '人员',
  project: '项目',
};
export function latestMonthlyBaselines(rows: MonthlyBaseline[]) {
  return {
    opening: rows.filter((row) => row.kind === 'opening').sort((a, b) => b.version - a.version)[0],
    closing: rows.filter((row) => row.kind === 'closing').sort((a, b) => b.version - a.version)[0],
  };
}
