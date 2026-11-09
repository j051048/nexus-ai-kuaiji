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
      origin_period: r.origin_period === undefined ? e.period : text(r.origin_period),
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

export function parseAssetReconciliation(
  raw: unknown,
  expected: FormalExpectedScope,
  assetId: string,
) {
  const v = object(raw);
  scope(v.scope, expected);
  if (v.asset_id !== assetId || !/^[0-9a-f]{64}$/.test(text(v.fingerprint))) throw invalid();
  const history = list(v.history).map((rawRow) => {
    const r = object(rawRow);
    if (
      !/^\d{4}-(0[1-9]|1[0-2])$/.test(text(r.period)) ||
      !/^[0-9a-f]{64}$/.test(text(r.fingerprint))
    )
      throw invalid();
    return {
      id: text(r.id),
      book_id: text(r.book_id),
      period: text(r.period),
      fingerprint: text(r.fingerprint),
      net_cents: integer(r.net_cents, Number.MIN_SAFE_INTEGER),
    };
  });
  unique(history.map((r) => r.id));
  const accounts = list(v.accounts).map((rawAccount) => {
    const a = object(rawAccount);
    return {
      id: text(a.id),
      name: text(a.name),
      version: integer(a.version, 1),
      original_version: integer(a.original_version, 1),
    };
  });
  unique(accounts.map((a) => a.id));
  const can_generate = flag(v.can_generate),
    blockers = list(v.blockers).map(text);
  const amount_cents = integer(v.amount_cents, Number.MIN_SAFE_INTEGER),
    net_posted_cents = integer(v.net_posted_cents, Number.MIN_SAFE_INTEGER),
    cumulative_cents = integer(v.cumulative_cents);
  if (
    cumulative_cents - net_posted_cents !== amount_cents ||
    history.reduce((sum, row) => sum + row.net_cents, 0) !== net_posted_cents ||
    (can_generate && (blockers.length > 0 || amount_cents <= 0))
  )
    throw invalid();
  return {
    asset_id: assetId,
    asset_name: text(v.asset_name),
    fingerprint: text(v.fingerprint),
    prior_posted_cents: integer(v.prior_posted_cents),
    net_posted_cents,
    amount_cents,
    cumulative_cents,
    can_generate,
    blockers,
    history,
    accounts,
    replaces_draft_id: v.replaces_draft_id === null ? null : text(v.replaces_draft_id),
  };
}

export function financeSection(
  search: string,
  expected: Pick<FormalExpectedScope, 'bookId' | 'companyId' | 'period'>,
): 'aging' | 'periodic' | undefined {
  const query = new URLSearchParams(search);
  if (
    ['finance', 'book', 'company', 'period'].some((key) => query.getAll(key).length !== 1) ||
    query.get('book') !== expected.bookId ||
    query.get('company') !== expected.companyId ||
    query.get('period') !== expected.period
  )
    return undefined;
  const section = query.get('finance');
  return section === 'aging' || section === 'periodic' ? section : undefined;
}

export function parseSettlement(raw: unknown, e: FormalExpectedScope, itemId: string) {
  const v = object(raw);
  scope(v.scope, e);
  const item = object(v.item),
    source = object(v.source);
  if (item.id !== itemId || item.book_id !== e.bookId) throw invalid();
  const total = integer(item.amount_cents, 1),
    remaining = integer(item.remaining_cents),
    allocated = integer(item.allocated_cents);
  if (total !== remaining + allocated) throw invalid();
  const locked = flag(v.locked),
    can_allocate = flag(v.can_allocate),
    fingerprint = text(v.fingerprint);
  if ((locked && can_allocate) || !/^[0-9a-f]{64}$/.test(fingerprint)) throw invalid();
  const vouchers = list(v.vouchers).map((rawVoucher) => {
    const r = object(rawVoucher);
    if (r.book_id !== e.bookId) throw invalid();
    const available_cents = integer(r.available_cents, 1);
    if (available_cents > integer(r.settlement_cents, 1)) throw invalid();
    return {
      id: text(r.id),
      description: text(r.description),
      voucher_date: text(r.voucher_date),
      available_cents,
    };
  });
  const allocations = list(v.allocations).map((rawAllocation) => {
    const r = object(rawAllocation);
    if (r.book_id !== e.bookId || r.item_id !== itemId) throw invalid();
    return {
      id: text(r.id),
      voucher_id: text(r.voucher_id),
      amount_cents: integer(r.amount_cents, 1),
      reason: text(r.reason),
      allocated_on: text(r.allocated_on),
      reversal_id: r.reversal_id === null ? null : text(r.reversal_id),
      reversal_reason: r.reversal_reason === null ? null : text(r.reversal_reason),
    };
  });
  unique(vouchers.map((r) => r.id));
  unique(allocations.map((r) => r.id));
  if (allocations.reduce((sum, r) => sum + (r.reversal_id ? 0 : r.amount_cents), 0) !== allocated)
    throw invalid();
  return {
    locked,
    can_allocate,
    fingerprint,
    vouchers,
    allocations,
    source: {
      kind: oneOf(source.kind, ['manual', 'opening', 'voucher']),
      verified: flag(source.verified),
    },
    item: {
      id: itemId,
      counterparty: text(item.counterparty_name),
      kind: oneOf(item.kind, Object.keys(agingKinds) as Kind[]),
      amount_cents: total,
      remaining_cents: remaining,
      allocated_cents: allocated,
      original_date: text(item.original_date),
    },
  };
}
