import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { presentApiError } from './api-error-presentation';
import type { Session } from './types';

/** One in-flight operation per mounted scope. A lost response never retries a write. */
export function useOperationRequests(session: Session) {
  const active = useRef(true),
    request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [denied, setDenied] = useState(false);
  useEffect(() => {
    active.current = true;
    const revoke = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      request.current?.abort();
      request.current = null;
      setDenied(true);
      setBusy(false);
      setError('当前授权已失效，请返回工作台重新确认权限。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      active.current = false;
      request.current?.abort();
      request.current = null;
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);
  const run = useCallback(
    async <T>(
      work: (signal: AbortSignal) => Promise<T>,
      accept: (value: T) => void,
      write = false,
    ) => {
      if (request.current || denied) return false;
      const controller = new AbortController();
      request.current = controller;
      setBusy(true);
      setError('');
      try {
        const result = await work(controller.signal);
        if (!active.current || controller.signal.aborted) return false;
        accept(result);
        return true;
      } catch (failure) {
        if (!active.current || controller.signal.aborted) return false;
        if (
          failure instanceof ApiError &&
          [401, 403, 404].includes(failure.status) &&
          failure.code !== 'admin_mfa_required'
        )
          setDenied(true);
        const p = presentApiError(failure);
        if (p)
          setError(
            `${p.title}。${p.nextStep}${write && !(failure instanceof ApiError && failure.status < 500) ? ' 本次提交结果待核实，请先刷新记录，不要重复创建。' : ''}`,
          );
        return false;
      } finally {
        if (request.current === controller) {
          request.current = null;
          if (active.current) setBusy(false);
        }
      }
    },
    [denied],
  );
  return { run, busy, error, denied };
}
