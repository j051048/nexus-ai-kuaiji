import { createElement } from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  AccrualPreviewView,
  AccrualRequestView,
  previewTotals,
  requestStatus,
  surtaxBasisLabel,
  yuanToCents,
} from './TaxAccrualPanel';
import type { AccrualPreview, AccrualRequest } from './TaxAccrualPanel';

const sample: AccrualPreview = {
  entries: [
    { account: '6403', side: 'debit', cents: 12_000, memo: '附加税计提' },
    { account: '2221.03', side: 'credit', cents: 12_000, memo: '附加税计提' },
  ],
  debit_cents: 12_000,
  credit_cents: 12_000,
  refund_note: '',
  surtax_basis: { source: 'tax_profile', rate_bp: 1200 },
  posted: false,
  note: '预览仅供参考：本接口不写账，也不会生成草稿凭证。',
};

describe('accrual preview', () => {
  it('totals are stated in yuan and stay balanced', () => {
    expect(previewTotals(sample)).toContain('借方 120.00');
    expect(previewTotals(sample)).toContain('贷方 120.00');
  });

  it('renders each entry on its own side without claiming the voucher was posted', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, { data: sample, blocked: '' }),
    );
    expect(html).toContain('2221.03');
    expect(html).toContain('附加税计提');
    expect(html).toContain('120.00');
    expect(html).not.toContain('已过账');
    expect(html).toContain('预览不写账');
    expect(html).toContain('附加税按税务档案核定税率 12% 计征');
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

const outcome: AccrualRequest = {
  request: { id: 'abcdef1234567890', status: 'pending', version: 1 },
  voucher: {
    command_key: 'tax-accrual:0123456789abcdef0123456789abcdef01234567',
    voucher_date: '2026-10-31',
    line_count: 4,
    debit_cents: 25_360,
    credit_cents: 25_360,
  },
  refund_note: '',
  surtax_basis: { source: 'tax_profile', rate_bp: 1200 },
  surtax_cents: 360,
  posted: false,
  note: '已生成待复核的手工凭证申请',
};

describe('accrual manual request', () => {
  it('states the pending status and where to review it, never that it was posted', () => {
    const html = renderToStaticMarkup(createElement(AccrualRequestView, { outcome, error: '' }));
    expect(html).toContain('已生成申请 #abcdef12');
    expect(html).toContain('待复核');
    expect(html).toContain('手工凭证办理');
    expect(html).toContain('制单人不能复核本人申请');
    expect(html).toContain('253.60');
    expect(html).toContain('附加税按税务档案核定税率 12% 计征');
    expect(html).not.toContain('已过账');
  });

  it('carries the overpaid-CIT note instead of silently dropping it', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualRequestView, {
        outcome: {
          ...outcome,
          refund_note: '本期企业所得税为多缴，未生成计提分录；请走退税或抵减',
        },
        error: '',
      }),
    );
    expect(html).toContain('请走退税或抵减');
  });

  it('shows the server rejection as an alert with no success block', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualRequestView, { outcome: null, error: '计提科目 6801 不在当前科目表中' }),
    );
    expect(html).toContain('计提科目 6801');
    expect(html).not.toContain('已生成申请');
  });

  it('unknown status is shown verbatim instead of being guessed', () => {
    expect(requestStatus({ ...outcome, request: { ...outcome.request, status: 'archived' } })).toBe(
      'archived',
    );
    expect(requestStatus(outcome)).toBe('待复核');
  });

  it('a formal voucher is what makes it posted, not the request status', () => {
    const posted: AccrualRequest = { ...outcome, posted_voucher_id: 'voucher-1' };
    expect(requestStatus(posted)).toBe('已过账');
    const html = renderToStaticMarkup(
      createElement(AccrualRequestView, { outcome: posted, error: '' }),
    );
    expect(html).toContain('已生成正式凭证，记账完成');
    expect(html).not.toContain('申请尚未过账');
  });

  it('explicit policy items are named as such, not as a profile rate', () => {
    expect(surtaxBasisLabel({ source: 'request', rate_bp: null })).toContain('政策条目');
  });
});

describe('yuan parsing', () => {
  it('converts text to integer cents without floating point', () => {
    expect(yuanToCents('1000')).toBe(100_000);
    expect(yuanToCents('1000.5')).toBe(100_050);
    expect(yuanToCents('0.07')).toBe(7);
    expect(yuanToCents(' 1,234.56 ')).toBe(123_456);
    expect(yuanToCents('-20.10')).toBe(-2_010);
    expect(yuanToCents('-0.50')).toBe(-50);
  });

  it('refuses anything that is not a two-decimal amount', () => {
    for (const text of ['', 'abc', '1.234', '1e5', '1.', '.5', '--1', '1 000'])
      expect(yuanToCents(text)).toBeNull();
  });
});
