export const closingCategories: Record<string, string> = {
  unreviewed: '凭证审核与导出',
  unprocessed: '尚未生成草稿',
  missing_evidence: '资料完整性',
  business_pending: '企业审批与移交',
  open_cases: '异常与补件',
  background_pending: '后台任务',
  model_uncertain: '模型发送未知',
  unmatched_bank: '银行流水核对',
  unmatched_business: '业务与流水匹配',
  delivery_pending: '导入回执核验',
  fact_revision: '事实修订与调整',
};
export interface ClosingReport {
  engine_version: number;
  passed: boolean;
  issue_count: number;
  truncated: boolean;
  counts: Record<string, number>;
  issues: { category: string; object_type: string; object_id: string; detail: string }[];
}
export interface ClosingSnapshot {
  id: string;
  book_id: string;
  period: string;
  input_fingerprint: string;
  created_by: string;
  created_at: string;
  report: ClosingReport;
  stale: boolean;
  historical: boolean;
  reviewed_by_me: boolean;
}
export interface ClosingOverview {
  book_id: string;
  book_name: string;
  company_id: string;
  period: string;
  period_status: 'open' | 'closed';
  current_report: ClosingReport;
  current_fingerprint: string;
  snapshots: ClosingSnapshot[];
  receipt: { snapshot_id: string; reason: string; closed_by: string; created_at: string } | null;
  coverage_note: string;
}
export function canConfirmClose(snapshot: ClosingSnapshot | null, status: string): boolean {
  return (
    status === 'open' &&
    !!snapshot?.report.passed &&
    !snapshot.stale &&
    !snapshot.historical &&
    snapshot.reviewed_by_me
  );
}
export function closingTarget(type: string, id: string): string | null {
  const value = encodeURIComponent(id);
  return type === 'draft'
    ? `draft:${value}`
    : type === 'source'
      ? `source:${value}`
      : type === 'event'
        ? `event:${value}`
        : type === 'case'
          ? `case:${value}`
          : type === 'export'
            ? `export:${value}`
            : type === 'revision'
              ? `revision:${value}`
              : type === 'ocr_job' || type === 'proposal_job'
                ? `job:${value}`
                : null;
}
