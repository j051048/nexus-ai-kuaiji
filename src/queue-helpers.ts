import type { BusinessEvent } from './business-types';
import type { QueueBatch, QueueItem, QueueJob, QueueJobStatus } from './queue-types';

export const MAX_BATCH_ATTACHMENTS = 100;
export const QUEUE_POLL_MS = 2500;
export const RETRY_NOTICE =
  '重试会再次发送附件至模型服务。中断或响应未确认的任务可能已产生供应商费用；请先核对处理记录。';
export const queueStatusLabels: Record<QueueJobStatus, string> = {
  queued: '等待调度',
  running: '正在识别',
  retry_wait: '等待自动重试',
  succeeded: '识别完成 · 待核对',
  failed: '识别失败',
  interrupted: '处理中断 · 待确认',
  stale: '依据已变更',
  cancelled: '已取消',
};
export const batchStatusLabels: Record<QueueBatch['status'], string> = {
  queued: '等待调度',
  running: '处理中',
  completed: '已完成',
  attention: '需要处理',
  cancelled: '已取消',
};
export function jobActive(job: Pick<QueueJob, 'status'>): boolean {
  return ['queued', 'running', 'retry_wait'].includes(job.status);
}
export function latestQueueJobs(jobs: QueueJob[]): QueueJob[] {
  const seen = new Set<string>();
  return [...jobs]
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))
    .filter((job) => {
      if (seen.has(job.attachment_id)) return false;
      seen.add(job.attachment_id);
      return true;
    });
}
export function batchActive(batch: QueueBatch): boolean {
  return batch.progress.pending > 0 || batch.progress.running > 0;
}
export function canRetryJob(job: QueueJob): boolean {
  return job.permissions.retry && ['failed', 'interrupted', 'stale'].includes(job.status);
}
export function canCancelJob(job: QueueJob): boolean {
  return job.permissions.cancel && (jobActive(job) || job.status === 'interrupted');
}
export function displayedPercent(progress: QueueBatch['progress']): number {
  return Math.min(100, Math.max(0, Number.isFinite(progress.percent) ? progress.percent : 0));
}
export function selectedQueueItems(
  events: BusinessEvent[],
  companyId: string,
  attachmentIds: string[],
): QueueItem[] {
  const selected = new Set(attachmentIds);
  return events
    .filter((event) => event.company_id === companyId && event.permissions.process_intelligence)
    .flatMap((event) =>
      event.attachments
        .filter((attachment) => !attachment.superseded && selected.has(attachment.id))
        .map((attachment) => ({
          event_id: event.id,
          attachment_id: attachment.id,
          expected_version: event.version,
        })),
    );
}
export function batchSelectionError(items: QueueItem[]): string {
  return !items.length
    ? '请选择至少一份已上传的原始附件。'
    : items.length > MAX_BATCH_ATTACHMENTS
      ? `每批最多 ${MAX_BATCH_ATTACHMENTS} 份附件，请减少选择。`
      : '';
}
