import type { WorkspaceTask } from './workspace-types';

export type TaskScope = {
  companyId: string;
  period: string;
  switched: boolean;
  notice: string;
};

/** The task envelope is a navigation hint, not an authorization. */
export function taskScope(
  task: Pick<WorkspaceTask, 'company_id' | 'company_name' | 'period'>,
  current: { companyId: string; period: string },
): TaskScope {
  const companyId = task.company_id || '';
  const period = task.period || '';
  const switched =
    Boolean(companyId && companyId !== current.companyId) ||
    Boolean(period && period !== current.period);
  const scope = period ? `${task.company_name} · ${period}` : task.company_name;
  return {
    companyId,
    period,
    switched,
    notice: switched ? `已切换到 ${scope}` : '',
  };
}
