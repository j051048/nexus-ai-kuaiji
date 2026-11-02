/** UI projections only. All mutations are re-authorized by the existing services. */
export type BatchKind = 'monthly_open' | 'review_export' | 'package_draft';
export const batchNames: Record<BatchKind, string> = {
  monthly_open: '月初开期',
  review_export: '已复核凭证导出',
  package_draft: '交付包草稿',
};
export type BatchItem = {
  id: string;
  company_id: string;
  company_name: string;
  status: string;
  error_code?: string;
  error_detail?: string;
  attempt?: number;
  max_attempts?: number;
  cancel_requested_at?: string | null;
  cancel_reason?: string;
  skip_reason?: string;
  result?: Record<string, unknown> | null;
};
export type BatchOperation = {
  id: string;
  kind: BatchKind;
  status: string;
  request?: { period?: string };
  progress: { total: number; completed: number; percent: number };
  items: BatchItem[];
};
export type Plan = {
  period: string;
  ready: number;
  blocked: number;
  manifest: string;
  companies: {
    company_id: string;
    book_id?: string;
    book_name?: string;
    blocked?: string;
    blockers?: unknown[];
    drafts?: { draft_id: string; version: number }[];
    report_id?: string;
    report_version?: number;
  }[];
};
export type Handoff = {
  source: { kind: 'tax_workpaper'; id: string; version: number; fingerprint: string };
  scope: { book_id: string; period: string; company_name: string; book_name: string };
  maker_id: string;
  role: string;
  next_action: string;
  assignee_id: string | null;
  status: string;
  due_on: string | null;
};
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const integer = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
function invalid(): never {
  throw new Error('响应范围或版本不一致，请重新读取后确认。');
}
export function acceptPlan(
  raw: unknown,
  companies: string[],
  period: string,
  kind: Exclude<BatchKind, 'monthly_open'>,
): Plan {
  if (
    !object(raw) ||
    raw.period !== period ||
    !hash(raw.manifest) ||
    !integer(raw.ready) ||
    !integer(raw.blocked) ||
    !Array.isArray(raw.companies)
  )
    return invalid();
  const seen = new Set<string>();
  let ready = 0;
  for (const c of raw.companies) {
    if (
      !object(c) ||
      !id(c.company_id) ||
      !companies.includes(c.company_id) ||
      seen.has(c.company_id)
    )
      return invalid();
    seen.add(c.company_id);
    if (id(c.blocked)) continue;
    if (!id(c.book_id)) return invalid();
    if (kind === 'review_export') {
      if (
        !hash(c.manifest) ||
        !Array.isArray(c.drafts) ||
        !c.drafts.length ||
        c.drafts.some(
          (d) => !object(d) || !id(d.draft_id) || !integer(d.version) || d.version < 1,
        ) ||
        new Set(c.drafts.map((d) => d.draft_id)).size !== c.drafts.length
      )
        return invalid();
    } else if (
      !id(c.report_id) ||
      !integer(c.report_version) ||
      c.report_version < 1 ||
      c.report_status !== 'published'
    )
      return invalid();
    ready++;
  }
  if (seen.size !== companies.length || ready !== raw.ready || seen.size - ready !== raw.blocked)
    return invalid();
  return raw as Plan;
}
export function acceptBatch(raw: unknown, expectedId?: string): BatchOperation {
  if (
    !object(raw) ||
    !id(raw.id) ||
    (expectedId && raw.id !== expectedId) ||
    !Object.prototype.hasOwnProperty.call(batchNames, String(raw.kind)) ||
    !['queued', 'running', 'completed', 'attention', 'cancelled'].includes(String(raw.status)) ||
    !object(raw.progress) ||
    !integer(raw.progress.total) ||
    !integer(raw.progress.completed) ||
    raw.progress.completed > raw.progress.total ||
    !integer(raw.progress.percent) ||
    raw.progress.percent > 100 ||
    !Array.isArray(raw.items) ||
    raw.items.length !== raw.progress.total
  )
    return invalid();
  const seen = new Set<string>();
  const companies = new Set<string>();
  for (const item of raw.items) {
    if (
      !object(item) ||
      !id(item.id) ||
      seen.has(item.id) ||
      !id(item.company_id) ||
      companies.has(item.company_id) ||
      typeof item.company_name !== 'string' ||
      ![
        'queued',
        'running',
        'succeeded',
        'skipped',
        'failed',
        'retry_wait',
        'cancelled',
        'unknown',
      ].includes(String(item.status))
    )
      return invalid();
    seen.add(item.id);
    companies.add(item.company_id);
  }
  return raw as BatchOperation;
}
export function itemActions(
  item: Pick<BatchItem, 'status' | 'cancel_requested_at' | 'error_code'>,
): ('cancel' | 'skip')[] {
  if (item.cancel_requested_at || item.error_code?.includes('unknown')) return [];
  if (item.status === 'running') return ['cancel'];
  return ['queued', 'retry_wait'].includes(item.status) ? ['cancel', 'skip'] : [];
}
export function itemLabel(item: Pick<BatchItem, 'status' | 'cancel_requested_at' | 'error_code'>) {
  if (item.status === 'running' && item.cancel_requested_at) return '取消已申请 · 等待执行确认';
  if (item.error_code?.includes('unknown') || item.status === 'unknown')
    return '结果未知 · 请人工核对，勿重发';
  return (
    (
      {
        queued: '排队中',
        running: '处理中',
        succeeded: '已完成',
        skipped: '已跳过',
        failed: '待人工处理',
        retry_wait: '等待重试',
        cancelled: '已取消',
      } as Record<string, string>
    )[item.status] ?? '状态待核实'
  );
}
export function acceptHandoffs(raw: unknown): Handoff[] {
  if (!object(raw) || !Array.isArray(raw.items)) return invalid();
  const seen = new Set<string>();
  for (const row of raw.items) {
    if (
      !object(row) ||
      !object(row.source) ||
      row.source.kind !== 'tax_workpaper' ||
      !id(row.source.id) ||
      seen.has(row.source.id) ||
      !integer(row.source.version) ||
      row.source.version < 1 ||
      !hash(row.source.fingerprint) ||
      !object(row.scope) ||
      !id(row.scope.book_id) ||
      !id(row.scope.period) ||
      !id(row.scope.company_name) ||
      !id(row.scope.book_name) ||
      !id(row.maker_id) ||
      row.role !== 'reviewer' ||
      !id(row.next_action)
    )
      return invalid();
    seen.add(row.source.id);
  }
  return raw.items as Handoff[];
}
export const blockedLabel = (code: string) =>
  (
    ({
      no_book: '尚未建立当月账套',
      no_access: '缺少本账套复核权限',
      no_approved: '没有已复核的待导出凭证',
      draft_limit: '凭证数量超限，请分批办理',
      no_report: '缺少已复核发布的报表',
      package_not_ready: '锁期、报表或交付依据尚未齐全',
    }) as Record<string, string>
  )[code] ?? '前置条件不满足，请回企业核对';

export function acceptAssignment(raw: unknown, expected: Handoff, assignee: string): void {
  if (
    !object(raw) ||
    !object(raw.source) ||
    !object(raw.scope) ||
    raw.source.kind !== expected.source.kind ||
    raw.source.id !== expected.source.id ||
    raw.source.version !== expected.source.version ||
    raw.source.fingerprint !== expected.source.fingerprint ||
    raw.scope.book_id !== expected.scope.book_id ||
    raw.assignee_id !== assignee ||
    raw.status !== 'assigned'
  )
    invalid();
}
export function batchResultUrl(kind: BatchKind, item: BatchItem, period: string): string | null {
  if (
    item.status !== 'succeeded' ||
    !id(item.result?.book_id) ||
    !/^20\d{2}-(0[1-9]|1[0-2])$/.test(period)
  )
    return null;
  const query = new URLSearchParams({
    book: item.result.book_id,
    company: item.company_id,
    period,
  });
  if (kind === 'package_draft') {
    if (!id(item.result?.package_id)) return null;
    query.set('package', item.result.package_id);
  } else if (kind === 'review_export') {
    if (!id(item.result?.export_id)) return null;
    query.set('delivery', item.result.export_id);
  }
  return `/workspace/${kind === 'monthly_open' ? 'accounting' : 'delivery'}?${query}`;
}
