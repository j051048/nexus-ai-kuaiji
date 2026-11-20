import { describe, expect, it } from 'vitest';
import { describeSettlementDifference, parseAnalysis, type AnalysisScope } from './analysis-result';

const scope: AnalysisScope = {
  firmId: 'f',
  companyId: 'c',
  bookId: 'b',
  period: '2026-10',
  kind: 'expense_bridge',
  accountCode: '6602.02',
  voucherId: '',
  offset: 0,
  limit: 50,
};
const value = () => ({
  firm_id: 'f',
  company_id: 'c',
  book_id: 'b',
  period: '2026-10',
  kind: 'expense_bridge',
  status: 'partial',
  summary: 'summary',
  facts: {
    current_total: '1.00',
    previous_total: null,
    difference: null,
    currency: 'CNY',
    current_rows: [],
    previous_rows: [],
  },
  coverage: {
    scope: 'current_only',
    rows_read: 1,
    detail_rows: 1,
    total_rows: 1,
    has_more: false,
    detail_complete: true,
    total_complete: true,
    offset: 0,
  },
  request: {
    account_code: '6602.02',
    compare_period: null,
    target_voucher_id: null,
    offset: 0,
    limit: 50,
  },
  missing: ['previous_period'],
  handoff: null,
  fingerprint: 'a'.repeat(64),
  source_version: 'b'.repeat(64),
});
describe('analysis atomic acceptance', () => {
  it('accepts only the complete requested scope', () => {
    expect(parseAnalysis(value(), scope).summary).toBe('summary');
    for (const key of ['firm_id', 'company_id', 'book_id', 'period', 'kind'])
      expect(() => parseAnalysis({ ...value(), [key]: 'wrong' }, scope)).toThrow();
  });
  it('rejects target/filter/page drift and malformed coverage', () => {
    for (const key of ['offset', 'limit', 'account_code'])
      expect(() =>
        parseAnalysis({ ...value(), request: { ...value().request, [key]: 'wrong' } }, scope),
      ).toThrow();
    expect(() =>
      parseAnalysis({ ...value(), coverage: { ...value().coverage, total_rows: true } }, scope),
    ).toThrow();
    expect(() => parseAnalysis({ ...value(), fingerprint: '' }, scope)).toThrow();
  });
  it('rejects an old source version on the next page', () =>
    expect(() => parseAnalysis(value(), { ...scope, sourceVersion: 'c'.repeat(64) })).toThrow());
  it('rejects wrong currency and malformed evidence rows before publication', () => {
    for (const patch of [
      { currency: 'USD' },
      { current_rows: [{ amount_cents: true }] },
      { previous_rows: 'bad' },
    ])
      expect(() =>
        parseAnalysis({ ...value(), facts: { ...value().facts, ...patch } }, scope),
      ).toThrow();
  });

  it('accepts all backend settlement_difference reasons and renders exact guidance', () => {
    const settlementScope: AnalysisScope = {
      ...scope,
      kind: 'settlement_difference',
      accountCode: '',
      settlementSourceId: 's1',
      bankSourceId: 'b1',
      feeSourceId: 'f1',
    };
    const make = (reason: string, extraFacts: Record<string, unknown> = {}) => ({
      ...value(),
      kind: 'settlement_difference',
      status: reason === 'none' || reason === 'fee_evidence' ? 'complete' : 'partial',
      facts: {
        settlement_total: '1000.00',
        bank_total: reason === 'none' ? '1000.00' : '650.00',
        difference: reason === 'none' ? '0.00' : '350.00',
        fee_total: reason === 'fee_evidence_mismatch' ? '340.00' : '350.00',
        reason,
        settlement_source: { id: 's1', source_name: '平台结算单', date: '2026-10-10' },
        bank_source: { id: 'b1', source_name: '银行到账流水', date: '2026-10-10' },
        fee_source:
          reason === 'unresolved' || reason === 'none'
            ? null
            : {
                id: 'f1',
                source_name: '手续费明细',
                date: reason === 'cross_period_evidence' ? '2026-09-28' : '2026-10-10',
              },
        ...extraFacts,
      },
      request: {
        account_code: null,
        compare_period: null,
        target_voucher_id: null,
        settlement_source_id: 's1',
        bank_source_id: 'b1',
        fee_source_id: 'f1',
        offset: 0,
        limit: 50,
      },
      missing: [],
    });

    for (const reason of [
      'none',
      'fee_evidence',
      'fee_evidence_mismatch',
      'cross_period_evidence',
      'unverified_source_relation',
      'fee_relation_mismatch',
      'unresolved',
    ]) {
      const parsed = parseAnalysis(make(reason), settlementScope);
      expect(parsed.facts.reason).toBe(reason);
    }

    const balanced = describeSettlementDifference(make('none').facts);
    expect(balanced.headline).toContain('已对平');
    expect(balanced.headline).not.toContain('缺手续费');
    expect(balanced.nextStep).toContain('无需补件');

    const crossPeriod = describeSettlementDifference(make('cross_period_evidence').facts);
    expect(crossPeriod.headline).toContain('2026-09');
    expect(crossPeriod.headline).toContain('跨期');

    const mismatch = describeSettlementDifference(make('fee_evidence_mismatch').facts);
    expect(mismatch.headline).toContain('350.00');
    expect(mismatch.headline).toContain('340.00');
    expect(mismatch.headline).toContain('手续费明细');

    const unverified = describeSettlementDifference(make('unverified_source_relation').facts);
    expect(unverified.headline).toContain('来源关系尚未核对');

    const relationMismatch = describeSettlementDifference(make('fee_relation_mismatch').facts);
    expect(relationMismatch.headline).toContain('不在已核对的来源关系');

    const unresolved = describeSettlementDifference(make('unresolved').facts);
    expect(unresolved.headline).toContain('缺手续费或扣款明细');
  });
});
