export type AnalysisScope = {
  firmId: string | null;
  companyId: string;
  bookId: string;
  period: string;
  kind: 'expense_bridge' | 'invoice_voucher_diff';
  accountCode: string;
  voucherId: string;
  offset: number;
  limit: number;
  sourceVersion?: string;
};
export class AnalysisContractError extends Error {}
export type Analysis = {
  firm_id: string;
  kind: AnalysisScope['kind'];
  company_id: string;
  book_id: string;
  period: string;
  status: 'complete' | 'partial';
  summary: string;
  facts: Record<string, unknown>;
  coverage: {
    scope: string;
    rows_read: number;
    detail_rows: number;
    total_rows: number;
    detail_complete: boolean;
    total_complete: boolean;
    has_more: boolean;
    offset: number;
  };
  request: Record<string, unknown>;
  missing: string[];
  handoff: { reason: string; owner_role?: string; action_target?: string } | null;
  fingerprint: string;
  source_version: string;
};
function record(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
const integer = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);

/** Validate the whole response before any fields reach React state. */
export function parseAnalysis(v: unknown, scope: AnalysisScope): Analysis {
  const fail = () => {
    throw new AnalysisContractError('核对结果的范围或来源已变化，请重新核对。');
  };
  if (
    !record(v) ||
    !scope.firmId ||
    v.firm_id !== scope.firmId ||
    v.company_id !== scope.companyId ||
    v.book_id !== scope.bookId ||
    v.period !== scope.period ||
    v.kind !== scope.kind
  )
    return fail();
  if (
    !['complete', 'partial'].includes(String(v.status)) ||
    typeof v.summary !== 'string' ||
    !record(v.facts) ||
    !record(v.coverage) ||
    !record(v.request) ||
    !hash(v.fingerprint) ||
    !hash(v.source_version)
  )
    return fail();
  if (scope.sourceVersion && scope.sourceVersion !== v.source_version) return fail();
  const r = v.request,
    c = v.coverage;
  if (
    r.offset !== scope.offset ||
    r.limit !== scope.limit ||
    r.compare_period !== null ||
    r.account_code !== (scope.kind === 'expense_bridge' ? scope.accountCode : null) ||
    r.target_voucher_id !== (scope.kind === 'invoice_voucher_diff' ? scope.voucherId : null)
  )
    return fail();
  if (
    typeof c.scope !== 'string' ||
    !['rows_read', 'detail_rows', 'total_rows', 'offset'].every((k) => integer(c[k])) ||
    !['detail_complete', 'total_complete', 'has_more'].every((k) => typeof c[k] === 'boolean') ||
    c.offset !== scope.offset ||
    Number(c.detail_rows) > Number(c.total_rows)
  )
    return fail();
  if (c.detail_complete && (scope.offset !== 0 || c.has_more || c.detail_rows !== c.total_rows))
    return fail();
  if (
    !Array.isArray(v.missing) ||
    !v.missing.every((x) => typeof x === 'string') ||
    (v.handoff !== null && (!record(v.handoff) || typeof v.handoff.reason !== 'string'))
  )
    return fail();
  const fields =
    scope.kind === 'expense_bridge'
      ? ['current_total', 'previous_total', 'difference']
      : ['voucher_total', 'source_total', 'difference'];
  const facts = v.facts;
  if (facts.currency !== 'CNY') return fail();
  const rowFields =
    scope.kind === 'expense_bridge' ? ['current_rows', 'previous_rows'] : ['sources'];
  for (const key of rowFields) {
    const rows = facts[key];
    if (
      !Array.isArray(rows) ||
      rows.some(
        (row) =>
          !record(row) ||
          typeof row.amount_cents !== 'number' ||
          !Number.isSafeInteger(row.amount_cents) ||
          (key === 'sources'
            ? typeof row.id !== 'string' || !integer(row.version) || typeof row.kind !== 'string'
            : typeof row.voucher_id !== 'string' ||
              !integer(row.voucher_no) ||
              typeof row.description !== 'string' ||
              typeof row.date !== 'string'),
      )
    )
      return fail();
  }
  if (
    fields.some(
      (k) =>
        facts[k] !== null &&
        (typeof facts[k] !== 'string' || !/^-?\d+\.\d{2}$/.test(String(facts[k]))),
    )
  )
    return fail();
  return v as Analysis;
}
