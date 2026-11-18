import { ApiError, errorMessage } from './api';

/**
 * 待办对象在打开前会重新读取。服务端不再返回该对象时，必须把
 * "已办完、不可见、被撤销"与普通网络/服务器错误区分开，避免用户
 * 误以为刷新即可恢复，或把空队列当成已完成。
 */
export function taskStale(error: unknown): boolean {
  return error instanceof Error && error.message === '待办内容已更新，请刷新列表后重新打开。';
}

export function taskOpenErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return '待办对象不存在、已办完或当前账号不可见，已从当前列表移除。';
    }
    if (error.status === 403) {
      return '当前账号已无权访问该待办，已从当前列表移除。';
    }
  }
  return errorMessage(error);
}
