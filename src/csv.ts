import type { SourceRow } from './types';

export const MAX_CSV_BYTES = 2 * 1024 * 1024;
export const MAX_CSV_ROWS = 1000;
export const CSV_HEADERS = ['external_id', 'date', 'description', 'counterparty', 'amount', 'direction', 'evidence_ref'] as const;
export const EXAMPLE_CSV = `${CSV_HEADERS.join(',')}\nDEMO-001,2026-10-08,办公用品采购,示例办公用品商店,268.00,expense,发票-DEMO-001\nDEMO-002,2026-10-09,"设计服务费,第一期",示例设计客户,3500.00,income,合同-DEMO-002\n`;

/** Strict CSV reader: quoted commas/newlines and escaped double quotes are preserved. */
export function parseCsv(text: string): string[][] {
  if (new TextEncoder().encode(text).length > MAX_CSV_BYTES) throw new Error('CSV 文件不能超过 2 MB。');
  const input = text.replace(/^\uFEFF/, '');
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  let afterQuote = false;
  const pushRow = () => {
    row.push(field);
    if (row.some(cell => cell.trim() !== '')) rows.push(row);
    if (rows.length > MAX_CSV_ROWS + 1) throw new Error(`单次最多导入 ${MAX_CSV_ROWS} 行流水，请拆分文件。`);
    row = [];
    field = '';
    afterQuote = false;
  };
  for (let index = 0; index < input.length; index += 1) {
    const char = input[index];
    if (quoted) {
      if (char === '"' && input[index + 1] === '"') { field += '"'; index += 1; }
      else if (char === '"') { quoted = false; afterQuote = true; }
      else field += char;
    } else if (char === ',') {
      row.push(field); field = ''; afterQuote = false;
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && input[index + 1] === '\n') index += 1;
      pushRow();
    } else if (char === '"' && field === '' && !afterQuote) {
      quoted = true;
    } else if (char === '"' || afterQuote) {
      throw new Error(`CSV 第 ${rows.length + 1} 行的引号格式不正确。含逗号的内容须用双引号包裹。`);
    } else {
      field += char;
    }
  }
  if (quoted) throw new Error('CSV 有未闭合的双引号，请检查文件末尾。');
  if (field !== '' || row.length || afterQuote) pushRow();
  return rows;
}

export function toSourceRows(text: string): SourceRow[] {
  const [header, ...rows] = parseCsv(text);
  if (!header) throw new Error('请上传 CSV 或粘贴流水内容。');
  const names = header.map(value => value.trim());
  if (new Set(names).size !== names.length) throw new Error('CSV 表头包含重复字段。');
  const missing = CSV_HEADERS.filter(name => !names.includes(name));
  if (missing.length) throw new Error(`CSV 缺少字段：${missing.join('、')}。请先下载示例模板。`);
  if (!rows.length) throw new Error('CSV 只有表头，请至少添加一行流水。');
  const seen = new Set<string>();
  return rows.map((row, index) => {
    const prefix = `第 ${index + 2} 行`;
    if (row.length !== names.length) throw new Error(`${prefix}列数与表头不一致。含逗号的字段须用双引号包裹。`);
    const value = Object.fromEntries(CSV_HEADERS.map(name => [name, row[names.indexOf(name)].trim()])) as unknown as SourceRow;
    if (names.includes('stable_transaction_id')) {
      value.stable_transaction_id = row[names.indexOf('stable_transaction_id')].trim();
      if (value.stable_transaction_id.length > 200) throw new Error(`${prefix}稳定交易编号超过 200 字。`);
    }
    if (!value.external_id || !value.description || !value.date || !value.amount || !value.direction) {
      throw new Error(`${prefix}缺少流水编号、日期、摘要、金额或收支方向。`);
    }
    if (value.external_id.length > 200 || value.description.length > 500 || value.counterparty.length > 200 || value.evidence_ref.length > 500) {
      throw new Error(`${prefix}文本字段过长，请精简后重试。`);
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value.date) || Number.isNaN(Date.parse(value.date)) || new Date(value.date).toISOString().slice(0, 10) !== value.date) {
      throw new Error(`${prefix}日期不正确，请使用 YYYY-MM-DD 格式。`);
    }
    if (!/^(?:0|[1-9][0-9]{0,11})(?:\.\d{1,2})?$/.test(value.amount) || moneyToCents(value.amount) <= 0n) {
      throw new Error(`${prefix}金额必须是大于零、最多两位小数的数字，请勿填写千分位或货币符号。`);
    }
    if (value.direction !== 'expense' && value.direction !== 'income') throw new Error(`${prefix}收支方向只能填写 expense（支出）或 income（收入）。`);
    if (seen.has(value.external_id)) throw new Error(`${prefix}流水编号在当前文件中重复：${value.external_id}。`);
    seen.add(value.external_id);
    return value;
  });
}

export function moneyToCents(value: string): bigint {
  if (!/^(?:0|[1-9][0-9]{0,11})(?:\.\d{1,2})?$/.test(value)) throw new Error('金额须为非负数，最多两位小数，不能包含多余前导零。');
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
}

export function formatCents(value: bigint): string {
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  return `${negative ? '-' : ''}${(absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

export function formatMoney(value: string): string {
  try { return formatCents(moneyToCents(value)); } catch { return value; }
}

export function downloadCsv(text: string, filename: string) {
  const blob = new Blob([text.startsWith('\uFEFF') ? text : `\uFEFF${text}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.replace(/[<>:"/\\|?*]/g, '_');
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
