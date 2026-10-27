import type { ReviewResponsibilityData } from './review-policy';
import type { JournalLine, Pagination } from './types';

export interface ReviewSelection {
  book_id: string;
  draft_id: string;
  expected_version: number;
}
export interface ReviewPending extends ReviewSelection {
  company_id: string;
  company_name: string;
  book_name: string;
  period: string;
  template: string;
  risk: 'basic_exception' | 'basic_clear';
  description: string;
  source_date: string;
  source_amount: string;
}
export interface ReviewList {
  items: ReviewPending[];
  companies: { id: string; name: string }[];
  pagination: Pagination;
  risk_scope: string;
}
export interface ReviewSnapshot extends ReviewSelection {
  review_responsibility?: ReviewResponsibilityData;
  company_id: string;
  company_name: string;
  book_name: string;
  period: string;
  template: string;
  fact_version: number | null;
  source_amount: string;
  source_date: string;
  description: string;
  evidence_ref: string;
  lines: JournalLine[];
  issues: string[];
  ready: boolean;
  review_fingerprint: string;
}
export interface ReviewPreview {
  id: string;
  expires_at: string;
  items: ReviewSnapshot[];
  ready_count: number;
  blocked_count: number;
}
export interface ReviewReceipt {
  id: string;
  preview_id: string;
  status: 'completed' | 'partial' | 'failed';
  approved_count: number;
  failed_book_count: number;
  results: {
    book_id: string;
    company_name: string;
    book_name: string;
    status: 'succeeded' | 'failed';
    approved_ids: string[];
    error: string | null;
  }[];
}
