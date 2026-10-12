import type { OperationsCost, OperationsOverview, OperationsPolicy, OperationsPolicyInput } from './operations-types';

export function currentOperationsPeriod(date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).formatToParts(date);
  return `${parts.find(part => part.type === 'year')?.value}-${parts.find(part => part.type === 'month')?.value}`;
}
export function formatOperatingCost(value: string | null | undefined): string {
  if (value === null || value === undefined || !/^-?\d+(?:\.\d{1,6})?$/.test(value)) return '待核定';
  const [integer, fraction = ''] = value.split('.');
  return `¥ ${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(6, '0')}`;
}
export function formatTokenCount(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isSafeInteger(value) || value < 0 ? '未返回' : value.toLocaleString('zh-CN');
}
function micros(value: string | null | undefined): bigint | null {
  if (value === null || value === undefined || !/^\d+(?:\.\d{1,6})?$/.test(value)) return null;
  const [integer, fraction = ''] = value.split('.'); return BigInt(integer) * 1000000n + BigInt(fraction.padEnd(6, '0'));
}
export function budgetPercentage(overview: Pick<OperationsOverview, 'policy' | 'usage'>): number | null {
  if (!overview.policy.quota_enabled) return null;
  const limit = micros(overview.policy.monthly_limit); const exposure = micros(overview.usage.exposure);
  if (limit === null || limit === 0n || exposure === null) return null;
  return Number(exposure * 10000n / limit) / 100;
}
export function policyFromOverview(policy: OperationsPolicy): OperationsPolicyInput {
  const amountInput = (value: string) => /^\d+\.\d{2}0{1,4}$/.test(value) ? value.slice(0, value.indexOf('.') + 3) : value;
  return { expected_version: policy.version, quota_enabled: policy.quota_enabled, monthly_limit: amountInput(policy.monthly_limit), ocr_reserve: amountInput(policy.ocr_reserve), proposal_reserve: amountInput(policy.proposal_reserve), failure_threshold: policy.failure_threshold, failure_window_minutes: policy.failure_window_minutes, backlog_threshold: policy.backlog_threshold, backlog_age_minutes: policy.backlog_age_minutes, queue_wait_minutes: policy.queue_wait_minutes, worker_stale_seconds: policy.worker_stale_seconds };
}
export function normalizeOperatingAmount(value: string): string {
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(value)) throw new Error('金额须为非负十进制数，最多两位小数。');
  const [integer, fraction = ''] = value.split('.');
  return `${BigInt(integer).toString()}.${fraction.padEnd(2, '0')}`;
}
export function normalizePolicyRequest(policy: OperationsPolicyInput): OperationsPolicyInput {
  return { ...policy, monthly_limit: normalizeOperatingAmount(policy.monthly_limit), ocr_reserve: normalizeOperatingAmount(policy.ocr_reserve), proposal_reserve: normalizeOperatingAmount(policy.proposal_reserve) };
}
export function validatePolicyInput(policy: OperationsPolicyInput): string {
  const amountFields: [keyof OperationsPolicyInput, string][] = [['monthly_limit', '月度额度'], ['ocr_reserve', '单次票据识别预留'], ['proposal_reserve', '单次分录建议预留']];
  for (const [key, label] of amountFields) {
    const value = String(policy[key]);
    if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(value)) return `${label}请填写非负金额，最多 12 位整数和两位小数。`;
    if (policy.quota_enabled && micros(value) === 0n) return `开启额度控制后，${label}必须大于零。`;
  }
  const thresholds: [keyof OperationsPolicyInput, string, number, number][] = [['failure_threshold', '连续失败次数', 1, 1000], ['failure_window_minutes', '失败观察窗口', 1, 10080], ['backlog_threshold', '积压份数', 1, 1000], ['backlog_age_minutes', '积压时间', 1, 10080], ['queue_wait_minutes', '排队等待时间', 1, 10080], ['worker_stale_seconds', '后台心跳时限', 30, 3600]];
  for (const [key, label, minimum, maximum] of thresholds) {
    const value = policy[key];
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) return `${label}请填写大于零的整数。`;
    if (value < minimum || value > maximum) return `${label}请填写 ${minimum}–${maximum} 范围内的整数。`;
  }
  return '';
}
export function validateCostReconciliation(amount: string, reason: string): string {
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(amount)) return '核定费用请填写非负金额，最多 12 位整数和两位小数。';
  if (!reason.trim()) return '请填写供应商账单或核对依据。';
  return '';
}
export function canReconcileOperatingCost(cost: OperationsCost, canManage: boolean): boolean {
  return canManage && cost.can_reconcile === true && ['uncertain', 'estimated', 'reconciled'].includes(cost.state);
}
export const operatingCostStateLabels: Record<string, string> = { reserved: '处理预留', sent: '已发送 · 待回执', running: '处理中', estimated: '已估算', reported: '已报告用量', uncertain: '费用待核定', interrupted: '费用待核定', confirmed: '已人工核定', reconciled: '已人工核定', released: '已释放预留', cancelled: '已取消' };
export const operationsAlertStateLabels: Record<string, string> = { open: '待处理', acknowledged: '已记录处置', resolved: '已恢复', closed: '已关闭' };
