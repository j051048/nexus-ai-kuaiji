import type { Session } from './types';

export type DeliveryLine = {
  ordinal: number;
  account_code: string;
  debit: string;
  credit: string;
  auxiliary_values: Record<string, string>;
};
export type DeliveryManifest = {
  export_id: string;
  status: 'captured' | 'pending_legacy';
  origin: string;
  period?: string;
  company_id?: string;
  book_id?: string;
  currency?: string;
  canonical_fingerprint: string | null;
  catalog_fingerprint: string | null;
  reason?: string;
  items: {
    draft_id: string;
    source_id: string;
    date: string;
    description: string;
    amount: string;
    currency: string;
    approved_version: number;
    exported_version: number;
    lines: DeliveryLine[];
  }[];
};
export type DeliveryReceiptItem = {
  draft_id: string;
  external_voucher_id: string | null;
  reported_status: 'accepted' | 'rejected' | 'unknown';
  state: 'verified' | 'received_unverified' | 'discrepancy';
  issues: string[];
  missing_fields: string[];
  notes: {
    id: string;
    issue_key: string;
    reason: string;
    created_by: string;
    created_at: string;
  }[];
};
export type DeliveryReceipt = {
  id: string;
  export_id: string;
  created_at: string;
  created_by: string;
  reason: string;
  import_format: string;
  raw_hash: string;
  items: DeliveryReceiptItem[];
  summary: { verified: number; received_unverified: number; discrepancy: number };
  evidence_mode: 'manually_supplied_receipt';
  automatic_resend: false;
};
export type DeliveryOverview = {
  book_id: string;
  book_name: string;
  company_id: string;
  company_name: string;
  period: string;
  period_status: 'open' | 'closed';
  can_import: boolean;
  mode: string;
  exports: {
    id: string;
    filename: string;
    count: number;
    created_at: string;
    manifest_status: 'captured' | 'pending_legacy';
    receipt_count: number;
  }[];
  pagination: DeliveryPagination;
};
export type DeliveryPagination = {
  limit: number;
  offset: number;
  total: number;
  has_more: boolean;
};
export type DeliveryDetail = {
  manifest: DeliveryManifest;
  can_import: boolean;
  receipts: DeliveryReceipt[];
  pagination: DeliveryPagination;
};
export type DeliveryWorkbenchProps = {
  session: Session;
  bookId: string;
  companyId?: string;
  period?: string;
  initialExportId?: string;
  onBack: () => void;
  onBusyChange?: (value: boolean) => void;
  embedded?: boolean;
};
