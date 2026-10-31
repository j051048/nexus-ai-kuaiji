/** Validate the entire read projection before accepting any row. Hashes bind
 * identities; they are not client authorization or signatures. */
export interface DeliveryPageScope {
  company_id: string;
  book_id: string;
  period: string;
  inputs_fingerprint: string;
  conversation_id: string;
  turn_id: string;
  kind: 'bank' | 'control';
  limit: number;
  start: number;
  snapshot_fingerprint?: string;
}
export interface DeliveryPage extends DeliveryPageScope {
  schema: 'assistant-delivery-page-v1';
  snapshot_fingerprint: string;
  page_fingerprint: string;
  end: number;
  total: number;
  has_more: boolean;
  cursor: string;
  accounting_approved: false;
  rows: {
    key: string;
    label: string;
    passed: boolean;
    issues: {
      code: string;
      reason: string;
      owner_role: string;
      next_action: string;
      evidence_ids: string[];
    }[];
  }[];
}
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim();
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const integer = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;

export function parseDeliveryPage(raw: unknown, scope: DeliveryPageScope): DeliveryPage {
  const fail = () => {
    throw new Error('明细范围或版本不一致，已停止展示。请重新打开本次诊断。');
  };
  if (!object(raw)) return fail();
  for (const key of [
    'company_id',
    'book_id',
    'period',
    'inputs_fingerprint',
    'conversation_id',
    'turn_id',
    'kind',
    'limit',
    'start',
  ] as const)
    if (raw[key] !== scope[key]) return fail();
  if (
    raw.schema !== 'assistant-delivery-page-v1' ||
    raw.accounting_approved !== false ||
    !hash(raw.snapshot_fingerprint) ||
    !hash(raw.page_fingerprint) ||
    (scope.snapshot_fingerprint && raw.snapshot_fingerprint !== scope.snapshot_fingerprint) ||
    !integer(raw.start) ||
    !integer(raw.end) ||
    !integer(raw.total) ||
    !integer(raw.limit) ||
    raw.limit < 1 ||
    raw.limit > 20 ||
    raw.end < raw.start ||
    raw.end > raw.total ||
    raw.has_more !== raw.end < raw.total ||
    !text(raw.cursor) ||
    raw.cursor.length > 2048 ||
    !Array.isArray(raw.rows) ||
    raw.rows.length !== raw.end - raw.start ||
    raw.rows.length !== Math.min(raw.limit, raw.total - raw.start)
  )
    return fail();
  const keys = new Set();
  const codes =
    scope.kind === 'bank'
      ? ['bank_coverage_missing', 'bank_account_difference', 'bank_adjustments']
      : ['control_account_difference'];
  for (const row of raw.rows) {
    if (
      !object(row) ||
      !hash(row.key) ||
      keys.has(row.key) ||
      !text(row.label) ||
      typeof row.passed !== 'boolean' ||
      !Array.isArray(row.issues)
    )
      return fail();
    keys.add(row.key);
    for (const issue of row.issues) {
      if (
        !object(issue) ||
        !text(issue.code) ||
        !codes.includes(issue.code) ||
        !text(issue.reason) ||
        !text(issue.owner_role) ||
        !text(issue.next_action) ||
        !Array.isArray(issue.evidence_ids) ||
        issue.evidence_ids.length !== 1 ||
        issue.evidence_ids[0] !== `book:${scope.book_id}`
      )
        return fail();
    }
  }
  return raw as unknown as DeliveryPage;
}
