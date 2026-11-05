import { expect, it } from 'vitest';
import { WorkspaceProgress } from './workspace-progress';

it('月度工作筛选随原列表恢复，换月份与身份不继承', () => {
  const state = new WorkspaceProgress('one');
  state.saveMonthly({
    period: '2026-10',
    query: '企业',
    search: '企业',
    offset: 20,
    focus: 'mine',
  });
  expect(state.getMonthly('2026-10')?.focus).toBe('mine');
  expect(state.getMonthly('2026-11')).toBeUndefined();
  expect(new WorkspaceProgress('two').getMonthly('2026-10')).toBeUndefined();
});
it('不接受未知的月度筛选', () => {
  const state = new WorkspaceProgress('one');
  state.saveMonthly({ period: '2026-10', query: '', search: '', offset: 0, focus: 'bad' as never });
  expect(state.getMonthly('2026-10')?.focus).toBe('all');
});
