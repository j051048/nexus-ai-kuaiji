import { useCallback, useMemo, useState } from 'react';
import { WorkspaceProgress, type HomeProgress, type MonthlyProgress } from './workspace-progress';
import type { WorkspaceRoute } from './workspace-route';

export function useWorkspaceProgress(scope?: string) {
  const memory = useMemo(() => new WorkspaceProgress(scope ?? ''), [scope]);
  const [, refresh] = useState(0);
  const capture = useCallback(
    (route: WorkspaceRoute, taskKey?: string) => {
      memory.capture(route, window.scrollY, taskKey);
      refresh((value) => value + 1);
    },
    [memory],
  );
  const takeOrigin = useCallback(() => {
    const point = memory.takeOrigin();
    refresh((value) => value + 1);
    return point;
  }, [memory]);
  const arrived = useCallback(
    (route: WorkspaceRoute) => {
      memory.arrived(route);
      refresh((value) => value + 1);
    },
    [memory],
  );
  const clearTrail = useCallback(() => {
    memory.clearTrail();
    refresh((value) => value + 1);
  }, [memory]);
  const saveHome = useCallback((value: HomeProgress) => memory.saveHome(value), [memory]);
  const saveMonthly = useCallback((value: MonthlyProgress) => memory.saveMonthly(value), [memory]);
  return {
    memory,
    capture,
    takeOrigin,
    arrived,
    clearTrail,
    saveHome,
    saveMonthly,
    origin: memory.origin(),
  };
}
