import { createElement } from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AccrualPreviewView, previewTotals } from './TaxAccrualPanel';
import type { AccrualPreview } from './TaxAccrualPanel';

const sample: AccrualPreview = {
  entries: [
    { account: '6403', label: '附加税费用', debit_cents: 12_000, credit_cents: 0 },
    { account: '2221.03', label: '应交城市维护建设税', debit_cents: 0, credit_cents: 12_000 },
  ],
  debit_cents: 12_000,
  credit_cents: 12_000,
  posted: false,
  note: '预览仅供参考：本接口不写账，也不会生成草稿凭证。',
};

describe('accrual preview', () => {
  it('totals are stated in yuan and stay balanced', () => {
    expect(previewTotals(sample)).toContain('借方 120.00');
    expect(previewTotals(sample)).toContain('贷方 120.00');
  });

  it('renders entries without claiming the voucher was posted', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, { data: sample, blocked: '' }),
    );
    expect(html).toContain('2221.03');
    expect(html).not.toContain('已过账');
    expect(html).toContain('预览不写账');
  });

  it('shows the server explanation instead of inventing amounts', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, { data: null, blocked: '缺少已复核的增值税底稿' }),
    );
    expect(html).toContain('缺少已复核的增值税底稿');
    expect(html).not.toContain('<table');
  });

  it('empty entries read as nothing to accrue rather than zero rows', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, { data: { ...sample, entries: [] }, blocked: '' }),
    );
    expect(html).toContain('本期没有可计提的税金');
  });
});
