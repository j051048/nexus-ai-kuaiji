import type { WorkspaceProfile, TaskKind } from './workspace-types';

/** Presentation only. Each API still resolves the actor's current grants. */
export function workspaceExperience(profile: WorkspaceProfile | null) {
  const processing = !!(
    profile &&
    (profile.can_accounting || profile.can_finance || profile.can_manage)
  );
  const employee = profile?.role === 'employee';
  const approver = profile?.role === 'approver';
  return {
    processing,
    simple: !!profile && !processing,
    businessLabel: employee ? '我的提交' : approver ? '部门事项' : '业务事项',
    actionHint: employee
      ? '退回修改 · 待补资料'
      : approver
        ? '部门审批 · 退回核对'
        : profile?.role === 'finance'
          ? '资料核验 · 移交会计'
          : '异常 · 复核 · 交付',
    waitingLabel: employee ? '我的办理进度' : '等待他人 / 后台',
    kinds: (processing
      ? null
      : approver
        ? ['intake', 'business', 'approval', 'case', 'material']
        : ['intake', 'business', 'case', 'material']) as TaskKind[] | null,
  };
}
