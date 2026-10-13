export type LearningLine = {
  account_code: string;
  debit: string;
  credit: string;
  auxiliary_values?: Record<string, string>;
};
export type CounterpartyIdentity = {
  status: 'resolved' | 'unresolved' | 'ambiguous';
  original: string;
  entity_id: string | null;
  canonical_value: string | null;
  entity_version: number | null;
  alias_ids: string[];
  alias_versions: [string, number][];
};
export type LearningSample = {
  counterparty_identity?: CounterpartyIdentity | null;
  ordinary_rule_eligible?: boolean;
  rule_scope_reason?: string | null;
  id: string;
  draft_id: string;
  reviewed_version: number;
  reviewed_by: string;
  created_at: string;
  sample_fingerprint: string;
  corrected: boolean;
  source_kind: 'bank' | 'business';
  business_type: string;
  counterparty: string;
  amount: string;
  direction: 'expense' | 'income';
};
export type LearningDefinition = {
  name: string;
  source_kind: 'bank' | 'business';
  conditions: {
    business_type: 'reimbursement' | 'procurement_expense' | 'daily_cashflow';
    direction: 'expense' | 'income';
    category: 'office' | 'travel' | 'rent' | 'communication' | 'other' | null;
    payment_status: 'paid' | 'unpaid';
    counterparty: string;
    counterparty_id?: string | null;
  };
  lines: {
    account_code: string;
    side: 'debit' | 'credit';
    amount_field: 'gross' | 'net' | 'tax';
  }[];
};
export type LearningRule = {
  id: string;
  book_id: string;
  company_id: string;
  period: string;
  current_version: number;
  state_version: number;
  status: 'draft' | 'published' | 'retired';
  definition: LearningDefinition;
  sample_ids: string[];
  reason: string;
  evaluation_id: string | null;
};
export type LearningOverview = {
  book_id: string;
  company_id: string;
  book_name: string;
  period: string;
  can_manage: boolean;
  samples: LearningSample[];
  rules: LearningRule[];
  pagination: { limit: number; offset: number; total: number; has_more: boolean };
};
export type LearningDetail = LearningSample & {
  snapshot: {
    source_kind: 'business' | 'bank';
    business_type: LearningDefinition['conditions']['business_type'];
    payload: LearningDefinition['conditions'] & { amount: string; occurred_on: string };
    fact_version: number | null;
  };
  original: { description: string; lines: LearningLine[] };
  final: { description: string; lines: LearningLine[] };
  corrections: { user_id: string; reason: string; version: number }[];
  evidence_fingerprint: string;
};
export type LearningEvaluation = {
  id: string;
  passed: boolean;
  positive_count: number;
  negative_count: number;
  scanned_count: number;
  matched_count: number;
  failures: { sample_id: string; reason: string }[];
  input_fingerprint: string;
};
export type LearningRuleVersion = {
  version: number;
  definition: LearningDefinition;
  sample_ids: string[];
  reason: string;
  created_by: string;
  created_at: string;
};
export type RuleUsage = {
  rule_id: string;
  version: number;
  reviewed_hits: number;
  accountant_modified: number;
  unchanged: number;
  render_unavailable: number;
  modified_rate: number | null;
};
export type LearningGovernance = {
  book_id: string;
  company_id: string;
  period: string;
  sample_count: number;
  master_fingerprint: string;
  counterparties: { id: string; code: string; name: string; version: number }[];
  duplicate_groups: { semantic_fingerprint: string; rule_ids: string[] }[];
  conflicts: {
    rule_id: string;
    version: number;
    status: string;
    conflicts: { rule_id: string; version: number; name: string; same_semantics: boolean }[];
  }[];
  usage: RuleUsage[];
};

export function ruleUsageSummary(usage: RuleUsage[], ruleId: string) {
  return usage
    .filter((row) => row.rule_id === ruleId)
    .reduce(
      (result, row) => ({
        reviewed_hits: result.reviewed_hits + row.reviewed_hits,
        accountant_modified: result.accountant_modified + row.accountant_modified,
        render_unavailable: result.render_unavailable + row.render_unavailable,
      }),
      { reviewed_hits: 0, accountant_modified: 0, render_unavailable: 0 },
    );
}

export const learningStatus = { draft: '待发布', published: '已发布', retired: '已停用' };
export const learningCategories = {
  office: '办公',
  travel: '交通 / 差旅',
  rent: '租赁',
  communication: '通信',
  other: '其他',
};
export const learningBusinessTypes = {
  reimbursement: '报销',
  procurement_expense: '采购 / 费用',
  daily_cashflow: '日常收支',
};

export function definitionFromSample(sample: LearningDetail): LearningDefinition {
  if (sample.ordinary_rule_eligible === false)
    throw new Error('清偿样例不可建立普通分类规则；核销需要实际分配与原应收应付凭证。');
  return {
    name: `${sample.counterparty} · 会计处理规则`.slice(0, 120),
    source_kind: sample.source_kind,
    conditions: {
      business_type: sample.snapshot.business_type,
      direction: sample.direction,
      category: sample.source_kind === 'bank' ? null : sample.snapshot.payload.category,
      payment_status: sample.snapshot.payload.payment_status,
      counterparty: sample.counterparty,
      ...(sample.counterparty_identity?.status === 'resolved'
        ? { counterparty_id: sample.counterparty_identity.entity_id }
        : {}),
    },
    lines: sample.final.lines.map((line) => ({
      account_code: line.account_code,
      side: line.debit !== '0.00' ? 'debit' : 'credit',
      amount_field: 'gross',
    })),
  };
}
