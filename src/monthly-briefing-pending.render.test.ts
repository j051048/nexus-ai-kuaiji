import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BriefingPending } from './MonthlyBriefing';

const source = {
  state: 'pending_report',
  reason: 'no_published_report',
  axes: {
    internal_close: { done: true, evidence: 'period_closed' },
    external_receipt: { done: false, evidence: 'exports=0' },
    tax_filing: { done: false, evidence: 'no_filing_receipt' },
    notification: { done: false, evidence: 'sink_written=0' },
    client_sign: { done: false, evidence: 'acknowledgements=0' },
  },
  axis_labels: {
    internal_close: '内部记账与锁期',
    external_receipt: '外部软件回执',
    tax_filing: '税务申报',
    notification: '通知送达',
    client_sign: '客户签收',
  },
  axis_bases: {
    internal_close: '账套月结锁定或历史期间关闭',
    external_receipt: '导出批次的客户回执核验记录',
    tax_filing: '目前没有申报回执台账',
    notification: '交付通知的实际外发记录',
    client_sign: '客户对冻结资料包的回执',
  },
};

describe('BriefingPending', () => {
  it('shows the pending label and all five independent axes', () => {
    const html = renderToStaticMarkup(createElement(BriefingPending, { label: "待会计复核 / 发布", source }));
    expect(html).toContain('待会计复核 / 发布');
    expect(html).toContain('客户可见口径只认已发布且与锁期一致的冻结报表');
    for (const label of Object.values(source.axis_labels)) expect(html).toContain(label);
    expect(html).toContain('内部记账与锁期');
    expect(html).toContain('data-done="true"');
    expect(html.match(/data-done="false"/g)?.length).toBe(4);
  });

  it('renders without axes instead of inventing amounts', () => {
    const html = renderToStaticMarkup(createElement(BriefingPending, { label: "成果版本不一致" }));
    expect(html).toContain('成果版本不一致');
    expect(html).not.toContain('briefing-axes');
    expect(html).not.toContain('¥');
  });
});
