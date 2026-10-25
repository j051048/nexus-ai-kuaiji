import { describe, expect, it } from 'vitest';
import { emptyWorkspaceRoute } from './workspace-route';
import {
  homeProgress,
  WorkspaceProgress,
  type HomeProgress,
  type MonthlyProgress,
} from './workspace-progress';

const view: HomeProgress = {
  bucket: 'waiting',
  kind: 'business',
  companyId: 'company-a',
  offset: 20,
  sort: 'updated',
  density: 'table',
  query: '本期查询',
  optionsOpen: true,
};
describe('V06 办理进度只保留本会话的选择，不缓存账务事实', () => {
  it('保留筛选页码和离开时位置，但不同企业不继承旧选择', () => {
    const state = new WorkspaceProgress('user-a');
    state.saveHome(view);
    state.capture({ ...emptyWorkspaceRoute, companyId: 'company-a' }, 360, 'business:a');
    expect(state.getHome('company-a')).toMatchObject({
      ...view,
      scrollY: 360,
      taskKey: 'business:a',
    });
    expect(state.getHome('company-b')).toMatchObject({
      companyId: 'company-b',
      offset: 0,
      kind: '',
      query: '',
    });
  });
  it('新身份没有上一账号的进度，读取副本不能修改内部选择', () => {
    const a = new WorkspaceProgress('a');
    a.saveHome(view);
    const copy = a.getHome('company-a');
    copy.offset = 80;
    expect(a.getHome('company-a').offset).toBe(20);
    expect(new WorkspaceProgress('b').getHome('company-a').query).toBe('');
  });
  it('月度返回保留原月份搜索，换月不沿用旧页码', () => {
    const state = new WorkspaceProgress('a');
    state.saveMonthly({ period: '2026-10', query: '查询', search: '查询', offset: 20 });
    expect(state.getMonthly('2026-10')).toMatchObject({ search: '查询', offset: 20 });
    expect(state.getMonthly('2026-11')).toBeUndefined();
  });
  it('多步办理按原记录逐级返回，浏览器返回也消耗相应上游点', () => {
    const state = new WorkspaceProgress('a');
    state.capture({ ...emptyWorkspaceRoute, view: 'monthly', period: '2026-10' }, 0);
    state.capture(
      {
        ...emptyWorkspaceRoute,
        view: 'service',
        bookId: 'book-a',
        companyId: 'a',
        period: '2026-10',
      },
      0,
    );
    expect(state.takeOrigin()?.label).toBe('返回本月服务单');
    expect(state.origin()?.label).toBe('返回客户月度进度');
    state.arrived({ ...emptyWorkspaceRoute, view: 'monthly', period: '2026-10' });
    expect(state.origin()).toBeUndefined();
  });
  it('只保存白名单字段，不保存传入的附件、金额或凭据', () => {
    const state = new WorkspaceProgress('a');
    state.saveHome({
      ...view,
      token: 'secret',
      amount: '900',
      attachments: ['original'],
    } as HomeProgress);
    const saved = JSON.stringify(state.getHome('company-a'));
    expect(saved).not.toMatch(/secret|900|attachments/);
    state.capture(
      {
        ...emptyWorkspaceRoute,
        view: 'business',
        eventId: 'event-a',
        token: 'secret',
      } as typeof emptyWorkspaceRoute,
      0,
    );
    expect(JSON.stringify(state.origin())).not.toContain('secret');
  });
});

describe('V08 进度字段只接纳声明类型，不强制转换异常值', () => {
  it.each([undefined, null, 120, false, {}, ['查询']])('异常查询 %j 回退为空字符串', (query) => {
    expect(homeProgress({ ...view, query } as HomeProgress, 'company-a').query).toBe('');
  });

  it('枚举、布尔、焦点和数值异常时不保留对象或错误开关', () => {
    const result = homeProgress(
      {
        ...view,
        kind: ['business'],
        sort: ['updated'],
        optionsOpen: 'false',
        taskKey: { token: 'must-not-be-retained' },
        offset: '20',
        scrollY: '360',
      } as unknown as HomeProgress,
      'company-a',
    );
    expect(result).toMatchObject({
      kind: '',
      sort: 'priority',
      optionsOpen: false,
      taskKey: undefined,
      offset: 0,
      scrollY: 0,
    });
    expect(JSON.stringify(result)).not.toContain('must-not-be-retained');
  });

  it.each([undefined, null, false, 42, {}, []])('非法公司范围 %j 不与默认范围碰撞', (companyId) => {
    const state = new WorkspaceProgress('a');
    state.saveHome({ ...view, companyId: '', query: '全所筛选' });
    const invalid = companyId as string;
    // An omitted argument is the existing, explicit default scope contract.
    if (companyId === undefined) {
      expect(state.getHome()).toMatchObject({ companyId: '', query: '全所筛选' });
      return;
    }
    expect(state.getHome(invalid)).toMatchObject({ companyId: '', query: '', offset: 0 });
    state.saveHome({ ...view, companyId: invalid });
    expect(state.getHome()).toMatchObject({ companyId: '', query: '', offset: 0 });
  });

  it.each([null, undefined, false, 42, []])('非法首页进度 %j 不抛异常也不保留旧记录', (value) => {
    const state = new WorkspaceProgress('a');
    state.saveHome(view);
    expect(() => state.saveHome(value as unknown as HomeProgress)).not.toThrow();
    expect(state.getHome('company-a').query).toBe('');
  });

  it('字符串有长度界限，标识符超长或含控制字符时不截断为另一个身份', () => {
    const result = homeProgress(
      {
        ...view,
        query: '问'.repeat(121),
        taskKey: 'x'.repeat(201),
        offset: 200000,
        scrollY: 20000000,
      },
      'company-a',
    );
    expect(result).toMatchObject({
      query: '问'.repeat(120),
      taskKey: undefined,
      offset: 100000,
      scrollY: 10000000,
    });
    for (const companyId of ['x'.repeat(201), 'company-a\n']) {
      expect(homeProgress({ ...view, companyId }, companyId)).toMatchObject({
        companyId: '',
        query: '',
        offset: 0,
      });
    }
  });

  it.each([NaN, Infinity, -1, true, '20'])('偏移和滚动位置 %j 不被强制转换', (value) => {
    expect(
      homeProgress({ ...view, offset: value, scrollY: value } as HomeProgress, 'company-a'),
    ).toMatchObject({ offset: 0, scrollY: 0 });
  });

  it('月度查询及搜索字段异常时使用空值，合法月份及页码仍可恢复', () => {
    const state = new WorkspaceProgress('a');
    expect(() =>
      state.saveMonthly({
        period: '2026-10',
        query: null,
        search: 123,
        offset: '20',
      } as unknown as MonthlyProgress),
    ).not.toThrow();
    expect(state.getMonthly('2026-10')).toMatchObject({
      period: '2026-10',
      query: '',
      search: '',
      offset: 0,
    });
    state.saveMonthly({
      period: '2026-10',
      query: 'q'.repeat(130),
      search: 's'.repeat(130),
      offset: 20,
    });
    const copy = state.getMonthly('2026-10')!;
    expect(copy).toMatchObject({ query: 'q'.repeat(120), search: 's'.repeat(120), offset: 20 });
    copy.search = '外部修改';
    expect(state.getMonthly('2026-10')?.search).toBe('s'.repeat(120));
  });

  it.each([undefined, null, false, 202610, '', '2026-00', '2026-13', '2026-1', '2026-10-01'])(
    '非法月份 %j 不产生可恢复进度',
    (period) => {
      const state = new WorkspaceProgress('a');
      expect(state.getMonthly(period as string)).toBeUndefined();
      state.saveMonthly({ period: '2026-10', query: '旧查询', search: '旧查询', offset: 20 });
      state.saveMonthly({
        period,
        query: '新查询',
        search: '新查询',
        offset: 20,
      } as MonthlyProgress);
      expect(state.getMonthly(period as string)).toBeUndefined();
      expect(state.getMonthly('2026-10')).toBeUndefined();
    },
  );

  it.each([null, undefined, false, 42, []])('非法月度进度 %j 不抛异常也不保留旧记录', (value) => {
    const state = new WorkspaceProgress('a');
    state.saveMonthly({ period: '2026-10', query: '旧查询', search: '旧查询', offset: 20 });
    expect(() => state.saveMonthly(value as unknown as MonthlyProgress)).not.toThrow();
    expect(state.getMonthly('2026-10')).toBeUndefined();
  });

  it('捕获位置拒绝异常焦点标识和滚动值，正常焦点仍保持', () => {
    const state = new WorkspaceProgress('a');
    state.saveHome(view);
    const route = { ...emptyWorkspaceRoute, companyId: 'company-a' };
    state.capture(route, 120, 'business:a');
    expect(state.getHome('company-a')).toMatchObject({ scrollY: 120, taskKey: 'business:a' });
    state.capture(route, '120' as unknown as number, { ref: 'secret' } as unknown as string);
    expect(state.getHome('company-a')).toMatchObject({ scrollY: 0, taskKey: undefined });
    state.capture(route, 120, 'business:a\n');
    expect(state.getHome('company-a').taskKey).toBeUndefined();
  });
});
