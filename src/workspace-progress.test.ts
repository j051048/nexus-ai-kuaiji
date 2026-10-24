import { describe, expect, it } from 'vitest';
import { emptyWorkspaceRoute } from './workspace-route';
import { WorkspaceProgress, type HomeProgress } from './workspace-progress';

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
