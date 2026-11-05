import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WorkflowGuide, type WorkflowGuidance } from './WorkflowGuide';

describe('财务核验的紧凑引导', () => {
  const guide: WorkflowGuidance = {
    title: '核验资料后交接',
    status: '原件金额存在差异，尚未交接',
    next: '先核对原件，缺资料时退回补充',
    tone: 'warning',
    steps: [{ id: 'finance', label: '财务核验', state: 'blocked' }],
    checks: ['核对真实发生日期和金额'],
  };
  it('可按业务命名折叠说明，阻断及下一步仍直接可见', () => {
    const html = renderToStaticMarkup(
      createElement(WorkflowGuide, {
        label: '事项办理引导',
        guide,
        compact: true,
        detailsLabel: '查看核验步骤与说明',
      }),
    );
    expect(html).toContain('<summary>查看核验步骤与说明</summary>');
    expect(html.indexOf('原件金额存在差异')).toBeLessThan(html.indexOf('<details'));
    expect(html.indexOf('先核对原件')).toBeLessThan(html.indexOf('<details'));
    expect(html).not.toMatch(/<details[^>]*\bopen/);
    expect(html).not.toContain('<button');
  });
  it('原有会计复核紧凑说明保持兼容', () => {
    const html = renderToStaticMarkup(
      createElement(WorkflowGuide, {
        label: '会计复核引导',
        guide,
        compact: true,
      }),
    );
    expect(html).toContain('<summary>查看复核步骤与说明</summary>');
  });
});
