import { ApiError } from './api';
import type { BusinessScope } from './business-types';

export type ReadRecovery = { action: 'none' | 'permissions' | 'reload'; next: string };

/** Only for failed reads. It must never be used to resend mutations. */
export function readRecovery(failure: unknown): ReadRecovery {
  if (failure instanceof ApiError) {
    if (failure.status === 401)
      return { action: 'none', next: '登录已失效，请通过现有登录页面重新登录后再继续。' };
    if (failure.status === 403 && failure.code === 'admin_mfa_required')
      return { action: 'none', next: '请先在现有安全提示中完成二次验证，再读取资料。' };
    if (failure.status === 403)
      return {
        action: 'permissions',
        next: '请联系本企业或事务所管理员确认授权，再重新读取权限；无需反复登录。',
      };
    if (failure.status === 429)
      return { action: 'reload', next: '当前请求较多，请稍后手动重新读取；页面不会自动重复请求。' };
    if (failure.status === 404)
      return {
        action: 'reload',
        next: '记录可能已变更或不在当前授权范围，请重新读取清单并选择当前任务。',
      };
  }
  return { action: 'reload', next: '资料暂未读取成功，不代表清单为空。检查网络后可手动重新读取。' };
}

export function businessListEmpty(scope: BusinessScope, status: string) {
  if (scope === 'company' && status === 'department_approved')
    return {
      title: '目前没有待你核验的事项',
      text: '此处仅显示待你核验的事项。仍在等待部门审批的资料尚未进入此清单，可查看全部状态了解进度。',
      action: 'all-states' as const,
    };
  if (status)
    return {
      title: '当前筛选下没有业务事项',
      text: '当前处理状态下没有匹配记录，可查看全部状态；企业和授权范围保持不变。',
      action: 'all-states' as const,
    };
  return {
    title: '当前范围还没有业务事项',
    text:
      scope === 'own'
        ? '选择报销、采购费用或日常收支模板，上传原件并核对信息后提交；已有资料请先核对企业和状态筛选。'
        : '当前企业与授权范围内暂无匹配事项；如预期有资料，请联系资料负责人确认提交进度。',
    action: 'none' as const,
  };
}
