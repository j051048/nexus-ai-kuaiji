import { describe, expect, it } from 'vitest';
import { workspaceExperience } from './workspace-experience';
import type { WorkspaceProfile } from './workspace-types';

const employee: WorkspaceProfile = {
  role: 'employee',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: false,
  companies: [{ id: 'one', name: '虚构企业' }],
  book_count: 0,
};

describe('角色工作入口', () => {
  it('未读到当前角色时不推测拥有后台能力', () => {
    expect(workspaceExperience(null).processing).toBe(false);
  });
  it('员工与审批人聚焦自己的事项，不显示技术处理入口', () => {
    expect(workspaceExperience(employee)).toMatchObject({
      processing: false,
      simple: true,
      businessLabel: '我的提交',
      kinds: ['intake', 'business', 'case', 'material'],
    });
    expect(workspaceExperience({ ...employee, role: 'approver', can_approve: true })).toMatchObject(
      { processing: false, businessLabel: '部门事项' },
    );
  });
  it('兼任能力来自当前服务端能力位，角色标题不遮蔽合法工作', () => {
    expect(workspaceExperience({ ...employee, can_finance: true }).processing).toBe(true);
    expect(workspaceExperience({ ...employee, can_accounting: true }).kinds).toBeNull();
    expect(workspaceExperience({ ...employee, can_manage: true }).processing).toBe(true);
    expect(workspaceExperience({ ...employee, role: 'accountant' }).processing).toBe(false);
  });
});
