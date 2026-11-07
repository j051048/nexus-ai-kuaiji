import { describe, expect, it } from 'vitest';
import { parseAnalysis, type AnalysisScope } from './analysis-result';

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
});
