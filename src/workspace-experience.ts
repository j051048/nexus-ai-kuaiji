import type { WorkspaceProfile, TaskKind } from './workspace-types';
import { presentationRole, workSurface } from './workspace-presentation';

/** Presentation only. Each API still resolves the actor's current grants. */
export function workspaceExperience(profile: WorkspaceProfile | null) {
  const role = presentationRole(profile);
  const processing = !!profile && workSurface(role) !== 'client';
  const employee = role === 'employee';
  const approver = role === 'approver';
  // Keep the primary surface simple while retaining the actor's authorized
  // additional jobs. These are filter choices; the server still scopes rows.
  const kinds: TaskKind[] = ['intake', 'business', 'case', 'material'];
  if (profile?.can_approve) kinds.push('approval');
  if (profile?.can_finance) kinds.push('finance');
  if (profile?.can_finance || profile?.can_accounting || profile?.can_manage) {
    kinds.push('handoff', 'ocr', 'proposal', 'migration');
  }
  if (profile?.can_review) kinds.push('review', 'export', 'delivery');
  if (profile?.can_manage) kinds.push('alert');
  return {
    processing,
    simple: !!profile && !processing,
    businessLabel: employee ? '我的事项' : approver ? '部门事项' : '业务事项',
    actionHint: employee
      ? '退回修改 · 待补资料'
      : approver
        ? '部门审批 · 退回核对'
        : role === 'finance'
          ? '资料核验 · 移交会计'
          : '异常 · 复核 · 交付',
    waitingLabel: employee ? '我的办理进度' : '等待他人 / 后台',
    kinds: processing ? null : kinds,
  };
}
