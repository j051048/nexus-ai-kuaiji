import { describe, expect, it } from 'vitest';
import { WorkspaceProgress } from './workspace-progress';
import { emptyWorkspaceRoute, type WorkspaceRoute } from './workspace-route';

const home = { ...emptyWorkspaceRoute, companyId: 'company-a' };
const business: WorkspaceRoute = {
  ...home,
  view: 'business',
  eventId: 'event-a',
  period: '2026-10',
};
const revision: WorkspaceRoute = { ...business, view: 'revision' };
const visit = (
  state: WorkspaceProgress,
  route: WorkspaceRoute,
  key: string,
  action: 'POP' | 'PUSH' | 'REPLACE',
) => state.arrived(route, { key, action });

function journey() {
  const state = new WorkspaceProgress('firm-a:user-a');
  visit(state, home, 'home-1', 'POP');
  state.capture(home, 200, 'business:event-a');
  visit(state, business, 'business-1', 'PUSH');
  state.capture(business, 80);
  visit(state, revision, 'revision-1', 'PUSH');
  return state;
}

describe('V09 每个历史位置保留自己的业务来源', () => {
  it('待办→事项→修订，后退再前进仍返回确切原事项', () => {
    const state = journey();
    expect(state.origin()?.route).toEqual(business);
    visit(state, business, 'business-1', 'POP');
    expect(state.origin()?.route).toEqual(home);
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()).toMatchObject({ label: '返回原业务事项', route: business });
    expect(state.takeOrigin()?.route.companyId).toBe('company-a');
    expect(state.origin()?.route).toEqual(home);
  });
  it('同一路由从不同来源打开，按entry而不是URL恢复', () => {
    const state = journey();
    visit(state, home, 'home-1', 'POP');
    state.clearTrail();
    const monthly = { ...home, view: 'monthly' as const, period: '2026-10' };
    visit(state, monthly, 'monthly-1', 'PUSH');
    state.capture(monthly, 0);
    visit(state, business, 'business-2', 'PUSH');
    expect(state.origin()?.route).toEqual(monthly);
    visit(state, business, 'business-1', 'POP');
    expect(state.origin()?.route).toEqual(home);
    visit(state, business, 'business-2', 'POP');
    expect(state.origin()?.route).toEqual(monthly);
  });
  it('未知历史位置不能套用当前另一个任务的来源', () => {
    const state = journey();
    visit(
      state,
      { ...revision, eventId: 'event-b', companyId: 'company-b' },
      'unknown-entry',
      'POP',
    );
    expect(state.origin()).toBeUndefined();
  });
  it('同key不匹配路由不能恢复旧企业来源', () => {
    const state = journey();
    visit(state, { ...revision, eventId: 'event-b', companyId: 'company-b' }, 'revision-1', 'POP');
    expect(state.origin()).toBeUndefined();
  });
  it('新登录scope没有旧身份的历史位置，即使key相同', () => {
    journey();
    const state = new WorkspaceProgress('firm-b:user-b');
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()).toBeUndefined();
  });
  it('读取副本和页内返回不能改写历史快照', () => {
    const state = journey();
    const origin = state.origin()!;
    origin.route.companyId = 'company-b';
    state.takeOrigin();
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()?.route).toEqual(business);
  });
  it('页内返回产生新条目，再后退仍恢复原页面来源', () => {
    const state = journey();
    state.takeOrigin();
    visit(state, business, 'returned-business', 'PUSH');
    expect(state.origin()?.route).toEqual(home);
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()?.route).toEqual(business);
  });
  it('应用内部REPLACE按最终URL保存来源，恢复时不依赖当前trail', () => {
    const state = journey();
    const enriched = { ...revision, bookId: 'book-a' };
    state.rememberEntry(enriched, 'revision-enriched');
    state.clearTrail();
    visit(state, enriched, 'revision-enriched', 'POP');
    expect(state.origin()?.route).toEqual(business);
    visit(state, revision, 'revision-enriched', 'POP');
    expect(state.origin()).toBeUndefined();
  });
  it('直接菜单跳转清当前来源，浏览器返回仍恢复原路径', () => {
    const state = journey();
    state.clearTrail();
    visit(state, { ...home, view: 'cases' }, 'cases-1', 'PUSH');
    expect(state.origin()).toBeUndefined();
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()?.route).toEqual(business);
  });
  it('保留数量有界，已淘汰的历史位置不猜测返回来源', () => {
    const state = journey();
    for (let n = 0; n < 90; n++) visit(state, revision, `extra-${n}`, 'REPLACE');
    visit(state, revision, 'revision-1', 'POP');
    expect(state.origin()).toBeUndefined();
  });
});
