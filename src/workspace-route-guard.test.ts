import { beforeEach, describe, expect, it, vi } from 'vitest';
import { emptyWorkspaceRoute } from './workspace-route';

// React Router registers its blocker predicate in a passive effect. Preserve
// the already-registered predicate while re-rendering, exactly the window in
// which a native back immediately after input used to bypass the guard.
type Position = { key: string; pathname: string; search: string };
type Predicate = (visit: { currentLocation: Position; nextLocation: Position }) => boolean;
const state = vi.hoisted(() => ({
  refs: [] as { current: unknown }[],
  cursor: 0,
  callbacks: [] as { value: unknown; deps: readonly unknown[] }[],
  callbackCursor: 0,
  registered: null as Predicate | null,
  rendered: null as Predicate | null,
}));
vi.mock('react', () => ({
  useEffect: vi.fn(),
  useRef: <T>(value: T) => {
    const index = state.cursor++;
    state.refs[index] ??= { current: value };
    return state.refs[index] as { current: T };
  },
  useCallback: <T>(value: T, deps: readonly unknown[]) => {
    const index = state.callbackCursor++;
    const prior = state.callbacks[index];
    if (
      !prior ||
      deps.length !== prior.deps.length ||
      deps.some((item, i) => item !== prior.deps[i])
    )
      state.callbacks[index] = { value, deps };
    return state.callbacks[index].value as T;
  },
}));
vi.mock('react-router-dom', () => ({
  useLocation: () => ({ key: 'revision-2', pathname: '/workspace/revision', search: '?event=e' }),
  useNavigate: () => vi.fn(),
  useNavigationType: () => 'POP',
  useBlocker: (predicate: Predicate) => {
    state.registered ??= predicate;
    state.rendered = predicate;
    return { state: 'unblocked' };
  },
}));

import { useWorkspaceRoute } from './useWorkspaceRoute';

function GuardProbe(locked: boolean) {
  state.cursor = 0;
  state.callbackCursor = 0;
  return useWorkspaceRoute({
    scope: 'synthetic-user-scope',
    value: emptyWorkspaceRoute,
    onRestore: vi.fn(),
    onRecordEntry: vi.fn(),
    locked,
    canDiscard: locked,
  });
}
const revision = { key: 'revision-2', pathname: '/workspace/revision', search: '?event=e' };
const sameUrlEarlier = { ...revision, key: 'revision-1' };
const business = { key: 'business-1', pathname: '/workspace/business', search: '?event=e' };

describe('native back protection before passive effects settle', () => {
  beforeEach(() => {
    state.refs = [];
    state.callbacks = [];
    state.registered = null;
    state.rendered = null;
  });
  it('the registered predicate immediately sees newly dirty input after accepted discard', () => {
    GuardProbe(false);
    const registered = state.registered!;
    GuardProbe(true);
    expect(registered({ currentLocation: revision, nextLocation: business })).toBe(true);
    expect(registered({ currentLocation: revision, nextLocation: sameUrlEarlier })).toBe(true);
  });
  it('keeps the registered callback stable and reads a later clean state without another effect', () => {
    GuardProbe(true);
    const registered = state.registered!;
    GuardProbe(false);
    expect(state.rendered).toBe(registered);
    expect(registered({ currentLocation: revision, nextLocation: business })).toBe(false);
  });
  it('does not block the identical current history entry', () => {
    GuardProbe(true);
    expect(state.registered!({ currentLocation: revision, nextLocation: revision })).toBe(false);
  });
});
