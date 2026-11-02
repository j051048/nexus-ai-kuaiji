import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FilingList } from './TaxFilingRecords';
import { formatYuan, parseFilings, type TaxFiling } from './tax-filings';

const row: TaxFiling = {
  id: 'fil_1',
  period: '2026-10',
  kind: 'vat',
  kind_label: '增值税',
  status: 'paid',
  status_label: '已缴款',
  external_ref: 'SB2026100001',
  occurred_at: '2026-11-10',
  amount_cents: 3000,
  evidence_note: '电子税务局回执已归档',
};

describe('filing records', () => {
  it('keeps unknown payloads out instead of inventing rows', () => {
    expect(parseFilings({ filings: [{ status: 'approved', external_ref: 'x' }] })).toEqual([]);
    expect(parseFilings(null)).toEqual([]);
    const rows = parseFilings({ filings: [row] });
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('paid');
  });

  it('formats cents as yuan without rounding drift', () => {
    expect(formatYuan(3000)).toBe('30.00');
    expect(formatYuan(0)).toBe('0.00');
  });

  it('renders the registered evidence and the not-filed boundary', () => {
    expect(renderToStaticMarkup(createElement(FilingList, { rows: [] }))).toContain('登记前一律视为「未申报」');
    const html = renderToStaticMarkup(createElement(FilingList, { rows: [row] }));
    expect(html).toContain('增值税 · 已缴款');
    expect(html).toContain('SB2026100001');
    expect(html).toContain('30.00 元');
  });
});
