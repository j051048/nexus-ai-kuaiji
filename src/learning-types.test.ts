import { describe, expect, it } from 'vitest';
import { definitionFromSample, learningStatus, ruleUsageSummary } from './learning-types';
import type { LearningDetail } from './learning-types';

const sample = {
  id: 'sample', draft_id: 'draft', reviewed_version: 2, reviewed_by: 'accountant', created_at: '2026-10-12', sample_fingerprint: 'fp', corrected: true,
  source_kind: 'bank', business_type: 'daily_cashflow', counterparty: '甲企业', amount: '113.00', direction: 'expense',
  snapshot: { source_kind: 'bank', business_type: 'daily_cashflow', payload: { business_type: 'daily_cashflow', direction: 'expense', category: 'other', payment_status: 'paid', counterparty: '甲企业', amount: '113.00', occurred_on: '2026-10-03' }, fact_version: null },
  original: { description: '原分类', lines: [] }, final: { description: '会计分类', lines: [{ account_code: '6602.99', debit: '113.00', credit: '0.00' }, { account_code: '1002', debit: '0.00', credit: '113.00' }] }, corrections: [], evidence_fingerprint: 'proof',
} satisfies LearningDetail;

describe('审核样例规则建议', () => {
  it('银行规则保留来源并按总额公式而非固定金额', () => {
    const definition = definitionFromSample(sample);
    expect(definition.source_kind).toBe('bank');
    expect(definition.conditions.category).toBeNull();
    expect(definition.lines).toEqual([{ account_code: '6602.99', side: 'debit', amount_field: 'gross' }, { account_code: '1002', side: 'credit', amount_field: 'gross' }]);
    expect(JSON.stringify(definition)).not.toContain('113.00');
  });
  it('业务规则保留精确交易对方和业务条件', () => {
    const definition = definitionFromSample({ ...sample, source_kind: 'business', snapshot: { ...sample.snapshot, source_kind: 'business', business_type: 'procurement_expense', payload: { ...sample.snapshot.payload, category: 'office' }, fact_version: 3 } });
    expect(definition.source_kind).toBe('business');
    expect(definition.conditions.business_type).toBe('procurement_expense');
    expect(definition.conditions.counterparty).toBe('甲企业');
    expect(definition.conditions.category).toBe('office');
  });
  it('发布状态清楚区分人工评估前后', () => {
    expect(learningStatus).toEqual({ draft: '待发布', published: '已发布', retired: '已停用' });
  });
  it('已解析身份进入规则条件，仍保留原文和独立样例', () => {
    const identity = { status: 'resolved' as const, original: '甲企业', entity_id: 'party-1', canonical_value: '甲企业正式名称', entity_version: 2, alias_ids: ['alias-1'], alias_versions: [['alias-1', 2] as [string, number]] };
    const value = definitionFromSample({ ...sample, counterparty_identity: identity });
    expect(value.conditions.counterparty_id).toBe('party-1');
    expect(value.conditions.counterparty).toBe('甲企业');
    expect(definitionFromSample({ ...sample, counterparty_identity: { ...identity, status: 'ambiguous', entity_id: null } }).conditions.counterparty_id).toBeUndefined();
  });
  it('真实使用统计按规则隔离并合并多个历史版本', () => {
    expect(ruleUsageSummary([
      { rule_id: 'one', version: 1, reviewed_hits: 4, accountant_modified: 1, unchanged: 3, render_unavailable: 0, modified_rate: .25 },
      { rule_id: 'one', version: 2, reviewed_hits: 2, accountant_modified: 0, unchanged: 1, render_unavailable: 1, modified_rate: 0 },
      { rule_id: 'other', version: 1, reviewed_hits: 500, accountant_modified: 300, unchanged: 200, render_unavailable: 0, modified_rate: .6 },
    ], 'one')).toEqual({ reviewed_hits: 6, accountant_modified: 1, render_unavailable: 1 });
  });
});
