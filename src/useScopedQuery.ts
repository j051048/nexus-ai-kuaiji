import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from './api';

export type ScopedQuery<T> = {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
};

/**
 * 作用域读取的统一竞态控制：scope 变化自动中止并清空旧数据，过期响应直接丢弃，
 * 组件不再各自手写 epoch/AbortController/撤权清空样板。
 */
export function useScopedQuery<T>(
  load: (signal: AbortSignal) => Promise<T>,
  scopeKey: string,
): ScopedQuery<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const epoch = useRef(0);
  const loader = useRef(load);
  loader.current = load;

  const run = useCallback(() => {
    const stamp = ++epoch.current;
    const controller = new AbortController();
    setLoading(true);
    setData(null);
    setError('');
    loader
      .current(controller.signal)
      .then((value) => {
        if (epoch.current === stamp) setData(value);
      })
      .catch((failure) => {
        if (
          epoch.current === stamp &&
          !(failure instanceof DOMException && failure.name === 'AbortError')
        ) {
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (epoch.current === stamp) setLoading(false);
      });
    return () => {
      controller.abort();
      if (epoch.current === stamp) epoch.current += 1;
    };
  }, []);

  useEffect(() => {
    const cancel = run();
    return cancel;
    // scopeKey 变化即中止在途请求并重新读取；run 本身保持稳定。
  }, [run, scopeKey]);

  const reload = useCallback(() => {
    run();
  }, [run]);
  return { data, error, loading, reload };
}
