import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  MAX_CENTS,
  balanceDirection,
  centsToApiString,
  formatCents,
  parseYuanToCents,
  sumCents,
} from './money';

describe('parseYuanToCents', () => {
  it('parses decimals exactly instead of going through binary floats', () => {
    expect(parseYuanToCents('0.1')).toBe(10n);
    expect(parseYuanToCents('0.2')).toBe(20n);
    expect(parseYuanToCents('0.3')).toBe(30n);
    expect(parseYuanToCents('1.005')).toBeNull();
    expect(parseYuanToCents('19.99')).toBe(1999n);
    expect(parseYuanToCents(' 1,234,567.89 ')).toBe(123456789n);
  });

  it('rejects malformed text, exponents, bad grouping and excessive precision', () => {
    for (const value of [
      '',
      '   ',
      '1e3',
      '1.234',
      'abc',
      '1,23.4',
      '12,34',
      '01',
      '.5',
      '1.',
      '-1',
    ]) {
      expect(parseYuanToCents(value), value).toBeNull();
    }
    expect(parseYuanToCents('999,999,999,999.99')).toBe(MAX_CENTS);
    expect(parseYuanToCents('1,000,000,000,000.00')).toBeNull();
  });
});

describe('formatCents', () => {
  it('formats cents, signs, zero and currency without floating point', () => {
    expect(formatCents(123456789n)).toBe('1,234,567.89');
    expect(formatCents(-123456789n)).toBe('-1,234,567.89');
    expect(formatCents(-123456789n, { negative: 'parentheses', currency: true })).toBe(
      '(\u00a51,234,567.89)',
    );
    expect(formatCents(0n)).toBe('0.00');
    expect(formatCents(0n, { zero: 'dash' })).toBe('\u2014');
    expect(formatCents(5n, { currency: true })).toBe('\u00a50.05');
  });

  it('formats and sums values beyond Number.MAX_SAFE_INTEGER', () => {
    const huge = 90_071_992_547_409_993n;
    expect(formatCents(huge)).toBe('900,719,925,474,099.93');
    expect(sumCents([huge, 7n])).toBe(90_071_992_547_410_000n);
  });
});

describe('sumCents', () => {
  it('matches a BigInt reference for 200 deterministic random amounts', () => {
    let seed = 20261117n;
    const values: bigint[] = [];
    let reference = 0n;
    for (let index = 0; index < 200; index += 1) {
      seed = (seed * 6364136223846793005n + 1442695040888963407n) & 0xffffffffffffffffn;
      const value = (seed % 99_999_999n) * 100n + (seed % 100n);
      values.push(value);
      reference += value;
    }
    expect(sumCents(values)).toBe(reference);
  });
});

describe('centsToApiString', () => {
  it('keeps two decimals and a sign without grouping', () => {
    expect(centsToApiString(1999n)).toBe('19.99');
    expect(centsToApiString(-5n)).toBe('-0.05');
  });
});

describe('balanceDirection', () => {
  it('maps signed balances to debit, credit and zero', () => {
    expect(balanceDirection(1n)).toBe('借');
    expect(balanceDirection(-1n)).toBe('贷');
    expect(balanceDirection(0n)).toBe('平');
  });
});

describe('money conversion guard', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  // `visual-pages.test.ts` parses CSS pixel declarations, not money.
  const allowed = new Set(['money.test.ts', 'visual-pages.test.ts']);

  function sources(directory: string): string[] {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return sources(path);
      return /\.tsx?$/.test(entry.name) ? [path] : [];
    });
  }

  it('does not add new float money parsing or Number * 100 rounding outside the whitelist', () => {
    const offenders: string[] = [];
    for (const file of sources(here)) {
      const name = relative(here, file).replace(/\\/g, '/');
      if (allowed.has(name) || name === 'money.ts') continue;
      const source = readFileSync(file, 'utf8');
      if (/parseFloat\s*\(/.test(source)) offenders.push(`${name}: parseFloat`);
      if (/Math\.round\(\s*Number\([^)]*\)\s*\*\s*100\s*\)/.test(source))
        offenders.push(`${name}: Number * 100`);
    }
    expect(offenders).toEqual([]);
  });
});
