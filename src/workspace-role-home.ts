import { roleCopy, type WorkspaceProfile } from './workspace-types';

export type RoleHomeGuide = {
  label: string;
  focus: string;
  actions: string[];
  boundary: string;
};

/**
 * Presentation-only summary. It never grants access: every action is still
 * re-authorized by the API and the current scope key.
 */
export function roleHomeGuide(profile: WorkspaceProfile): RoleHomeGuide {
  const copy = roleCopy[profile.role] ?? roleCopy.viewer;
  const focus: Record<WorkspaceProfile['role'], string> = {
    employee: '先完成本人待提交、待补件和被退回的事项。',
    approver: '先处理有权限部门内的待审批事项和缺件说明。',
    finance: '先核验本企业资料完整性，再移交会计处理。',
    accountant: '先处理异常、凭证复核与审核后导出，再进入客户账套。',
    manager: '先关注资料收集、成员协作和已授权结果，不查看未审内部数字。',
    viewer: '只查看当前服务端授权范围内的事项和进度。',
  };
  const actions: string[] = [];
  if (profile.can_submit) actions.push('提交本人事项与补件');
  if (profile.can_approve) actions.push('处理部门审批');
  if (profile.can_finance) actions.push('财务核验与移交');
  if (profile.can_review) actions.push('凭证复核与审核后导出');
  if (profile.can_accounting) actions.push('进入当前授权账套');
  if (profile.can_manage) actions.push('成员与服务单协作');
  if (profile.can_operations || profile.can_firm_operations) actions.push('事务所运营视图');
  if (!actions.length) actions.push('读取当前授权范围');
  return {
    label: copy.name,
    focus: focus[profile.role] ?? focus.viewer,
    actions,
    boundary: '这里只显示当前授权能力；切换企业、直接打开链接和批量操作仍由服务端逐次校验。',
  };
}
