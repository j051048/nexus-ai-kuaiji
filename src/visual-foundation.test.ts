/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = (name: string) =>
  readFileSync(new URL(`./${name}`, import.meta.url), 'utf8').replace(/\r\n?/g, '\n');
const tokens = () => source('tokens.css');

function declarations(css: string) {
  return new Map([...css.matchAll(/(--[\w-]+)\s*:\s*([^;{}]+);/g)].map((m) => [m[1], m[2].trim()]));
}

function resolve(name: string, values = declarations(tokens()), seen = new Set<string>()): string {
  if (seen.has(name)) throw new Error(`Circular token: ${name}`);
  seen.add(name);
  const value = values.get(name);
  if (!value) throw new Error(`Missing token: ${name}`);
  return value.replace(/var\((--[\w-]+)\)/g, (_, ref: string) =>
    resolve(ref, values, new Set(seen)),
  );
}

function luminance(hex: string) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return channels
    .map((n) => (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4))
    .reduce((sum, n, i) => sum + n * [0.2126, 0.7152, 0.0722][i], 0);
}

function contrast(a: string, b: string) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

describe('V01 visual foundation', () => {
  it('uses contrasting focus indicators on light workspaces and dark navigation', () => {
    expect(contrast(resolve('--focus-ring'), resolve('--surface'))).toBeGreaterThanOrEqual(3);
    expect(contrast(resolve('--sidebar-focus-ring'), '#193e31')).toBeGreaterThanOrEqual(3);
    const sidebar =
      source('agent-shell.css').match(/\.agent-shell \.sidebar\s*\{([^}]+)\}/)?.[1] ?? '';
    expect(sidebar).toContain('--focus-ring: var(--sidebar-focus-ring)');
  });

  it('keeps shared approval badges and form failures readable', () => {
    const styles = source('styles.css');
    for (const selector of [
      '.badge.draft',
      '.badge.exception',
      '.badge.approved',
      '.badge.exported',
      '.form-error,\n.form-success',
      '.form-success',
    ]) {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const block = styles.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))?.[1] ?? '';
      const property = (name: string) => {
        const value = block.match(new RegExp(`(?:^|\\n)\\s*${name}:\\s*([^;]+)`))?.[1].trim() ?? '';
        return value.replace(/var\((--[\w-]+)\)/g, (_, ref: string) => resolve(ref));
      };
      expect(contrast(property('color'), property('background')), selector).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });
  it('loads one local token layer before legacy and shell styles, without remote fonts', () => {
    const entry = source('main.tsx');
    expect(entry.indexOf("import './tokens.css'")).toBeGreaterThanOrEqual(0);
    expect(entry.indexOf("import './tokens.css'")).toBeLessThan(
      entry.indexOf("import './styles.css'"),
    );
    expect(tokens()).toContain(':root');
    expect(tokens()).not.toMatch(/@import|url\s*\(/i);
  });

  it('provides non-circular root aliases to components outside review and queue containers', () => {
    for (const name of [
      '--surface',
      '--surface-soft',
      '--surface-subtle',
      '--border',
      '--line',
      '--text-muted',
      '--muted',
      '--ink',
      '--accent',
      '--serif',
      '--font-display',
    ]) {
      expect(resolve(name), name).not.toContain('var(');
    }
    expect(resolve('--serif')).toBe(resolve('--font-sans'));
  });

  it('keeps normal text, brand buttons and status text readable against their semantic surfaces', () => {
    for (const foreground of ['--text-primary', '--text-secondary', '--text-muted']) {
      for (const background of ['--canvas', '--surface', '--surface-soft']) {
        expect(
          contrast(resolve(foreground), resolve(background)),
          `${foreground}/${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
    expect(contrast('#ffffff', resolve('--green'))).toBeGreaterThanOrEqual(4.5);
    for (const state of ['success', 'warning', 'danger', 'info']) {
      expect(
        contrast(resolve(`--status-${state}`), resolve(`--status-${state}-soft`)),
        state,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('does not shadow the global light surface text tokens with shell-specific literals', () => {
    const shell = source('agent-shell.css').match(/\.agent-shell\s*\{([^}]+)\}/)?.[1] ?? '';
    expect(shell).not.toMatch(/--(?:ink|muted|line)\s*:\s*#/);
    const base = source('styles.css').match(/:root\s*\{([^}]+)\}/)?.[1] ?? '';
    expect(base).not.toMatch(/--(?:ink|muted|line|green)\s*:\s*#/);
  });

  it('defines a readable control scale, focus and reduced-motion tokens', () => {
    const scale = {
      '--font-size-xs': '12px',
      '--font-size-sm': '13px',
      '--font-size-base': '14px',
      '--font-size-input': '14px',
      '--font-size-input-mobile': '16px',
    };
    for (const [name, value] of Object.entries(scale)) expect(resolve(name)).toBe(value);
    expect(parseInt(resolve('--control-height'))).toBeGreaterThanOrEqual(36);
    expect(parseInt(resolve('--control-height-touch'))).toBeGreaterThanOrEqual(44);
    expect(source('styles.css')).toContain('var(--focus-ring)');
    expect(source('styles.css')).toContain('prefers-reduced-motion');
  });
});
