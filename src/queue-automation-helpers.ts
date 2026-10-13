export interface QueueAutomationPolicy {
  enabled: boolean;
  allow_verified_supplements: boolean;
  version: number;
  updated_at: string | null;
  permissions: { manage: boolean };
  resumable_reasons: string[];
}
export interface JobMetricStage {
  stage: string;
  label: string;
  samples: number;
  total_ms: number;
  mean_ms: number;
  max_ms: number;
}
export const resumeReasonLabels: Record<string, string> = {
  processing_disabled: '企业处理开关已恢复',
  ocr_unavailable: '票据服务已配置',
  provider_unavailable: '记账服务已配置',
  evidence_missing: '已独立核验补件',
  case_pending: '异常待办已关闭',
  queue_capacity: '积压容量已释放',
  quota_exhausted: '模型额度已恢复',
  pricing_missing: '模型单价已配置',
  policy_invalid: '费用配置已补齐',
};
export function durationLabel(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—';
  if (milliseconds < 1000) return `${Math.round(milliseconds)} 毫秒`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} 秒`;
  return `${(milliseconds / 60_000).toFixed(1)} 分钟`;
}
export function automationReason(value: string): string | null {
  const text = value.trim();
  return text.length < 3
    ? '请填写至少 3 字的配置依据。'
    : text.length > 1000
      ? '配置依据不能超过 1000 字。'
      : null;
}
