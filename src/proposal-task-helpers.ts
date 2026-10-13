export type ProposalTaskStatus = 'waiting' | 'queued' | 'running' | 'retry_wait' | 'succeeded' | 'failed' | 'interrupted' | 'stale' | 'cancelled' | 'needs_attention';
export interface ProposalTask {
  id: string; company_id: string; book_id: string; event_id: string; draft_id: string; status: ProposalTaskStatus;
  attempt_count: number; created_at: string; updated_at: string; available_at: string; error_code: string; error_detail: string;
  current_run_id: string | null; expected_fact_version: number; expected_draft_version: number;
  result_rule_id?: string | null; result_rule_version?: number | null; result_template_id: string | null; result_template_version: number | null; result_draft_version: number | null;
  permissions: { retry: boolean; cancel: boolean }; company_name?: string; book_name?: string; draft_description?: string;
  attempts?: { attempt_no: number; status: string; phase: string; created_at: string; finished_at: string | null; error_code: string; error_detail: string; ai_run_id: string | null }[];
}
export const proposalStatusLabels: Record<ProposalTaskStatus, string> = {
  waiting: '等待票据识别', queued: '等待分录处理', running: '正在生成草稿', retry_wait: '等待再次处理', succeeded: '草稿已生成',
  failed: '处理失败', interrupted: '处理中断', stale: '依据或版本变化', cancelled: '已取消', needs_attention: '需要人工处理',
};
export const activeProposalStatuses = new Set<ProposalTaskStatus>(['waiting', 'queued', 'running', 'retry_wait']);
export function proposalOperationReason(value: string): string | null {
  if (!value.trim()) return '请填写处理依据。';
  if (value.trim().length < 3) return '处理依据至少填写 3 字。';
  if (value.trim().length > 1000) return '处理依据不能超过 1000 字。';
  return null;
}
export function hasNewProposalDraft(previous: Map<string, number>, jobs: ProposalTask[]): boolean {
  return jobs.some(job => job.status === 'succeeded' && job.result_draft_version !== null && previous.get(job.id) !== job.result_draft_version);
}
