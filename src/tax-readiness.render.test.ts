import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TaxReadinessList } from './TaxReadinessList';
import { parseReadiness } from './tax-readiness';

const base = {
  period: '2026-10',
  supported: true,
  blocked_reason: '',
  external_result: 'none',
  kinds: [
    {
      kind: 'vat',
      label: '增值税',
      readiness: 'unreviewed',
      reason: '',
      next_action: '由另一位复核人独立复核',
      reviewed: false,
      current: true,
      workpaper_version: 3,
    },
  ],
};

describe('filing readiness list', () => {
  it('shows the workpaper state and never claims an external filing', () => {
    const data = parseReadiness(base);
    expect(data).not.toBeNull();
    const html = renderToStaticMarkup(createElement(TaxReadinessList, { data }));
    expect(html).toContain('申报前清单 · 2026-10');
    expect(html).toContain('待独立复核');
    expect(html).toContain('底稿 V3');
    expect(html).toContain('未复核');
    expect(html).toContain('登记前一律视为「未申报」');
    expect(html).not.toContain('已申报成功');
  });

  it('renders the blocker instead of an estimate', () => {
    const data = parseReadiness({
      ...base,
      supported: false,
      blocked_reason: '一般纳税人的进项抵扣资格尚未接通，请交人工处理',
      kinds: [
        {
          kind: 'vat',
          label: '增值税',
          readiness: 'blocked',
          reason: '一般纳税人的进项抵扣资格尚未接通，请交人工处理',
          next_action: '人工核验后登记，系统不估算',
          reviewed: false,
          current: false,
          workpaper_version: null,
        },
      ],
    });
    const html = renderToStaticMarkup(createElement(TaxReadinessList, { data }));
    expect(html).toContain('本期不能出底稿');
    expect(html).toContain('已阻断');
    expect(html).toContain('系统不估算');
  });

  it('drops unknown states rather than inventing a ready badge', () => {
    expect(parseReadiness({ ...base, period: '2026-9' })).toBeNull();
    const data = parseReadiness({
      ...base,
      kinds: [{ kind: 'vat', label: '增值税', readiness: 'filed' }],
    });
    expect(data?.kinds).toEqual([]);
  });
});
