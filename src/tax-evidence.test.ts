import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { taxCents, taxEvidence } from './tax-evidence';

const fixture = JSON.parse(
  readFileSync(
    new URL('../nexus_backend/tests/fixtures/n04_tax_contract.json', import.meta.url),
    'utf8',
  ),
);

const paper = () => structuredClone(fixture.reviewed);

describe('frozen tax evidence projection', () => {
  it('projects real frozen identities and exact yuan without trimming invoice zeros', () => {
    const result = taxEvidence(paper());
    expect(result.accounts).toEqual([
      expect.objectContaining({ code: '1002', name: '银行存款', role: 'excluded' }),
      expect.objectContaining({ code: '2202', name: '应付账款', role: 'vat_payable' }),
    ]);
    expect(result.invoices[0]).toEqual(
      expect.objectContaining({
        number: '000000000001',
        issued_on: '2026-10-05',
        direction: 'output',
        amount: '1000.00',
        tax: '30.00',
        total: '1030.00',
        batch_id: 'batch-1',
        row_index: 1,
        original_sha256: 'a'.repeat(64),
      }),
    );
    expect(result.accounts[1].label).toBe('计入增值税账面对照金额');
  });

  it.each([
    [0, '0.00'],
    [1, '0.01'],
    [999999, '9999.99'],
    [Number.MAX_SAFE_INTEGER, '90071992547409.91'],
  ])('formats integer cents %s without floating point arithmetic', (value, expected) => {
    expect(taxCents(value)).toBe(expected);
  });
  it.each([true, false, '100', -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, null])(
    'refuses malformed cents %s',
    (value) => expect(() => taxCents(value)).toThrow(),
  );

  it('does not collapse separate account identities sharing a code', () => {
    const p = paper();
    p.source_snapshot.vat_accounts[1].definition.code = '1002';
    p.source_snapshot.ledger.lines[1].code_snapshot = '1002';
    const result = taxEvidence(p);
    expect(result.accounts).toHaveLength(2);
    expect(result.accounts[0].account_id).not.toBe(result.accounts[1].account_id);
  });

  it.each([
    'missing mapping',
    'wrong version',
    'foreign definition',
    'mixed frozen name',
    'unknown role',
    'duplicate mapping',
    'foreign voucher',
    'orphan line',
    'wrong schema',
    'missing evidence',
    'bad invoice hash',
    'bad fingerprint',
    'invoice divergence',
    'fraction cents',
    'bool cents',
    'wrong original amount',
    'invalid date',
    'numeric invoice number',
    'duplicate invoice identity',
  ])('rejects %s before projecting any evidence', (fault) => {
    const p = paper();
    const source = p.source_snapshot;
    const invoice = p.lines[0];
    if (fault === 'missing mapping') source.vat_accounts.pop();
    if (fault === 'wrong version') source.vat_accounts[1].account_version = 2;
    if (fault === 'foreign definition') source.vat_accounts[1].definition.company_id = 'other';
    if (fault === 'mixed frozen name')
      source.ledger.lines.push({
        ...source.ledger.lines[0],
        id: 'another-line',
        name_snapshot: 'different',
      });
    if (fault === 'unknown role') Object.assign(source.vat_accounts[0], { role: 'guessed' });
    if (fault === 'duplicate mapping') source.vat_accounts.push(source.vat_accounts[0]);
    if (fault === 'foreign voucher') source.ledger.vouchers[0].company_id = 'other';
    if (fault === 'orphan line') source.ledger.lines[0].voucher_id = 'other-voucher';
    if (fault === 'wrong schema') source.schema = 'other';
    if (fault === 'missing evidence') Object.assign(source, { ledger: null });
    if (fault === 'bad invoice hash') invoice.original_sha256 = 'no-hash';
    if (fault === 'bad fingerprint') invoice.row_fingerprint = 'no-hash';
    if (fault === 'invoice divergence') invoice.counterparty = 'different';
    if (fault === 'fraction cents') invoice.amount_cents = 1.5;
    if (fault === 'bool cents') Object.assign(invoice, { amount_cents: true });
    if (fault === 'wrong original amount') invoice.original.amount_cents += 1;
    if (fault === 'invalid date') invoice.issued_on = '2026-02-30';
    if (fault === 'numeric invoice number') Object.assign(invoice.original, { number: 1 });
    if (fault === 'duplicate invoice identity') p.lines.push(invoice);
    // Except for deliberate divergence, mutate both copies to prove leaf validation.
    if (fault !== 'invoice divergence') source.invoices = structuredClone(p.lines);
    expect(() => taxEvidence(p)).toThrow(/证据/);
  });

  it('keeps an explicit unused mapping rather than inventing a new backend prohibition', () => {
    const p = paper();
    const extra = structuredClone(p.source_snapshot.vat_accounts[0]);
    extra.account_id = 'unused';
    extra.definition.account_id = 'unused';
    p.source_snapshot.vat_accounts.push(extra);
    expect(taxEvidence(p).accounts).toHaveLength(3);
  });
  it('shows an absent invoice number honestly without guessing one', () => {
    const p = paper();
    Reflect.deleteProperty(p.lines[0].original, 'number');
    p.source_snapshot.invoices = structuredClone(p.lines);
    expect(taxEvidence(p).invoices[0].number).toBe('未提供');
  });
});
