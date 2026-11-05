import { firmStageLabels, type FirmSnapshot } from './firm-cockpit-focus';

type Scope = {
  firm_id: string;
  user_id: string;
  period: string;
  search: string;
  limit: number;
  offset: number;
};
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown): value is string => typeof value === 'string';
const id = (value: unknown): value is string => text(value) && value.length > 0;

export function parseFirmSnapshot(value: unknown, expected: Scope): FirmSnapshot | null {
  if (!object(value) || !object(value.scope) || !object(value.summary) || !object(value.pagination))
    return null;
  if (
    ['firm_id', 'user_id', 'period', 'search'].some(
      (key) =>
        value.scope && object(value.scope) && value.scope[key] !== expected[key as keyof Scope],
    )
  )
    return null;
  if (value.period !== expected.period) return null;
  const s = value.summary,
    p = value.pagination;
  if (
    ![
      'clients',
      'opened',
      'closed',
      'overdue',
      'exceptions',
      'risk_clients',
      'projection_stale',
    ].every((k) => count(s[k]))
  )
    return null;
  if ((s.closed as number) > (s.opened as number) || (s.opened as number) > (s.clients as number))
    return null;
  if (
    !count(p.total) ||
    p.limit !== expected.limit ||
    p.offset !== expected.offset ||
    p.has_more !== expected.offset + expected.limit < p.total
  )
    return null;
  const validClient = (row: unknown) => {
    if (
      !object(row) ||
      !id(row.company_id) ||
      !text(row.company_name) ||
      !(row.book_id === null || id(row.book_id)) ||
      !count(row.book_count)
    )
      return false;
    if (
      !text(row.period) ||
      (expected.period && row.period !== expected.period) ||
      !text(row.period_status) ||
      !text(row.stage) ||
      !Object.prototype.hasOwnProperty.call(firmStageLabels, row.stage)
    )
      return false;
    if (
      !object(row.tasks) ||
      !['intake', 'review', 'exception', 'delivery', 'overdue'].every(
        (k) => object(row.tasks) && count(row.tasks[k]),
      )
    )
      return false;
    return (
      (row.lead_user_id === null || id(row.lead_user_id)) &&
      text(row.lead_name) &&
      text(row.reviewer_name) &&
      text(row.due_on) &&
      count(row.risk) &&
      (row.book_count === 1 ? id(row.book_id) : row.book_id === null)
    );
  };
  for (const [key, max] of [
    ['companies', expected.limit],
    ['coordination', 6],
    ['risks', 20],
  ] as const) {
    const rows = value[key];
    if (
      !Array.isArray(rows) ||
      rows.length > max ||
      !rows.every(validClient) ||
      new Set(rows.map((r) => r.company_id)).size !== rows.length
    )
      return null;
  }
  if (
    (value.companies as unknown[]).length !==
    Math.min(expected.limit, Math.max(0, p.total - expected.offset))
  )
    return null;
  if (!object(value.stages) || !Object.values(value.stages).every(count)) return null;
  if (
    !Array.isArray(value.workload) ||
    value.workload.length > 50 ||
    !value.workload.every(
      (r) =>
        object(r) &&
        (r.user_id === null || id(r.user_id)) &&
        text(r.name) &&
        ['clients', 'pending', 'exceptions', 'overdue', 'risk_clients'].every((k) => count(r[k])),
    )
  )
    return null;
  const a = value.assignments;
  if (
    !object(a) ||
    a.basis !== 'active_task_assignments' ||
    !count(a.total) ||
    !count(a.people_total) ||
    typeof a.stale !== 'boolean' ||
    !Array.isArray(a.people) ||
    a.people.length > 20 ||
    !a.people.every(
      (r) => object(r) && id(r.user_id) && text(r.name) && count(r.pending) && count(r.overdue),
    )
  )
    return null;
  return value as unknown as FirmSnapshot;
}
