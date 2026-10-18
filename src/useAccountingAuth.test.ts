import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Me, Session } from './types';

// Exercise the hook's async/event lifecycle without a DOM renderer. Browser
// navigation and rendering are covered separately by the application e2e tests.
const hooks = vi.hoisted(() => ({
  state: [] as unknown[],
  cleanups: [] as (() => void)[],
}));
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  setApiScope: vi.fn(),
  invalidateApiScope: vi.fn(),
  signOut: vi.fn(),
  signInWithPassword: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock('react', () => ({
  useState(initial: unknown) {
    const slot = hooks.state.length;
    hooks.state.push(initial);
    return [
      initial,
      (value: unknown) => {
        hooks.state[slot] =
          typeof value === 'function'
            ? (value as (previous: unknown) => unknown)(hooks.state[slot])
            : value;
      },
    ];
  },
  useRef: (value: unknown) => ({ current: value }),
  useCallback: (callback: unknown) => callback,
  useEffect(effect: () => void | (() => void)) {
    const cleanup = effect();
    if (cleanup) hooks.cleanups.push(cleanup);
  },
}));

vi.mock('./api', () => {
  class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return {
    api: mocks.api,
    ApiError,
    setApiScope: mocks.setApiScope,
    invalidateApiScope: mocks.invalidateApiScope,
    errorMessage: (failure: unknown) => (failure instanceof Error ? failure.message : '请求失败'),
  };
});

vi.mock('./auth', () => {
  const client = {
    auth: {
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
      signInWithPassword: mocks.signInWithPassword,
      signOut: mocks.signOut,
      getSession: mocks.getSession,
    },
  };
  return {
    configureAuth: () => client,
    getAuthClient: () => client,
    loginError: (value: string) => value,
  };
});

import { ApiError } from './api';
import { useAccountingAuth } from './useAccountingAuth';

const identity: Me = {
  mode: 'supabase',
  user: { id: 'user-one', name: '虚构会计', email: 'fictional@example.invalid' },
  firms: [
    { id: 'firm-a', name: '虚构事务所甲', role: 'owner' },
    { id: 'firm-b', name: '虚构事务所乙', role: 'reviewer' },
  ],
  current_firm_id: 'firm-a',
  permissions: { manage_firm: true, create_company: true, create_book: true },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (failure: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

async function start() {
  const clearWorkspace = vi.fn();
  // eslint-disable-next-line react-hooks/rules-of-hooks -- Mocked lifecycle harness; no React renderer is used here.
  const auth = useAccountingAuth(clearWorkspace, false);
  await vi.waitFor(() => expect(hooks.state[4]).toBe(false));
  await auth.login('fictional@example.invalid', 'fictional-password');
  return { auth, clearWorkspace, session: hooks.state[1] as Session };
}

beforeEach(() => {
  hooks.state.length = 0;
  hooks.cleanups.length = 0;
  vi.resetAllMocks();
  vi.stubGlobal('window', new EventTarget());
  mocks.api.mockImplementation(async (path: string) =>
    path === '/auth/config'
      ? {
          mode: 'supabase',
          supabase_url: 'https://auth.example.invalid',
          publishable_key: 'fictional',
        }
      : identity,
  );
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: 'fictional-token' } },
    error: null,
  });
  mocks.signOut.mockResolvedValue({ error: null });
});

afterEach(() => {
  for (const cleanup of hooks.cleanups.reverse()) cleanup();
  vi.unstubAllGlobals();
});

describe('background authorization revalidation', () => {
  it('coalesces resource denials while preserving the session and selected firm', async () => {
    const { auth, session, clearWorkspace } = await start();
    const pending = deferred<Me>();
    mocks.api.mockReturnValue(pending.promise);
    const clears = clearWorkspace.mock.calls.length;
    const reads = mocks.api.mock.calls.length;
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    expect(mocks.api.mock.calls.length).toBe(reads + 1);
    expect(mocks.api.mock.calls[mocks.api.mock.calls.length - 1]?.[1]).toBe(session);
    expect(mocks.invalidateApiScope).toHaveBeenCalledTimes(1);
    expect(hooks.state[1]).toBe(session);
    expect(hooks.state[4]).toBe(false);
    pending.resolve(identity);
    await auth.refreshIdentity();
    expect(hooks.state[1]).toBe(session);
    expect(clearWorkspace.mock.calls.length).toBe(clears);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it('falls back only after the selected firm itself is denied', async () => {
    const { session } = await start();
    mocks.api
      .mockRejectedValueOnce(new ApiError('当前事务所授权已撤销', 403))
      .mockResolvedValueOnce({ ...identity, current_firm_id: 'firm-b' });
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    await vi.waitFor(() => expect((hooks.state[1] as Session).firm_id).toBe('firm-b'));
    expect(mocks.api.mock.calls[mocks.api.mock.calls.length - 1]?.[1]).toMatchObject({
      firm_id: null,
    });
    expect((hooks.state[1] as Session).scope_key).not.toBe(session.scope_key);
  });

  it('does not switch firms on a temporary identity service error', async () => {
    const { session } = await start();
    const reads = mocks.api.mock.calls.length;
    mocks.api.mockRejectedValueOnce(new ApiError('暂时无法读取身份', 503));
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    await vi.waitFor(() => expect(hooks.state[5]).toBe('暂时无法读取身份'));
    expect(mocks.api.mock.calls.length).toBe(reads + 1);
    expect(hooks.state[1]).toBe(session);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it('ignores a late identity response after logout', async () => {
    const { auth, session } = await start();
    const pending = deferred<Me>();
    mocks.api.mockReturnValue(pending.promise);
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    await auth.logout();
    pending.resolve(identity);
    await Promise.resolve();
    await Promise.resolve();
    expect(hooks.state[1]).toBeNull();
    expect(hooks.state[2]).toBeNull();
  });

  it('does not resurrect an old firm after a new login while revalidation is pending', async () => {
    const { auth, session } = await start();
    const pending = deferred<Me>();
    mocks.api.mockReturnValueOnce(pending.promise);
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    mocks.api.mockResolvedValueOnce({ ...identity, current_firm_id: 'firm-b' });
    await auth.login('fictional@example.invalid', 'fictional-password');
    const switched = hooks.state[1] as Session;
    pending.resolve(identity);
    await Promise.resolve();
    await Promise.resolve();
    window.dispatchEvent(new CustomEvent('accounting:unauthorized', { detail: session.scope_key }));
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    expect(hooks.state[1]).toBe(switched);
    expect(switched.firm_id).toBe('firm-b');
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it('updates changed role and display metadata without changing the authorization scope', async () => {
    const { auth, session, clearWorkspace } = await start();
    const clears = clearWorkspace.mock.calls.length;
    const changed: Me = {
      ...identity,
      user: { ...identity.user, name: '虚构新姓名' },
      firms: [{ id: 'firm-a', name: '虚构新事务所名', role: 'viewer' }],
      permissions: { manage_firm: false, create_company: false, create_book: false },
    };
    mocks.api.mockResolvedValue(changed);
    await auth.refreshIdentity();
    const updated = hooks.state[1] as Session;
    expect(updated).not.toBe(session);
    expect(updated).toMatchObject({
      scope_key: session.scope_key,
      user_name: '虚构新姓名',
      firm_name: '虚构新事务所名',
      role: 'viewer',
    });
    await auth.refreshIdentity();
    expect(hooks.state[1]).toBe(updated);
    expect(clearWorkspace.mock.calls.length).toBe(clears);
  });

  it('keeps an authenticated identity with no remaining workspace after confirmed revocation', async () => {
    const { session } = await start();
    mocks.api
      .mockRejectedValueOnce(new ApiError('当前事务所授权已撤销', 403))
      .mockResolvedValueOnce({
        ...identity,
        firms: [],
        current_firm_id: null,
        permissions: { manage_firm: false, create_company: false, create_book: false },
      });
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    await vi.waitFor(() => expect((hooks.state[1] as Session).firm_id).toBeNull());
    expect((hooks.state[1] as Session).user_id).toBe(identity.user.id);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it('keeps MFA identity refresh soft and preserves current workspace selections', async () => {
    const { auth, session, clearWorkspace } = await start();
    const clears = clearWorkspace.mock.calls.length;
    await auth.refreshIdentity();
    expect(hooks.state[1]).toBe(session);
    expect(clearWorkspace.mock.calls.length).toBe(clears);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it('still clears all session state on a real unauthorized event', async () => {
    const { session, clearWorkspace } = await start();
    const clears = clearWorkspace.mock.calls.length;
    window.dispatchEvent(new CustomEvent('accounting:unauthorized', { detail: session.scope_key }));
    expect(hooks.state[1]).toBeNull();
    expect(hooks.state[2]).toBeNull();
    expect(clearWorkspace.mock.calls.length).toBe(clears + 1);
    expect(mocks.signOut).toHaveBeenCalledWith({ scope: 'local' });
  });
});
