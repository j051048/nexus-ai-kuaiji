import { CheckCircle2, Clock3, LoaderCircle, TriangleAlert } from 'lucide-react';
import { jobActive, queueStatusLabels } from './queue-helpers';
import type { QueueJob } from './queue-types';

export function QueueJobRecord({ job, onOpenBatch, compact = false }: { job: QueueJob; onOpenBatch?: (id: string) => void; compact?: boolean }) {
  return <div className={`queue-job-record ${job.status} ${compact ? 'compact' : ''}`}>
    <div className="queue-job-status">{job.status === 'running' ? <LoaderCircle className="spinner" size={14} /> : job.status === 'succeeded' ? <CheckCircle2 size={14} /> : jobActive(job) ? <Clock3 size={14} /> : <TriangleAlert size={14} />}<strong>{queueStatusLabels[job.status]}</strong><span>已尝试 {job.attempt_count} 次</span></div>
    {job.error_detail && <p>{job.error_detail}</p>}
    {job.status === 'retry_wait' && <p>暂时失败，后台会按重试时间继续处理，无需重复提交。</p>}
    {job.status === 'interrupted' && <p>处理曾中断，后台保留记录；请在任务中心核对后手工重试。</p>}
    {jobActive(job) && <p>任务已持久保存，可离开页面。识别结果仍需人工核对。</p>}
    {onOpenBatch && <button type="button" className="text-button" onClick={() => onOpenBatch(job.batch_id)}>查看处理任务</button>}
  </div>;
}
