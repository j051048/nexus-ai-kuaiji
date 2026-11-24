import { createElement } from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  AccrualPreviewView,
  AccrualRequestView,
  centsToYuanInput,
  needsAdjustReason,
  previewTotals,
  profitSourceLabel,
  requestStatus,
  stampDutyLabel,
  surtaxBasisLabel,
  yuanToCents,
} from './TaxAccrualPanel';
import type { AccrualPreview, AccrualRequest, ProfitSource } from './TaxAccrualPanel';

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

describe('stamp duty three states', () => {
  it('labels missing / confirmed-zero / computed as different things', () => {
    expect(stampDutyLabel('unprovided')).toContain('未提供合同资料');
    expect(stampDutyLabel('zero_confirmed')).toContain('已确认本期无应税合同');
    expect(stampDutyLabel('computed')).toContain('按合同明细计提');
    expect(stampDutyLabel('unprovided', '服务端原文')).toBe('服务端原文');
    expect(stampDutyLabel(undefined)).toBe('');
  });

  it('says stamp duty is not accrued when contract data is missing', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, {
        data: {
          ...sample,
          stamp_duty_status: 'unprovided',
          stamp_duty_notice: '未提供合同资料，本次未计提印花税，不能视作本期零业务',
        },
        blocked: '',
      }),
    );
    expect(html).toContain('未提供合同资料');
    expect(html).toContain('data-stamp-status="unprovided"');
    expect(html).toContain('不能视作本期零业务');
  });

  it('keeps the missing-contract warning even when there are no accrual lines', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, {
        data: {
          ...sample,
          entries: [],
          stamp_duty_status: 'unprovided',
          stamp_duty_notice: '未提供合同资料，本次未计提印花税，不能视作本期零业务',
        },
        blocked: '',
      }),
    );
    expect(html).toContain('本期没有可计提的税金');
    expect(html).toContain('未提供合同资料');
  });

  it('states confirmed zero business instead of a silent zero', () => {
    const html = renderToStaticMarkup(
      createElement(AccrualPreviewView, {
        data: { ...sample, stamp_duty_status: 'zero_confirmed' },
        blocked: '',
      }),
    );
    expect(html).toContain('已确认本期无应税合同（零业务）');
    expect(html).toContain('data-stamp-status="zero_confirmed"');
  });

  it('carries the stamp note into the request receipt', () => {
    const outcome: AccrualRequest = {
      request: { id: 'req-12345678', status: 'pending', version: 1 },
      voucher: {
        command_key: 'k',
        voucher_date: '2026-10-31',
        line_count: 4,
        debit_cents: 100,
        credit_cents: 100,
      },
      refund_note: '',
      stamp_duty_status: 'unprovided',
      stamp_duty_notice: '未提供合同资料，本次未计提印花税，不能视作本期零业务',
      surtax_basis: { source: 'request', rate_bp: null },
      surtax_cents: 0,
      posted: false,
      note: '',
    };
    const html = renderToStaticMarkup(createElement(AccrualRequestView, { outcome, error: '' }));
    expect(html).toContain('未提供合同资料');
  });
});

describe('累计利润来源与人工调整原因', () => {
  const reviewed: ProfitSource = {
    suggested_profit_cents: 800_000,
    source_kind: 'reviewed_report',
    source_label: '已复核利润表本年累计',
    source_period: '2026-01..2026-02',
    period_range: '2026-01..2026-02',
    month_count: 2,
    months: [
      {
        period: '2026-01',
        book_id: 'b1',
        report_run_id: 'r1',
        version: 2,
        status: 'reviewed',
        reviewed: true,
        profit_cents: 1_000_000,
      },
      {
        period: '2026-02',
        book_id: 'b2',
        report_run_id: 'r2',
        version: 1,
        status: 'reviewed',
        reviewed: true,
        profit_cents: -200_000,
      },
    ],
    source_version: 1,
    is_final_report: true,
    report_run_id: 'r2',
    notice: null,
  };

  it('来源说明写清累计期间、月数与逐月版本', () => {
    const label = profitSourceLabel(reviewed);
    expect(label).toContain('来源：已复核利润表本年累计');
    expect(label).toContain('累计期间 2026-01..2026-02（共 2 个月）');
    expect(label).toContain('2026-01 v2 / 2026-02 v1');
    expect(
      profitSourceLabel({
        ...reviewed,
        is_final_report: false,
        source_label: '含试算利润表本年累计',
      }),
    ).toContain('含未复核试算报表');
  });

  it('没有完整来源时明确说明且人工填报必须写依据', () => {
    const empty: ProfitSource = {
      ...reviewed,
      suggested_profit_cents: null,
      source_kind: null,
      source_label: null,
      month_count: 1,
      months: [],
      missing_months: ['2026-01'],
      is_final_report: false,
      notice: '缺少 2026-01 的有效利润表快照，无法汇总本年累计利润',
    };
    expect(profitSourceLabel(empty)).toContain('缺少 2026-01');
    expect(needsAdjustReason(null, empty)).toBe(false);
    // 来源不全时会计手填了累计利润，必须填人工依据并由服务端记录。
    expect(needsAdjustReason(800_000, empty)).toBe(true);
  });

  it('人工改成不同金额时必须填原因，与预填一致则不要求', () => {
    expect(needsAdjustReason(800_000, reviewed)).toBe(false);
    expect(needsAdjustReason(750_000, reviewed)).toBe(true);
    expect(needsAdjustReason(null, reviewed)).toBe(false);
    expect(needsAdjustReason(750_000, null)).toBe(false);
  });

  it('分转元仍用字符串拼接，不引入浮点误差', () => {
    expect(centsToYuanInput(1_234_500)).toBe('12345.00');
    expect(centsToYuanInput(7)).toBe('0.07');
    expect(centsToYuanInput(-12_345)).toBe('-123.45');
  });
});
