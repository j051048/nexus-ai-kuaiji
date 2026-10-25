import { describe, expect, it } from 'vitest';
import { roleHomeGuide } from './workspace-role-home';
import type { WorkspaceProfile } from './workspace-types';

const profile = (changes: Partial<WorkspaceProfile>): WorkspaceProfile => ({
  role: 'employee',
  can_submit: false,
  can_approve: false,
  can_finance: false,
  can_review: false,
  can_accounting: false,
  can_manage: false,
  companies: [],
  book_count: 0,
  ...changes,
});

describe('V07 岗位入门复用主身份，不把额外权限变成新的主任务', () => {
  it('员工有可执行的第一步、交接对象和虚构示例，不把提交说成记账完成', () => {
    const guide = roleHomeGuide(profile({ can_submit: true }));
    expect(guide.firstStep).toContain('上传');
    expect(guide.handoff).toContain('审批');
    expect(guide.example).toContain('例如');
    expect(guide.example).not.toMatch(/已入账|记账完成|自动报税/);
  });
  it('财务兼任复核仍先解释核验和交接，不把首页变成会计作业', () => {
    const guide = roleHomeGuide(
      profile({
        display_role: 'finance',
        can_finance: true,
        can_review: true,
        can_accounting: true,
      }),
    );
    expect(guide.label).toBe('财务核验');
    expect(guide.firstStep).toContain('核对');
    expect(guide.handoff).toContain('会计');
    expect(guide.firstStep).not.toContain('批量复核');
  });
  it('会计解释原件、草稿与审核后导出，负责人先安排客户与期限', () => {
    const accountant = roleHomeGuide(profile({ display_role: 'accountant', can_accounting: true }));
    expect(accountant.firstStep).toContain('原件');
    expect(accountant.handoff).toContain('复核');
    const owner = roleHomeGuide(profile({ display_role: 'firm_owner', can_firm_operations: true }));
    expect(owner.firstStep).toContain('客户');
    expect(owner.handoff).toContain('负责人');
  });
  it('岗位名与能力错配时解释联系管理员，不指导执行没有的权限', () => {
    const guide = roleHomeGuide(profile({ display_role: 'finance' }));
    expect(guide.firstStep).toContain('管理员');
    expect(guide.handoff).not.toMatch(/点击.*核验|点击.*导出/);
  });
  it('只读用户只得到只读指引，输入的企业名和金额不进入静态示例', () => {
    const current = profile({
      display_role: 'viewer',
      companies: [{ id: 'private', name: '不得出现在示例中' }],
    });
    const guide = roleHomeGuide(current);
    expect(guide.firstStep).toContain('查看');
    expect(guide.handoff).toContain('负责人');
    expect(guide.example).not.toContain('不得出现在示例中');
  });
});
