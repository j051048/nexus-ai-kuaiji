import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  parseTaxContext,
  parseTaxWorkpaper,
  parseTaxList,
  taxAction,
  taxRateBp,
  taxFailureBlock,
  taxBlocked,
  taxBlockAfterRead,
} from './tax-workpapers';

// Shared contract is read at test runtime, not imported into the production
// TypeScript graph: the Vercel upload intentionally excludes the backend tree.
const fixture = JSON.parse(
  readFileSync(
    new URL('../nexus_backend/tests/fixtures/n04_tax_contract.json', import.meta.url),
    'utf8',
  ),
);

const scope = { firm_id: 'f', company_id: 'c', book_id: 'b', period: '2026-10' };

it('accepts the shared contract captured from the real isolated HTTP workflow', () => {
  const expected = fixture.context.scope;
  const context = parseTaxContext(fixture.context, expected);
  const draft = parseTaxWorkpaper(fixture.draft, expected);
  const reviewed = parseTaxWorkpaper(fixture.reviewed, expected);
  const papers = parseTaxList(fixture.list_response, expected);
  expect(context.permissions.manage).toBe(false);
  expect(draft.status).toBe('draft');
  expect(reviewed.status).toBe('reviewed');
  expect(reviewed.source_digest).toBe(draft.source_digest);
  expect(papers).toHaveLength(1);
  expect(papers[0].id).toBe(reviewed.id);
  expect(taxAction(reviewed, context, reviewed.reviewed_by!, reviewed.version).export).toBe(true);
});
it('refuses a workpaper whose frozen voucher belongs to another company before showing amounts', () => {
  const forged = structuredClone(fixture.draft);
  forged.source_snapshot.ledger.vouchers[0].company_id = 'foreign-company';
  expect(() => parseTaxWorkpaper(forged, fixture.context.scope)).toThrow();
});
const profile = {
  id: 'p',
  effective_from: '2026-10',
  taxpayer_type: 'small_scale',
  filing_cycle: 'monthly',
  vat_rate_bp: 100,
  surtax_rate_bp: 600,
  reason: '虚构测试口径',
};
const context = {
  scope: { ...scope, book_name: '虚构企业' },
  permissions: { edit: true, review: true, export: true, manage: true },
  profile,
  accounts: [{ account_id: 'a', account_version: 1, code: '2221', name: '测试科目' }],
  blocking_reasons: [],
};
const paper = {
  ...scope,
  id: 'w',
  version: 1,
  status: 'draft',
  profile_id: 'p',
  created_by: 'maker',
  created_at: '2026-10-29',
  reviewed_by: null,
  reviewed_at: null,
  review_reason: '',
  variance_reason: '',
  output_net: '100.00',
  output_tax: '1.00',
  input_net: '0.00',
  input_tax: '0.00',
  tax: '1.00',
  surtax: '0.06',
  credit_carry: '0.00',
  ledger: '0.00',
  variance: '1.00',
  source_digest: 'a'.repeat(64),
  filing: 'not_filed',
  source_snapshot: {
    schema: 'n02-tax-v2',
    period: scope.period,
    scope: { ...scope, ledger_id: 'ledger-b' },
    profile,
    vat_accounts: [],
    invoices: [],
    ledger: { vouchers: [], lines: [] },
  },
  lines: [],
};

describe('N04 tax contracts', () => {
  it('accepts exact scope and preserves decimal strings', () => {
    expect(parseTaxContext(context, { firm_id: 'f', book_id: 'b' }).scope.company_id).toBe('c');
    expect(parseTaxWorkpaper(paper, scope).tax).toBe('1.00');
  });
  it.each(['firm_id', 'company_id', 'book_id', 'period'])(
    'rejects wrong %s before accepting any paper',
    (key) => {
      expect(() => parseTaxWorkpaper({ ...paper, [key]: 'other' }, scope)).toThrow();
      expect(() =>
        parseTaxWorkpaper(
          {
            ...paper,
            source_snapshot: { ...paper.source_snapshot, scope: { ...scope, [key]: 'other' } },
          },
          scope,
        ),
      ).toThrow();
    },
  );
  it.each([true, -1, 1.1, '1', null])('rejects invalid version %s', (version) => {
    expect(() => parseTaxWorkpaper({ ...paper, version }, scope)).toThrow();
  });
  it('rejects forged status, money, digest, source profile and self review', () => {
    for (const patch of [
      { filing: 'filed' },
      { tax: 1 },
      { tax: 'NaN' },
      { source_digest: '' },
      { profile_id: 'other' },
      { status: 'reviewed', reviewed_by: 'maker', reviewed_at: 'today' },
    ])
      expect(() => parseTaxWorkpaper({ ...paper, ...patch }, scope)).toThrow();
  });
  it('validates a whole list atomically and rejects duplicate IDs and versions', () => {
    expect(parseTaxList({ workpapers: [paper] }, scope)).toHaveLength(1);
    expect(() =>
      parseTaxList({ workpapers: [paper, { ...paper, company_id: 'other' }] }, scope),
    ).toThrow();
    expect(() => parseTaxList({ workpapers: [paper, paper] }, scope)).toThrow();
    expect(() =>
      parseTaxList({ workpapers: [paper, { ...paper, id: 'different' }] }, scope),
    ).toThrow();
  });
  it('refuses guessed permissions and ambiguous account identities', () => {
    expect(() =>
      parseTaxContext(
        { ...context, permissions: { ...context.permissions, review: 'true' } },
        scope,
      ),
    ).toThrow();
    expect(() =>
      parseTaxContext({ ...context, accounts: [...context.accounts, ...context.accounts] }, scope),
    ).toThrow();
    expect(() =>
      parseTaxContext({ ...context, scope: { ...scope, book_id: 'else' } }, scope),
    ).toThrow();
  });
  it('never permits self review, stale export, unreviewed export or missing permission', () => {
    const parsed = parseTaxWorkpaper(paper, scope),
      ctx = parseTaxContext(context, scope);
    expect(taxAction(parsed, ctx, 'maker', 1).review).toBe(false);
    expect(taxAction(parsed, ctx, 'reviewer', 1).review).toBe(true);
    expect(taxAction(parsed, ctx, 'reviewer', 1).export).toBe(false);
    const reviewed = parseTaxWorkpaper(
      { ...paper, status: 'reviewed', reviewed_by: 'reviewer', reviewed_at: 'today' },
      scope,
    );
    expect(taxAction(reviewed, ctx, 'reviewer', 1).export).toBe(true);
    expect(taxAction(reviewed, ctx, 'reviewer', 2).export).toBe(false);
    expect(taxAction(reviewed, ctx, 'reviewer', 1, true).export).toBe(false);
    expect(
      taxAction(
        parsed,
        { ...ctx, permissions: { ...ctx.permissions, review: false } },
        'reviewer',
        1,
      ).review,
    ).toBe(false);
  });
  it('uses exact percent conversion; empty/ambiguous input is not zero', () => {
    expect(taxRateBp('1.25')).toBe(125);
    expect(taxRateBp('0')).toBe(0);
    expect(taxRateBp('100')).toBe(10000);
    for (const bad of ['', ' ', '1e2', '-1', '100.01', '1.001'])
      expect(() => taxRateBp(bad)).toThrow();
  });
  it('transient failures require a fresh read, not a replacement workpaper', () => {
    const parsed = parseTaxWorkpaper(paper, scope);
    const block = taxFailureBlock(503, parsed);
    expect(taxBlocked(block, parsed)).toBe(true);
    expect(taxBlocked(taxBlockAfterRead(block), parsed)).toBe(false);
  });
  it('binds actions to the effective profile, not merely a previous save attempt', () => {
    const parsed = parseTaxWorkpaper(paper, scope),
      ctx = parseTaxContext(context, scope);
    expect(
      taxAction(
        parsed,
        { ...ctx, profile: { ...ctx.profile!, id: 'new-current-profile' } },
        'reviewer',
        1,
      ).review,
    ).toBe(false);
    expect(taxAction(parsed, { ...ctx, profile: null }, 'reviewer', 1).review).toBe(false);
    expect(taxAction(parsed, ctx, 'reviewer', 1).review).toBe(true);
  });
  it('a source conflict blocks only that exact frozen version even after reading', () => {
    const parsed = parseTaxWorkpaper(paper, scope);
    const block = taxFailureBlock(409, parsed);
    expect(taxBlocked(taxBlockAfterRead(block), parsed)).toBe(true);
    expect(taxBlocked(block, { ...parsed, id: 'w2', version: 2 })).toBe(false);
    expect(taxBlocked(block, { ...parsed, source_digest: 'b'.repeat(64) })).toBe(false);
    expect(taxBlocked(taxBlockAfterRead(taxFailureBlock(422)), parsed)).toBe(false);
  });
});
