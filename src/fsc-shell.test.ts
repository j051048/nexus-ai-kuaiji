import { describe, expect, it } from 'vitest';
import { fscRoleProfile } from './fsc-shell';
import type { PresentationRole } from './workspace-types';

const allRoles: PresentationRole[] = [
  'firm_owner',
  'admin',
  'reviewer',
  'accountant',
  'finance',
  'manager',
  'approver',
  'employee',
  'viewer',
];

describe('第一期：集团共享财务中心岗位职责与权限边界契约', () => {
  it('每个岗位均具备大白话职责说明、数据权限徽章及 4 条高频提问气泡', () => {
    for (const role of allRoles) {
      const profile = fscRoleProfile(role);
      expect(profile.title).toBeTruthy();
      expect(profile.dutySummary.length).toBeGreaterThan(10);
      expect(profile.scopeBadge).toBeTruthy();
      expect(profile.scopeDetail).toBeTruthy();
      expect(profile.quickPrompts).toHaveLength(4);
    }
  });

  it('严格区分集团领导、分管会计、子公司负责人与普通员工的数据查询范围边界', () => {
    const ownerAll = fscRoleProfile('firm_owner', { companyCount: 5 });
    expect(ownerAll.scopeBadge).toContain('全集团 5 家子公司');

    const ownerSingle = fscRoleProfile('firm_owner', {
      companyName: '安达出租车公司',
      companyCount: 5,
    });
    expect(ownerSingle.scopeBadge).toContain('安达出租车公司');
    expect(ownerSingle.scopeBadge).toContain('全集团权限');

    const accountant = fscRoleProfile('accountant', {
      companyName: '星河广告公司',
      companyCount: 2,
    });
    expect(accountant.scopeBadge).toBe('当前子公司：星河广告公司');
    expect(accountant.scopeBadge).not.toContain('全集团');

    const employee = fscRoleProfile('employee', {
      companyName: '安达出租车公司',
      companyCount: 1,
    });
    expect(employee.scopeBadge).toBe('仅限本人提交的单据');
    expect(employee.scopeDetail).toContain('看不到他人');
  });
});
