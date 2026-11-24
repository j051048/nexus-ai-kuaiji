import { useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, Database, RefreshCw, Server } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-observability.css';
import { readFirmSnapshot } from './firm-observability-contract';
import { cny, percent, readValueBaseline, type ValueBaseline } from './value-baseline';
import { FscOrganizationPanel } from './FscOrganizationPanel';
import { FscContractPanel } from './FscContractPanel';
import { FscImportPanel } from './FscImportPanel';
import { FscEvidencePanel } from './FscEvidencePanel';
import { FscProfilesPanel } from './FscProfilesPanel';

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
    target: 'queue' | 'operations' | 'runs' | 'workbench';
    automatic_action: false;
  }[];
  queues: Record<string, Record<string, number>>;
  operations: {
    materials_pending: number;
    drafts_pending: number;
    source_rejections: number;
    notification_unknown: number;
    oldest_queue_age_seconds: number;
  };
  sla?: {
    thresholds: {
      queue_age_seconds: number;
      source_rejections: number;
      notification_unknown: number;
    };
    breaches: { code: string; count: number }[];
    automatic_action: false;
  };
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
  const [companies, setCompanies] = useState<{ id: string; name: string }[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [value, setValue] = useState<ValueBaseline | null>(null);
  const [fde, setFde] = useState<{
    adapters: {
      code: string;
      label: string;
      live_external: boolean;
      native_format?: boolean;
      contract_version: string;
      contract_sha256: string;
      certification: string;
    }[];
    catalog_fingerprint: string;
  } | null>(null);
  const [valueLoading, setValueLoading] = useState(false);
  const [valueError, setValueError] = useState('');
  const [radar, setRadar] = useState<{
    period: string;
    books: {
      book_id: string;
      company_name: string;
      closable: boolean;
      period_status: string;
      blockers: { code: string; label: string; count: number; next_action: string }[];
    }[];
    counts: { books: number; blocked: number; closable: number; closed: number };
    note: string;
  } | null>(null);
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
  useEffect(() => {
    const controller = new AbortController();
    void api<{ books: { company_id: string; company_name: string }[] }>('/workbench', session, {
      signal: controller.signal,
    })
      .then((result) => {
        const unique = new Map<string, string>();
        for (const book of result.books) unique.set(book.company_id, book.company_name);
        const items = [...unique].map(([id, name]) => ({ id, name }));
        setCompanies(items);
        setCompanyId((current) => current || items[0]?.id || '');
      })
      .catch(() => setCompanies([]));
    return () => controller.abort();
  }, [session]);
  useEffect(() => {
    if (!companyId) {
      setValue(null);
      return;
    }
    const controller = new AbortController();
    setValueLoading(true);
    setValueError('');
    void api<ValueBaseline>(
      '/firm/observability/value?' +
        new URLSearchParams({ company_id: companyId, period }).toString(),
      session,
      { signal: controller.signal },
    )
      .then((raw) => setValue(readValueBaseline(raw)))
      .catch((failure) => {
        setValue(null);
        setValueError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setValueLoading(false);
      });
    return () => controller.abort();
  }, [companyId, period, revision, session]);
  useEffect(() => {
    if (!companyId) {
      setFde(null);
      return;
    }
    const controller = new AbortController();
    void api<NonNullable<typeof fde>>(
      '/firm/fde/adapters?' + new URLSearchParams({ company_id: companyId }).toString(),
      session,
      { signal: controller.signal },
    )
      .then((raw) => setFde(raw))
      .catch(() => setFde(null));
    return () => controller.abort();
  }, [companyId, revision, session]);
  useEffect(() => {
    const controller = new AbortController();
    void api<NonNullable<typeof radar>>(
      '/firm/observability/closing-radar?' + new URLSearchParams({ period }).toString(),
      session,
      { signal: controller.signal },
    )
      .then(setRadar)
      .catch(() => setRadar(null));
    return () => controller.abort();
  }, [period, revision, session]);
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
          <p className="eyebrow">运行状态与链路追踪</p>
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
          <section className="observability-panel" aria-label="办理积压与队列年龄">
            <header>
              <h2>办理积压</h2>
              <span className="field-help">仅聚合当前事务所；每项打开原办理对象，不自动处理。</span>
            </header>
            {data.sla?.breaches.length ? (
              <p className="form-error" role="alert">
                共享中心 SLA 超限：
                {data.sla.breaches.map((item) => `${item.code} ${item.count}`).join('；')}
                ；需人工核查，不自动重试。
              </p>
            ) : null}
            <div className="observability-cards observability-operations">
              <article>
                <span>资料待验收</span>
                <strong>{data.operations.materials_pending}</strong>
                <a className="text-button" href="/workspace/monthly?focus=materials">
                  打开月度工作台
                </a>
              </article>
              <article>
                <span>待复核凭证</span>
                <strong>{data.operations.drafts_pending}</strong>
                <a className="text-button" href="/workspace/monthly?focus=review">
                  打开待复核
                </a>
              </article>
              <article>
                <span>规费/成本拒绝行</span>
                <strong>{data.operations.source_rejections}</strong>
                <a className="text-button" href="/workspace/accounting">
                  打开税工作台
                </a>
              </article>
              <article>
                <span>通知 unknown</span>
                <strong>{data.operations.notification_unknown}</strong>
                <a className="text-button" href="/workspace/operations">
                  打开人工接手
                </a>
              </article>
              <article>
                <span>最长队列等待</span>
                <strong>
                  {data.operations.oldest_queue_age_seconds >= 3600
                    ? `${(data.operations.oldest_queue_age_seconds / 3600).toFixed(1)} 小时`
                    : `${data.operations.oldest_queue_age_seconds} 秒`}
                </strong>
                <a className="text-button" href="/workspace/queue">
                  打开任务队列
                </a>
              </article>
            </div>
          </section>
          <section className="observability-panel" aria-label="FDE 价值基线">
            <header>
              <h2>价值基线</h2>
              <span className="field-help">只统计系统可观测结果；系统耗时不是人工工时。</span>
            </header>
            {companies.length > 1 && (
              <label className="observability-value-picker">
                企业
                <select value={companyId} onChange={(event) => setCompanyId(event.target.value)}>
                  {companies.map((company) => (
                    <option key={company.id} value={company.id}>
                      {company.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {valueError ? (
              <p className="inline-error">{valueError}</p>
            ) : valueLoading ? (
              <p className="field-help">正在读取价值基线…</p>
            ) : value ? (
              <>
                <div className="observability-cards observability-value">
                  <article>
                    <span>自动映射率</span>
                    <strong>{percent(value.rates.draft_mapping_rate)}</strong>
                    <small>
                      {value.counts.mapped_drafts} 张映射 / {value.counts.submitted_events} 笔提交
                    </small>
                  </article>
                  <article>
                    <span>首次通过率</span>
                    <strong>{percent(value.rates.first_pass_rate)}</strong>
                    <small>{value.counts.returned_events} 笔退回</small>
                  </article>
                  <article>
                    <span>例外率</span>
                    <strong>{percent(value.rates.exception_rate)}</strong>
                    <small>以提交事件为分母</small>
                  </article>
                  <article>
                    <span>复核转化率</span>
                    <strong>{percent(value.rates.review_conversion_rate)}</strong>
                    <small>{value.counts.approved_drafts} 张已复核</small>
                  </article>
                  <article>
                    <span>AI 单笔已通过成本</span>
                    <strong>{cny(value.cost.ai_cost_per_approved_micro)}</strong>
                    <small>仅 AI 模型费用，不含人工与基础设施</small>
                  </article>
                </div>
                <p className="observability-value-note">{value.elapsed.note}</p>
              </>
            ) : (
              <p className="field-help">选择企业后读取价值基线。</p>
            )}
          </section>
          {radar && (
            <section className="observability-panel" aria-label="持续结账雷达">
              <header>
                <h2>持续结账雷达</h2>
                <span className="field-help">{radar.note}</span>
              </header>
              <div className="observability-cards observability-radar-counts">
                <article>
                  <span>账套</span>
                  <strong>{radar.counts.books}</strong>
                  <small>{radar.period} 范围</small>
                </article>
                <article>
                  <span>有阻断</span>
                  <strong>{radar.counts.blocked}</strong>
                  <small>需项目经理处理</small>
                </article>
                <article>
                  <span>可关账</span>
                  <strong>{radar.counts.closable}</strong>
                  <small>仍需人工确认</small>
                </article>
                <article>
                  <span>已关账</span>
                  <strong>{radar.counts.closed}</strong>
                  <small>只读投影</small>
                </article>
              </div>
              <div className="observability-radar-books">
                {radar.books
                  .filter((book) => book.blockers.length > 0)
                  .slice(0, 8)
                  .map((book) => (
                    <article key={book.book_id}>
                      <strong>{book.company_name}</strong>
                      <span>
                        {book.period_status} ·{' '}
                        {book.blockers.map((item) => `${item.label} ${item.count}`).join(' / ')}
                      </span>
                      <small>{book.blockers[0]?.next_action}</small>
                    </article>
                  ))}
                {radar.books.every((book) => book.blockers.length === 0) && (
                  <p className="field-help">当前期间没有登记阻断；关账仍需人工判断。</p>
                )}
              </div>
            </section>
          )}
          <FscProfilesPanel session={session} />
          <FscOrganizationPanel session={session} />
          <FscContractPanel session={session} />
          <FscImportPanel session={session} />
          <FscEvidencePanel session={session} />
          <section className="observability-panel" aria-label="FDE 适配器契约">
            <div className="observability-panel-head">
              <h2>FDE 适配器契约</h2>
              <span>本地合成契约，不代表供应商认证</span>
            </div>
            {fde ? (
              <>
                <p className="fde-contract-fingerprint">
                  目录指纹 <code>{fde.catalog_fingerprint.slice(0, 16)}</code>
                </p>
                <div className="fde-contract-list">
                  {fde.adapters.map((adapter) => (
                    <article key={adapter.code}>
                      <strong>{adapter.label}</strong>
                      <span>
                        {adapter.code} · {adapter.contract_version} · local_contract_only
                      </span>
                      <code>{adapter.contract_sha256.slice(0, 12)}</code>
                      <span>
                        {adapter.live_external ? '已接外部' : '未接真实外部'}
                        {adapter.native_format === false ? ' · 非原生格式' : ''}
                      </span>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <p className="observability-empty">选择企业后读取适配器契约。</p>
            )}
            <p className="fde-contract-note">
              适配器可用不等于客户真实接口已接通，也不授予写入或过账权限。
            </p>
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
                          : s.target === 'workbench'
                            ? '月度工作台'
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
