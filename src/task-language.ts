import { presentationRole } from './workspace-presentation';
import { taskLabels } from './task-labels';
import type { TaskPresentation } from './task-presentation';
import type { TaskKind, WorkspaceProfile, WorkspaceTask } from './workspace-types';

// Primary job controls language, never authorization. Finance keeps its working terminology.
export function usesPlainTaskLanguage(profile: WorkspaceProfile | null): boolean {
  return (
    !!profile && ['employee', 'approver', 'manager', 'viewer'].includes(presentationRole(profile))
  );
}

const kindLabels: Record<TaskKind, string> = {
  intake: '未提交的资料',
  business: '我的事项',
  approval: '部门审批',
  finance: '财务核对',
  handoff: '交给会计处理',
  case: '问题与补充资料',
  review: '会计核对',
  ocr: '票据处理',
  proposal: '记账建议',
  export: '账务文件',
  delivery: '文件接收结果',
  alert: '需要协助的问题',
  migration: '旧账转入进度',
  material: '需要提供的资料',
};

export function taskKindLabel(kind: TaskKind, profile: WorkspaceProfile | null): string {
  const labels = usesPlainTaskLanguage(profile) ? kindLabels : taskLabels;
  return Object.prototype.hasOwnProperty.call(labels, kind) ? labels[kind] : '工作事项';
}
const business = {
  draft: '尚未提交',
  submitted: '已提交，等待下一步处理',
  department_approved: '等待财务核对资料',
  finance_approved: '财务已核对，等待交给会计',
  handed_off: '资料已交给会计',
  returned: '需要修改后重交',
};
const jobs = {
  waiting: '等待补齐资料',
  queued: '正在排队，尚未开始',
  running: '正在处理',
  retry_wait: '等待系统再次处理',
  failed: '处理未成功，请查看原因',
  interrupted: '处理已中断',
  stale: '资料已变化，需要重新核对',
  cancelled: '已取消',
  unknown: '处理结果尚未确认',
};
const clientDetails: Record<TaskKind, Readonly<Record<string, string>>> = {
  intake: { draft: '尚未提交' },
  business,
  approval: { ...business, submitted: '等待部门审批' },
  finance: { ...business, submitted: '等待财务核对资料' },
  handoff: {
    submitted: '等待会计接收资料',
    finance_approved: business.finance_approved,
    handed_off: business.handed_off,
  },
  material: {
    pending: '需要提供资料',
    submitted: '资料已提交，等待核对',
    returned: '需要修改后重交',
    accepted: '这项资料已核对',
  },
  case: {
    open: '问题待处理',
    awaiting_info: '等待补充资料',
    response_submitted: '补充资料已提交，等待核对',
    in_review: '问题处理中',
    resolved: '这项问题已处理',
    done: '这项问题已处理',
    cancelled: '已取消',
  },
  review: {
    draft: '等待会计核对这笔业务',
    approved: '会计已审核，等待导出',
    exported: '文件已导出，等待接收确认',
  },
  ocr: { ...jobs, succeeded: '票据内容已读取，仍需核对' },
  proposal: { ...jobs, needs_attention: '需要会计协助', succeeded: '记账建议已生成，等待会计核对' },
  export: {
    approved: '会计已审核，等待导出',
    exported: '文件已导出，等待接收确认',
    unverified: '等待确认文件接收结果',
    unknown: '文件导出结果尚未确认',
  },
  delivery: {
    unverified: '等待确认文件接收结果',
    unknown: '文件接收结果尚未确认',
    exported: '文件已导出，等待接收确认',
    verified: '文件接收结果已核对',
    mismatch: '文件接收结果有差异',
    failed: '文件接收结果需要核对',
  },
  alert: { open: '需要负责人协助', resolved: '这项问题已处理' },
  migration: {
    collecting: '正在收集旧账资料',
    processing: '正在处理旧账资料',
    needs_attention: '旧账转入需要协助',
    ready: '旧账整理完成，等待另一人核对',
    accepted: '旧账已核对，等待接管',
    active: '旧账已接管',
    cancelled: '已取消',
  },
};

/** Copy projection only: preserve every decision, action, assignee and raw task identity. */
export function plainTaskPresentation(
  task: WorkspaceTask,
  base: TaskPresentation,
): TaskPresentation {
  const labels = Object.prototype.hasOwnProperty.call(clientDetails, task.kind)
    ? clientDetails[task.kind]
    : undefined;
  let detail =
    labels && Object.prototype.hasOwnProperty.call(labels, task.state)
      ? labels[task.state]
      : base.detail;
  let next = base.next;
  if (base.actionable) {
    if (task.kind === 'approval' && task.state === 'submitted') detail = '需要你审批';
    if (task.kind === 'finance' && task.state === 'department_approved') detail = '需要你核对资料';
    if (task.kind === 'material' && task.state === 'submitted') detail = '需要核对已提交资料';
    if (task.kind === 'review' && task.state === 'draft') detail = '需要你核对原件与凭证';
    if (task.kind === 'export' && task.state === 'approved') detail = '已审核，可核对后导出';
    if (task.kind === 'delivery' && task.state === 'unverified') detail = '需要核对文件接收结果';
    if (task.kind === 'migration' && task.state === 'ready') detail = '需要独立核对旧账资料';
  }
  // Do not erase permission/unknown-result warnings or tell a different actor to perform a step.
  const mayExplain =
    base.key !== 'blocked' &&
    (task.bucket === 'waiting' || base.actionable || base.key === 'done' || base.key === 'running');
  if (mayExplain) {
    if (task.kind === 'material' && task.state === 'submitted' && !base.actionable)
      next =
        base.responsible === '负责人待确认'
          ? '资料已提交，请联系企业财务确认由谁核对；无需重复提交。'
          : `等待${base.responsible}核对已提交的资料；如需补充，会在此处通知，无需重复提交。`;
    if (task.kind === 'material' && task.state === 'accepted')
      next = '这项资料已核对，可查看原件和处理记录；不代表本月账务或交付已经完成。';
    if (task.kind === 'material' && task.state === 'returned' && base.actionable)
      next = '查看退回原因，在同一资料任务补齐或修改原件，核对后重新提交。';
    if (task.state === 'handed_off')
      next = '资料已交给会计；这不代表账务已审核或已导出，请查看后续办理进度。';
    if (task.kind === 'ocr' && task.state === 'succeeded')
      next = '票据内容已读取，仍须对照原件核对金额、日期和交易对方；识别不代表票据已验真。';
    if (task.kind === 'proposal' && task.state === 'succeeded')
      next = '系统已生成记账建议，等待会计对照原件核对；审核后才可导出，不代表已经完成做账。';
  }
  const kindLabel = Object.prototype.hasOwnProperty.call(kindLabels, task.kind)
    ? kindLabels[task.kind]
    : base.kindLabel;
  return { ...base, kindLabel, detail, next };
}
