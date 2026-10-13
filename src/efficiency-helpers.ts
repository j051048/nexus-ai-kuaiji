export type WorkActivity =
  'review' | 'correction' | 'supplement' | 'month_close' | 'business_entry';
export type WorkState = 'active' | 'paused' | 'completed';
export interface WorkSession {
  id: string;
  book_id: string | null;
  event_id: string | null;
  company_id: string;
  activity: WorkActivity;
  state: WorkState;
  credited_seconds: number;
  started_at: string;
  last_active_at: string;
  updated_at: string;
  version: number;
}
export interface EfficiencyOverview {
  book_id: string;
  company_id: string;
  period: string;
  human: {
    credited_seconds: number;
    session_count: number;
    measurement: string;
    heartbeat_cap_seconds: number;
    activities: {
      activity: WorkActivity;
      state: WorkState;
      credited_seconds: number;
      session_count: number;
    }[];
  };
  review: {
    reviewed_drafts: number;
    without_recorded_correction: number;
    with_recorded_correction: number;
    company_rule_covered: number;
  };
  supplement: {
    completed_first_response_intervals: number;
    calendar_wait_seconds: number;
    measurement: string;
  };
  definitions: { without_recorded_correction: string; company_rule_covered: string; human: string };
}
export const workActivities: Record<WorkActivity, string> = {
  review: '凭证复核',
  correction: '事实与分录修正',
  supplement: '异常及补件',
  month_close: '月结检查',
  business_entry: '本人业务录入',
};
export const workStates: Record<WorkState, string> = {
  active: '计时中',
  paused: '已暂停',
  completed: '已完成',
};
export function durationLabel(seconds: number): string {
  const safe = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return safe >= 3600
    ? `${Math.floor(safe / 3600)}小时${Math.floor((safe % 3600) / 60)}分钟`
    : safe >= 60
      ? `${Math.floor(safe / 60)}分钟${safe % 60}秒`
      : `${safe}秒`;
}
export function measuredRate(part: number, total: number): string {
  return total > 0 ? `${((part * 100) / total).toFixed(1)}%` : '暂无样本';
}
export function canAcknowledgeActivity(
  visible: boolean,
  focused: boolean,
  elapsedSinceInput: number,
): boolean {
  return (
    visible &&
    focused &&
    Number.isFinite(elapsedSinceInput) &&
    elapsedSinceInput >= 0 &&
    elapsedSinceInput <= 30_000
  );
}
