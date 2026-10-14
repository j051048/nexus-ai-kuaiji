import type { JournalLine } from './types';
import { parseCsv } from './csv';

export function validMonthlyPeriod(value: string): boolean {
  return /^20\d{2}-(0[1-9]|1[0-2])$/.test(value);
}
export function previousMonthlyPeriod(value: string): string {
  if (!validMonthlyPeriod(value)) return '';
  const [year, month] = value.split('-').map(Number);
  return month === 1 ? `${year - 1}-12` : `${year}-${String(month - 1).padStart(2, '0')}`;
}

export function monthlyImportSummary(
  content: string,
  format: 'json' | 'csv',
  expectedPeriod: string,
): { rows: number; period: string } {
  let rows: Record<string, unknown>[];
  if (format === 'json') {
    let value;
    try {
      value = JSON.parse(content.replace(/^\uFEFF/, ''));
    } catch {
      throw new Error('JSON 余额文件格式错误，请按模板检查括号和引号');
    }
    if (
      value?.period !== expectedPeriod ||
      value?.currency !== 'CNY' ||
      value?.scope !== 'posted' ||
      !Array.isArray(value?.lines)
    )
      throw new Error('文件需声明正确月份、人民币和已过账余额范围');
    rows = value.lines;
  } else {
    const [header, ...entries] = parseCsv(content);
    const required = ['account_code', 'balance', 'auxiliary_values', 'period', 'currency', 'scope'];
    if (
      !header ||
      required.some((name) => !header.includes(name)) ||
      new Set(header).size !== header.length
    )
      throw new Error('CSV 需使用余额模板的完整表头');
    rows = entries.map((cells) => {
      if (cells.length !== header.length) throw new Error('CSV 列数与表头不一致');
      const row = Object.fromEntries(header.map((name, index) => [name, cells[index]]));
      if (row.period !== expectedPeriod || row.currency !== 'CNY' || row.scope !== 'posted')
        throw new Error('每行需声明正确月份、人民币和已过账余额范围');
      return row;
    });
  }
  if (!rows.length || rows.length > 1000) throw new Error('一次余额文件需包含 1–1000 项');
  if (
    rows.some(
      (row) =>
        !row ||
        typeof row.account_code !== 'string' ||
        !row.account_code.trim() ||
        typeof row.balance !== 'string' ||
        signedMonthlyCents(row.balance) === null,
    )
  )
    throw new Error('每项应提供真实科目与最多两位小数的文本金额');
  return { rows: rows.length, period: expectedPeriod };
}

export function monthlyMoney(value: string | null | undefined): string {
  if (typeof value !== 'string' || !/^-?(0|[1-9]\d*)(\.\d{1,2})?$/.test(value)) return '待核实';
  const [whole, fraction = ''] = value.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}

export function signedMonthlyCents(value: string): bigint | null {
  if (typeof value !== 'string' || !/^-?(0|[1-9]\d*)(\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  const amount = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  return value.startsWith('-') ? -amount : amount;
}

export function recurringLinesIssue(lines: JournalLine[]): string {
  if (lines.length < 2) return '至少填写两行借贷分录';
  let debit = 0n,
    credit = 0n;
  for (const line of lines) {
    if (!line.account_code) return '请为每行选择真实会计科目';
    const d = signedMonthlyCents(line.debit),
      c = signedMonthlyCents(line.credit);
    if (d === null || c === null || d < 0n || c < 0n) return '借贷金额应为非负、最多两位小数';
    if (d > 0n === c > 0n) return '每行只能有一方正金额';
    debit += d;
    credit += c;
  }
  return debit !== credit ? '借贷合计不平，请核对明确的金额' : '';
}
