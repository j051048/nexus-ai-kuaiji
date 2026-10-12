import { formatMoney } from './csv';
import { ApiError } from './api';
import type { IntelligenceConfig, IntelligenceDetail, IntelligenceFinding, IntelligenceRun, JournalProposal, OcrResult } from './intelligence-types';

export function recognizedMoney(value: string | null | undefined, currency: string | null = 'CNY'): string {
  if (value === null || value === undefined || value === '') return '待核对';
  // The server returns canonical decimal strings. Preserve them without float rounding.
  return `${currency === 'CNY' ? '¥ ' : currency ? `${currency} ` : ''}${formatMoney(value)}`;
}
export function processingUnavailable(config: IntelligenceConfig | null, companyId: string, kind: 'ocr' | 'proposal'): string {
  if (!config) return '正在读取模型配置…';
  if (!config.configured || !(kind === 'ocr' ? config.vision_ready : config.journal_ready)) return '等待管理员配置模型服务';
  if (!config.enabled_company_ids.includes(companyId)) return '该企业尚未开启票据与模型处理';
  return '';
}
export function ocrResult(run: IntelligenceRun): OcrResult | null {
  return run.kind === 'ocr' && run.result && 'documents' in run.result ? run.result : null;
}
export function journalResult(run: IntelligenceRun): JournalProposal | null {
  return run.kind === 'proposal' && run.result && 'lines' in run.result ? run.result : null;
}
export function latestRuns(runs: IntelligenceRun[], kind: IntelligenceRun['kind']): IntelligenceRun[] {
  const sorted = [...runs].filter(run => run.kind === kind).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const seen = new Set<string>();
  return sorted.filter(run => { const key = run.attachment_id ?? run.draft_id ?? run.id; if (seen.has(key)) return false; seen.add(key); return true; });
}
export function canApplyProposal(detail: IntelligenceDetail, run: IntelligenceRun, draftVersion: number): boolean {
  return detail.permissions.apply_proposal && run.status === 'succeeded' && run.input_version === draftVersion
    && !detail.findings.some(finding => finding.severity === 'blocking' && !finding.resolved);
}
export function canResolveFinding(detail: IntelligenceDetail, finding: IntelligenceFinding): boolean {
  return detail.permissions.resolve && !!finding.id && !finding.resolved && !finding.non_overridable && finding.payload.non_overridable !== true;
}
export const RUN_WAIT_TIMEOUT_MS = 3 * 60 * 1000;
export function runWaitingTimedOut(run: IntelligenceRun, currentTime = Date.now()): boolean {
  const created = Date.parse(run.created_at);
  return run.status === 'running' && Number.isFinite(created) && currentTime - created >= RUN_WAIT_TIMEOUT_MS;
}
export function runPending(run: IntelligenceRun, currentTime = Date.now()): boolean {
  return run.status === 'running' && !runWaitingTimedOut(run, currentTime);
}
export function runStatusLabel(run: IntelligenceRun, currentTime = Date.now()): string {
  if (runWaitingTimedOut(run, currentTime)) return '等待超时 · 可手工重新发起';
  return { running: '处理中', succeeded: '已返回 · 待人工核对', failed: '处理失败', stale: '依据已变更 · 请重新处理' }[run.status];
}
export function canStartIntelligenceAction(name: string, dirty: boolean, busy: boolean, locked: boolean): boolean {
  return !busy && !locked && (!dirty || name === 'refresh');
}
export function shouldReadAfterFailure(failure: unknown): boolean {
  return failure instanceof TypeError || failure instanceof ApiError && [409, 503].includes(failure.status);
}
