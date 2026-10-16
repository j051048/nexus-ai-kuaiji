import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorMessage } from './api';
import type { Session } from './types';

/** No shared cache: a scope or path change hides data synchronously, before effects run. */
export function useServicePlanData<T>(session: Session, path: string, revision = 0) {
  const target = `${session.scope_key}:${path}`;
  const current = useRef(target);
  current.current = target;
  const active = useRef(true);
  const epoch = useRef(0);
  const authority = useRef(0);
  const locked = useRef(false);
  const seenRevision = useRef(revision);
  const controller = useRef<AbortController>();
  const [stored, setStored] = useState<{ target: string; value: T }>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reset, setReset] = useState(0);
  const [revoked, setRevoked] = useState(false);
  const clear = useCallback(() => {
    epoch.current++;
    authority.current++;
    controller.current?.abort();
    setStored(undefined);
    setLoading(false);
    setNotice('');
    setReset((value) => value + 1);
  }, []);
  const load = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const stamp = ++epoch.current;
    const started = target;
    setLoading(true);
    setStored(undefined);
    setError('');
    try {
      const value = await api<T>(path, session, { signal: request.signal });
      if (
        active.current &&
        !request.signal.aborted &&
        current.current === started &&
        epoch.current === stamp
      ) {
        setStored({ target: started, value });
        setRevoked(false);
        return true;
      }
    } catch (failure) {
      if (
        active.current &&
        !request.signal.aborted &&
        current.current === started &&
        epoch.current === stamp
      ) {
        clear();
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          setRevoked(true);
        setError(errorMessage(failure));
      }
    } finally {
      if (active.current && current.current === started && !request.signal.aborted)
        setLoading(false);
    }
    return false;
  }, [path, session, target, clear]);
  useEffect(() => {
    active.current = true;
    clear();
    setRevoked(false);
    setBusy(false);
    void load();
    return () => {
      active.current = false;
      authority.current++;
      epoch.current++;
      controller.current?.abort();
    };
  }, [load, clear]);
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    void load();
  }, [revision, load]);
  useEffect(() => {
    const revoke = (event: Event) => {
      const scope = (event as CustomEvent<string>).detail;
      if (scope && scope !== session.scope_key) return;
      clear();
      setLoading(false);
      setBusy(false);
      setRevoked(true);
      setError('当前授权已更新，资料与编辑内容已清空，请返回工作台重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key, clear]);
  async function mutate(writePath: string, body: unknown, message: string, method = 'POST') {
    if (locked.current || revoked) return false;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const started = target;
    const permission = authority.current;
    const same = () =>
      active.current && current.current === started && authority.current === permission;
    try {
      await api(writePath, session, { method, body });
      if (!same()) return false;
      setReset((value) => value + 1);
      const loaded = await load();
      if (same()) setNotice(loaded ? message : `${message} 请重新读取核对当前状态。`);
      return loaded;
    } catch (failure) {
      if (!same()) return false;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        clear();
        setRevoked(true);
        setError(errorMessage(failure));
      } else if (failure instanceof ApiError && failure.status === 409) {
        setReset((value) => value + 1);
        await load();
        if (current.current === started && active.current)
          setError(
            '资料已被他人更新或依据发生变化。已读取最新状态，请重新核对后操作；未自动重提。',
          );
      } else {
        setError(
          errorMessage(failure) +
            (failure instanceof TypeError ? ' 操作结果可能已保存，请先刷新核对。' : ''),
        );
      }
      return false;
    } finally {
      locked.current = false;
      if (active.current && current.current === started) setBusy(false);
    }
  }
  function invalidate(message: string) {
    clear();
    setRevoked(true);
    setError(message);
  }
  return {
    data: stored?.target === target && !revoked ? stored.value : null,
    loading,
    busy,
    error,
    notice,
    reset,
    revoked,
    load,
    mutate,
    invalidate,
  };
}
