import { useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import './operations-performance.css';

interface Timing {
  mean_ms: number | null;
  max_ms: number | null;
  p95_ms?: number | null;
}
interface Performance {
  shared_transactions: {
    count: number;
    writes: number;
    rollbacks: number;
    streams: number;
    connection: Timing;
    lock_wait: Timing;
    transaction: Timing;
  };
  queue_runtime: {
    kinds: {
      kind: string;
      attempts: number;
      completed_attempts: number;
      succeeded_attempts: number;
      completed_per_minute: number;
      sample_limit_reached: boolean;
      queue_wait: Timing;
      processing: Timing;
    }[];
    notes: string;
  };
  pool: { enabled?: boolean; pool_max?: number; pool_size?: number; pool_available?: number };
  measurement: string;
}
function millis(value: number | null | undefined) {
  return value == null ? '尚未采集' : `${value.toFixed(1)} ms`;
}

export function OperationsPerformance({
  session,
  companyId,
}: {
  session: Session;
  companyId: string;
}) {
  const [data, setData] = useState<Performance | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let requesting = false;
    setData(null);
    setError('');
    async function read() {
      if (!companyId || controller.signal.aborted || requesting || document.hidden) return;
      requesting = true;
      try {
        const value = await api<Performance>(
          `/operations/companies/${encodeURIComponent(companyId)}/telemetry`,
          session,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setData(value);
          setError('');
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          setData(null);
          setError(errorMessage(failure));
        }
      } finally {
        requesting = false;
      }
    }
    void read();
    const timer = window.setInterval(() => void read(), 15_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [session, companyId]);
  return (
    <section className="panel operations-performance" aria-label="企业运行性能">
      <h2>企业运行性能 · 最近 15 分钟</h2>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p>正在读取当前企业的运行采样…</p>}
      {data && (
        <>
          <p>
            {data.shared_transactions.count} 个事务样本 · 写入 {data.shared_transactions.writes} ·
            回滚 {data.shared_transactions.rollbacks} · {data.shared_transactions.streams}{' '}
            个进程记录
          </p>
          <div className="performance-table">
            <table>
              <thead>
                <tr>
                  <th>事务阶段</th>
                  <th>平均耗时</th>
                  <th>最大耗时</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ['connection', '连接获取'],
                    ['lock_wait', '锁等待'],
                    ['transaction', '事务处理'],
                  ] as const
                ).map(([key, label]) => (
                  <tr key={key}>
                    <td>{label}</td>
                    <td>{millis(data.shared_transactions[key].mean_ms)}</td>
                    <td>{millis(data.shared_transactions[key].max_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="performance-table">
            <table>
              <thead>
                <tr>
                  <th>后台类型</th>
                  <th>尝试 / 完成 / 成功</th>
                  <th>排队 P95</th>
                  <th>处理 P95</th>
                  <th>完成尝试 / 分钟</th>
                </tr>
              </thead>
              <tbody>
                {data.queue_runtime.kinds.map((item) => (
                  <tr key={item.kind}>
                    <td>
                      {item.kind === 'ocr' ? '票据解析／识别' : '分录草稿'}
                      {item.sample_limit_reached ? '（有限样本）' : ''}
                    </td>
                    <td>
                      {item.attempts} / {item.completed_attempts} / {item.succeeded_attempts}
                    </td>
                    <td>{millis(item.queue_wait.p95_ms)}</td>
                    <td>{millis(item.processing.p95_ms)}</td>
                    <td>{item.completed_per_minute}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="field-help">{data.measurement}</p>
          <details>
            <summary>采样说明与连接池</summary>
            <p>{data.queue_runtime.notes}</p>
            <p>
              {data.pool.enabled
                ? `当前进程连接池：${data.pool.pool_size ?? 0} 个连接，上限 ${data.pool.pool_max ?? '—'}，可用 ${data.pool.pool_available ?? '—'}。`
                : '当前使用本地演示数据库。'}
            </p>
          </details>
        </>
      )}
    </section>
  );
}
