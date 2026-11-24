import type { JournalLine } from './types';
import { centsToApiString, parseYuanToCents, sumCents } from './money';

export type JournalBalanceSummary = {
  debitCents: bigint;
  creditCents: bigint;
  diffCents: bigint;
  shortSide: 'debit' | 'credit' | null;
  shortAmountCents: bigint;
  balanced: boolean;
  hasInvalidAmount: boolean;
};

function lineAmountCents(value: string | number | null | undefined): bigint | null {
  if (value === null || value === undefined) return 0n;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return parseYuanToCents(value.toFixed(2));
  }
  const trimmed = value.trim();
  if (!trimmed) return 0n;
  return parseYuanToCents(trimmed);
}

/**
 * 纯整数分试算凭证全量借贷平衡状态；存在非法金额时绝不误判为已平衡。
 */
export function journalBalanceSummary(lines: JournalLine[]): JournalBalanceSummary {
  let hasInvalidAmount = false;
  const debits: bigint[] = [];
  const credits: bigint[] = [];
  for (const line of lines) {
    const d = lineAmountCents(line.debit);
    const c = lineAmountCents(line.credit);
    if (d === null || c === null) {
      hasInvalidAmount = true;
    } else {
      debits.push(d);
      credits.push(c);
    }
  }
  const debitCents = sumCents(debits);
  const creditCents = sumCents(credits);
  const diffCents = debitCents - creditCents;
  const shortSide = diffCents > 0n ? 'credit' : diffCents < 0n ? 'debit' : null;
  const shortAmountCents = diffCents >= 0n ? diffCents : -diffCents;
  return {
    debitCents,
    creditCents,
    diffCents,
    shortSide,
    shortAmountCents,
    balanced: !hasInvalidAmount && diffCents === 0n,
    hasInvalidAmount,
  };
}

/**
 * 中国会计经典 `=` 键配平行为：用除当前行以外全部行的借贷差额填平当前行，
 * 若其余行存在非法金额则返回 null，不覆盖用户原有数据。
 */
export function balanceLineWithEquals(
  lines: JournalLine[],
  targetIndex: number,
): JournalLine[] | null {
  if (targetIndex < 0 || targetIndex >= lines.length) return null;
  const others = lines.filter((_, index) => index !== targetIndex);
  const otherSummary = journalBalanceSummary(others);
  if (otherSummary.hasInvalidAmount) return null;
  const diff = otherSummary.diffCents;
  const nextDebit = diff < 0n ? centsToApiString(-diff) : '0.00';
  const nextCredit = diff > 0n ? centsToApiString(diff) : '0.00';
  return lines.map((line, index) =>
    index === targetIndex ? { ...line, debit: nextDebit, credit: nextCredit } : line,
  );
}

/**
 * 合计栏「一键配平」：优先寻找借贷均为零的末行进行配平，否则配平最后一行。
 */
export function smartBalanceLines(lines: JournalLine[]): JournalLine[] | null {
  if (!lines.length) return null;
  const summary = journalBalanceSummary(lines);
  if (summary.hasInvalidAmount || summary.balanced) return null;
  let targetIndex = -1;
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const d = lineAmountCents(lines[index].debit);
    const c = lineAmountCents(lines[index].credit);
    if (d === 0n && c === 0n) {
      targetIndex = index;
      break;
    }
  }
  if (targetIndex === -1) targetIndex = lines.length - 1;
  return balanceLineWithEquals(lines, targetIndex);
}
