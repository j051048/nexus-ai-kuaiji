import { describe, expect, it } from 'vitest';
import {
  agentDestination,
  runProgress,
  runStages,
  runStatusLabel,
  type AgentRun,
} from './agent-workspace-types';

const run = (overrides: Partial<AgentRun> = {}): AgentRun => ({
  key: 'migration:job',
  kind: 'migration',
  id: 'job',
  company_id: 'company',
  company_name: '合成企业',
  book_id: 'book',
  event_id: null,
  batch_id: null,
  title: '合成迁移任务',
  status: 'succeeded',
  processed: 25,
  total: 25,
  updated_at: '2026-10-13T05:00:00Z',
  needs_attention: false,
  next_action: '核对差异',
  next_owner: '迁移经办人',
  route: { type: 'migration', id: 'exact-project', book_id: 'book', job_id: 'job' },
  ...overrides,
});

describe('后台事实与准确导航', () => {
  it('OCR成功仅完成识别，不伪造分录、人工复核或导出完成', () => {
    expect(runStages({ kind: 'ocr', status: 'succeeded' })).toEqual([
      { label: '收到票据', state: 'complete' },
      { label: '识别提取', state: 'complete' },
      { label: '会计复核', state: 'unknown' },
    ]);
  });
  it('未知状态和已取消任务不伪造执行成功', () => {
    expect(
      runStages({ kind: 'proposal', status: 'provider_new_state' }).every(
        (stage) => stage.state === 'unknown',
      ),
    ).toBe(true);
    expect(
      runStages({ kind: 'migration', status: 'cancelled' }).some(
        (stage) => stage.state === 'complete',
      ),
    ).toBe(false);
    expect(runStatusLabel('new_status')).toBe('状态待核实');
  });
  it('事实变化但仍为queued的任务准确展示人工介入，不伪造已经执行', () => {
    const stages = runStages({ kind: 'proposal', status: 'queued', needs_attention: true });
    expect(stages[1]).toEqual({ label: '执行条件待核对', state: 'attention' });
    expect(stages[2].state).toBe('unknown');
  });
  it('缺失总量保持未知，有实际总量才显示百分比', () => {
    expect(runProgress({ processed: 9, total: null })).toEqual({
      label: '已处理 9 项 · 总量待核实',
      percent: null,
    });
    expect(runProgress({ processed: 9, total: 30 })).toEqual({
      label: '已处理 9 / 30 项',
      percent: 30,
    });
    expect(runProgress({ processed: 99, total: 30 }).percent).toBe(100);
  });
  it('无计数的终态显示本步骤结束，不显示等待执行或推断成功', () => {
    for (const status of [
      'succeeded',
      'failed',
      'cancelled',
      'interrupted',
      'stale',
      'needs_attention',
    ]) {
      expect(runProgress({ processed: 0, total: null, status })).toEqual({
        label: '本步骤已结束 · 未记录处理数量',
        percent: null,
      });
    }
    expect(runProgress({ processed: 9, total: null, status: 'failed' })).toEqual({
      label: '已处理 9 项 · 总量待核实',
      percent: null,
    });
    expect(runProgress({ processed: 9, total: 30, status: 'failed' })).toEqual({
      label: '已处理 9 / 30 项',
      percent: 30,
    });
    expect(runProgress({ processed: 0, total: null, status: 'running' })).toEqual({
      label: '正在执行 · 总量待核实',
      percent: null,
    });
  });
  it('执行记录使用真实项目和作业 ID，不借用其他账套', () => {
    expect(agentDestination(run())).toEqual({
      type: 'migration',
      id: 'exact-project',
      book_id: 'book',
      job_id: 'job',
      event_id: null,
      batch_id: null,
    });
    expect(
      agentDestination(
        run({ route: { type: 'migration', id: 'exact-project', job_id: 'other-job' } }),
      ),
    ).toBeNull();
    expect(
      agentDestination(run({ route: { type: 'migration', id: 'exact-project', job_id: 'job' } })),
    ).toBeNull();
  });
  it('OCR精确传递关联业务和批次，缺失批次不回退列表首项', () => {
    const ocr = run({
      kind: 'ocr',
      id: 'ocr-job',
      batch_id: null,
      event_id: 'exact-event',
      route: { type: 'ocr', id: 'ocr-job', job_id: 'ocr-job' },
    });
    expect(agentDestination(ocr)).toEqual({
      type: 'ocr',
      id: 'ocr-job',
      job_id: 'ocr-job',
      event_id: 'exact-event',
      batch_id: null,
    });
  });
});
