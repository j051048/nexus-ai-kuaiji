export interface ReconciliationEvent {
  event_id: string; event_version: number; fact_version: number; business_draft_version: number; business_source_fingerprint: string;
  book_id: string; company_id: string; company_name: string; description: string; date: string; counterparty: string; amount: string;
  direction: 'expense' | 'income'; draft_status: string; links: { id: string; date: string; counterparty: string; amount: string }[]; permissions: { confirm: boolean };
}
export interface BankCandidate {
  source_id: string; source_fingerprint: string; draft_version: number | null; date: string; description: string; counterparty: string; amount: string;
  external_id: string; stable_transaction_id: string | null; reasons: string[]; risks: string[]; selectable: boolean;
}
export interface CandidatePage { event: ReconciliationEvent; candidates: BankCandidate[]; total: number; exact_amount_count: number; ambiguous: boolean; offset: number; limit: number; has_more: boolean }
export interface ReconciliationPage { book: { id: string; name: string; company_name: string; period: string; period_status: string }; events: ReconciliationEvent[]; total: number; offset: number; has_more: boolean }
export interface MatchReceipt { id: string; created_at: string; reason: string; items: { book_id: string; event_id: string; bank_source_ids: string[]; links_current: boolean }[]; review_required: boolean }
