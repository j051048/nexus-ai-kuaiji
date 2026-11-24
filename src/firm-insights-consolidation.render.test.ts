import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  ConsolidationWorksheetView,
  consolidationHighlights,
  worksheetCompanyIds,
} from './firm-insights-consolidation';
import type { WorksheetSheet } from './firm-insights-consolidation';

const sheet: WorksheetSheet = {
  period: '2026-10',
  statement: '集团管理合并试算（非经审计法定合并报表）',
  statement_note: '仅供内部管理参考，不能对外报送。',
  included_companies: [
    { company_id: 'c-a', company_name: '合成甲公司', standard_kind: 'small_enterprise' },
    { company_id: 'c-b', company_name: '合成乙公司', standard_kind: 'small_enterprise' },
    { company_id: 'c-c', company_name: '合成丙公司', standard_kind: 'enterprise' },
  ],
  missing_companies: [{ company_id: 'c-d', company_name: '合成丁公司', reason: 'not_opened' }],
  denied_companies: [],
  totals: {
    pairs: 1,
    matched_cents: 5_000_000,
    matched: '50,000.00',
    difference_cents: 0,
    difference: '0.00',
    open_pairs: 0,
    comparable: true,
    scope: '同口径可比合计（只含会计准则一致的法人对）',
  },
  totals_by_standard: [
    {
      standard: 'small_enterprise',
      standard_label: '小企业会计准则',
      pairs: 1,
      matched_cents: 5_000_000,
      matched: '50,000.00',
      difference_cents: 0,
      difference: '0.00',
      open_pairs: 0,
    },
  ],
  not_merged_totals: {
    pairs: 1,
    matched_cents: 9_000_000,
    matched: '90,000.00',
    difference_cents: 0,
    difference: '0.00',
    label: '异准则未合并往来（未计入同口径可比合计）',
  },
  not_merged_pairs: [],
  open_differences: [
    {
      kind: 'internal_receivable',
      counterpart_kind: 'internal_payable',
      left_company_name: '合成丙公司',
      right_company_name: '合成乙公司',
      matched_cents: 1_000_000,
      matched: '10,000.00',
      difference_cents: 35_000,
      difference: '350.00',
      status: 'difference',
    },
  ],
};

describe('集团合并试算底稿视图', () => {
  it('列出纳入与缺报法人，并固定声明非审计合并报表', () => {
    const html = renderToStaticMarkup(
      createElement(ConsolidationWorksheetView, { sheet, error: '', loading: false }),
    );
    expect(html).toContain('集团管理合并试算（非经审计法定合并报表）');
    expect(html).toContain('纳入 3 家法人');
    expect(html).toContain('缺报法人 1 家：合成丁公司');
    expect(html).toContain('未达差额明细 1 对');
    expect(html).toContain('差额 350.00 元');
  });

  it('同口径可比合计只汇总同准则，跨准则金额单列且写明不进入合计', () => {
    const html = renderToStaticMarkup(
      createElement(ConsolidationWorksheetView, { sheet, error: '', loading: false }),
    );
    expect(html).toContain('同口径可比合计');
    expect(html).toContain('50,000.00 元');
    expect(html).toContain('data-cross-standard="true"');
    expect(html).toContain('90,000.00 元');
    expect(html).toContain('不进入上面的可比合计');
    // 顶层合计绝不能出现两套准则相加后的 140,000.00。
    expect(html).not.toContain('140,000.00');
  });

  it('加载中与失败时给出明确状态，不渲染空表冒充结果', () => {
    const loading = renderToStaticMarkup(
      createElement(ConsolidationWorksheetView, { sheet: null, error: '', loading: true }),
    );
    expect(loading).toContain('正在读取合并试算底稿');
    const failed = renderToStaticMarkup(
      createElement(ConsolidationWorksheetView, {
        sheet: null,
        error: '请至少选择一家法人',
        loading: false,
      }),
    );
    expect(failed).toContain('请至少选择一家法人');
    expect(failed).not.toContain('同口径可比合计');
  });

  it('法人清单由已报与缺报合并去重，缺报法人在分母里', () => {
    expect(
      worksheetCompanyIds({
        included_companies: [{ company_id: 'c-a' }, { company_id: 'c-b' }],
        missing_companies: [{ company_id: 'c-b' }, { company_id: 'c-d' }],
      }),
    ).toEqual(['c-a', 'c-b', 'c-d']);
    expect(worksheetCompanyIds({})).toEqual([]);
  });

  it('摘要行可被纯函数复算', () => {
    const lines = consolidationHighlights(sheet);
    expect(lines[0]).toContain('纳入 3 家法人');
    expect(lines[1]).toContain('匹配 50,000.00 元');
    expect(lines[1]).toContain('未达差额 0.00 元');
    expect(lines.some((line) => line.includes('异准则未合并往来'))).toBe(true);
    expect(lines.some((line) => line.includes('合成丁公司'))).toBe(true);
  });

  it('多准则分组不可比时隐藏顶层混算总计，并把字符串缺报法人映射为中文名', () => {
    const multi: WorksheetSheet = {
      ...sheet,
      included_companies: [
        { company_id: 'c-a', name: '合成甲公司' },
        { company_id: 'c-b', name: '合成乙公司' },
        { company_id: 'c-c', name: '合成丙公司' },
        { company_id: 'c-e', name: '合成戊公司' },
      ],
      missing_companies: ['c-d'],
      totals: {
        pairs: 2,
        matched_cents: null,
        matched: null,
        difference_cents: null,
        difference: null,
        open_pairs: 1,
        comparable: false,
        scope: '存在多种会计准则分组，顶层不混算总计，请按下方分准则小计复算',
      },
      totals_by_standard: [
        sheet.totals_by_standard[0],
        {
          standard: 'enterprise',
          standard_label: '企业会计准则',
          pairs: 1,
          matched_cents: 3_000_000,
          matched: '30,000.00',
          difference_cents: 35_000,
          difference: '350.00',
          open_pairs: 1,
        },
      ],
    };
    const html = renderToStaticMarkup(
      createElement(ConsolidationWorksheetView, {
        sheet: multi,
        error: '',
        loading: false,
        companyNames: { 'c-d': '合成丁公司' },
      }),
    );
    expect(html).toContain('不混算总计');
    expect(html).toContain('缺报法人 1 家：合成丁公司');
    expect(html).not.toContain('<tfoot>');
    expect(html).not.toContain('80,000.00');
  });
});
