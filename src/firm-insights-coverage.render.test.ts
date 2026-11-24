import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CoverageCounts, MissingCompanies, StandardBreakdown } from './firm-insights-coverage';

const kpis = {
  companies: 5,
  managed_companies: 5,
  opened_companies: 4,
  reported_companies: 2,
  known_reports: 2,
};

const financial = {
  coverage_label: '已纳入 2/5 家（不完整：缺 合成丁公司）',
  known_books: 2,
  total_books: 5,
  managed_companies: 5,
  opened_companies: 4,
  reported_companies: 2,
  companies_without_books: 1,
  mixed_standards: true,
  totals_comparable: false,
  standards_breakdown: [
    {
      standard_kind: 'small_enterprise',
      standard_label: '小企业会计准则',
      books: 1,
      assets_cents: 250_000_000,
      revenue_cents: 80_000_000,
      profit_cents: 12_000_000,
      cash_closing_cents: 30_000_000,
    },
    {
      standard_kind: 'enterprise',
      standard_label: '企业会计准则',
      books: 1,
      assets_cents: 310_000_000,
      revenue_cents: 95_000_000,
      profit_cents: 18_000_000,
      cash_closing_cents: 41_000_000,
    },
  ],
  missing_companies: [
    {
      company_id: 'c',
      company_name: '合成丙公司',
      book_id: 'book-c',
      owner_name: '待安排',
      top_blocker: '报表尚未复核',
      has_trial_draft: true,
      reason: 'report_missing',
    },
    {
      company_id: 'd',
      company_name: '合成丁公司',
      book_id: null,
      owner_name: '待安排',
      top_blocker: '本期未开账',
      has_trial_draft: false,
      reason: 'not_opened',
    },
  ],
  pending_book_reports: [
    {
      company_id: 'a',
      company_name: '集团甲公司',
      book_id: 'book-a2',
      top_blocker: '报表尚未复核',
      reason: 'book_report_missing',
    },
  ],
};

describe('集团首页法人口径展示', () => {
  it('区分已纳管 / 已开账 / 已报送三档计数', () => {
    const html = renderToStaticMarkup(createElement(CoverageCounts, { kpis }));
    expect(html).toContain('已纳管');
    expect(html).toContain('>5<');
    expect(html).toContain('当期已开账');
    expect(html).toContain('>4<');
    expect(html).toContain('已报送');
    expect(html).toContain('>2<');
    expect(html).not.toContain('undefined');
  });

  it('未开账法人点名且标明不是零申报，已报送法人的未复核账套单列', () => {
    const html = renderToStaticMarkup(createElement(MissingCompanies, { financial }));
    expect(html).toContain('未纳入合计法人（2 家');
    expect(html).toContain('合成丁公司');
    expect(html).toContain('本期未开账（没有账套，不是零申报）');
    expect(html).toContain('data-reason="not_opened"');
    expect(html).toContain('合成丙公司');
    expect(html).toContain('报表尚未复核');
    expect(html).toContain('已有法人报送、但仍有账套未复核（1 套');
    expect(html).toContain('集团甲公司');
  });

  it('多准则只给分口径小计，不生成混算总计行', () => {
    const html = renderToStaticMarkup(createElement(StandardBreakdown, { financial }));
    expect(html).toContain('小企业会计准则');
    expect(html).toContain('企业会计准则');
    expect(html).toContain('不得相加');
    expect(html).toContain('¥250.00');
    expect(html).toContain('¥310.00');
    // 表头 1 行 + 两个准则各 1 行，没有“合计”汇总行。
    expect(html.match(/<tr>/g)?.length).toBe(3);
    expect(html).not.toContain('合计');
  });

  it('单一准则时不渲染分口径表', () => {
    const html = renderToStaticMarkup(
      createElement(StandardBreakdown, {
        financial: { ...financial, mixed_standards: false },
      }),
    );
    expect(html).toBe('');
  });
});
