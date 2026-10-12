export interface OperationsCompany { id: string; name: string; can_manage_budget: boolean }
export interface OperationsPolicy {
  quota_enabled: boolean; monthly_limit: string; ocr_reserve: string; proposal_reserve: string;
  failure_threshold: number; failure_window_minutes: number; backlog_threshold: number; backlog_age_minutes: number;
  queue_wait_minutes: number; worker_stale_seconds: number; version: number; can_manage: boolean;
}
export interface OperationsPricing { configured: boolean; input_per_million: string | null; output_per_million: string | null; model: string }
export interface OperationsUsage {
  requests: number; input_tokens: number | null; output_tokens: number | null; reported_requests: number; uncertain_requests: number;
  reserved_cost: string | null; carried_reserved_cost: string | null; estimated_cost: string | null; confirmed_cost: string | null;
  exposure: string | null; available: string | null; overrun_count: number;
}
export interface OperationsAlert {
  id: string; code: string; severity: string; state: string; message: string; opened_at: string; last_seen_at: string; version: number;
  reason?: string | null; acknowledgement_reason?: string | null; can_acknowledge?: boolean;
}
export interface OperationsCost {
  id: string; kind: 'ocr' | 'proposal'; model: string; state: string;
  reserved_cost: string | null; estimated_cost: string | null; confirmed_cost: string | null;
  input_tokens: number | null; output_tokens: number | null; created_at: string; version: number; reason: string | null;
  can_reconcile?: boolean;
}
export interface OperationsOverview {
  company: { id: string; name: string }; period: string; currency: 'CNY'; policy: OperationsPolicy;
  pricing: { ocr: OperationsPricing; proposal: OperationsPricing }; usage: OperationsUsage;
  queue: { queued: number; running: number; retry_wait: number; succeeded: number; failed: number; interrupted: number; stale: number; cancelled: number; recent_failures: number; oldest_pending_at: string | null };
  exceptions: { events: number; blocking: number; warning: number; oldest_opened_at: string | null; updated_at: string | null };
  workers: { available: number | boolean; last_heartbeat_at: string | null };
  alerts: OperationsAlert[]; costs: OperationsCost[]; can_manage_budget: boolean;
}
export type OperationsPolicyInput = Omit<OperationsPolicy, 'version' | 'can_manage'> & { expected_version: number };
