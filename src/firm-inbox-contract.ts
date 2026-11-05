import { taskLabels, type TaskKind, type WorkspaceTask } from './workspace-types';
export type Bucket = 'all' | 'today' | 'overdue' | 'blocked' | 'review' | 'failed';
export type FirmItem = Omit<WorkspaceTask, 'key' | 'bucket' | 'overdue' | 'priority'> & {
  assignee_id: string | null;
  assignee_name: string | null;
  assignment_version: number;
  assignment_note: string;
  bucket: Exclude<Bucket, 'all'>;
  task_bucket: 'action' | 'waiting';
  permissions: { assign: boolean; remind: boolean; claim: boolean };
};
export type FirmInboxData = {
  counts: Record<Exclude<Bucket, 'all'>, number>;
  items: FirmItem[];
  assignees: { id: string; name: string; source: 'firm' | 'client' }[];
  note: string;
  pagination: { total: number; limit: number; offset: number; has_more: boolean };
};
const object = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown) => typeof v === 'string';
const count = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
export function parseFirmInbox(
  v: unknown,
  firm: string | null,
  user: string,
  bucket: Bucket,
  offset: number,
): FirmInboxData | null {
  if (
    !object(v) ||
    !object(v.scope) ||
    v.scope.firm_id !== firm ||
    v.scope.user_id !== user ||
    v.scope.bucket !== bucket ||
    !object(v.counts) ||
    !object(v.pagination)
  )
    return null;
  const p = v.pagination;
  if (
    !count(p.total) ||
    p.limit !== 20 ||
    p.offset !== offset ||
    p.has_more !== offset + 20 < p.total
  )
    return null;
  if (
    !['today', 'overdue', 'blocked', 'review', 'failed'].every(
      (k) => object(v.counts) && count(v.counts[k]),
    )
  )
    return null;
  if (
    !Array.isArray(v.items) ||
    v.items.length !== Math.min(20, Math.max(0, p.total - offset)) ||
    !v.items.every((r) => {
      if (
        !object(r) ||
        !text(r.kind) ||
        !Object.prototype.hasOwnProperty.call(taskLabels, r.kind as string) ||
        !text(r.ref) ||
        !r.ref ||
        !text(r.company_id) ||
        !r.company_id
      )
        return false;
      if (
        ![
          'company_name',
          'title',
          'state',
          'responsible',
          'action_label',
          'due_on',
          'updated_at',
          'assignment_note',
        ].every((k) => text(r[k])) ||
        !['book_id', 'event_id', 'draft_id', 'batch_id', 'assignee_id', 'assignee_name'].every(
          (k) => r[k] === null || text(r[k]),
        )
      )
        return false;
      if (
        !count(r.assignment_version) ||
        !['today', 'overdue', 'blocked', 'review', 'failed'].includes(String(r.bucket)) ||
        !['action', 'waiting'].includes(String(r.task_bucket)) ||
        !object(r.permissions)
      )
        return false;
      return ['assign', 'remind', 'claim'].every(
        (k) => object(r.permissions) && typeof r.permissions[k] === 'boolean',
      );
    }) ||
    new Set(v.items.map((r) => `${r.kind}:${r.ref}`)).size !== v.items.length
  )
    return null;
  if (
    !Array.isArray(v.assignees) ||
    !v.assignees.every(
      (a) =>
        object(a) && text(a.id) && text(a.name) && ['firm', 'client'].includes(String(a.source)),
    ) ||
    !text(v.note)
  )
    return null;
  return v as unknown as FirmInboxData;
}
export function firmTaskTarget(row: FirmItem): WorkspaceTask {
  return {
    ...row,
    kind: row.kind as TaskKind,
    key: `${row.kind}:${row.ref}`,
    bucket: row.task_bucket,
    priority: 0,
    overdue: row.bucket === 'overdue',
  };
}
