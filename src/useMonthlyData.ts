import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

/** This cache is component-local. URL identifiers never grant access. */
export function useMonthlyData<T>(session: Session, path: string, revision = 0) {
  const scope = `${session.scope_key}:${path}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const epoch = useRef(0),
    active = useRef(true),
    controller = useRef<AbortController>();
  const [stored, setStored] = useState<{ scope: string; value: T } | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState('');
  const [revoked, setRevoked] = useState(false);
  const load = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const saved = scope,
      stamp = ++epoch.current;
    setLoading(true);
    setError('');
    try {
      const result = await api<T>(path, session, { signal: request.signal });
      if (
        active.current &&
        !request.signal.aborted &&
        saved === scopeRef.current &&
        stamp === epoch.current
      ) {
        setStored({ scope: saved, value: result });
        setRevoked(false);
      }
    } catch (failure) {
      if (
        active.current &&
        !request.signal.aborted &&
        saved === scopeRef.current &&
        stamp === epoch.current
      ) {
        setStored(null);
        setError(errorMessage(failure));
      }
    } finally {
      if (active.current && saved === scopeRef.current && stamp === epoch.current)
        setLoading(false);
    }
  }, [path, scope, session]);
  useEffect(() => {
    active.current = true;
    setStored(null);
    setRevoked(false);
    void load();
    return () => {
      active.current = false;
      epoch.current++;
      controller.current?.abort();
    };
  }, [load, revision]);
  useEffect(() => {
    const clear = (event: Event) => {
      const changedScope = (event as CustomEvent<string>).detail;
      if (changedScope && changedScope !== session.scope_key) return;
      epoch.current++;
      controller.current?.abort();
      setStored(null);
      setLoading(false);
      setRevoked(true);
      setError('当前授权已更新，请返回工作台重新读取。');
    };
    window.addEventListener('accounting:forbidden', clear);
    window.addEventListener('accounting:unauthorized', clear);
    return () => {
      window.removeEventListener('accounting:forbidden', clear);
      window.removeEventListener('accounting:unauthorized', clear);
    };
  }, [session.scope_key]);
  return {
    data: stored?.scope === scope && !revoked ? stored.value : null,
    loading,
    error,
    revoked,
    load,
  };
}
