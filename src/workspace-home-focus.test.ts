import { describe, expect, it } from 'vitest';
import { currentHomeTask } from './workspace-home-focus';
import type { WorkspaceTask, WorkspaceTasks } from './workspace-types';

const task: WorkspaceTask = {
  key: 'finance:a',
  kind: 'finance',
  ref: 'a',
  company_id: 'a',
  company_name: '虚构 A',
  book_id: null,
  event_id: 'a',
  draft_id: null,
  batch_id: null,
  title: '真实核验事项',
  state: 'department_approved',
  priority: 10,
  due_on: null,
  updated_at: '',
  bucket: 'action',
  action_label: '核验资料',
  responsible: '财务',
  overdue: false,
};
const tasks: WorkspaceTasks = {
  tasks: [task],
  counts: { action: 9 },
  pagination: { offset: 0, limit: 20, total: 9, has_more: false },
};
const current = {
  tasks,
  bucket: 'action' as const,
  offset: 0,
  company: '',
  loading: false,
  error: false,
  scopeMatches: true,
  searchPending: false,
};

describe('当前清单推荐项防止过期与跨企业操作', () => {
  it('只推荐当前已授权返回的一项，不从总量伪造任务', () => {
    expect(currentHomeTask(current)).toBe(task);
    expect(currentHomeTask({ ...current, tasks: { ...tasks, tasks: [] } })).toBeUndefined();
  });
  it.each([
    { loading: true },
    { error: true },
    { scopeMatches: false },
    { searchPending: true },
    { bucket: 'waiting' as const },
    { offset: 20 },
    { company: 'b' },
  ])('过期、搜索中、翻页或范围不符时不推荐 %o', (changed) => {
    expect(currentHomeTask({ ...current, ...changed })).toBeUndefined();
  });
  it('财务只从当前清单选择核验、移交、补件或资料工作，不被兼任复核抢占', () => {
    const review = { ...task, key: 'review:a', kind: 'review' as const };
    expect(currentHomeTask({ ...current, tasks: { ...tasks, tasks: [review, task] } }, true)).toBe(
      task,
    );
    expect(
      currentHomeTask({ ...current, tasks: { ...tasks, tasks: [review] } }, true),
    ).toBeUndefined();
  });
});
