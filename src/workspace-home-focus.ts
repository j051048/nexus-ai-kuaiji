import type { WorkspaceTask, WorkspaceTasks } from './workspace-types';

/** Presentation only: open() re-authorizes the selected task before navigation. */
export function currentHomeTask(
  state: {
    tasks: WorkspaceTasks | null;
    bucket: 'action' | 'waiting';
    offset: number;
    company: string;
    loading: boolean;
    error: boolean;
    scopeMatches: boolean;
    searchPending: boolean;
  },
  finance = false,
): WorkspaceTask | undefined {
  if (
    !state.tasks ||
    state.loading ||
    state.error ||
    !state.scopeMatches ||
    state.searchPending ||
    state.bucket !== 'action' ||
    state.tasks.pagination.offset !== state.offset
  )
    return undefined;
  return state.tasks.tasks.find(
    (task) =>
      task.bucket === 'action' &&
      (!state.company || task.company_id === state.company) &&
      (!finance || ['finance', 'handoff', 'case', 'material'].includes(task.kind)),
  );
}
