import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

export function useMonthlyCommand(
  session: Session,
  bookId: string,
  options: {
    revoked: boolean;
    load: () => Promise<void>;
    onBusyChange?: (busy: boolean) => void;
    onChanged?: () => void;
  },
) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const active = useRef(true),
    locked = useRef(false),
    epoch = useRef(0);
  const scope = `${session.scope_key}:${bookId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const keys = useRef(new Map<string, { signature: string; key: string }>());
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, [scope]);
  useEffect(() => {
    options.onBusyChange?.(busy);
    return () => options.onBusyChange?.(false);
  }, [busy, options.onBusyChange]);
  useEffect(() => {
    if (options.revoked) {
      epoch.current++;
      keys.current.clear();
      locked.current = false;
      setBusy(false);
      setError('');
      setNotice('');
    }
  }, [options.revoked]);
  async function run<T>(
    path: string,
    body: Record<string, unknown>,
    message: string,
    onSuccess?: (value: T) => void,
    idempotent = false,
  ): Promise<void> {
    if (locked.current || options.revoked) return;
    const signature = JSON.stringify([scope, body]);
    if (idempotent && keys.current.get(path)?.signature !== signature)
      keys.current.set(path, { signature, key: crypto.randomUUID() });
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const captured = scope,
      stamp = ++epoch.current;
    const valid = () =>
      active.current &&
      captured === scopeRef.current &&
      stamp === epoch.current &&
      !options.revoked;
    try {
      const value = await api<T>(`/books/${encodeURIComponent(bookId)}/monthly${path}`, session, {
        method: 'POST',
        body: { ...body, ...(idempotent ? { idempotency_key: keys.current.get(path)!.key } : {}) },
      });
      if (!valid()) return;
      keys.current.delete(path);
      onSuccess?.(value);
      setNotice(message);
      await options.load();
      if (valid()) options.onChanged?.();
    } catch (failure) {
      if (valid()) {
        await options.load();
        if (valid()) setError(errorMessage(failure));
      }
    } finally {
      if (valid()) {
        locked.current = false;
        setBusy(false);
      }
    }
  }
  return { busy, error, notice, run };
}
