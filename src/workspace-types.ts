import type { Pagination } from './types';
export type WorkspaceRole = 'accountant' | 'finance' | 'approver' | 'employee' | 'manager' | 'viewer';
export type WorkspaceProfile = { role: WorkspaceRole; can_submit: boolean; can_approve: boolean; can_finance: boolean; can_review: boolean; can_accounting: boolean; can_manage: boolean; companies: { id: string; name: string }[]; book_count: number };
export type TaskKind = 'intake' | 'business' | 'approval' | 'finance' | 'handoff' | 'case' | 'review' | 'ocr' | 'proposal' | 'export' | 'delivery' | 'alert';
export type WorkspaceTask = { key: string; kind: TaskKind; ref: string; company_id: string; company_name: string; book_id: string | null; event_id: string | null; draft_id: string | null; batch_id: string | null; title: string; state: string; priority: number; due_on: string | null; updated_at: string; bucket: 'action' | 'waiting'; action_label: string; responsible: string; overdue: boolean };
export type WorkspaceTasks = { tasks: WorkspaceTask[]; counts: Partial<Record<'action' | 'waiting', number>>; pagination: Pagination };
export const roleCopy: Record<WorkspaceRole, { name: string; subtitle: string }> = {
  employee: { name: '我的工作台', subtitle: '上传票据、确认事项，随时查看审批和补件进度。' },
  approver: { name: '审批工作台', subtitle: '先处理本部门待审批事项，再查看资料与办理进度。' },
  finance: { name: '财务工作台', subtitle: '核验业务与票据，把资料完整的事项移交会计。' },
  accountant: { name: '会计工作台', subtitle: '集中处理授权企业的异常、凭证复核和交付回执。' },
  manager: { name: '企业工作台', subtitle: '关注资料收集、成员协作与后台处理情况。' },
  viewer: { name: '工作空间', subtitle: '查看当前授权范围内的事项与办理进度。' },
};
export const taskLabels: Record<TaskKind, string> = { intake: '资料草稿', business: '我的事项', approval: '部门审批', finance: '财务核验', handoff: '移交会计', case: '异常与补件', review: '凭证复核', ocr: '票据识别', proposal: '草稿生成', export: '审核后导出', delivery: '交付核验', alert: '运行告警' };
export function taskDestination(task: WorkspaceTask) {
  switch (task.kind) {
    case 'intake': return { type: 'intake' as const, id: task.ref };
    case 'business': case 'approval': case 'finance': case 'handoff': return { type: 'business' as const, id: task.event_id ?? task.ref };
    case 'case': return { type: 'case' as const, id: task.ref };
    case 'review': return { type: 'review' as const, id: task.draft_id ?? task.ref, bookId: task.book_id };
    case 'ocr': return { type: 'ocr' as const, id: task.batch_id, jobId: task.ref };
    case 'proposal': return { type: 'proposal' as const, id: task.ref };
    case 'export': return { type: 'export' as const, id: task.ref, bookId: task.book_id };
    case 'delivery': return { type: 'delivery' as const, id: task.ref, bookId: task.book_id };
    case 'alert': return { type: 'alert' as const, id: task.company_id };
  }
}
