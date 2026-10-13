import type { Pagination } from './types';

export type AgentRunKind = 'ocr' | 'proposal' | 'migration';
export type AgentRunFilter = 'active' | 'attention' | 'done' | 'all';
export type AgentRoute = {
  type: AgentRunKind;
  id: string;
  book_id?: string;
  job_id: string;
  event_id?: string | null;
  batch_id?: string | null;
};
export type AgentRun = {
  key: string;
  kind: AgentRunKind;
  id: string;
  company_id: string;
  company_name: string;
  book_id: string | null;
  event_id: string | null;
  batch_id: string | null;
  title: string;
  status: string;
  processed: number;
  total: number | null;
  updated_at: string;
  needs_attention: boolean;
  next_action: string;
  next_owner: string;
  route: AgentRoute;
  error_code?: string | null;
  error_detail?: string | null;
  phase?: string | null;
};
export type AgentRuns = { runs: AgentRun[]; pagination: Pagination };
export type AgentCounts = { active: number; attention: number; done: number; total: number };
export type AgentSummary = {
  counts: AgentCounts;
  by_kind: Record<AgentRunKind, AgentCounts>;
  updated_at: string | null;
  mode: 'persisted_job_states';
};
export const agentKindLabels: Record<AgentRunKind, string> = {
  ocr: '票据识别',
  proposal: '分录草稿',
  migration: '旧账迁移',
};
export const agentFilterLabels: Record<AgentRunFilter, string> = {
  active: '后台处理中',
  attention: '需要介入',
  done: '执行记录',
  all: '全部任务',
};
export type RunStage = { label: string; state: 'complete' | 'current' | 'attention' | 'unknown' };

/** A trace describes this persisted job only. Other accounting stages remain unverified. */
export function runStages(
  run: Pick<AgentRun, 'kind' | 'status'> & { needs_attention?: boolean },
): RunStage[] {
  const phases =
    run.kind === 'ocr'
      ? ['收到票据', '识别提取', '会计复核']
      : run.kind === 'proposal'
        ? ['收到事项', '生成分录', '会计复核']
        : ['收到旧账', '解析 / 校验', '独立验收'];
  if (run.needs_attention && (run.status === 'queued' || run.status === 'waiting'))
    return [
      { label: phases[0], state: 'complete' },
      { label: '执行条件待核对', state: 'attention' },
      { label: phases[2], state: 'unknown' },
    ];
  if (run.status === 'queued' || run.status === 'waiting')
    return [
      { label: phases[0], state: 'complete' },
      { label: '等待执行', state: 'current' },
      { label: phases[2], state: 'unknown' },
    ];
  if (run.status === 'running')
    return [
      { label: phases[0], state: 'complete' },
      { label: phases[1], state: 'current' },
      { label: phases[2], state: 'unknown' },
    ];
  if (run.status === 'succeeded')
    return [
      { label: phases[0], state: 'complete' },
      { label: phases[1], state: 'complete' },
      { label: phases[2], state: 'unknown' },
    ];
  if (['failed', 'needs_attention', 'interrupted', 'stale', 'retry_wait'].includes(run.status))
    return [
      { label: phases[0], state: 'complete' },
      { label: run.status === 'retry_wait' ? '等待重试' : '待人工核对', state: 'attention' },
      { label: phases[2], state: 'unknown' },
    ];
  return [
    { label: phases[0], state: 'unknown' },
    { label: run.status === 'cancelled' ? '已取消' : '状态待核实', state: 'unknown' },
    { label: phases[2], state: 'unknown' },
  ];
}

export function runProgress(run: Pick<AgentRun, 'processed' | 'total'> & { status?: string }): {
  label: string;
  percent: number | null;
} {
  const count = Number.isSafeInteger(run.processed) && run.processed >= 0 ? run.processed : 0;
  if (run.total == null || !Number.isSafeInteger(run.total) || run.total <= 0) {
    if (count) return { label: `已处理 ${count} 项 · 总量待核实`, percent: null };
    const ended = [
      'succeeded',
      'failed',
      'cancelled',
      'interrupted',
      'stale',
      'needs_attention',
    ].includes(run.status ?? '');
    return {
      label: ended
        ? '本步骤已结束 · 未记录处理数量'
        : run.status === 'running'
          ? '正在执行 · 总量待核实'
          : run.status === 'retry_wait'
            ? '等待重试 · 未记录处理数量'
            : '等待执行进度',
      percent: null,
    };
  }
  return {
    label: `已处理 ${count} / ${run.total} 项`,
    percent: Math.min(100, Math.max(0, Math.floor((count / run.total) * 100))),
  };
}

export function agentDestination(run: AgentRun): AgentRoute | null {
  // Never borrow a project, batch, draft or book from another row.
  if (!run.route || run.route.type !== run.kind || !run.route.id || run.route.job_id !== run.id)
    return null;
  if (run.kind !== 'ocr' && (!run.route.book_id || run.route.book_id !== run.book_id)) return null;
  return { ...run.route, event_id: run.event_id, batch_id: run.batch_id };
}

export function runStatusLabel(status: string): string {
  return (
    (
      {
        queued: '排队中',
        waiting: '等待资料',
        running: '正在执行',
        retry_wait: '等待重试',
        succeeded: '任务已完成',
        failed: '执行失败',
        needs_attention: '需要人工介入',
        interrupted: '执行中断',
        stale: '事实已更新',
        cancelled: '已取消',
      } as Record<string, string>
    )[status] ?? '状态待核实'
  );
}
