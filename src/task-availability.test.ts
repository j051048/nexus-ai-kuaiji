import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { taskOpenErrorMessage } from './task-availability';

describe('task open availability', () => {
  it('explains a missing or completed task without implying a retry will restore it', () => {
    expect(taskOpenErrorMessage(new ApiError('not found', 404))).toBe(
      '待办对象不存在、已办完或当前账号不可见，已从当前列表移除。',
    );
  });

  it('explains revoked access separately', () => {
    expect(taskOpenErrorMessage(new ApiError('forbidden', 403))).toBe(
      '当前账号已无权访问该待办，已从当前列表移除。',
    );
  });

  it('keeps server and network errors distinguishable', () => {
    expect(taskOpenErrorMessage(new ApiError('服务器暂时不可用', 500))).toBe('服务器暂时不可用');
    expect(taskOpenErrorMessage(new Error('网络中断'))).toBe('网络中断');
  });
});
