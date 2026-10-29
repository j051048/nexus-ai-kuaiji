/** Display only frozen evidence. This module never calculates or approves tax. */
export type TaxAccountEvidence = {
  account_id: string;
  account_version: number;
  code: string;
  name: string;
  role: 'vat_payable' | 'excluded';
  label: string;
};
export type TaxInvoiceEvidence = {
  number: string;
  issued_on: string;
  counterparty: string;
  direction: 'input' | 'output';
  amount: string;
  tax: string;
  total: string;
  batch_id: string;
  original_sha256: string;
  row_fingerprint: string;
  row_index: number;
};
export type TaxEvidence = { accounts: TaxAccountEvidence[]; invoices: TaxInvoiceEvidence[] };

function invalid(): never {
  throw new Error('本版税务证据的身份、科目用途或金额不完整，未展示或接纳；请重新读取。');
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value) return invalid();
  return value;
}
function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : invalid();
}
function integer(value: unknown, min: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min) return invalid();
  return value;
}
function hash(value: unknown): string {
  const digest = text(value);
  return /^[a-f0-9]{64}$/.test(digest) ? digest : invalid();
}
function identity(value: Record<string, unknown>): string {
  return JSON.stringify([text(value.account_id), integer(value.account_version, 1)]);
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const record = object(value);
    return `{${Object.keys(record)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(record[k])}`)
      .join(',')}}`;
  }
  if (value === undefined || typeof value === 'bigint') return invalid();
  return JSON.stringify(value);
}
/** Exact integer-cent formatting: no binary floating point division or rounding. */
export function taxCents(value: unknown): string {
  const cents = BigInt(integer(value, 0));
  return `${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`;
}

export function taxEvidence(raw: unknown): TaxEvidence {
  const paper = object(raw),
    source = object(paper.source_snapshot),
    scope = object(source.scope);
  if (source.schema !== 'n02-tax-v2' || source.period !== paper.period) return invalid();
  for (const key of ['firm_id', 'company_id', 'book_id', 'period']) {
    if (text(scope[key]) !== text(paper[key])) return invalid();
  }
  const ledgerId = text(scope.ledger_id);
  const ledger = object(source.ledger),
    vouchers = new Set<string>();
  for (const rawVoucher of array(ledger.vouchers)) {
    const voucher = object(rawVoucher),
      id = text(voucher.id);
    if (vouchers.has(id) || voucher.status !== 'posted' || voucher.ledger_id !== ledgerId)
      return invalid();
    for (const key of ['firm_id', 'company_id', 'book_id', 'period']) {
      if (voucher[key] !== scope[key]) return invalid();
    }
    vouchers.add(id);
  }
  const labels = new Map<string, { code: string; name: string }>(),
    lineIds = new Set<string>();
  for (const rawLine of array(ledger.lines)) {
    const line = object(rawLine),
      key = identity(line),
      id = text(line.id);
    if (lineIds.has(id) || !vouchers.has(text(line.voucher_id))) return invalid();
    lineIds.add(id);
    integer(line.line_no, 1);
    integer(line.debit_cents, 0);
    integer(line.credit_cents, 0);
    const label = { code: text(line.code_snapshot), name: text(line.name_snapshot) };
    const prior = labels.get(key);
    if (prior && (prior.code !== label.code || prior.name !== label.name)) return invalid();
    labels.set(key, label);
  }
  const mapped = new Set<string>();
  const accounts = array(source.vat_accounts).map((rawMapping): TaxAccountEvidence => {
    const mapping = object(rawMapping),
      key = identity(mapping),
      definition = object(mapping.definition);
    if (mapped.has(key) || !['vat_payable', 'excluded'].includes(text(mapping.role)))
      return invalid();
    if (
      definition.account_id !== mapping.account_id ||
      definition.version !== mapping.account_version ||
      definition.ledger_id !== ledgerId
    )
      return invalid();
    for (const field of ['firm_id', 'company_id'])
      if (definition[field] !== scope[field]) return invalid();
    const frozen = { code: text(definition.code), name: text(definition.name) },
      posted = labels.get(key);
    if (posted && (posted.code !== frozen.code || posted.name !== frozen.name)) return invalid();
    mapped.add(key);
    return {
      account_id: text(mapping.account_id),
      account_version: integer(mapping.account_version, 1),
      ...(posted ?? frozen),
      role: mapping.role as TaxAccountEvidence['role'],
      label: mapping.role === 'vat_payable' ? '计入增值税账面对照金额' : '不计入增值税账面对照金额',
    };
  });
  for (const key of labels.keys()) if (!mapped.has(key)) return invalid();

  const rows = array(paper.lines);
  if (canonical(rows) !== canonical(array(source.invoices))) return invalid();
  const invoiceIds = new Set<string>();
  const invoices = rows.map((rawInvoice): TaxInvoiceEvidence => {
    const row = object(rawInvoice),
      original = object(row.original);
    const amount = integer(row.amount_cents, 0),
      tax = integer(row.tax_cents, 0),
      total = integer(row.total_cents, 1);
    if (BigInt(amount) + BigInt(tax) !== BigInt(total)) return invalid();
    for (const field of ['amount_cents', 'tax_cents', 'total_cents'])
      if (original[field] !== row[field]) return invalid();
    if (row.direction !== 'input' && row.direction !== 'output') return invalid();
    if (original.direction !== row.direction || original.date !== row.issued_on) return invalid();
    const issued = text(row.issued_on);
    const date = new Date(`${issued}T00:00:00Z`);
    if (
      !/^20\d{2}-\d{2}-\d{2}$/.test(issued) ||
      !Number.isFinite(date.getTime()) ||
      date.toISOString().slice(0, 10) !== issued ||
      issued.slice(0, 7) !== paper.period
    )
      return invalid();
    const batchId = text(row.batch_id),
      rowIndex = integer(row.row_index, 1);
    const key = JSON.stringify([batchId, rowIndex]);
    if (invoiceIds.has(key)) return invalid();
    invoiceIds.add(key);
    if (original.number !== undefined && typeof original.number !== 'string') return invalid();
    if (typeof row.counterparty !== 'string') return invalid();
    return {
      number: typeof original.number === 'string' && original.number ? original.number : '未提供',
      issued_on: issued,
      counterparty: row.counterparty || '未提供',
      direction: row.direction,
      amount: taxCents(amount),
      tax: taxCents(tax),
      total: taxCents(total),
      batch_id: batchId,
      row_index: rowIndex,
      original_sha256: hash(row.original_sha256),
      row_fingerprint: hash(row.row_fingerprint),
    };
  });
  return { accounts, invoices };
}
