import type { Me, Pagination, Session } from './types';
export type WorkspaceRole =
  'accountant' | 'finance' | 'approver' | 'employee' | 'manager' | 'viewer';
export type WorkspaceProfile = {
  role: WorkspaceRole;
  can_submit: boolean;
  can_approve: boolean;
  can_finance: boolean;
  can_review: boolean;
  can_accounting: boolean;
  can_manage: boolean;
  can_operations?: boolean;
  companies: { id: string; name: string }[];
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
  if (task.kind === 'material') {
    return (
      (
        {
          pending: '待提供资料',
          submitted: '待独立验收',
          returned: '待补充重提',
          accepted: '已验收',
        } as Record<string, string>
      )[task.state] ?? '状态待核实'
    );
  }
  if (task.state === 'draft') return task.kind === 'review' ? '待会计复核' : '待完善';
  const labels: Record<string, string> = {
    submitted: '待部门审批',
    department_approved: '待财务核验',
    finance_approved: '待移交会计',
    handed_off: '已移交会计',
    returned: '待修改重提',
    open: '待处理',
    awaiting_info: '等待补件',
    response_submitted: '补件待核验',
    in_review: '处理中',
    queued: '排队中',
    waiting: '等待资料',
    running: '后台处理中',
    retry_wait: '等待重试',
    needs_attention: '需要人工介入',
    failed: '执行失败',
    interrupted: '执行已中断',
    stale: '事实已更新',
    approved: '已审核待导出',
    unverified: '等待回执核验',
    collecting: '资料收集中',
    processing: '后台处理中',
    ready: '待独立验收',
    accepted: '待接管',
    active: '已接管',
    cancelled: '已取消',
    succeeded: '已完成',
    done: '已完成',
    resolved: '已处理',
    unknown: '状态待核实',
    pending: '待提供资料',
  };
  return labels[task.state] ?? '状态待核实';
}
export const roleCopy: Record<WorkspaceRole, { name: string; subtitle: string }> = {
  employee: { name: '我的工作台', subtitle: '上传票据、确认事项，随时查看审批和补件进度。' },
  approver: { name: '审批工作台', subtitle: '先处理本部门待审批事项，再查看资料与办理进度。' },
  finance: { name: '财务工作台', subtitle: '核验业务与票据，把资料完整的事项移交会计。' },
  accountant: {
    name: '会计工作台',
    subtitle: '集中处理授权企业的异常、凭证复核、旧账迁移与交付回执。',
  },
  manager: { name: '企业工作台', subtitle: '关注资料收集、成员协作与后台处理情况。' },
  viewer: { name: '工作空间', subtitle: '查看当前授权范围内的事项与办理进度。' },
};
export const taskLabels: Record<TaskKind, string> = {
  intake: '资料草稿',
  business: '我的事项',
  approval: '部门审批',
  finance: '财务核验',
  handoff: '移交会计',
  case: '异常与补件',
  review: '凭证复核',
  ocr: '票据识别',
  proposal: '草稿生成',
  export: '审核后导出',
  delivery: '交付核验',
  alert: '运行告警',
  migration: '旧账迁移',
  material: '周期资料',
};
export function taskDestination(task: WorkspaceTask) {
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
