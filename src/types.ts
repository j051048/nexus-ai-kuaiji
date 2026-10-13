export interface Session {
  token: string;
  mode: 'local' | 'supabase';
  firm_name: string;
  user_name: string;
  firm_id: string | null;
  user_id: string;
  role: FirmRole | null;
  scope_key: string;
}

export type FirmRole = 'owner' | 'admin' | 'operator' | 'reviewer' | 'viewer' | 'client_employee' | 'client_member';
export type BookRole = 'editor' | 'reviewer' | 'viewer';
export type CompanyRole = 'owner' | 'admin' | 'finance' | 'employee';
export interface AuthConfig { mode: 'local' | 'supabase'; supabase_url?: string; publishable_key?: string; personas?: { id: string; label: string }[] }
export interface Firm { id: string; name: string; role: FirmRole }
export interface Me {
  user: { id: string; name: string; email: string };
  firms: Firm[];
  current_firm_id: string | null;
  permissions: { manage_firm: boolean; create_company: boolean; create_book: boolean };
  mode: 'local' | 'supabase';
}
export interface Company { id: string; name: string; tax_id?: string }
export interface Member<Role extends string> { user_id: string; name: string; email: string; role: Role; department_id?: string | null }
export interface BookPermissions { edit: boolean; review: boolean; export: boolean; manage: boolean }

export interface Book {
  id: string;
  name: string;
  period: string;
  period_status?: 'open' | 'closed';
  pending_count: number;
  exception_count: number;
  approved_count: number;
  company_id?: string;
  company_name?: string;
  permissions: BookPermissions;
}

export interface Workbench {
  books: Book[];
  stats: Pick<Book, 'pending_count' | 'exception_count' | 'approved_count'> & { book_count: number };
  pagination?: Pagination;
}

export interface Pagination { limit: number; offset: number; total: number; has_more: boolean }

export interface SourceRow {
  external_id: string;
  date: string;
  description: string;
  counterparty: string;
  amount: string;
  direction: 'expense' | 'income';
  evidence_ref: string;
  stable_transaction_id?: string;
}

export interface Source extends SourceRow {
  id: string;
  status: string;
}

export interface JournalLine {
  account_code: string;
  debit: string;
  credit: string;
  auxiliary_values?: Partial<Record<'department' | 'counterparty' | 'employee' | 'project', string>>;
}

export interface Draft {
  source_inactive?: boolean;
  id: string;
  source_id: string;
  description: string;
  status: 'draft' | 'approved' | 'exported';
  version: number;
  explanation: string;
  issues: string[];
  lines: JournalLine[];
  detail_loaded?: boolean;
  business_origin?: { event_id: string; submitted_version: number } | null;
}

export interface BookDetail {
  book: Book;
  accounts: { code: string; name: string; auxiliary_requirements?: string }[];
  sources: Source[];
  drafts: Draft[];
  pagination?: Pagination;
  source_pagination?: Pagination;
  source_page_ids?: string[];
}
