import type { WorkspaceProfile } from './workspace-types';
import { presentationRole, roleHome } from './workspace-presentation';

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
  const home = roleHome(presentationRole(profile));
  const actions: string[] = [];
  if (profile.can_submit) actions.push('提交本人事项与补件');
  if (profile.can_approve) actions.push('处理部门审批');
  if (profile.can_finance) actions.push('财务核验与移交');
  if (profile.can_review) actions.push('凭证复核与审核后导出');
  if (profile.can_accounting) actions.push('进入当前授权账套');
  if (profile.can_manage) actions.push('成员与服务单协作');
  if (profile.can_firm_operations) actions.push('事务所运营视图');
  if (!actions.length) actions.push('读取当前授权范围');
  return {
    label: home.name,
    focus: home.focus,
    actions,
    boundary: '这里只显示当前授权能力；切换企业、直接打开链接和批量操作仍由服务端逐次校验。',
  };
}
