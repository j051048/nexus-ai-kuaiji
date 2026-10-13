import type { Pagination } from './types';

export type MigrationProduct = 'chanjet_tplus' | 'yonyou' | 'kingdee' | 'generic';
export type MigrationCategory =
  'package' | 'accounts' | 'auxiliaries' | 'vouchers' | 'balances' | 'open_items' | 'attachment';
export type MigrationRecordKind = Exclude<MigrationCategory, 'package' | 'attachment'>;
export interface MigrationPermissions {
  read: boolean;
  manage: boolean;
  review: boolean;
  activate: boolean;
  can_create?: boolean;
  upload?: boolean;
  independent?: boolean;
}
export interface MigrationTarget {
  book_id: string;
  company_name: string;
  book_name?: string;
  period: string;
  ledger_id?: string;
  empty?: boolean;
  period_status?: string;
}
export interface MigrationProject {
  id: string;
  version: number;
  state: string;
  source_product: MigrationProduct;
  product_version: string;
  external_book_id: string;
  source_system_key: string;
  history_start: string;
  history_end: string;
  cutover_date: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  activated_at?: string;
}
export interface MigrationFile {
  id: string;
  filename: string;
  category: MigrationCategory;
  size_bytes: number;
  state?: string;
  row_count?: number;
  created_at?: string;
  sha256?: string;
  encoding?: string;
  headers?: string[];
  superseded?: boolean;
  mapping?: MigrationColumnMapping;
}
export interface MigrationJob {
  id: string;
  kind: string;
  state: string;
  progress?: number;
  progress_total?: number;
  progress_done?: number;
  attempt?: number;
  error?: string;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
  permissions?: { retry: boolean; cancel: boolean };
  phase?: string;
  status?: string;
  processed_count?: number;
  record_count?: number;
  attempt_count?: number;
  error_detail?: string;
  expected_record_count?: number;
}
export interface MigrationAccountMapping {
  source_code: string;
  target_code: string;
  action: 'use_existing' | 'create';
}
export interface MigrationAuxMapping {
  kind: string;
  source_code: string;
  target_id?: string;
  action: 'use_existing' | 'create';
}
export interface MigrationMapping {
  id?: string;
  version: number;
  accounts?: MigrationAccountMapping[];
  auxiliaries?: MigrationAuxMapping[];
  settlement_accounts?: { receivable?: string[]; payable?: string[] };
}
export interface MigrationIssue {
  id?: string;
  code: string;
  severity: 'blocking' | 'warning' | 'info';
  message: string;
  file_id?: string;
  row_number?: number;
  record_kind?: string;
  external_id?: string;
  next_action?: string;
  details?: Record<string, unknown>;
}
export interface MigrationValidation {
  id: string;
  state: string;
  basis_token: string;
  project_version?: number;
  mapping_version?: number;
  blocking_count: number;
  warning_count: number;
  issues_count?: number;
  valid?: boolean;
  created_at?: string;
  summary?: Record<string, unknown>;
  totals?: Record<string, string | number>;
  current?: boolean;
  coverage?: Record<string, unknown>;
  checks?: Record<string, unknown>;
}
export interface MigrationAcceptance {
  id: string;
  validation_id: string;
  basis_token: string;
  accepted_by?: string;
  created_at?: string;
  state?: string;
}
export interface MigrationCutover {
  id?: string;
  validation_id?: string;
  activated_at?: string;
  state?: string;
  snapshot?: unknown;
}
export interface MigrationDetail {
  project: MigrationProject;
  target: MigrationTarget;
  permissions: MigrationPermissions;
  files: MigrationFile[];
  jobs: MigrationJob[];
  mapping?: MigrationMapping | null;
  latest_validation?: MigrationValidation | null;
  acceptance?: MigrationAcceptance | null;
  cutover?: MigrationCutover | null;
  can_accept?: boolean;
  can_activate?: boolean;
  eligible_bank_books?: { id: string; name: string; period: string; period_status?: string }[];
}
export interface MigrationList {
  projects: MigrationProject[];
  target: MigrationTarget;
  permissions: MigrationPermissions;
  pagination: Pagination;
}
export interface MigrationRecord {
  id?: string;
  kind?: string;
  source_code?: string;
  code?: string;
  name?: string;
  external_id?: string;
  period?: string;
  file_id?: string;
  row_number?: number;
  source_file_id?: string;
  source_row?: number;
  target_code?: string;
  target_id?: string;
  mapping?: MigrationAccountMapping | MigrationAuxMapping;
  data?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface MigrationRecords {
  records: MigrationRecord[];
  pagination: Pagination;
  mapping?: MigrationMapping;
}
export interface MigrationIssues {
  issues: MigrationIssue[];
  pagination: Pagination;
}
export interface MigrationSample {
  id: string;
  state: string;
  voucher_id?: string;
  external_id?: string;
  reason?: string;
  summary?: string;
  created_at?: string;
}
export interface MigrationSamples {
  candidates: MigrationSample[];
  pagination: Pagination;
}
export interface MigrationColumnMapping {
  columns: Record<string, string | number>;
  date_format: 'ymd' | 'dmy' | 'mdy' | 'excel_serial';
  allow_grouping: boolean;
  balance_mode?: 'net_debit' | 'split';
  status_values?: Record<string, 'posted' | 'unposted' | 'void'>;
  confirmed?: boolean;
}
