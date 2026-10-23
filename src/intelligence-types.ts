import type { JournalLine } from './types';
import type { QueueJob } from './queue-types';

export interface IntelligenceConfig {
  structured_ready?: boolean;
  configured: boolean;
  vision_ready: boolean;
  journal_ready: boolean;
  provider_label: string;
  enabled_company_ids: string[];
}
export interface IntelligenceSettings {
  enabled: boolean;
  can_manage: boolean;
}
export interface OcrDocument {
  document_type: string;
  invoice_number: string | null;
  issued_on: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  gross_amount: string | null;
  net_amount: string | null;
  tax_amount: string | null;
  currency: string | null;
  pages: { page: number; raw_text: string }[];
  evidence: { field: string; page: number; quote: string }[];
}
export interface OcrResult {
  documents: OcrDocument[];
}
export interface JournalProposal {
  description: string;
  lines: JournalLine[];
  reasoning: string;
  evidence_refs: { attachment_id: string; extraction_id: string; field: string; page: number }[];
  warnings: string[];
}
export interface IntelligenceRun {
  extraction_method?: 'structured_xml' | 'vision_model';
  verification_status?: 'not_verified';
  method_label?: string;
  id: string;
  kind: 'ocr' | 'proposal';
  status: 'running' | 'succeeded' | 'failed' | 'stale';
  attachment_id?: string | null;
  draft_id?: string | null;
  book_id?: string | null;
  input_version: number;
  result: OcrResult | JournalProposal | null;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  error_code?: string | null;
  error_detail?: string | null;
  created_at: string;
}
export interface IntelligenceFinding {
  id: string | null;
  code: string;
  severity: 'blocking' | 'warning';
  payload: { message: string; [key: string]: unknown };
  non_overridable?: boolean;
  resolved: boolean;
  resolution_reason?: string | null;
  event_id?: string;
  event_description?: string;
  company_name?: string;
}
export interface BankCandidate {
  id: string;
  external_id: string;
  date: string;
  description: string;
  counterparty: string;
  amount: string;
  direction: 'expense' | 'income';
  status?: string;
}
export interface BankLink {
  bank_source_id: string;
  reason?: string;
  external_id?: string;
  amount?: string;
  [key: string]: unknown;
}
export interface IntelligenceDetail {
  runs: IntelligenceRun[];
  findings: IntelligenceFinding[];
  bank_candidates: BankCandidate[];
  bank_links: BankLink[];
  permissions: {
    process: boolean;
    resolve: boolean;
    link_bank: boolean;
    apply_proposal: boolean;
    generate_proposal?: boolean;
  };
  context_fingerprint?: string;
  queue_jobs?: QueueJob[];
}
