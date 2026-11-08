import { describe, it, expect } from 'vitest';
import { WorkspaceProgress } from './workspace-progress';
import { emptyWorkspaceRoute } from './workspace-route';
import { taskDestination } from './workspace-types';

describe('待办办理与返回', () => {
  it('事务所列表进入办理后有返回入口，保留分类页码且新登录不继承', () => {
    const memory = new WorkspaceProgress('a');
    memory.saveInbox({ bucket: 'blocked', offset: 20 });
    memory.capture({ ...emptyWorkspaceRoute, view: 'inbox' }, 420, 'delivery:b');
    expect(memory.origin()?.label).toBe('返回事务所待办');
    expect(memory.getInbox()).toMatchObject({
      bucket: 'blocked',
      offset: 20,
      scrollY: 420,
      taskKey: 'delivery:b',
    });
    expect(new WorkspaceProgress('b').getInbox()).toMatchObject({ bucket: 'all', offset: 0 });
  });
  it('消息来源也保留返回，不把阅读当作办结', () => {
    const memory = new WorkspaceProgress('a');
    memory.capture({ ...emptyWorkspaceRoute, view: 'messages' }, 0);
    expect(memory.origin()?.label).toBe('返回消息');
  });
  it('缺少批次的识别待办仍能定位当前任务，而不是静默无反应', () => {
    expect(
      taskDestination({ kind: 'ocr', ref: 'job-a', batch_id: null, event_id: 'event-a' } as never),
    ).toMatchObject({ type: 'business', id: 'event-a' });
  });
});
