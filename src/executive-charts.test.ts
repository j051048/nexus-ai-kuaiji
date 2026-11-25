import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  CHART_PALETTE,
  DonutPieChart,
  GroupedBarChart,
  ProgressRing,
  TrendAreaChart,
  formatCompactCny,
} from './ExecutiveCharts';

describe('第二期：集团经营驾驶舱矢量商务图表组件库', () => {
  it('formatCompactCny 精准格式化元、万、亿、负数与缺报态', () => {
    expect(formatCompactCny(null)).toBe('核算中');
    expect(formatCompactCny(0)).toBe('0元');
    expect(formatCompactCny(850_000)).toBe('8,500元');
    expect(formatCompactCny(6_420_000)).toBe('6.42万');
    expect(formatCompactCny(-12_850_000)).toBe('-12.85万');
    expect(formatCompactCny(128_500_000)).toBe('128.5万');
    expect(formatCompactCny(25_600_000_000)).toBe('2.56亿');
  });

  it('TrendAreaChart 渲染平滑折线面积图，并对缺报月份诚实断线不补 0', () => {
    const html = renderToStaticMarkup(
      createElement(TrendAreaChart, {
        labels: ['07月', '08月', '09月', '10月'],
        series: [
          {
            key: 'rev',
            label: '已复核收入',
            color: CHART_PALETTE[0],
            area: true,
            values: [10_000_000, null, 12_500_000, 14_000_000],
          },
        ],
        ariaLabel: '集团收支走势图',
      }),
    );
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="集团收支走势图"');
    expect(html).toContain('07月');
    expect(html).toContain('10月');
    // 只有 3 个非空点，因此渲染 3 个数据圆点，不把 null 补为 0 点
    expect(html.match(/class="exec-chart-point"/g)).toHaveLength(3);
  });

  it('TrendAreaChart 全缺报时显示友好空态提示', () => {
    const html = renderToStaticMarkup(
      createElement(TrendAreaChart, {
        labels: ['09月', '10月'],
        series: [{ key: 'rev', label: '收入', color: CHART_PALETTE[0], values: [null, null] }],
        ariaLabel: '走势图',
        emptyText: '暂无已复核走势，不补 0',
      }),
    );
    expect(html).toContain('暂无已复核走势，不补 0');
    expect(html).not.toContain('<svg');
  });

  it('DonutPieChart 渲染多子公司环形占比与图例百分比，并支持点击下钻回调', () => {
    const html = renderToStaticMarkup(
      createElement(DonutPieChart, {
        slices: [
          {
            id: '安达出租车',
            label: '安达出租车',
            value: 600_000,
            displayValue: '60.00万',
            color: CHART_PALETTE[0],
          },
          {
            id: '星河广告',
            label: '星河广告',
            value: 400_000,
            displayValue: '40.00万',
            color: CHART_PALETTE[1],
          },
        ],
        centerLabel: '收入合计',
        centerValue: '100.0万',
        ariaLabel: '子公司收入占比',
        onSelect: () => {},
      }),
    );
    expect(html).toContain('收入合计');
    expect(html).toContain('100.0万');
    expect(html).toContain('安达出租车');
    expect(html).toContain('60.0%');
    expect(html).toContain('40.0%');
    expect(html).toContain('clickable');
  });

  it('GroupedBarChart 区分已复核盈亏柱体与未出报表“核算中”占位', () => {
    const html = renderToStaticMarkup(
      createElement(GroupedBarChart, {
        items: [
          {
            id: '安达出租车',
            label: '安达出租车',
            known: true,
            revenueCents: 12_000_000,
            expensesCents: 8_000_000,
            profitCents: 4_000_000,
          },
          {
            id: '内部医院',
            label: '内部医院',
            known: false,
            revenueCents: 0,
            expensesCents: 0,
            profitCents: 0,
          },
        ],
        ariaLabel: '子公司对比柱状图',
        onSelect: () => {},
      }),
    );
    expect(html).toContain('安达出租车');
    expect(html).toContain('12.00万');
    expect(html).toContain('内部医院');
    expect(html).toContain('核算中');
    expect(html).toContain('点击任意子公司柱体可下钻明细');
  });

  it('ProgressRing 准确计算结账覆盖百分比', () => {
    const html = renderToStaticMarkup(
      createElement(ProgressRing, { value: 4, total: 5, label: '复核覆盖率' }),
    );
    expect(html).toContain('aria-label="复核覆盖率 80%"');
    expect(html).toContain('80%');
  });
});
