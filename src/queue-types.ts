export type QueueJobStatus =
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'succeeded'
  | 'failed'
  | 'interrupted'
  | 'stale'
  | 'cancelled';
export interface QueueAttempt {
  attempt_no: number;
  status: string;
  phase?: string;
  created_at: string;
  finished_at?: string | null;
  error_code?: string | null;
  error_detail?: string | null;
}
export interface QueueJob {
  id: string;
  batch_id: string;
  event_id: string;
  event_description: string;
  attachment_id: string;
  filename: string;
  status: QueueJobStatus;
  attempt_count: number;
  error_code?: string | null;
  error_detail?: string | null;
  created_at: string;
  updated_at: string;
  next_attempt_at?: string | null;
  permissions: { retry: boolean; cancel: boolean };
  attempts?: QueueAttempt[];
}
export interface QueueProgress {
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  interrupted: number;
  stale: number;
  cancelled: number;
  pending: number;
  running: number;
  percent: number;
}
export interface QueueBatch {
  id: string;
  company_id: string;
  company_name: string;
  created_at: string;
  status: 'queued' | 'running' | 'completed' | 'attention' | 'cancelled';
  progress: QueueProgress;
  jobs: QueueJob[];
}
export interface QueueItem {
  event_id: string;
  attachment_id: string;
  expected_version: number;
}
