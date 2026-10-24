import { describe, expect, it } from 'vitest';
import { readWorkspaceRoute } from './workspace-route';
import { taskDestination, type WorkspaceTask } from './workspace-types';

describe('V06 资料任务编号仅属于资料页面，不阻断其他页面的路由恢复', () => {
  it('服务单忽略上一页遗留的资料任务编号', () => {
    expect(
      readWorkspaceRoute('/workspace/service', '?book=b&company=c&period=2026-10&material=old-a')
        .materialId,
    ).toBe('');
  });
  it('直接打开资料任务仍保留确切编号', () => {
    expect(
      readWorkspaceRoute('/workspace/materials', '?company=c&material=owned-a').materialId,
    ).toBe('owned-a');
  });
});

describe('V06 客户的票据待办回到原事项，授权会计保留批次处理入口', () => {
  const task = {
    kind: 'ocr',
    ref: 'job-a',
    event_id: 'event-a',
    batch_id: 'batch-a',
  } as WorkspaceTask;
  it('客户打开票据待办应直达已授权业务原件', () => {
    expect(taskDestination(task, true)).toEqual({ type: 'business', id: 'event-a' });
  });
  it('专业工作台继续打开原批次及任务', () => {
    expect(taskDestination(task)).toEqual({ type: 'ocr', id: 'batch-a', jobId: 'job-a' });
  });
});
