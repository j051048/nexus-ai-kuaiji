import type { ReviewSnapshot } from './review-types';

export interface ReviewBasis {
  kind: 'company_rule' | 'template' | 'manual' | 'candidate';
  rule_id: string | null; rule_version: number | null;
  template_id: string | null; template_version: number | null; manual_version: number;
}
export interface ReviewPacketItem extends ReviewSnapshot {
  check_state: 'ready' | 'blocked' | 'stale'; stale_reason: string | null;
  comparison: {
    original_counterparty: string; business_tax_amount: string | null;
    recognized: { run_id: string; tax_amount: string | null; total_amount: string | null; seller: unknown; buyer: unknown }[];
    reference: string;
  };
}
export interface ReviewPacket {
  id: string; company_id: string; company_name: string; book_id: string; book_name: string;
  period: string; template: string; counterparty_id: string | null; counterparty: string;
  party_status: string; basis: ReviewBasis;
  line_shape: { account_code: string; side: string; auxiliary_values: Record<string, string> }[];
  count: number; ready_count: number; blocked_count: number; stale_count: number;
  total_amount: string; min_amount: string; max_amount: string; items: ReviewPacketItem[];
}
export interface ReviewPacketSet {
  id: string; parent_preview_id: string | null; expires_at: string; packages: ReviewPacket[];
  items: ReviewPacketItem[]; ready_count: number; blocked_count: number; stale_count: number;
  confirmed: boolean; scope: string;
}
