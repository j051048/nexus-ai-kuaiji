import type { SourceRow } from './types';

export type ImportField = 'external_id' | 'stable_transaction_id' | 'date' | 'description' | 'counterparty' | 'amount' | 'direction' | 'income' | 'expense' | 'evidence_ref';
export interface ImportMapping {
  columns: Partial<Record<ImportField, number>>;
  date_format: 'ymd' | 'dmy' | 'mdy' | 'excel_serial';
  amount_mode: 'direction' | 'split' | 'income_positive' | 'expense_positive';
  allow_grouping: boolean;
}
export interface ImportFileInfo {
  file_id: string; filename: string; sha256: string; headers: string[]; sample_rows: string[][]; row_count: number;
  encoding: string; suggested_columns: Partial<Record<ImportField, number>>; ambiguous_fields: ImportField[]; sheet_name?: string;
}
export interface ImportProfile { id: string; namespace: string; bank_account: string; headers: string[]; mapping: ImportMapping; version: number }
export interface ImportPreviewRow { line: number; original_position: number; raw: string[]; source: SourceRow | null; status: 'new' | 'duplicate' | 'conflict' | 'error'; errors: string[]; warnings: string[] }
export interface ImportPreview { id: string; rows: ImportPreviewRow[]; totals: { income: string; expense: string }; error_count: number; new_count: number; duplicate_count: number; company_name: string; period: string; namespace: string; bank_account: string }
export interface ImportReceipt { book_id: string; imported: number; duplicates: number; preview_id: string; file_id: string; replayed?: boolean }
