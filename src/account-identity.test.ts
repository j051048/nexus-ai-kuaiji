import { describe, expect, it } from 'vitest';
import { accountIdentity } from './account-identity';
import type { Me, Session } from './types';
import type { PresentationRole, WorkspaceProfile } from './workspace-types';

const session: Session = {
  token: 'local-fixture',
  mode: 'local',
  firm_name: '示例事务所',
  user_name: '示例人员',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'operator',
  scope_key: 'scope-a',
};
function profile(patch: Partial<WorkspaceProfile> = {}): WorkspaceProfile {
  return {
    role: 'employee',
    display_role: 'employee',
    can_submit: false,
    can_approve: false,
    can_finance: false,
    can_review: false,
    can_accounting: false,
    can_manage: false,
    companies: [{ id: 'company-a', name: '示例企业' }],
    book_count: 0,
    ...patch,
  };
}
function me(patch: Partial<Me> = {}): Me {
  return {
    user: { id: session.user_id, name: session.user_name, email: 'fixture@example.invalid' },
    firms: [{ id: 'firm-a', name: '示例事务所', role: 'operator' }],
    current_firm_id: 'firm-a',
    permissions: {
      manage_firm: false,
      manage_admin: false,
      create_company: false,
      create_book: false,
    },
    mode: 'local',
    ...patch,
  };
}
const roleLabels: [PresentationRole, string][] = [
  ['employee', '企业员工'],
  ['approver', '部门审批人'],
  ['finance', '企业财务'],
  ['manager', '企业管理人员'],
  ['accountant', '经办会计'],
  ['reviewer', '复核会计'],
  ['firm_owner', '事务所负责人'],
  ['admin', '事务所管理员'],
  ['viewer', '只读成员'],
];

describe('account identity presentation without permission escalation', () => {
  it.each(roleLabels)('keeps the server primary job %s as %s', (display_role, label) => {
    const value = accountIdentity({ session, profile: profile({ display_role }) });
    expect(value).toMatchObject({
      name: '示例人员',
      role: label,
      firm: '示例事务所',
      confirmed: true,
    });
    expect(value.scope).toBe('仅限获授权的企业、部门及账套');
  });

  it('does not turn additional review authority into a finance primary job change', () => {
    const value = accountIdentity({
      session,
      profile: profile({
        display_role: 'finance',
        can_finance: true,
        can_review: true,
        can_accounting: true,
      }),
    });
    expect(value.role).toBe('企业财务');
    expect(value.capabilities).toEqual(['核验获授权企业资料', '查看获授权账套', '复核获授权账套']);
    expect(value.scope).not.toContain('全部');
  });

  it('does not infer posting, review or export from book visibility', () => {
    const value = accountIdentity({
      session,
      profile: profile({ can_accounting: true, book_count: 20 }),
    });
    expect(value.capabilities).toEqual(['查看获授权账套']);
    expect(value.capabilities.join()).not.toMatch(/记账|审核|复核|导出/);
  });

  it('names all additive capabilities with their bounded resources', () => {
    const value = accountIdentity({
      session,
      profile: profile({
        can_submit: true,
        can_approve: true,
        can_finance: true,
        can_accounting: true,
        can_review: true,
        can_manage: true,
      }),
    });
    expect(value.capabilities).toEqual([
      '提交本人业务',
      '审批获授权部门事项',
      '核验获授权企业资料',
      '查看获授权账套',
      '复核获授权账套',
      '管理获授权企业与人员',
    ]);
    expect(value.role).toBe('企业员工');
  });

  it('does not mislabel legacy firm admin fallback as firm owner', () => {
    expect(
      accountIdentity({
        session: { ...session, role: 'admin' },
        profile: profile({
          role: 'accountant',
          display_role: undefined,
          can_firm_operations: true,
        }),
      }).role,
    ).toBe('事务所管理员');
  });

  it('requires matched live me permissions for all-firm scope', () => {
    const owner = me({
      firms: [{ id: 'firm-a', name: '示例事务所', role: 'owner' }],
      permissions: {
        manage_firm: true,
        manage_admin: true,
        create_company: true,
        create_book: true,
      },
    });
    expect(
      accountIdentity({ session, profile: profile({ display_role: 'firm_owner' }), me: owner })
        .scope,
    ).toBe('本事务所全部企业（不含其他事务所）');
    expect(
      accountIdentity({
        session: { ...session, role: 'owner' },
        profile: profile({ can_firm_operations: true }),
      }).scope,
    ).toBe('仅限获授权的企业、部门及账套');
  });

  it('waits for the profile instead of guessing a customer primary job from session role', () => {
    expect(accountIdentity({ session: { ...session, role: 'client_employee' } })).toMatchObject({
      role: '身份确认中',
      confirmed: false,
      capabilities: [],
    });
  });

  it('clears old capabilities and scope after a denied or unavailable authorization read', () => {
    const value = accountIdentity({
      session,
      profile: profile({ display_role: 'admin', can_manage: true, can_review: true }),
      me: me({ permissions: { manage_firm: true, create_company: true, create_book: true } }),
      unavailable: true,
    });
    expect(value).toMatchObject({ role: '权限待重新确认', confirmed: false, capabilities: [] });
    expect(value.scope).not.toContain('全部');
  });

  it.each([
    me({ user: { id: 'other-user', name: 'other', email: 'other@example.invalid' } }),
    me({ current_firm_id: 'firm-b' }),
    me({ firms: [{ id: 'firm-b', name: '其他事务所', role: 'owner' }] }),
  ])('rejects identity/firm mismatches rather than retaining capabilities', (identity) => {
    for (const administration of [false, true]) {
      const value = accountIdentity({
        session,
        profile: profile({ can_manage: true }),
        me: identity,
        administration,
      });
      expect(value).toMatchObject({ role: '身份确认中', confirmed: false, capabilities: [] });
      expect(value.scope).not.toContain('全部');
    }
  });

  it('does not treat unknown role strings as authorized read-only roles', () => {
    const malformed = {
      ...profile(),
      role: 'unexpected',
      display_role: 'unexpected',
    } as unknown as WorkspaceProfile;
    expect(accountIdentity({ session, profile: malformed })).toMatchObject({
      role: '身份确认中',
      confirmed: false,
      capabilities: [],
    });
  });

  it('accepts only true capability flags, not truthy transport values', () => {
    const malformed = {
      ...profile(),
      can_review: 'yes',
      can_accounting: 1,
      can_manage: 'true',
    } as unknown as WorkspaceProfile;
    expect(accountIdentity({ session, profile: malformed }).capabilities).toEqual([]);
  });

  it('never identifies a platform super-admin by account email or user name', () => {
    const value = accountIdentity({
      session: { ...session, user_name: 'super-admin' },
      me: me({
        user: { id: session.user_id, name: 'super-admin', email: 'owner@example.invalid' },
      }),
      profile: profile(),
    });
    expect(value.role).toBe('企业员工');
    expect(value.role + value.scope + value.capabilities.join()).not.toMatch(/超级|平台/);
  });
});

describe('administration identity uses current membership and management scope', () => {
  it('keeps an enterprise manager job when firm membership is otherwise read-only', () => {
    const identity = me({
      firms: [{ id: 'firm-a', name: '示例事务所', role: 'viewer' }],
      permissions: {
        manage_firm: false,
        manage_admin: true,
        create_company: false,
        create_book: false,
      },
    });
    const workspace = accountIdentity({
      session,
      profile: profile({ display_role: 'manager', can_manage: true }),
      me: identity,
    });
    const admin = accountIdentity({ session, me: identity, administration: true });
    expect(admin.role).toBe(workspace.role);
    expect(admin.role).toBe('企业管理人员');
    expect(admin.scope).toBe('仅限获授权的企业、部门及账套');
  });

  it.each([
    ['owner', '事务所负责人'],
    ['admin', '事务所管理员'],
  ] as const)('shows %s using a matched current firm', (role, label) => {
    const identity = me({
      firms: [{ id: 'firm-a', name: '示例事务所', role }],
      permissions: {
        manage_firm: true,
        manage_admin: true,
        create_company: true,
        create_book: true,
      },
    });
    expect(
      accountIdentity({ session: { ...session, role }, me: identity, administration: true }),
    ).toMatchObject({
      role: label,
      confirmed: true,
      scope: '本事务所全部企业（不含其他事务所）',
      capabilities: ['管理获授权企业与人员'],
    });
  });

  it('shows client admin as enterprise management without all-firm access', () => {
    const identity = me({
      firms: [{ id: 'firm-a', name: '示例事务所', role: 'client_employee' }],
      permissions: {
        manage_firm: false,
        manage_admin: true,
        create_company: false,
        create_book: false,
      },
    });
    expect(accountIdentity({ session, me: identity, administration: true })).toMatchObject({
      role: '企业管理人员',
      confirmed: true,
      scope: '仅限获授权的企业、部门及账套',
      capabilities: ['管理获授权企业与人员'],
    });
  });

  it.each([
    ['operator', '经办会计'],
    ['reviewer', '复核会计'],
    ['viewer', '只读成员'],
    ['client_member', '企业成员'],
    ['client_employee', '企业成员'],
  ] as const)('shows a known %s with a clear lack of admin permission', (role, label) => {
    const identity = me({ firms: [{ id: 'firm-a', name: '示例事务所', role }] });
    expect(accountIdentity({ session, me: identity, administration: true })).toMatchObject({
      role: label,
      confirmed: true,
      scope: '当前账号没有管理后台权限',
      capabilities: [],
    });
  });

  it('does not mistake missing me for confirmed management identity', () => {
    expect(
      accountIdentity({ session: { ...session, role: 'owner' }, administration: true }),
    ).toMatchObject({ role: '身份确认中', confirmed: false, capabilities: [] });
  });
});
