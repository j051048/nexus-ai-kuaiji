import { describe, expect, it } from 'vitest';
import {
  canIndependentlyReview,
  parseCloseRows,
  parseCoverageRows,
  parseReportRows,
  parseReconciliation,
} from './formal-close-types';

const scope = { bookId: 'book-a', period: '2026-09' };
const content = {
  balance_sheet: {
    assets: 973190,
    liabilities: 1000000,
    equity: -26810,
    unclosed_profit: 0,
    items: [{ account_id: 'bank', code: '1002', name: '银行存款', amount_cents: 973190 }],
  },
  profit_statement: { revenue_cents: 0, expense_cents: 26810, profit_cents: -26810, items: [] },
  cash_flow: {
    opening_cash_cents: 1000000,
    closing_cash_cents: 973190,
    net_change_cents: -26810,
    operating_cents: -26810,
    investing_cents: 0,
    financing_cents: 0,
    unclassified_cents: 0,
  },
  checks: { balance_sheet_passed: true, profit_statement_passed: true, cash_flow_passed: true },
};
const report = {
  id: 'r1',
  book_id: 'book-a',
  period: '2026-09',
  version: 1,
  status: 'validated',
  created_by: 'maker',
  content_hash: 'a'.repeat(64),
  content_json: JSON.stringify(content),
};
const close = {
  id: 'c1',
  book_id: 'book-a',
  period: '2026-09',
  version: 1,
  status: 'preflight_failed',
  created_by: 'maker',
  report_run_id: 'r1',
  checks_json: JSON.stringify([
    {
      code: 'subledger_control',
      status: 'blocked',
      detail: { passed: false, reason: '控制科目配置待确认', rows: [] },
      owner_role: '会计负责人',
      next_action: '确认控制科目配置',
    },
  ]),
};
describe('formal close response boundary', () => {
  it('exposes frozen report totals and rejects malformed amounts before review', () => {
    const [value] = parseReportRows({ reports: [report] }, scope);
    expect(value.content.balance_sheet.assets).toBe(973190);
    expect(value.content.profit_statement.expense_cents).toBe(26810);
    expect(() =>
      parseReportRows(
        {
          reports: [
            {
              ...report,
              content_json: JSON.stringify({
                ...content,
                balance_sheet: { ...content.balance_sheet, assets: true },
              }),
            },
          ],
        },
        scope,
      ),
    ).toThrow();
  });
  it('keeps a created but blocked preflight blocked and preserves actionable reason when rows are empty', () => {
    const [value] = parseCloseRows({ runs: [close] }, scope);
    expect(value.status).toBe('preflight_failed');
    expect(value.checks[0].detail.reason).toBe('控制科目配置待确认');
    expect(value.checks[0].next_action).toBe('确认控制科目配置');
  });
  it.each([
    { book_id: 'book-b' },
    { period: '2026-08' },
    { version: true },
    { version: 1.5 },
    { version: 0 },
    { created_by: '' },
  ])('rejects a wrong scope or malformed report %j atomically', (change) => {
    expect(() =>
      parseReportRows({ reports: [report, { ...report, id: 'r2', ...change }] }, scope),
    ).toThrow();
  });
  it('rejects malformed close checks instead of displaying a successful state', () => {
    expect(() =>
      parseCloseRows({ runs: [{ ...close, checks_json: '[]', status: 'unknown' }] }, scope),
    ).toThrow();
    expect(() =>
      parseCloseRows(
        {
          runs: [
            {
              ...close,
              checks_json: JSON.stringify([{ code: 'bank', status: 'pass', detail: [] }]),
            },
          ],
        },
        scope,
      ),
    ).toThrow();
  });
  it('does not derive independent review from role or read access', () => {
    expect(canIndependentlyReview(report, 'maker', true)).toBe(false);
    expect(canIndependentlyReview(report, 'reviewer', false)).toBe(false);
    expect(canIndependentlyReview(report, 'reviewer', true)).toBe(true);
  });
  it('validates bank coverage identity and strict money/version values', () => {
    const row = {
      id: 'cov',
      account_id: 'bank',
      book_id: scope.bookId,
      period: scope.period,
      state: 'pending_review',
      created_by: 'maker',
      version: 1,
      opening_cents: 10000,
      closing_cents: 7500,
      source_sha256: 'b'.repeat(64),
      source_file_id: 'file',
      source_preview_id: 'preview',
    };
    expect(parseCoverageRows({ coverages: [row] }, scope)[0].closing_cents).toBe(7500);
    for (const change of [
      { book_id: 'book-b' },
      { period: '2026-08' },
      { version: false },
      { closing_cents: 1.5 },
      { source_sha256: 'forged' },
    ])
      expect(() => parseCoverageRows({ coverages: [{ ...row, ...change }] }, scope)).toThrow();
  });
  it('requires explicit reconciliation boolean, retaining missing-configuration diagnoses', () => {
    expect(parseReconciliation({ passed: false, rows: [], reason: '缺少配置' }).reason).toBe(
      '缺少配置',
    );
    expect(() => parseReconciliation({ passed: 'false', rows: [] })).toThrow();
    expect(() =>
      parseReconciliation({ passed: false, rows: [{ passed: 'true', difference_cents: 0 }] }),
    ).toThrow();
    expect(() =>
      parseReconciliation({ passed: false, rows: [{ passed: false, difference_cents: 0.5 }] }),
    ).toThrow();
  });
  it('keeps migration-era unverifiable coverage visible without inventing source identity', () => {
    const [value] = parseCoverageRows(
      {
        coverages: [
          {
            id: 'old',
            account_id: 'bank',
            book_id: scope.bookId,
            period: scope.period,
            state: 'legacy_unverified',
            version: 1,
            created_by: null,
            opening_cents: 0,
            closing_cents: 0,
            source_file_id: null,
            source_preview_id: null,
            source_sha256: null,
          },
        ],
      },
      scope,
    );
    expect(value.created_by).toBe('');
    expect(value.source_sha256).toBe('');
    expect(canIndependentlyReview(value, 'reviewer', true)).toBe(false);
  });
  it('keeps frozen legacy control rows readable with an explicit recheck warning', () => {
    const [value] = parseCloseRows(
      {
        runs: [
          {
            ...close,
            checks_json: JSON.stringify([
              { code: 'subledger_control', status: 'blocked', detail: [] },
            ]),
          },
        ],
      },
      scope,
    );
    expect(value.checks[0].detail.reason).toContain('历史预检');
  });
});
