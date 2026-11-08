import type { FormalExpectedScope } from './formal-books-types';

export const agingKinds = {
  receivable: '应收账款',
  payable: '应付账款',
  advance_received: '预收账款',
  advance_paid: '预付账款',
};
export const agingBuckets = {
  current: '未逾期',
  '1_30': '逾期1–30天',
  '31_60': '逾期31–60天',
  '61_90': '逾期61–90天',
  over_90: '逾期90天以上',
};
type Kind = keyof typeof agingKinds;
type Bucket = keyof typeof agingBuckets;
type Buckets = Record<Bucket, number>;
const invalid = () => new Error('数据或范围不一致，请重新读取；不会使用旧数据继续办理。');
const object = (v: unknown): Record<string, unknown> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid();
  return v as Record<string, unknown>;
};
const list = (v: unknown): unknown[] => {
  if (!Array.isArray(v)) throw invalid();
  return v;
};
const text = (v: unknown): string => {
  if (typeof v !== 'string' || !v.trim()) throw invalid();
  return v;
};
const integer = (v: unknown, min = 0): number => {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min) throw invalid();
  return v;
};
const flag = (v: unknown): boolean => {
  if (typeof v !== 'boolean') throw invalid();
  return v;
};
const oneOf = <T extends string>(v: unknown, values: readonly T[]): T => {
  if (!values.includes(v as T)) throw invalid();
  return v as T;
};
const unique = (ids: string[]) => {
  if (new Set(ids).size !== ids.length) throw invalid();
};
function scope(raw: unknown, e: FormalExpectedScope) {
  const v = object(raw);
  if (
    !e.firmId ||
    !e.companyId ||
    !e.period ||
    v.firm_id !== e.firmId ||
    v.company_id !== e.companyId ||
    v.book_id !== e.bookId ||
    v.period !== e.period
  )
    throw invalid();
}
function buckets(raw: unknown): Buckets {
  const v = object(raw);
  return Object.fromEntries(
    Object.keys(agingBuckets).map((key) => [key, integer(v[key])]),
  ) as Buckets;
}
export function parseAging(raw: unknown, e: FormalExpectedScope, cutoff: string) {
  const v = object(raw);
  scope(v.scope, e);
  if (v.as_of !== cutoff) throw invalid();
  const totals = buckets(v.buckets),
    kinds = object(v.by_kind);
  const by_kind = Object.fromEntries(
    Object.keys(agingKinds).map((k) => [k, buckets(kinds[k])]),
  ) as Record<Kind, Buckets>;
  const items = list(v.items).map((rawItem) => {
    const r = object(rawItem);
    if (r.book_id !== e.bookId) throw invalid();
    return {
      id: text(r.id),
      kind: oneOf(r.kind, Object.keys(agingKinds) as Kind[]),
      bucket: oneOf(r.bucket, Object.keys(agingBuckets) as Bucket[]),
      counterparty: text(r.counterparty_name ?? r.counterparty_id),
      original_date: text(r.original_date),
      due_date: r.due_date === null ? null : text(r.due_date),
      remaining_cents: integer(r.remaining_cents, 1),
    };
  });
  unique(items.map((r) => r.id));
  for (const k of Object.keys(agingKinds) as Kind[])
    for (const b of Object.keys(agingBuckets) as Bucket[]) {
      if (
        items
          .filter((r) => r.kind === k && r.bucket === b)
          .reduce((sum, r) => sum + r.remaining_cents, 0) !== by_kind[k][b]
      )
        throw invalid();
    }
  for (const b of Object.keys(agingBuckets) as Bucket[])
    if (Object.values(by_kind).reduce((sum, row) => sum + row[b], 0) !== totals[b]) throw invalid();
  return { by_kind, items };
}
export function parsePeriodic(raw: unknown, e: FormalExpectedScope) {
  const v = object(raw);
  scope(v.scope, e);
  const locked = flag(v.locked),
    can_generate = flag(v.can_generate);
  if (locked && can_generate) throw invalid();
  const accounts = list(v.accounts).map((rawAccount) => {
    const r = object(rawAccount);
    return {
      id: text(r.id),
      version: integer(r.version, 1),
      code: text(r.code),
      name: text(r.name),
    };
  });
  unique(accounts.map((a) => a.id));
  const assets = list(v.assets).map((rawAsset) => {
    const r = object(rawAsset);
    if (r.book_id !== e.bookId) throw invalid();
    const cost_cents = integer(r.cost_cents, 1),
      residual_cents = integer(r.residual_cents),
      posted_cents = integer(r.posted_cents);
    if (residual_cents >= cost_cents || posted_cents > cost_cents - residual_cents) throw invalid();
    return {
      id: text(r.id),
      name: text(r.name),
      kind: oneOf(r.kind, ['fixed', 'intangible']),
      status: text(r.status),
      cost_cents,
      residual_cents,
      posted_cents,
      life_months: integer(r.life_months, 1),
      start_period: text(r.start_period),
    };
  });
  unique(assets.map((a) => a.id));
  const drafts = list(v.drafts).map((rawDraft) => {
    const r = object(rawDraft),
      d = object(r.draft);
    if (d.book_id !== e.bookId || d.period !== e.period) throw invalid();
    const id = text(d.id),
      status = oneOf(d.status, ['draft', 'approved', 'posted', 'rejected']);
    const can_approve = flag(r.can_approve),
      can_post = flag(r.can_post);
    if (
      (can_approve && status !== 'draft') ||
      (can_post && status !== 'approved') ||
      (locked && (can_approve || can_post))
    )
      throw invalid();
    const lines = list(r.lines).map((rawLine) => {
      const l = object(rawLine);
      if (l.draft_id !== id) throw invalid();
      const debit_cents = integer(l.debit_cents),
        credit_cents = integer(l.credit_cents);
      if (debit_cents > 0 === credit_cents > 0) throw invalid();
      return {
        id: text(l.id),
        account_id: text(l.account_id),
        account_version: integer(l.account_version, 1),
        debit_cents,
        credit_cents,
      };
    });
    unique(lines.map((l) => l.id));
    if (!lines.length || lines.reduce((sum, l) => sum + l.debit_cents - l.credit_cents, 0) !== 0)
      throw invalid();
    return {
      id,
      status,
      version: integer(d.version, 1),
      description: text(d.description),
      created_by: text(d.created_by),
      can_approve,
      can_post,
      lines,
    };
  });
  unique(drafts.map((d) => d.id));
  return { locked, can_generate, accounts, assets, drafts };
}
export type PeriodicWorkspace = ReturnType<typeof parsePeriodic>;
