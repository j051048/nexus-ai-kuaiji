/**
 * Canonical money arithmetic. Amounts are integer cents held as bigint.
 * Parsing mirrors `nexus_backend/app/accounting/invariants.py::money_to_cents`
 * while additionally allowing thousand separators and surrounding whitespace.
 */

export const MAX_CENTS = 99_999_999_999_999n;

const PLAIN = /^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$/;
const GROUPED = /^[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?$/;

export interface FormatCentsOptions {
  currency?: boolean;
  zero?: 'zero' | 'dash';
  negative?: 'minus' | 'parentheses';
}

export function parseYuanToCents(text: string): bigint | null {
  const value = text.trim();
  if (!value) return null;
  const plain = PLAIN.test(value) ? value : GROUPED.test(value) ? value.replace(/,/g, '') : null;
  if (plain === null) return null;
  const [whole, fraction = ''] = plain.split('.');
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  return cents <= MAX_CENTS ? cents : null;
}

export function formatCents(cents: bigint, options: FormatCentsOptions = {}): string {
  if (options.zero === 'dash' && cents === 0n) return '\u2014';
  const negative = cents < 0n;
  const absolute = negative ? -cents : cents;
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const body = `${whole}.${(absolute % 100n).toString().padStart(2, '0')}`;
  const amount = options.currency ? `\u00a5${body}` : body;
  if (!negative) return amount;
  return options.negative === 'parentheses' ? `(${amount})` : `-${amount}`;
}

export function sumCents(values: Iterable<bigint>): bigint {
  let total = 0n;
  for (const value of values) total += value;
  return total;
}

export function balanceDirection(cents: bigint): '借' | '贷' | '平' {
  return cents > 0n ? '借' : cents < 0n ? '贷' : '平';
}

export function centsToApiString(cents: bigint): string {
  const negative = cents < 0n;
  const absolute = negative ? -cents : cents;
  const text = `${absolute / 100n}.${(absolute % 100n).toString().padStart(2, '0')}`;
  return negative ? `-${text}` : text;
}
