export type AuxiliaryDimension = 'department' | 'counterparty' | 'employee' | 'project';
export type AccountCategory =
  'asset' | 'liability' | 'equity' | 'cost' | 'income' | 'expense' | 'other';
export interface CatalogAccount {
  code: string;
  name: string;
  category: AccountCategory;
  normal_balance: 'debit' | 'credit';
  active: boolean;
  auxiliary_requirements: AuxiliaryDimension[];
  version: number;
}
export interface CatalogAccountInput extends Omit<CatalogAccount, 'version'> {
  expected_version?: number | null;
}
export interface CatalogTemplateLine {
  account_code: string;
  side: 'debit' | 'credit';
  amount_field: 'gross' | 'net' | 'tax';
}
export interface CatalogTemplateInput {
  name: string;
  business_type: 'reimbursement' | 'procurement_expense' | 'daily_cashflow';
  direction: 'expense' | 'income';
  category: 'office' | 'travel' | 'rent' | 'communication' | 'other' | null;
  payment_status: 'paid' | 'unpaid' | null;
  active: boolean;
  lines: CatalogTemplateLine[];
}
export interface CatalogTemplate extends CatalogTemplateInput {
  id: string;
  version: number;
}
export interface Catalog {
  book_id: string;
  company_id: string;
  book_name: string;
  period: string;
  can_manage: boolean;
  accounts: CatalogAccount[];
  templates: CatalogTemplate[];
}
export interface CatalogImportPreview {
  valid: boolean;
  create_count: number;
  update_count: number;
  request_fingerprint: string;
  rows: CatalogAccountInput[];
}
export interface CatalogTemplatePreview {
  issues: string[];
  lines: {
    account_code: string;
    debit: string;
    credit: string;
    auxiliary_values: Record<string, string>;
  }[];
}
