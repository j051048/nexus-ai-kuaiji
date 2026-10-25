import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { businessListEmpty, readRecovery } from './workspace-recovery';

describe('V07 读取失败的下一步沿用身份边界', () => {
  it('503和网络读取失败可手动重新读取，不说资料为空或已完成', () => {
    for (const failure of [new ApiError('服务暂不可用', 503), new TypeError('Failed to fetch')]) {
      const hint = readRecovery(failure);
      expect(hint.action).toBe('reload');
      expect(hint.next).toContain('重新读取');
      expect(hint.next).not.toMatch(/已完成|没有资料|自动重试/);
    }
  });
  it('登录失效、二次验证和权限不足互不替代', () => {
    expect(readRecovery(new ApiError('登录失效', 401)).action).toBe('none');
    const mfa = readRecovery(new ApiError('需二次验证', 403, 'admin_mfa_required'));
    expect(mfa.action).toBe('none');
    expect(mfa.next).toContain('二次验证');
    const denied = readRecovery(new ApiError('权限不足', 403));
    expect(denied.action).toBe('permissions');
    expect(denied.next).toContain('管理员');
    expect(denied.next).not.toContain('重新登录');
  });
  it('限流仅提示稍后手动读取，不更改权限或自动重试', () => {
    const hint = readRecovery(new ApiError('too many', 429));
    expect(hint.next).toContain('稍后');
    expect(hint.action).toBe('reload');
    expect(readRecovery(new Error('text says 403')).action).toBe('reload');
  });
});

describe('V07 空态描述实际筛选范围', () => {
  it('财务默认空态解释上游部门审批，允许仅清状态', () => {
    const empty = businessListEmpty('company', 'department_approved');
    expect(empty.title).toContain('核验');
    expect(empty.text).toContain('部门审批');
    expect(empty.action).toBe('all-states');
  });
  it('无筛选的空清单不宣称已完成审核或交付', () => {
    for (const scope of ['own', 'department', 'company', 'accounting'] as const) {
      const empty = businessListEmpty(scope, '');
      expect(empty.action).toBe('none');
      expect(empty.text).not.toMatch(/已完成|已交付|全部通过/);
    }
  });
});
