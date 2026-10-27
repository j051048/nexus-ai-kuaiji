import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from './api';
import { createReadGuard } from './scoped-read';

export type ScopedQuery<T> = {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
};

function isAbort(failure: unknown): boolean {
  return failure instanceof Error && failure.name === 'AbortError';
}

/**
 * 作用域读取的统一竞态控制：scope 变化自动中止并清空旧数据，过期响应直接丢弃，
 * 组件不再各自手写 epoch/AbortController/撤权清空样板。
 *
 * 竞态判定下沉到 `scoped-read` 的票据闸门，钩子与纯函数共用同一套语义。
 */
export function useScopedQuery<T>(
  load: (signal: AbortSignal) => Promise<T>,
  scopeKey: string,
): ScopedQuery<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const guard = useRef(createReadGuard());
  const loader = useRef(load);
  loader.current = load;

  const run = useCallback(() => {
    const ticket = guard.current.begin();
    setLoading(true);
    setData(null);
    setError('');
    loader
      .current(ticket.signal)
      .then((value) => {
        if (guard.current.accepts(ticket)) setData(value);
      })
      .catch((failure) => {
        if (guard.current.accepts(ticket) && !isAbort(failure)) setError(errorMessage(failure));
      })
      .finally(() => {
        if (guard.current.accepts(ticket)) setLoading(false);
      });
  }, []);

  useEffect(() => {
    run();
    // scopeKey 变化即中止在途请求并重新读取；run 本身保持稳定。
    return () => guard.current.cancel();
  }, [run, scopeKey]);

  const reload = useCallback(() => {
    run();
  }, [run]);
  return { data, error, loading, reload };
}
