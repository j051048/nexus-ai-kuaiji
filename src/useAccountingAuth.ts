import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, setApiScope } from './api';
import { configureAuth, getAuthClient, loginError } from './auth';
import type { AuthConfig, Firm, Me, Session } from './types';

export function useAccountingAuth(clearWorkspace: () => void) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [persona, setPersona] = useState('accountant');
  const sessionRef = useRef<Session | null>(null);
  const generation = useRef(0);
  const unsubscribe = useRef<(() => void) | null>(null);

  const clearSession = useCallback(() => {
    generation.current += 1;
    sessionRef.current = null;
    setApiScope(null);
    clearWorkspace();
    setSession(null);
    setMe(null);
  }, [clearWorkspace]);

  const loadIdentity = useCallback(
    async (mode: Session['mode'], token: string, firmId?: string, signal?: AbortSignal) => {
      clearSession();
      const attempt = generation.current;
      const candidate: Session = {
        mode,
        token,
        firm_id: firmId ?? null,
        firm_name: '',
        user_id: '',
        user_name: '',
        role: null,
        scope_key: crypto.randomUUID(),
      };
      setApiScope(candidate);
      const identity = await api<Me>('/me', candidate, { signal });
      if (signal?.aborted || attempt !== generation.current) return;
      const firm = identity.firms.find((item) => item.id === identity.current_firm_id);
      const next: Session = {
        ...candidate,
        firm_id: identity.current_firm_id,
        firm_name: firm?.name ?? '',
        role: firm?.role ?? null,
        user_id: identity.user.id,
        user_name: identity.user.name || identity.user.email,
      };
      setApiScope(next);
      sessionRef.current = next;
      setMe(identity);
      setSession(next);
    },
    [clearSession],
  );

  const initialize = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const settings = await api<AuthConfig>('/auth/config', undefined, { signal });
        if (signal?.aborted) return;
        unsubscribe.current?.();
        const client = configureAuth(settings);
        setConfig(settings);
        if (client) {
          const { data } = client.auth.onAuthStateChange((event) => {
            if (event === 'SIGNED_OUT') {
              clearSession();
              setLoading(false);
            }
          });
          unsubscribe.current = () => data.subscription.unsubscribe();
          clearSession();
        } else {
          const local = await api<{ token: string }>('/session', undefined, { signal });
          if (!signal?.aborted) await loadIdentity('local', local.token, undefined, signal);
        }
      } catch (failure) {
        if (!signal?.aborted) setError(errorMessage(failure));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [clearSession, loadIdentity],
  );

  useEffect(() => {
    const controller = new AbortController();
    void initialize(controller.signal);
    return () => {
      controller.abort();
      unsubscribe.current?.();
      setApiScope(null);
    };
  }, [initialize]);

  useEffect(() => {
    function onUnauthorized(event: Event) {
      if ((event as CustomEvent<string>).detail !== sessionRef.current?.scope_key) return;
      clearSession();
      setLoading(false);
      setError('登录已失效，请重新登录。');
      void getAuthClient()?.auth.signOut({ scope: 'local' });
    }
    function onForbidden(event: Event) {
      const current = sessionRef.current;
      if (!current || (event as CustomEvent<string>).detail !== current.scope_key) return;
      setLoading(true);
      setError('');
      // Re-read identity without the previous firm selector, since access may have been revoked.
      void loadIdentity(current.mode, current.token)
        .catch((failure) => setError(errorMessage(failure)))
        .finally(() => setLoading(false));
    }
    window.addEventListener('accounting:unauthorized', onUnauthorized);
    window.addEventListener('accounting:forbidden', onForbidden);
    return () => {
      window.removeEventListener('accounting:unauthorized', onUnauthorized);
      window.removeEventListener('accounting:forbidden', onForbidden);
    };
  }, [clearSession, loadIdentity]);

  async function login(email: string, password: string) {
    const client = getAuthClient();
    if (!client) return;
    setLoading(true);
    setError('');
    try {
      const result = await client.auth.signInWithPassword({ email, password });
      if (result.error) {
        setError(loginError(result.error.message));
        return;
      }
      await loadIdentity('supabase', result.data.session.access_token);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  async function switchFirm(firmId: string) {
    const current = sessionRef.current;
    if (!current || firmId === current.firm_id || loading) return;
    setLoading(true);
    setError('');
    try {
      await loadIdentity(current.mode, current.token, firmId);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    clearSession();
    setError('');
    await getAuthClient()?.auth.signOut({ scope: 'local' });
  }

  async function choosePersona(id: string) {
    if (config?.mode !== 'local' || loading || !config.personas?.some((item) => item.id === id))
      return;
    setLoading(true);
    setError('');
    clearSession();
    try {
      const local = await api<{ token: string }>(`/session?persona=${encodeURIComponent(id)}`);
      await loadIdentity('local', local.token);
      setPersona(id);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  async function createFirm(name: string) {
    const current = sessionRef.current;
    if (!current) return;
    setLoading(true);
    setError('');
    try {
      const firm = await api<Firm>('/firms', current, { method: 'POST', body: { name } });
      await loadIdentity(current.mode, current.token, firm.id);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  async function refreshIdentity() {
    const client = getAuthClient();
    if (!client) return;
    setLoading(true);
    setError('');
    try {
      const { data, error: sessionError } = await client.auth.getSession();
      if (sessionError || !data.session) throw new Error('登录已失效，请重新登录。');
      await loadIdentity(
        'supabase',
        data.session.access_token,
        sessionRef.current?.firm_id ?? undefined,
      );
    } catch (failure) {
      setError(errorMessage(failure));
      throw failure;
    } finally {
      setLoading(false);
    }
  }

  return {
    config,
    session,
    me,
    loading,
    error,
    initialize,
    login,
    logout,
    switchFirm,
    createFirm,
    persona,
    choosePersona,
    refreshIdentity,
  };
}
