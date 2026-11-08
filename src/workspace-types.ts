import { taskLabels } from './task-labels';
export { taskLabels } from './task-labels';
import { taskDetailLabel } from './task-presentation';
import type { Me, Pagination, Session } from './types';
export type WorkspaceRole =
  'accountant' | 'finance' | 'approver' | 'employee' | 'manager' | 'viewer';
export type PresentationRole =
  | 'employee'
  | 'approver'
  | 'finance'
  | 'manager'
  | 'accountant'
  | 'reviewer'
  | 'firm_owner'
  | 'admin'
  | 'viewer';
export type WorkspaceProfile = {
  role: WorkspaceRole;
  /** Optional server-derived presentation identity; never grants access. */
  display_role?: PresentationRole;
  can_submit: boolean;
  can_approve: boolean;
  can_finance: boolean;
  can_review: boolean;
  can_accounting: boolean;
  can_manage: boolean;
  can_operations?: boolean;
  can_firm_operations?: boolean;
  companies: { id: string; name: string }[];
  briefings?: {
    book_id: string;
    book_name: string;
    period: string;
    company_id: string;
    company_name: string;
  }[];
  book_count: number;
};
export type TaskKind =
  | 'intake'
  | 'business'
  | 'approval'
  | 'finance'
  | 'handoff'
  | 'case'
  | 'review'
  | 'ocr'
  | 'proposal'
  | 'export'
  | 'delivery'
  | 'alert'
  | 'migration'
  | 'material';
export type WorkspaceTask = {
  key: string;
  kind: TaskKind;
  ref: string;
  company_id: string;
  company_name: string;
  book_id: string | null;
  event_id: string | null;
  draft_id: string | null;
  batch_id: string | null;
  title: string;
  state: string;
  priority: number;
  due_on: string | null;
  updated_at: string;
  bucket: 'action' | 'waiting';
  action_label: string;
  responsible: string;
  overdue: boolean;
  amount?: string | null;
};
export type WorkspaceTasks = {
  tasks: WorkspaceTask[];
  counts: Partial<Record<'action' | 'waiting', number>>;
  pagination: Pagination;
};
export type WorkspaceBootstrap = {
  me: Me;
  profile: WorkspaceProfile | null;
  tasks: WorkspaceTasks | null;
};
/** A single authorized response, retained only until the first home mount. */
export type WorkspaceHomeBootstrap = Omit<WorkspaceBootstrap, 'me'> & {
  scope_key: string;
};
export type WorkspaceCompanies = {
  companies: { id: string; name: string; task_count: number }[];
  pagination: Pagination;
};
export type WorkspaceSort = 'priority' | 'due' | 'amount' | 'updated';
export type WorkspaceDensity = 'list' | 'table';
export type SavedWorkspaceView = {
  bucket: 'action' | 'waiting';
  kind: TaskKind | '';
  company_id: string;
  sort: WorkspaceSort;
  density: WorkspaceDensity;
};
export const workspaceSortLabels: Record<WorkspaceSort, string> = {
  priority: '风险优先',
  due: '期限最近',
  amount: '金额最大',
  updated: '最近更新',
};

/** Only filter identifiers are retained. Titles, company names and search text are never persisted. */
export function savedWorkspaceViews(raw: string | null): SavedWorkspaceView[] {
  try {
    const parsed: unknown = JSON.parse(raw ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, 3).flatMap((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const value = item as Record<string, unknown>;
      if (value.bucket !== 'action' && value.bucket !== 'waiting') return [];
      if (
        value.kind !== '' &&
        (typeof value.kind !== 'string' ||
          !Object.prototype.hasOwnProperty.call(taskLabels, value.kind))
      )
        return [];
      if (typeof value.company_id !== 'string' || value.company_id.length > 200) return [];
      if (
        typeof value.sort !== 'string' ||
        !Object.prototype.hasOwnProperty.call(workspaceSortLabels, value.sort)
      )
        return [];
      if (value.density !== 'table' && value.density !== 'list') return [];
      return [
        {
          bucket: value.bucket,
          kind: value.kind as TaskKind | '',
          company_id: value.company_id,
          sort: value.sort as WorkspaceSort,
          density: value.density,
        },
      ];
    });
  } catch {
    return [];
  }
}

export function viewStorageKey(identity: Pick<Session, 'mode' | 'user_id' | 'firm_id'>): string {
  // Authentication scopes are intentionally ephemeral; preferences belong to
  // a stable user and firm. This key grants no data access and holds no token.
  return `hengzhang:work-views:v2:${encodeURIComponent(JSON.stringify([identity.mode, identity.user_id, identity.firm_id]))}`;
}

export function formatTaskAmount(value: string | null | undefined): string {
  if (value == null || !/^-?\d+(?:\.\d{1,2})?$/.test(value)) return '—';
  const [integer, decimal = ''] = value.split('.');
  const normalized = BigInt(integer).toString();
  const negativeZero = value.startsWith('-') && normalized === '0';
  return `¥${negativeZero ? '-' : ''}${normalized.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${decimal.padEnd(2, '0')}`;
}

export function taskStateLabel(task: Pick<WorkspaceTask, 'kind' | 'state'>): string {
  return taskDetailLabel(task);
}

export function taskDestination(task: WorkspaceTask, client = false) {
  if ((client || !task.batch_id) && task.kind === 'ocr' && task.event_id)
    return { type: 'business' as const, id: task.event_id };
  switch (task.kind) {
    case 'material':
      return { type: 'material' as const, id: task.ref };
    case 'intake':
      return { type: 'intake' as const, id: task.ref };
    case 'business':
    case 'approval':
    case 'finance':
    case 'handoff':
      return { type: 'business' as const, id: task.event_id ?? task.ref };
    case 'case':
      return { type: 'case' as const, id: task.ref };
    case 'review':
      return { type: 'review' as const, id: task.draft_id ?? task.ref, bookId: task.book_id };
    case 'ocr':
      return { type: 'ocr' as const, id: task.batch_id, jobId: task.ref };
    case 'proposal':
      return { type: 'proposal' as const, id: task.ref };
    case 'export':
      return { type: 'export' as const, id: task.ref, bookId: task.book_id };
    case 'delivery':
      return { type: 'delivery' as const, id: task.ref, bookId: task.book_id };
    case 'alert':
      return { type: 'alert' as const, id: task.company_id };
    case 'migration':
      return { type: 'migration' as const, bookId: task.book_id, id: task.ref };
  }
}
