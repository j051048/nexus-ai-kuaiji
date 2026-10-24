import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AgentNavigation } from './AgentNavigation';
import { primaryNav, roleHome } from './workspace-presentation';
import { navigationCurrent } from './workspace-navigation';
import type { WorkspaceProfile } from './workspace-types';

const profile: WorkspaceProfile = {
  role: 'accountant',
  display_role: 'firm_owner',
  can_submit: true,
  can_approve: false,
  can_finance: false,
  can_review: true,
  can_accounting: true,
  can_manage: true,
  can_firm_operations: true,
  companies: [],
  book_count: 0,
};

function render(current = 'firm', extra: Partial<Parameters<typeof AgentNavigation>[0]> = {}) {
  return renderToStaticMarkup(
    createElement(AgentNavigation, {
      session: {
        token: 'offline',
        mode: 'local',
        firm_name: '虚构所',
        user_name: '虚构管理员',
        firm_id: 'firm-a',
        user_id: 'user-a',
        role: 'owner',
        scope_key: 'offline-a',
      },
      current,
      canAccounting: true,
      canMonthly: true,
      canReview: true,
      canOperations: true,
      canQuality: true,
      canProcessing: true,
      locked: false,
      firmSelector: null,
      footer: null,
      presentationNav: primaryNav('firm_owner', profile),
      onAdmin: () => {},
      onNavigate: () => {},
      onAccountingFilter: () => {},
      ...extra,
    }),
  );
}

describe('第二批岗位主任务与设置归位', () => {
  it('V04 当前页从同一注册表解析，修订及旧服务单别名不落到其他页面', () => {
    expect(navigationCurrent('revision')).toBe('revision');
    expect(navigationCurrent('service')).toBe('monthly');
    expect(navigationCurrent('accounting', true)).toBe('business');
    expect(navigationCurrent('accounting', false)).toBe('accounting');
    for (const view of ['ingestion', 'observability', 'operations', 'runs', 'quality']) {
      expect(navigationCurrent(view)).toBe(view);
    }
    expect(navigationCurrent('__proto__')).toBe('accounting');
    expect(navigationCurrent('future_unknown_route')).toBe('accounting');
  });
  it('V04 会计主任务最多五项，周期资料仍可从日常业务找到', () => {
    const accountant = { ...profile, display_role: 'accountant' as const };
    const items = primaryNav('accountant', accountant);
    expect(items.length).toBeLessThanOrEqual(5);
    const html = render('home', { presentationNav: items });
    expect(html).toContain('资料清单');
    expect(html).toContain('审核与导出');
  });
  it('V04 设置按人员、费用、业务处理和技术诊断分组，保留原入口', () => {
    const html = render('firm', { onAccess: () => {} });
    const group = (label: string) =>
      html.match(new RegExp(`<section[^>]*aria-label="${label}"[\\s\\S]*?<\\/section>`))?.[0] ?? '';
    expect(group('人员与授权')).toContain('企业与人员管理');
    expect(group('人员与授权')).toContain('成员与账套授权');
    expect(group('费用与人工投入')).toContain('费用与运行监控');
    expect(group('费用与人工投入')).toContain('客户人工投入');
    expect(group('资料处理与接入')).toContain('票据核对异常');
    expect(group('资料处理与接入')).toContain('票据处理任务');
    expect(group('技术诊断')).toContain('运行可观测性');
    expect(group('技术诊断')).toContain('试点评测');
    expect(group('技术诊断')).not.toContain('票据核对异常');
  });
  it('V04 显式空主导航不回退到另一套菜单', () => {
    const html = render('firm', { presentationNav: [] });
    expect(html.match(/agent-primary-item/g) ?? []).toHaveLength(0);
  });
  it('V04 业务能力撤销后主次菜单都不出现可办理事项入口', () => {
    const html = render('home', {
      presentationNav: primaryNav('finance'),
      canBusiness: false,
      canAccounting: false,
      canMonthly: false,
      canReview: false,
      canOperations: false,
      canProcessing: false,
      canQuality: false,
      onAdmin: undefined,
      onAccess: undefined,
    });
    expect(html).not.toContain('aria-label="业务事项"');
    expect(html).not.toContain('aria-label="会计工作台"');
    expect(html).not.toContain('aria-label="全所概览"');
    expect(html).toContain('资料清单');
  });
  it('事务所负责人和管理员主导航聚焦四项业务，不含技术和管理后台', () => {
    const ids = ['firm', 'accounting', 'monthly', 'cases'];
    expect(primaryNav('firm_owner', profile).map((item) => item.id)).toEqual(ids);
    expect(
      primaryNav('admin', { ...profile, display_role: 'admin' }).map((item) => item.id),
    ).toEqual(ids);
  });
  it('只有具备全所权限的管理员默认进入全所概览', () => {
    expect(roleHome('admin', { ...profile, display_role: 'admin' }).defaultView).toBe('firm');
    expect(roleHome('admin', { ...profile, can_firm_operations: false }).defaultView).toBe('admin');
    expect(
      primaryNav('admin', { ...profile, can_firm_operations: false }).some(
        (item) => item.id === 'firm',
      ),
    ).toBe(false);
  });
  it('技术入口集中在有名称的设置中，保留原业务和会计处理入口', () => {
    const html = render();
    const settings = html.match(/<details[^>]*aria-label="设置"[\s\S]*?<\/details>/)?.[0] ?? '';
    expect(settings).toContain('数据接入');
    expect(settings).toContain('运行可观测性');
    expect(settings).toContain('费用与运行监控');
    expect(settings).toContain('票据处理任务');
    expect(settings).toContain('后台运行');
    expect(settings).toContain('试点评测');
    expect(settings).toContain('企业与人员管理');
    expect(settings).not.toContain('异常与补件');
    expect(html).toContain('事实修订与调整');
    expect(html).toContain('审核与导出');
    expect(html).not.toContain('更多工具');
    expect(html).not.toContain('处理记录与管理');
  });
  it.each([
    'firm',
    'accounting',
    'monthly',
    'cases',
    'revision',
    'ingestion',
    'observability',
    'operations',
    'queue',
    'runs',
    'quality',
  ])('%s 深链接恰好有一个导航选中', (current) => {
    const html = render(current);
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    if (
      ['ingestion', 'observability', 'operations', 'queue', 'runs', 'quality'].includes(current)
    ) {
      expect(html).toMatch(/<details[^>]*aria-label="设置"[^>]*open=""/);
    }
  });
  it('企业管理兼任账套权限不产生全所或技术入口，授权的专业业务可发现', () => {
    const client = {
      ...profile,
      role: 'manager' as const,
      display_role: 'manager' as const,
      can_firm_operations: false,
    };
    const html = render('home', {
      presentationNav: primaryNav('manager', client),
      canOperations: false,
      canQuality: false,
      onAdmin: undefined,
    });
    for (const label of [
      '全所概览',
      '数据接入',
      '运行可观测性',
      '费用与运行监控',
      '试点评测',
      '企业与人员管理',
    ]) {
      expect(html).not.toContain(label);
    }
    expect(html).toContain('审核与导出');
    expect(html).toContain('异常与补件');
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  });
  it('锁定期间管理后台入口与其他操作一起禁用', () => {
    const html = render('firm', { locked: true });
    expect(html).toMatch(/<button[^>]*aria-label="企业与人员管理"[^>]*disabled=""/);
  });
  it('成员授权仅在提供真实权限入口时归入设置，锁定时禁用', () => {
    expect(render()).not.toContain('成员与账套授权');
    const enabled = render('firm', { onAccess: () => {} });
    expect(enabled.match(/<details[^>]*aria-label="设置"[\s\S]*?<\/details>/)?.[0]).toContain(
      '成员与账套授权',
    );
    const locked = render('firm', { onAccess: () => {}, locked: true });
    expect(locked).toMatch(/<button[^>]*aria-label="成员与账套授权"[^>]*disabled=""/);
  });
});
