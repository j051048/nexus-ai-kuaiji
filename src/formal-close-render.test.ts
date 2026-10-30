import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CloseEvidenceRows, FrozenCloseReport } from './FormalClosePanel';
import { parseReportRows } from './formal-close-types';
describe('formal close evidence display', () => {
  it('distinguishes statement balance, ledger balance and unmatched evidence instead of suggesting zero difference passed', () => {
    const html = renderToStaticMarkup(
      createElement(CloseEvidenceRows, {
        value: {
          rows: [
            {
              account_id: 'bank-a',
              name: '银行一',
              passed: false,
              difference_cents: 0,
              book_difference_cents: 500,
              opening_book_difference_cents: -300,
              unmatched_count: 1,
              coverage_state: 'pending_review',
            },
          ],
        },
      }),
    );
    expect(html).toContain('流水差额');
    expect(html).toContain('账面期末差额');
    expect(html).toContain('期初账面差额');
    expect(html).toContain('未匹配流水');
    expect(html).toContain('待独立核验');
  });
  it('renders the frozen three statements and checks for the selected version before independent review', () => {
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
    const [report] = parseReportRows(
      {
        reports: [
          {
            id: 'r',
            book_id: 'b',
            period: '2026-09',
            version: 3,
            created_by: 'maker',
            status: 'validated',
            content_hash: 'a'.repeat(64),
            content_json: JSON.stringify(content),
          },
        ],
      },
      { bookId: 'b', period: '2026-09' },
    );
    const html = renderToStaticMarkup(createElement(FrozenCloseReport, { report }));
    for (const text of [
      '报表 V3',
      '资产负债表',
      '利润表',
      '现金流量表',
      '银行存款',
      '9,731.90',
      '10,000.00',
      '268.10',
      '勾稽通过',
    ])
      expect(html).toContain(text);
  });
});
