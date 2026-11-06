import type { TaskKind, WorkspaceProfile, WorkspaceTask } from './workspace-types';
import { plainTaskPresentation, usesPlainTaskLanguage, taskKindLabel } from './task-language';

export type TaskSummaryKey = 'mine' | 'waiting' | 'running' | 'done' | 'blocked';
export type TaskPresentation = {
  kindLabel: string;
  key: TaskSummaryKey;
  label: string;
  detail: string;
  next: string;
  actionLabel: string;
  responsible: string;
  /** A presentation hint only. Opening an item always rechecks server grants. */
  actionable: boolean;
};

const businessStates = {
  draft: '待完善',
  submitted: '已提交，等待下一环节处理',
  department_approved: '待财务核验',
  finance_approved: '待移交会计',
  handed_off: '已移交会计',
  returned: '待修改重提',
};
const jobStates = {
  waiting: '等待资料',
  queued: '排队中',
  running: '后台处理中',
  retry_wait: '等待重试',
  failed: '执行失败',
  interrupted: '执行已中断',
  stale: '事实已更新',
  cancelled: '已取消',
  unknown: '执行结果待核实',
};

/** Explicit kind/state pairs avoid translating a status from another workflow. */
const details: Record<TaskKind, Readonly<Record<string, string>>> = {
  intake: { draft: '待完善' },
  business: businessStates,
  approval: { ...businessStates, submitted: '待部门审批' },
  finance: { ...businessStates, submitted: '待财务核验' },
  handoff: { submitted: '待会计接收', finance_approved: '待移交会计', handed_off: '已移交会计' },
  case: {
    open: '待处理',
    awaiting_info: '等待补件',
    response_submitted: '补件待核验',
    in_review: '处理中',
    resolved: '已处理',
    done: '已完成',
    cancelled: '已取消',
  },
  review: { draft: '待会计复核', approved: '已审核待导出', exported: '已导出，待回执核验' },
  ocr: { ...jobStates, succeeded: '识别完成，仍需核对' },
  proposal: { ...jobStates, needs_attention: '需要人工介入', succeeded: '草稿已生成，待会计复核' },
  export: {
    approved: '已审核待导出',
    exported: '已导出，待回执核验',
    unverified: '等待回执核验',
    unknown: '导出结果待核实',
  },
  delivery: {
    unverified: '等待回执核验',
    unknown: '导入结果待核实',
    exported: '已导出，待回执核验',
    verified: '回执已核验',
    mismatch: '交付存在差异',
    failed: '交付结果需核对',
  },
  alert: { open: '待处理', resolved: '已处理' },
  migration: {
    collecting: '资料收集中',
    processing: '后台处理中',
    needs_attention: '需要人工介入',
    ready: '待独立验收',
    accepted: '待接管',
    active: '已接管',
    cancelled: '已取消',
  },
  material: {
    pending: '待提供资料',
    submitted: '待独立验收',
    returned: '待补充重提',
    accepted: '已验收',
  },
};

const opaqueTitle = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24,64}|undefined|null|untitled|未命名)$/i;

/** Keep a real business summary; only replace empty or wholly technical titles. */
export function taskTitle(task: Pick<WorkspaceTask, 'title' | 'company_name' | 'amount'>, summary: Pick<TaskPresentation, 'kindLabel'>): string {
  const title = task.title.trim();
  if (title && !opaqueTitle.test(title)) return title;
  const amount = task.amount ? `¥${task.amount}` : '';
  return [summary.kindLabel, task.company_name, amount].filter(Boolean).join(' · ');
}

export function taskDetailLabel(task: Pick<WorkspaceTask, 'kind' | 'state'>): string {
  const states = Object.prototype.hasOwnProperty.call(details, task.kind)
    ? details[task.kind]
    : undefined;
  return states && Object.prototype.hasOwnProperty.call(states, task.state)
    ? states[task.state]
    : '状态待核实';
}

function capability(task: WorkspaceTask, profile: WorkspaceProfile): boolean {
  const actionStates: Record<TaskKind, readonly string[]> = {
    intake: ['draft'],
    business: ['draft', 'returned'],
    approval: ['submitted'],
    finance: ['department_approved'],
    handoff: ['submitted', 'finance_approved'],
    case: ['open', 'awaiting_info', 'response_submitted', 'in_review'],
    review: ['draft'],
    ocr: ['failed', 'interrupted', 'stale'],
    proposal: ['needs_attention', 'failed', 'interrupted', 'stale'],
    export: ['approved'],
    delivery: ['unverified', 'mismatch', 'failed'],
    alert: ['open'],
    migration: ['collecting', 'processing', 'needs_attention', 'ready', 'accepted'],
    material: ['pending', 'returned', 'submitted'],
  };
  if (!actionStates[task.kind].includes(task.state)) return false;
  switch (task.kind) {
    case 'intake':
      return profile.can_submit;
    case 'business':
      return ['draft', 'returned'].includes(task.state) && profile.can_submit;
    case 'approval':
      return profile.can_approve;
    case 'finance':
      return profile.can_finance;
    case 'handoff':
      return task.state === 'submitted' ? profile.can_accounting : profile.can_finance;
    case 'case':
      return (
        profile.can_submit ||
        profile.can_approve ||
        profile.can_finance ||
        profile.can_accounting ||
        profile.can_review ||
        profile.can_manage
      );
    case 'review':
    case 'export':
    case 'delivery':
      return profile.can_review;
    case 'ocr':
      return (
        profile.can_submit || profile.can_approve || profile.can_finance || profile.can_accounting
      );
    case 'proposal':
      return profile.can_accounting;
    case 'alert':
      return profile.can_manage;
    case 'migration':
      return task.state === 'ready'
        ? profile.can_review
        : profile.can_accounting || profile.can_manage;
    case 'material':
      return task.state === 'submitted'
        ? profile.can_review
        : profile.can_submit || profile.can_finance || profile.can_accounting || profile.can_manage;
  }
}

const categories: Record<TaskSummaryKey, string> = {
  mine: '待我处理',
  waiting: '等待处理',
  running: '处理中',
  done: '已完成',
  blocked: '需要关注',
};

function waitingNext(task: WorkspaceTask, responsible: string): string {
  if (task.state === 'handed_off')
    return '资料已交给会计，不代表凭证已审核或已导出；请查看后续办理进度。';
  if (task.kind === 'ocr' && task.state === 'succeeded')
    return '识别仅提供字段建议；请由获授权人员对照原件核对金额、日期和交易对方。';
  if (task.kind === 'proposal' && task.state === 'succeeded')
    return '草稿已生成，仍须由会计核对原件和分录，复核通过后才能导出。';
  if (task.kind === 'migration' && task.state === 'accepted')
    return `迁移验收已通过，等待${responsible}核对并接管；验收不代表已完成接管。`;
  return responsible === '负责人待确认'
    ? '请查看办理进度，并联系企业财务或事务所确认负责人；无需重复提交。'
    : `等待${responsible}处理；可查看进度或联系负责人，无需重复提交。`;
}

function actionNext(task: WorkspaceTask): string {
  switch (task.kind) {
    case 'intake':
      return '继续填写已保存的资料，对照原件核对后再提交。';
    case 'business':
      return task.state === 'returned'
        ? '查看退回原因，修改同一事项并补齐资料，核对后重新提交。'
        : '继续填写事项并上传原件，核对金额、日期和交易对方后提交。';
    case 'approval':
      return '打开事项，核对业务用途和原件；按页面授权完成部门审批或说明退回原因。';
    case 'finance':
      return '打开事项，核对金额、日期、交易对方与原件，再按页面授权核验或退回补充。';
    case 'handoff':
      return '核对资料和对应月份账套，再按页面授权移交或接收；生成草稿后仍需会计复核。';
    case 'case':
      return task.state === 'awaiting_info'
        ? '打开补件任务，按要求补充原始资料并说明，提交后等待独立核验。'
        : '打开处理任务，核对问题和补件记录，再按页面授权处理并留存理由。';
    case 'review':
      return '同屏核对原件、金额与分录，确认当前事实版本后按页面授权审核；审核后才可导出。';
    case 'ocr':
      return '打开识别任务，核对失败原因和已有结果；需要时转人工处理，重试资格以任务详情为准。';
    case 'proposal':
      return '打开生成任务，核对业务事实与差异，补充资料或转人工处理；不要用补造分录消除差异。';
    case 'export':
      return '核对已审核凭证和已有导出记录，再按页面授权导出；随后核验外部导入回执。';
    case 'delivery':
      return '先核对已有导出记录、外部回执和差异，不要重复发送；按页面授权核验结果。';
    case 'alert':
      return '打开告警，核对受影响企业和处理原因，再按现有权限处理或联系系统管理员。';
    case 'migration':
      return task.state === 'ready'
        ? '核对迁移资料、余额与差异，再由独立复核人员按页面授权验收。'
        : '打开迁移项目，核对原资料、映射和差异，再按当前阶段的页面授权继续办理。';
    case 'material':
      return task.state === 'submitted'
        ? '打开资料清单，对照原件和要求独立核验；有问题说明原因并退回。'
        : '按清单提供本期原始资料，核对后提交；提交后仍需独立验收。';
  }
}

/**
 * Read-only projection of already scoped tasks. bucket is server-derived;
 * responsible is display text, never an identity or a grant. Capabilities only
 * narrow suggestions; the destination endpoint remains authoritative.
 */
function taskPresentationCore(
  task: WorkspaceTask,
  profile: WorkspaceProfile | null,
): TaskPresentation {
  const detail = taskDetailLabel(task);
  const responsible = task.responsible.trim() || '负责人待确认';
  const result = (
    key: TaskSummaryKey,
    next: string,
    actionable = false,
    actionLabel = actionable ? task.action_label.trim() || '打开处理' : '查看进度',
  ): TaskPresentation => ({
    kindLabel: taskKindLabel(task.kind, null),
    key,
    label: categories[key],
    detail,
    next,
    actionLabel,
    responsible,
    actionable,
  });

  if (detail === '状态待核实')
    return result(
      'blocked',
      '请重新读取当前事项状态、已有结果与权限，并联系负责人核实；确认前不要重复提交或变更。',
      false,
      '查看详情',
    );
  if (task.state === 'unknown')
    return result(
      'blocked',
      task.kind === 'delivery' || task.kind === 'export'
        ? '先查询已有导出记录和外部回执，核对结果后联系会计；不要重复发送或提交。'
        : '执行结果暂未确认，请重新读取已有结果并联系负责人核对；不要重复发送或提交。',
      false,
      '查看详情',
    );
  if (task.state === 'cancelled')
    return {
      ...result('done', '任务已取消；可查看历史记录及取消原因，如仍需办理请联系负责人。'),
      label: '已结束',
    };
  if (
    (task.kind === 'material' && task.state === 'accepted') ||
    (task.kind === 'delivery' && task.state === 'verified') ||
    (task.kind === 'migration' && task.state === 'active') ||
    (['case', 'alert'].includes(task.kind) && ['resolved', 'done'].includes(task.state))
  )
    return result(
      'done',
      '本项办理结果已留存，可打开查看原件、处理记录和结果；其他业务阶段以各自状态为准。',
    );

  if (
    ['exported', 'unverified'].includes(task.state) ||
    (task.state === 'approved' && ['review', 'export'].includes(task.kind))
  ) {
    if (
      task.kind === 'delivery' &&
      task.state === 'unverified' &&
      task.bucket === 'action' &&
      profile?.can_review
    )
      return result('mine', actionNext(task), true);
    if (task.state !== 'approved')
      return result(
        'waiting',
        '已导出不代表外部已接收；先核对已有导出记录和回执，不要重复发送。',
        false,
        '查看回执',
      );
    const allowed = task.bucket === 'action' && !!profile?.can_review;
    return result(
      allowed ? 'mine' : 'waiting',
      allowed
        ? actionNext({ ...task, kind: 'export' })
        : '凭证已审核，等待获授权会计核对后导出；导出后仍需核验外部回执。',
      allowed,
      allowed ? task.action_label : '查看进度',
    );
  }

  const background =
    (['ocr', 'proposal'].includes(task.kind) &&
      ['queued', 'running', 'retry_wait'].includes(task.state)) ||
    (task.kind === 'migration' && task.state === 'processing' && task.bucket === 'waiting');
  if (background)
    return result(
      'running',
      task.state === 'queued'
        ? '任务正在排队，尚未开始处理；可查看进度，不要重复提交。长时间无变化请联系负责人。'
        : task.state === 'retry_wait'
          ? '任务正在等待系统安排重试；可查看原因和进度，不要重复提交。'
          : '后台正在处理，请查看进度，不要重复提交；长时间无变化请联系负责人核对。',
    );

  if (task.state === 'handed_off' || task.state === 'succeeded')
    return result('waiting', waitingNext(task, responsible));

  const blocked = ['failed', 'interrupted', 'stale', 'needs_attention', 'mismatch'].includes(
    task.state,
  );
  const allowed = task.bucket === 'action' && !!profile && capability(task, profile);
  if (allowed) return result(blocked ? 'blocked' : 'mine', actionNext(task), true);
  if (task.bucket === 'action')
    return result(
      blocked ? 'blocked' : 'waiting',
      !profile
        ? '当前操作权限尚未确认，请重新读取权限，再联系获授权人员办理。'
        : '当前账号未确认具备这一步的操作资格；请查看详情，联系获授权负责人办理。',
      false,
      '查看详情',
    );
  return result(blocked ? 'blocked' : 'waiting', waitingNext(task, responsible));
}

export function taskPresentation(
  task: WorkspaceTask,
  profile: WorkspaceProfile | null,
): TaskPresentation {
  const base = taskPresentationCore(task, profile);
  return usesPlainTaskLanguage(profile) ? plainTaskPresentation(task, base) : base;
}
