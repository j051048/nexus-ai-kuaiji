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
  pilot: {
    measurement: 'observed_only';
    session_count: number;
    human_seconds: number | null;
    measured_savings_seconds: null;
    review: {
      total_reviewed: number;
      independent: number;
      single_operator: number;
      unverified: number;
    };
    source_fingerprint: string;
  };
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
  if (
    !Number.isSafeInteger(part) ||
    !Number.isSafeInteger(total) ||
    part < 0 ||
    total < 0 ||
    part > total
  )
    return '数据待核对';
  return total > 0 ? `${((part * 100) / total).toFixed(1)}%` : '暂无样本';
}

export function readEfficiencyOverview(
  value: EfficiencyOverview,
  bookId: string,
): EfficiencyOverview {
  const count = (n: unknown) => typeof n === 'number' && Number.isSafeInteger(n) && n >= 0;
  const p = value?.pilot,
    r = p?.review,
    h = value?.human,
    v = value?.review;
  if (
    value?.book_id !== bookId ||
    !value.company_id ||
    !/^20\d{2}-(0[1-9]|1[0-2])$/.test(value.period) ||
    !p ||
    p.measurement !== 'observed_only' ||
    p.measured_savings_seconds !== null ||
    !count(p.session_count) ||
    !/^[a-f0-9]{64}$/.test(p.source_fingerprint) ||
    (p.session_count === 0 ? p.human_seconds !== null : !count(p.human_seconds)) ||
    !r ||
    ![r.total_reviewed, r.independent, r.single_operator, r.unverified].every(count) ||
    r.total_reviewed !== r.independent + r.single_operator + r.unverified ||
    !h ||
    h.session_count !== p.session_count ||
    !count(h.credited_seconds) ||
    (p.human_seconds !== null && p.human_seconds !== h.credited_seconds) ||
    !v ||
    !Object.values(v).every(count) ||
    v.reviewed_drafts !== v.with_recorded_correction + v.without_recorded_correction ||
    v.company_rule_covered > v.reviewed_drafts
  )
    throw new Error('统计范围或样本不一致，请重新读取；不会推算缺失数据。');
  return value;
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
