import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WorkflowGuide, type WorkflowGuidance } from './WorkflowGuide';

const guide: WorkflowGuidance = {
  title: '先核对，再移交',
  steps: [
    { id: 'prepare', label: '准备资料', state: 'done' },
    { id: 'check', label: '核对金额', state: 'blocked' },
    { id: 'submit', label: '交给会计', state: 'upcoming' },
  ],
  status: '金额存在差异，事项尚未交接。',
  next: '核对原件，或使用下方退回按钮补充资料。',
  tone: 'warning',
  checks: ['查看票据金额', '核对企业与交易日期'],
};

describe('工作流程引导的公共展示契约', () => {
  it('复核紧凑模式保留风险和下一步，只折叠步骤说明', () => {
    const html = renderToStaticMarkup(
      createElement(WorkflowGuide, { label: '会计复核引导', guide, compact: true }),
    );
    expect(html.indexOf('金额存在差异')).toBeLessThan(html.indexOf('<details'));
    expect(html).toMatch(
      /<details><summary>查看复核步骤与说明<\/summary><h3>先核对，再移交<\/h3><ol/,
    );
    expect(html).not.toMatch(/<details[^>]*\bopen/);
  });
  it('用有序步骤和唯一当前步骤说明阻断，保留原始业务状态', () => {
    const html = renderToStaticMarkup(createElement(WorkflowGuide, { label: '核验引导', guide }));
    expect(html).toContain('aria-label="核验引导"');
    expect(html).toContain('<ol');
    expect(html.match(/aria-current="step"/g)).toHaveLength(1);
    expect(html).toContain('金额存在差异，事项尚未交接。');
    expect(html).toContain('核对原件，或使用下方退回按钮补充资料。');
    expect(html).not.toContain('<button');
    expect(html).not.toContain('已交接会计');
  });

  it('检查要点默认折叠，复用已有动作，不产生新的确认或提交控件', () => {
    const html = renderToStaticMarkup(createElement(WorkflowGuide, { label: '核验引导', guide }));
    expect(html).toContain('<details');
    expect(html).toContain('本步核对要点');
    expect(html).not.toMatch(/<details[^>]*\bopen/);
    expect(html).not.toContain('<input');
    expect(html).not.toContain('<form');
  });

  it('把来源文字当作普通文本，不执行 HTML，也不推断完成状态', () => {
    const html = renderToStaticMarkup(
      createElement(WorkflowGuide, {
        label: '原件说明',
        guide: { ...guide, status: '<script>自动审核</script>', next: '人工核验', checks: [] },
      }),
    );
    expect(html).toContain('&lt;script&gt;自动审核&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<details');
  });
});
