/// <reference types="node" />
import { readFileSync } from 'node:fs';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

const pageStyles = [
  'workspace.css',
  'cockpit.css',
  'client-portal.css',
  'intake.css',
  'workflow-guide.css',
  'journal-grid.css',
  'review.css',
  'queue-automation.css',
];
const css = (file: string) => readFileSync(new URL(file, import.meta.url), 'utf8');

describe('V01 working page typography and shared surfaces', () => {
  it('reserves enough journal action space for readable labels and touch controls', () => {
    const action =
      css('journal-grid.css').match(
        /\.journal-entry-grid \.journal-grid-action\s*\{([^}]+)\}/,
      )?.[1] ?? '';
    expect(
      Number.parseFloat(action.match(/width:\s*([\d.]+)px/)?.[1] ?? '0'),
    ).toBeGreaterThanOrEqual(44);
    expect(css('journal-grid.css')).toMatch(
      /grid-template-columns:\s*24px minmax\(0, 1fr\) minmax\(0, 1fr\) 44px/,
    );
  });
  it('lets the workspace search inherit the mobile readable input scale', () => {
    const search = css('workspace.css').match(/\.agent-search input\s*\{([^}]+)\}/)?.[1] ?? '';
    expect(search).not.toMatch(/font-size:[^;]*!important/);
    expect(search).toContain('font-size: var(--font-size-input)');
  });
  it.each(pageStyles)('%s does not shrink readable text below 12px', (file) => {
    const smallText = [...css(file).matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)]
      .map((match) => Number(match[1]))
      .filter((size) => size > 0 && size < 12);
    expect(smallText, file).toEqual([]);
  });

  it('uses the shared sans family for work titles and removes oversized firm titles', () => {
    expect(css('workspace.css')).not.toContain('font-family: var(--serif');
    expect(css('workspace.css')).toContain('font-family: var(--font-sans)');
    expect(css('cockpit.css')).toMatch(
      /\.cockpit-heading h1\s*\{[^}]*font-family:\s*var\(--font-sans\)[^}]*font-size:\s*var\(--font-size-title\)/,
    );
  });

  it('does not let review or queue containers redefine shared surface/text tokens', () => {
    for (const file of ['review.css', 'queue-automation.css']) {
      expect(css(file), file).not.toMatch(/--(?:surface|surface-soft|border|text-muted):/);
    }
    expect(css('queue-automation.css')).toContain('var(--surface-soft)');
  });

  it('uses shared readable ink for secondary notes instead of faded page colors', () => {
    expect(css('workspace.css')).not.toContain('color: #7b8874');
    expect(css('cockpit.css')).toMatch(
      /\.cockpit-diagnostics\s*\{[^}]*color:\s*var\(--text-muted\)/,
    );
  });

  it('keeps mobile editable journal values at the input size and leaves room for long amounts', () => {
    expect(css('journal-grid.css')).toMatch(
      /@media\s*\(max-width:\s*700px\)[\s\S]*\.journal-entry-grid input,[\s\S]*font-size:\s*var\(--font-size-input-mobile\)/,
    );
    expect(css('journal-grid.css')).toMatch(
      /\.journal-entry-grid \.journal-grid-amount\s*\{[^}]*width:\s*clamp\(124px,/,
    );
    expect(css('journal-grid.css')).toContain('font-variant-numeric: tabular-nums');
    expect(css('journal-grid.css')).toContain('text-align: right');
  });

  it.each([
    ['workspace.css', '.workspace-primary-actions .button'],
    ['intake.css', '.intake-footer > div > button'],
    ['client-portal.css', '.client-portal-tabs button'],
  ])('%s keeps primary mobile labels at least 13px', (file, selector) => {
    const sizes: number[] = [];
    const tokenSize: Record<string, number> = {
      '--font-size-xs': 12,
      '--font-size-sm': 13,
      '--font-size-base': 14,
    };
    postcss.parse(css(file)).walkRules((rule) => {
      if (!rule.selectors.includes(selector) || rule.parent?.type !== 'atrule') return;
      rule.walkDecls('font-size', (declaration) => {
        const token = declaration.value.match(/^var\((--[^)]+)\)$/)?.[1];
        sizes.push(token ? tokenSize[token] : Number.parseFloat(declaration.value));
      });
    });
    expect(sizes.length).toBeGreaterThan(0);
    expect(sizes.every((size) => Number.isFinite(size) && size >= 13)).toBe(true);
  });
});
