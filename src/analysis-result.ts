export type AnalysisScope = {
  firmId: string | null;
  companyId: string;
  bookId: string;
  period: string;
  kind: 'expense_bridge' | 'invoice_voucher_diff' | 'settlement_difference';
  accountCode: string;
  voucherId: string;
  settlementSourceId?: string;
  bankSourceId?: string;
  feeSourceId?: string;
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

export const SETTLEMENT_DIFFERENCE_REASONS = [
  'none',
  'fee_evidence',
  'fee_evidence_mismatch',
  'cross_period_evidence',
  'unverified_source_relation',
  'fee_relation_mismatch',
  'unresolved',
] as const;

export type SettlementDifferenceReason = (typeof SETTLEMENT_DIFFERENCE_REASONS)[number];

function sourceLabel(source: unknown, fallback: string): string {
  if (!record(source)) return fallback;
  return String(source.source_name ?? source.description ?? source.id ?? fallback);
}

function sourceMonth(source: unknown): string {
  if (!record(source) || typeof source.date !== 'string') return '';
  return source.date.slice(0, 7);
}

export function describeSettlementDifference(facts: Record<string, unknown>): {
  headline: string;
  nextStep: string;
} {
  const reason = String(facts.reason ?? '');
  const diff = String(facts.difference ?? '未知');
  const feeTotal = String(facts.fee_total ?? '未知');
  const settlementName = sourceLabel(facts.settlement_source, '结算单');
  const bankName = sourceLabel(facts.bank_source, '银行到账');
  const feeName = sourceLabel(facts.fee_source, '手续费凭据');
  const feePeriod = sourceMonth(facts.fee_source);
  switch (reason) {
    case 'none':
      return {
        headline: '已对平，结算单与银行到账金额一致，无差额。',
        nextStep: '无需补件或登记差额。',
      };
    case 'fee_evidence':
      return {
        headline: `已由手续费明细 ${feeTotal} 元解释（${feeName}）。`,
        nextStep: '来源关系已核对，可在差异台账按政策生成草稿并交由另一人独立复核。',
      };
    case 'cross_period_evidence':
      return {
        headline: `手续费凭据属 ${feePeriod || '非本期间'}（跨期），覆盖范围不是本期间，不能据此归因。`,
        nextStep: '请核查本期间对应扣费/退款原件，或在差异台账按跨期原因登记处理。',
      };
    case 'fee_evidence_mismatch':
      return {
        headline: `金额冲突：${settlementName}与${bankName}差额 ${diff} 元，所选${feeName}为 ${feeTotal} 元，两笔金额不一致，不能据此归因。`,
        nextStep: '请对照两笔来源原件核查是否存在部分退款、多笔扣费或选错凭据。',
      };
    case 'unverified_source_relation':
      return {
        headline: `来源关系尚未核对：${feeName}金额与差额 ${diff} 元一致，但尚未将结算单、到账流水与该凭据绑定核对，不能据此确认归因。`,
        nextStep:
          '请先在规费差异中登记并核对三方来源关系，避免误把同月同额无关流水当成本笔手续费。',
      };
    case 'fee_relation_mismatch':
      return {
        headline: `所选${feeName}不在已核对的来源关系内，不能据此确认归因。`,
        nextStep: '请对照已核对的结算与到账关系重新核实对应扣费凭据。',
      };
    default:
      return {
        headline: '原因待核对，缺手续费或扣款明细。',
        nextStep: '请补充本期间对应手续费、退款或扣款原件后再核对，切勿盲目按金额归类。',
      };
  }
}

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
    r.target_voucher_id !== (scope.kind === 'invoice_voucher_diff' ? scope.voucherId : null) ||
    (r.settlement_source_id ?? null) !==
      (scope.kind === 'settlement_difference' ? (scope.settlementSourceId ?? '') : null) ||
    (r.bank_source_id ?? null) !==
      (scope.kind === 'settlement_difference' ? (scope.bankSourceId ?? '') : null) ||
    (r.fee_source_id ?? null) !==
      (scope.kind === 'settlement_difference' ? scope.feeSourceId || null : null)
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
  const facts = v.facts;
  if (scope.kind === 'settlement_difference') {
    if (
      typeof facts.settlement_total !== 'string' ||
      typeof facts.bank_total !== 'string' ||
      typeof facts.difference !== 'string' ||
      !(SETTLEMENT_DIFFERENCE_REASONS as readonly string[]).includes(String(facts.reason))
    )
      return fail();
    return v as Analysis;
  }
  const fields =
    scope.kind === 'expense_bridge'
      ? ['current_total', 'previous_total', 'difference']
      : ['voucher_total', 'source_total', 'difference'];
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
