import { useEffect, useRef } from 'react';
import { useBlocker, useLocation, useNavigate } from 'react-router-dom';
import {
  emptyWorkspaceRoute,
  readWorkspaceRoute,
  workspaceRouteKey,
  workspaceRouteUrl,
  type WorkspaceRoute,
} from './workspace-route';

export function useWorkspaceRoute({
  scope,
  value,
  metadata,
  onRestore,
  locked,
  canDiscard,
}: {
  scope?: string;
  value: WorkspaceRoute;
  metadata?: { companyId?: string; period?: string };
  onRestore: (route: WorkspaceRoute) => void;
  locked: boolean;
  canDiscard: boolean;
}) {
  const location = useLocation(),
    navigate = useNavigate();
  const restore = useRef(onRestore);
  restore.current = onRestore;
  const activeScope = useRef<string>();
  const firstScope = useRef(true);
  const lastLocation = useRef('');
  const ownNavigation = useRef('');
  const bypassBlock = useRef(false);
  const restoring = useRef<string | null>(null);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      !bypassBlock.current &&
      locked &&
      currentLocation.pathname + currentLocation.search !==
        nextLocation.pathname + nextLocation.search,
  );
  const key = workspaceRouteKey(value);

  useEffect(() => {
    if (!scope) return;
    if (activeScope.current !== scope) {
      activeScope.current = scope;
      const target = firstScope.current
        ? readWorkspaceRoute(location.pathname, location.search)
        : { ...emptyWorkspaceRoute };
      firstScope.current = false;
      restoring.current = workspaceRouteKey(target);
      lastLocation.current = location.key;
      ownNavigation.current = '';
      restore.current(target);
      return;
    }
    if (lastLocation.current === location.key) return;
    lastLocation.current = location.key;
    const path = location.pathname + location.search;
    if (ownNavigation.current === path) {
      ownNavigation.current = '';
      return;
    }
    const target = readWorkspaceRoute(location.pathname, location.search);
    restoring.current = workspaceRouteKey(target);
    restore.current(target);
  }, [scope, location.key, location.pathname, location.search]);

  useEffect(() => {
    if (!scope || activeScope.current !== scope) return;
    if (restoring.current !== null) {
      if (restoring.current !== key) return;
      restoring.current = null;
    }
    const path = workspaceRouteUrl(value, metadata, location.search);
    if (path === location.pathname + location.search) return;
    // State can settle over several renders before React Router exposes the
    // first navigation. Do not enqueue the same destination more than once.
    if (ownNavigation.current === path) return;
    const previous = readWorkspaceRoute(location.pathname, location.search);
    // Enrich authenticated metadata or update search without filling history.
    const replace =
      location.pathname === '/' ||
      workspaceRouteKey(previous) === key ||
      (previous.view === value.view &&
        previous.bookId === value.bookId &&
        previous.draftId === value.draftId &&
        previous.reviewDraftId === value.reviewDraftId);
    ownNavigation.current = path;
    bypassBlock.current = true;
    void navigate(path, { replace });
    bypassBlock.current = false;
  }, [
    scope,
    key,
    metadata?.companyId,
    metadata?.period,
    location.pathname,
    location.search,
    value,
    navigate,
  ]);

  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (!locked) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, [locked]);
  return {
    blocked: blocker.state === 'blocked',
    canDiscard,
    cancel: () => {
      if (blocker.state === 'blocked') blocker.reset();
    },
    proceed: () => {
      if (canDiscard && blocker.state === 'blocked') {
        blocker.proceed();
      }
    },
  };
}
