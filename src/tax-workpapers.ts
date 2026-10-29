import type { BookPermissions } from './types';
import { taxEvidence } from './tax-evidence';

export type TaxScope = { firm_id: string; company_id: string; book_id: string; period: string };
export type TaxProfile = {
  id: string;
  effective_from: string;
  taxpayer_type: 'small_scale' | 'general';
  filing_cycle: 'monthly' | 'quarterly';
  vat_rate_bp: number;
  surtax_rate_bp: number;
  reason: string;
};
export type TaxAccount = {
  account_id: string;
  account_version: number;
  code: string;
  name: string;
};
export type TaxContext = {
  scope: TaxScope & { book_name: string };
  permissions: BookPermissions;
  profile: TaxProfile | null;
  accounts: TaxAccount[];
  blocking_reasons: string[];
};
export type TaxWorkpaper = TaxScope & {
  id: string;
  version: number;
  status: 'draft' | 'reviewed';
  profile_id: string;
  created_by: string;
  created_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_reason: string;
  variance_reason: string;
  source_digest: string;
  filing: 'not_filed';
  output_net: string;
  output_tax: string;
  input_net: string;
  input_tax: string;
  tax: string;
  surtax: string;
  credit_carry: string;
  ledger: string;
  variance: string;
  source_snapshot: Record<string, unknown>;
  lines: Record<string, unknown>[];
};
function invalid(): never {
  throw new Error('税务响应的范围、版本或字段不完整，请重新读取；未接纳这次数据。');
}
function record(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return invalid();
  return raw as Record<string, unknown>;
}
function text(raw: unknown): string {
  return typeof raw === 'string' && raw.length > 0 ? raw : invalid();
}
function integer(raw: unknown, min = 1): number {
  return typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= min ? raw : invalid();
}
function optionalText(raw: unknown): string {
  return typeof raw === 'string' ? raw : invalid();
}
function scoped(raw: unknown, expected: Partial<TaxScope>): TaxScope {
  const item = record(raw);
  const scope = {
    firm_id: text(item.firm_id),
    company_id: text(item.company_id),
    book_id: text(item.book_id),
    period: text(item.period),
  };
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(scope.period)) return invalid();
  for (const key of ['firm_id', 'company_id', 'book_id', 'period'] as const)
    if (expected[key] !== undefined && scope[key] !== expected[key]) return invalid();
  return scope;
}
function profile(raw: unknown): TaxProfile {
  const p = record(raw);
  if (
    !['small_scale', 'general'].includes(text(p.taxpayer_type)) ||
    !['monthly', 'quarterly'].includes(text(p.filing_cycle))
  )
    return invalid();
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(text(p.effective_from))) return invalid();
  const vat = integer(p.vat_rate_bp, 0),
    surtax = integer(p.surtax_rate_bp, 0);
  if (vat > 10000 || surtax > 10000) return invalid();
  return {
    id: text(p.id),
    effective_from: text(p.effective_from),
    taxpayer_type: p.taxpayer_type as TaxProfile['taxpayer_type'],
    filing_cycle: p.filing_cycle as TaxProfile['filing_cycle'],
    vat_rate_bp: vat,
    surtax_rate_bp: surtax,
    reason: text(p.reason),
  };
}
export function parseTaxContext(raw: unknown, expected: Partial<TaxScope>): TaxContext {
  const value = record(raw),
    scope = scoped(value.scope, expected),
    rights = record(value.permissions);
  for (const key of ['edit', 'review', 'export', 'manage'])
    if (typeof rights[key] !== 'boolean') return invalid();
  if (!Array.isArray(value.accounts) || !Array.isArray(value.blocking_reasons)) return invalid();
  const seen = new Set<string>();
  const accounts = value.accounts.map((raw) => {
    const a = record(raw),
      account_id = text(a.account_id),
      account_version = integer(a.account_version);
    const key = JSON.stringify([account_id, account_version]);
    if (seen.has(key)) return invalid();
    seen.add(key);
    return { account_id, account_version, code: text(a.code), name: text(a.name) };
  });
  return {
    scope: { ...scope, book_name: text(record(value.scope).book_name) },
    permissions: rights as unknown as BookPermissions,
    profile: value.profile === null ? null : profile(value.profile),
    accounts,
    blocking_reasons: value.blocking_reasons.map(text),
  };
}
export function parseTaxWorkpaper(raw: unknown, scope: TaxScope): TaxWorkpaper {
  const p = record(raw);
  scoped(p, scope);
  if (p.status !== 'draft' && p.status !== 'reviewed') return invalid();
  if (p.filing !== 'not_filed' || !/^[a-f0-9]{64}$/.test(text(p.source_digest))) return invalid();
  const source = record(p.source_snapshot);
  scoped(source.scope, scope);
  if (record(source.profile).id !== p.profile_id) return invalid();
  for (const key of ['id', 'profile_id', 'created_by', 'created_at']) text(p[key]);
  for (const key of ['review_reason', 'variance_reason']) optionalText(p[key]);
  integer(p.version);
  for (const key of [
    'output_net',
    'output_tax',
    'input_net',
    'input_tax',
    'tax',
    'surtax',
    'credit_carry',
    'ledger',
    'variance',
  ]) {
    const pattern = key === 'ledger' || key === 'variance' ? /^-?\d+\.\d{2}$/ : /^\d+\.\d{2}$/;
    if (typeof p[key] !== 'string' || !pattern.test(p[key] as string)) return invalid();
  }
  if (!Array.isArray(p.lines)) return invalid();
  p.lines.forEach(record);
  if (p.status === 'reviewed') {
    text(p.reviewed_by);
    text(p.reviewed_at);
    if (p.created_by === p.reviewed_by) return invalid();
  } else if (p.reviewed_by !== null || p.reviewed_at !== null) return invalid();
  taxEvidence(p);
  return p as unknown as TaxWorkpaper;
}
export function parseTaxList(raw: unknown, scope: TaxScope): TaxWorkpaper[] {
  const list = record(raw).workpapers;
  if (!Array.isArray(list)) return invalid();
  const ids = new Set<string>(),
    versions = new Set<number>();
  const result = list.map((raw) => {
    const p = parseTaxWorkpaper(raw, scope);
    if (ids.has(p.id) || versions.has(p.version)) return invalid();
    ids.add(p.id);
    versions.add(p.version);
    return p;
  });
  return result.sort((a, b) => b.version - a.version);
}
export function taxAction(
  p: TaxWorkpaper,
  context: TaxContext,
  user: string,
  latest: number,
  blocked = false,
) {
  const current = p.version === latest && !blocked && p.profile_id === context.profile?.id;
  return {
    review: current && p.status === 'draft' && context.permissions.review && p.created_by !== user,
    export: current && p.status === 'reviewed' && context.permissions.export,
  };
}
export type TaxBlock = { kind: 'reload' | 'source'; target: string | null } | null;
function paperKey(p: TaxWorkpaper) {
  return JSON.stringify([
    p.firm_id,
    p.company_id,
    p.book_id,
    p.period,
    p.id,
    p.version,
    p.source_digest,
  ]);
}
export function taxFailureBlock(status?: number, target?: TaxWorkpaper): TaxBlock {
  return status === 409 && target
    ? { kind: 'source', target: paperKey(target) }
    : { kind: 'reload', target: null };
}
export function taxBlockAfterRead(block: TaxBlock): TaxBlock {
  return block?.kind === 'source' ? block : null;
}
export function taxBlocked(block: TaxBlock, p: TaxWorkpaper): boolean {
  return !!block && (block.kind === 'reload' || block.target === paperKey(p));
}
export function taxRateBp(raw: string): number {
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(raw))
    throw new Error('税率请填写 0–100 的百分数，最多两位小数。');
  const [whole, fraction = ''] = raw.split('.');
  const bp = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (bp > 10000) throw new Error('税率不能超过 100%。');
  return bp;
}
export function taxMoney(raw: string): string {
  const [whole, fraction] = raw.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction}`;
}
