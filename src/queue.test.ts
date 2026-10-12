import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { QueueBatchProgress } from './QueueWorkbench';
import { QueueJobRecord } from './QueueJobRecord';
import { batchActive, batchSelectionError, canCancelJob, canRetryJob, displayedPercent, jobActive, latestQueueJobs, MAX_BATCH_ATTACHMENTS, queueStatusLabels, RETRY_NOTICE, selectedQueueItems } from './queue-helpers';
import type { BusinessEvent } from './business-types';
import type { QueueBatch, QueueJob, QueueJobStatus } from './queue-types';

function event(companyId: string, id: string, allowed = true): BusinessEvent {
  return { id, firm_id: 'firm-a', company_id: companyId, department_id: null, applicant_id: 'user-a', template_id: 'reimbursement', template_version: 1, version: 4, status: 'draft', payload: { occurred_on: '2026-10-12', description: id, counterparty: '供应商', amount: '100.00', category: 'office', direction: 'expense', payment_status: 'unpaid' }, attachments: [{ id: `attachment-${id}`, filename: `${id}.png`, size_bytes: 100, media_type: 'image/png', sha256: 'sha' }], actions: [], versions: [], permissions: { edit: false, submit: false, department_approve: false, finance_approve: false, return: false, handoff: false, process_intelligence: allowed } };
}
function job(status: QueueJobStatus = 'queued', overrides: Partial<QueueJob> = {}): QueueJob {
  return { id: 'job-a', batch_id: 'batch-a', event_id: 'event-a', event_description: '办公报销', attachment_id: 'attachment-a', filename: '票据.png', status, attempt_count: 0, created_at: '2026-10-12T01:00:00Z', updated_at: '2026-10-12T01:00:00Z', permissions: { retry: true, cancel: true }, ...overrides };
}
const batch: QueueBatch = { id: 'batch-a', company_id: 'company-a', company_name: '已授权企业', created_at: '2026-10-12T01:00:00Z', status: 'attention', progress: { total: 3, completed: 2, succeeded: 1, failed: 1, interrupted: 0, stale: 0, cancelled: 0, pending: 1, running: 0, percent: 66.67 }, jobs: [] };

describe('批量附件范围和版本快照', () => {
  it('仅取同企业且有处理权限的原始附件，保留事项版本', () => {
    const events = [event('company-a', 'a'), event('company-b', 'b'), event('company-a', 'readonly', false)];
    expect(selectedQueueItems(events, 'company-a', ['attachment-a', 'attachment-b', 'attachment-readonly'])).toEqual([{ event_id: 'a', attachment_id: 'attachment-a', expected_version: 4 }]);
  });
  it('服务未返回处理权限时保守禁止选择', () => {
    const legacy = event('company-a', 'a'); delete legacy.permissions.process_intelligence;
    expect(selectedQueueItems([legacy], 'company-a', ['attachment-a'])).toEqual([]);
  });
  it('忽略不存在的附件，重复选中 ID 不重复提交', () => {
    expect(selectedQueueItems([event('company-a', 'a')], 'company-a', ['unknown', 'attachment-a', 'attachment-a'])).toHaveLength(1);
  });
  it('限制每批 100 份，并拒绝空选择', () => {
    const items = Array.from({ length: MAX_BATCH_ATTACHMENTS }, (_, index) => ({ event_id: `event-${index}`, attachment_id: `attachment-${index}`, expected_version: 1 }));
    expect(batchSelectionError([])).toContain('至少一份'); expect(batchSelectionError(items)).toBe('');
    expect(batchSelectionError([...items, items[0]])).toContain('最多 100');
  });
});
describe('后台状态和恢复动作', () => {
  it.each(['queued', 'running', 'retry_wait'] as const)('%s 保持待处理，不因页面等待时间误判失败', status => {
    const record = job(status, { created_at: '2020-01-01T00:00:00Z' });
    expect(jobActive(record)).toBe(true); expect(canRetryJob(record)).toBe(false); expect(canCancelJob(record)).toBe(true);
  });
  it.each(['failed', 'interrupted', 'stale'] as const)('%s 只允许有当前权限的人明确重试', status => {
    expect(canRetryJob(job(status))).toBe(true); expect(canCancelJob(job(status))).toBe(status === 'interrupted');
    expect(canRetryJob(job(status, { permissions: { retry: false, cancel: true } }))).toBe(false);
  });
  it.each(['succeeded', 'cancelled'] as const)('%s 不提供重试或取消', status => {
    expect(canRetryJob(job(status))).toBe(false); expect(canCancelJob(job(status))).toBe(false);
  });
  it('重试确认说明费用不确定，显示中断原记录而不声称安全完成', () => {
    expect(RETRY_NOTICE).toContain('再次发送附件'); expect(RETRY_NOTICE).toContain('可能已产生供应商费用');
    const html = renderToStaticMarkup(createElement(QueueJobRecord, { job: job('interrupted', { attempt_count: 2, error_detail: '<script>secret</script>' }) }));
    expect(html).toContain('处理中断'); expect(html).toContain('手工重试'); expect(html).toContain('已尝试 2 次');
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>'); expect(html).not.toContain('自动审核');
  });
  it('同附件以最新排队记录为准，不把历史成功展示为当前状态', () => {
    const old = job('succeeded', { id: 'old' }); const latest = job('queued', { id: 'new', created_at: '2026-10-12T02:00:00Z' });
    expect(latestQueueJobs([latest, old]).map(value => value.id)).toEqual(['new']); expect(queueStatusLabels[latestQueueJobs([old, latest])[0].status]).toBe('等待调度');
  });
});
describe('获授权进度呈现', () => {
  it('只呈现服务返回的可见条目计数，结束份数包含失败而不冒充成功', () => {
    const html = renderToStaticMarkup(createElement(QueueBatchProgress, { batch }));
    expect(html).toContain('3 份已结束'); expect(html).toContain('成功 1'); expect(html).toContain('需处理 1'); expect(html).toContain('67%');
    expect(html).not.toContain('成功率'); expect(batchActive(batch)).toBe(true);
  });
  it('将进度条限制在合法区间，不从隐藏条目推算总数', () => {
    expect(displayedPercent({ ...batch.progress, percent: 120 })).toBe(100);
    expect(displayedPercent({ ...batch.progress, percent: -10 })).toBe(0);
    expect(displayedPercent({ ...batch.progress, percent: Number.NaN })).toBe(0);
    expect(batchActive({ ...batch, progress: { ...batch.progress, pending: 0, running: 0 } })).toBe(false);
  });
  it('已完成只声明识别结束并要求人工核对', () => {
    const html = renderToStaticMarkup(createElement(QueueJobRecord, { job: job('succeeded') }));
    expect(html).toContain('识别完成 · 待核对'); expect(html).not.toContain('已审核'); expect(html).not.toContain('已过账');
  });
});
