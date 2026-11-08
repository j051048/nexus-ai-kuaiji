import { useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, Database, RefreshCw, Server } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-observability.css';
import { readFirmSnapshot } from './firm-observability-contract';

export type Snapshot = {
  firm_id: string;
  period: string;
  cost_summary: {
    sample_count: number;
    known_micro: number | null;
    unknown_count: number;
    estimated_count: number;
  };
  signals: {
    code: string;
    owner_role: string;
    next_action: string;
    target: 'queue' | 'operations' | 'runs';
    automatic_action: false;
  }[];
  queues: Record<string, Record<string, number>>;
  alerts: Record<string, { open: number; critical: number }>;
  worker_health?: {
    available: boolean;
    last_heartbeat_at: string | null;
    stale_after_seconds: number;
  };
  metrics: {
    buckets: number;
    transactions: number;
    writes: number;
    rollbacks: number;
    avg_connection_ms: number | null;
    avg_lock_ms: number | null;
    max_transaction_ms: number | null;
  };
  ai_cost_cny: number;
  projection: {
    total: number;
    dirty: number;
    never_built: number;
    failing: number;
    oldest_dirty_at: string;
  };
  trace_hint: string;
};

export function FirmObservability({ session }: { session: Session }) {
  return <ObservabilityWorkspace key={session.scope_key} session={session} />;
}

function ObservabilityWorkspace({ session }: { session: Session }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [period, setPeriod] = useState(new Date().toISOString().slice(0, 7));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const epoch = useRef(0);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    const invalidate = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      epoch.current += 1;
      setData(null);
      setLoading(false);
      setError('当前授权可能已更新，已清除页面数据。请重新读取。');
    };
    window.addEventListener('accounting:forbidden', invalidate);
    return () => {
      active.current = false;
      epoch.current += 1;
      window.removeEventListener('accounting:forbidden', invalidate);
    };
  }, [session.scope_key]);
  useEffect(() => {
    const controller = new AbortController();
    const stamp = ++epoch.current;
    const current = () => !controller.signal.aborted && stamp === epoch.current;
    setLoading(true);
    setData(null);
    setError('');
    void api<Snapshot>(
      '/firm/observability?' + new URLSearchParams({ period }).toString(),
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (current()) setData(readFirmSnapshot(value, session.firm_id, period));
      })
      .catch((failure) => {
        if (
          current() ||
          (active.current && failure instanceof ApiError && failure.status === 403)
        ) {
          setData(null);
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => controller.abort();
  }, [session, period, revision]);
  const queueLabels: Record<string, string> = {
    ocr: '票据识别',
    proposal: '分录生成',
    migration: '旧账迁移',
    batch: '批量运营',
  };
  return (
    <main className="firm-observability">
      <header>
        <div>
          <p className="eyebrow">OPERATIONS / TRACE</p>
          <h1>运行可观测性</h1>
          <p>队列、锁等待、后台处理服务、投影与 AI 成本集中在一个只读视图。</p>
        </div>
        <div className="firm-observability-actions">
          <input
            aria-label="运行记录月份"
            type="month"
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
          />
          <button
            className="button secondary"
            type="button"
            disabled={loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading && !data ? (
        <div className="loading-block">
          <Spinner />
          正在读取运行指标…
        </div>
      ) : data ? (
        <>
          <section className="observability-cards">
            <article>
              <Activity size={18} />
              <span>近一小时事务</span>
              <strong>{data.metrics.transactions}</strong>
              <small>
                写 {data.metrics.writes} · 回滚 {data.metrics.rollbacks}
              </small>
            </article>
            <article>
              <span>锁等待均值</span>
              <strong>
                {data.metrics.avg_lock_ms == null
                  ? '暂无样本'
                  : `${data.metrics.avg_lock_ms.toFixed(1)} ms`}
              </strong>
              <small>
                连接均值{' '}
                {data.metrics.avg_connection_ms == null
                  ? '暂无样本'
                  : `${data.metrics.avg_connection_ms.toFixed(1)} ms`}
              </small>
            </article>
            <article>
              <Server size={18} />
              <span>后台处理服务</span>
              <strong>
                {!data.worker_health
                  ? '状态未知'
                  : data.worker_health.available
                    ? '可用'
                    : '暂无有效心跳'}
              </strong>
              <small>
                {data.worker_health?.last_heartbeat_at
                  ? `最近心跳 ${new Date(data.worker_health.last_heartbeat_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}`
                  : data.worker_health
                    ? '尚未收到有效心跳'
                    : '当前接口尚未提供服务健康状态'}
              </small>
            </article>
            <article>
              <Database size={18} />
              <span>AI 已知费用小计</span>
              <strong>
                {data.cost_summary.known_micro === null
                  ? '暂无已知费用'
                  : `¥ ${(data.cost_summary.known_micro / 1_000_000).toFixed(2)}`}
              </strong>
              <small>
                {period} · {data.cost_summary.sample_count} 条记录 ·{' '}
                {data.cost_summary.unknown_count} 条未知 · {data.cost_summary.estimated_count}{' '}
                条估算
              </small>
            </article>
          </section>
          {data.signals.length > 0 && (
            <section className="observability-panel" aria-label="运行异常下一步">
              <header>
                <h2>需要管理员处理</h2>
              </header>
              <div className="observability-health">
                {data.signals.map((s) => (
                  <article key={s.code}>
                    <p>{s.next_action}</p>
                    <a className="button secondary" href={`/workspace/${s.target}`}>
                      打开
                      {s.target === 'queue'
                        ? '处理任务'
                        : s.target === 'operations'
                          ? '费用与运行记录'
                          : '执行记录'}
                    </a>
                  </article>
                ))}
              </div>
            </section>
          )}
          <div className="observability-grid">
            <section className="observability-panel">
              <header>
                <h2>任务队列</h2>
                <span className="field-help">{data.trace_hint}</span>
              </header>
              <div className="observability-queues">
                {Object.entries(data.queues).map(([kind, counts]) => (
                  <article key={kind}>
                    <strong>{queueLabels[kind] ?? kind}</strong>
                    <div>
                      {Object.entries(counts).map(([state, count]) => (
                        <span key={state}>
                          {state} {count}
                        </span>
                      ))}
                      {!Object.keys(counts).length && <span>无积压</span>}
                    </div>
                  </article>
                ))}
              </div>
            </section>
            <section className="observability-panel">
              <header>
                <h2>投影与告警</h2>
              </header>
              <div className="observability-health">
                <p>
                  活跃企业 <strong>{data.projection.total}</strong> · 脏投影{' '}
                  <strong>{data.projection.dirty}</strong> · 从未构建{' '}
                  <strong>{data.projection.never_built}</strong> · 连续失败{' '}
                  <strong>{data.projection.failing}</strong>
                </p>
                {Object.entries(data.alerts).map(([code, value]) => (
                  <p key={code} className={value.critical ? 'danger' : ''}>
                    <AlertTriangle size={14} />
                    {code} · 未结 {value.open} · 严重 {value.critical}
                  </p>
                ))}
                {!Object.keys(data.alerts).length && <p>当前没有未结运行告警。</p>}
              </div>
            </section>
          </div>
        </>
      ) : null}
    </main>
  );
}
