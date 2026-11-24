/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { QuickPhraseChips } from './QuickPhraseChips';

describe('QuickPhraseChips 快捷意见芯片', () => {
  const phrases = ['已核对本月报表与资料，确认收到', '已查收本期财务交付资料'] as const;

  it('渲染为无障碍按钮组并准确反映当前选中状态', () => {
    const html = renderToStaticMarkup(
      createElement(QuickPhraseChips, {
        label: '常用签收说明',
        phrases,
        value: '已核对本月报表与资料，确认收到',
        onSelect: () => {},
      }),
    );
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="常用签收说明"');
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain('quick-phrase-chip active');
  });

  it('空列表不渲染多余容器，禁用态正确透传', () => {
    const empty = renderToStaticMarkup(
      createElement(QuickPhraseChips, {
        label: '空组',
        phrases: [],
        value: '',
        onSelect: () => {},
      }),
    );
    expect(empty).toBe('');

    const disabledHtml = renderToStaticMarkup(
      createElement(QuickPhraseChips, {
        label: '禁用组',
        phrases,
        value: '',
        disabled: true,
        onSelect: () => {},
      }),
    );
    expect(disabledHtml.match(/disabled=""/g)).toHaveLength(2);
  });

  it('样式仅使用共享设计令牌且不把可读文字缩到 12px 以下', () => {
    const css = readFileSync(new URL('quick-phrase-chips.css', import.meta.url), 'utf8');
    expect(css).not.toMatch(/#[\da-f]{3,8}\b/i);
    expect(css).toContain('var(--focus-ring)');
    expect(css).toContain('var(--brand-soft)');
    const small = [...css.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)]
      .map((m) => Number(m[1]))
      .filter((size) => size > 0 && size < 12);
    expect(small).toEqual([]);
  });
});
