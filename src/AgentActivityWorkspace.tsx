import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleDot,
  Clock3,
  FileScan,
  Layers3,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { AgentCompanyPicker } from './AgentCompanyPicker';
import {
  agentDestination,
  agentFilterLabels,
  agentKindLabels,
  runProgress,
  runStages,
  runStatusLabel,
} from './agent-workspace-types';
import type {
  AgentRoute,
  AgentRun,
  AgentRunFilter,
  AgentRuns,
  AgentSummary,
} from './agent-workspace-types';
import type { Session } from './types';
import './workspace.css';
import './agent-activity.css';

export function AgentActivityWorkspace({
  session,
  onOpen,
  onBack,
  onBusyChange,
  revision = 0,
}: {
  session: Session;
  onOpen: (route: AgentRoute) => void | Promise<void>;
  onBack?: () => void;
  onBusyChange?: (busy: boolean) => void;
  revision?: number;
}) {
  const [filter, setFilter] = useState<AgentRunFilter>('active');
  const [company, setCompany] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AgentRuns | null>(null);
  const [summary, setSummary] = useState<AgentSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState('');
  const [privacyRevision, setPrivacyRevision] = useState(0);
  const [tick, setTick] = useState(0);
  const epoch = useRef(0);
  const scope = useRef(session.scope_key);
  scope.current = session.scope_key;
  const loadedScope = useRef('');
  const loadedFilter = useRef('');
  const summaryScope = useRef('');
  const alive = useRef(true);
  const openingLock = useRef(false);
  const busyCallback = useRef(onBusyChange);
  busyCallback.current = onBusyChange;
  const filterKey = JSON.stringify([session.scope_key, filter, company, search, offset]);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const request = ++epoch.current;
      const started = session.scope_key;
      setLoading(true);
      setError('');
      const results = await Promise.allSettled([
        api<AgentRuns>(
          `/agent-workspace/runs?${new URLSearchParams({ state: filter, limit: '20', offset: String(offset), ...(company ? { company_id: company } : {}), ...(search ? { q: search } : {}) })}`,
          session,
          { signal },
        ),
        api<AgentSummary>('/agent-workspace/summary', session, { signal }),
      ]);
      if (
        !alive.current ||
        scope.current !== started ||
        signal.aborted ||
        request !== epoch.current
      )
        return;
      const [runsResult, summaryResult] = results;
      if (runsResult.status === 'fulfilled' && summaryResult.status === 'fulfilled') {
        loadedScope.current = started;
        loadedFilter.current = JSON.stringify([started, filter, company, search, offset]);
        summaryScope.current = started;
        setData(runsResult.value);
        setSummary(summaryResult.value);
      } else {
        const caught =
          runsResult.status === 'rejected'
            ? runsResult.reason
            : summaryResult.status === 'rejected'
              ? summaryResult.reason
              : new Error('运行状态暂不可用');
        setData(null);
        setSummary(null);
        if (caught instanceof ApiError && [401, 403, 404].includes(caught.status))
          setPrivacyRevision((value) => value + 1);
        setError(errorMessage(caught));
      }
      setLoading(false);
    },
    [session, filter, company, search, offset],
  );
  useEffect(() => {
    alive.current = true;
    setData(null);
    setSummary(null);
    setFilter('active');
    setCompany('');
    setQuery('');
    setSearch('');
    setOffset(0);
    setOpening('');
    setError('');
    return () => {
      alive.current = false;
      epoch.current++;
      busyCallback.current?.(false);
    };
  }, [session.scope_key]);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query.trim());
      setOffset(0);
    }, 220);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current++;
    };
  }, [load, tick, revision]);
  useEffect(() => {
    if (filter !== 'active') return;
    const timer = setInterval(() => {
      if (!document.hidden && !openingLock.current) setTick((value) => value + 1);
    }, 15000);
    return () => clearInterval(timer);
  }, [filter]);
  async function open(run: AgentRun) {
    if (openingLock.current) return;
    const route = agentDestination(run);
    if (!route) {
      setError('任务缺少准确目标，请刷新状态后重试。');
      return;
    }
    const started = session.scope_key;
    openingLock.current = true;
    setOpening(run.key);
    onBusyChange?.(true);
    setError('');
    try {
      if (scope.current === started && alive.current) await onOpen(route);
    } catch (caught) {
      if (alive.current && scope.current === started) {
        setData(null);
        setSummary(null);
        if (caught instanceof ApiError && [401, 403, 404].includes(caught.status))
          setPrivacyRevision((value) => value + 1);
        setError(errorMessage(caught));
      }
    } finally {
      openingLock.current = false;
      if (alive.current && scope.current === started) {
        setOpening('');
        onBusyChange?.(false);
      }
    }
  }
  const visibleData =
    loadedScope.current === session.scope_key && loadedFilter.current === filterKey ? data : null;
  const visibleSummary = summaryScope.current === session.scope_key ? summary : null;
  const rows = visibleData?.runs ?? [];
  const locked = !!opening;
  return (
    <main className="agent-activity-workspace">
      <header className="agent-activity-heading">
        <div>
          {onBack && (
            <button className="text-button agent-activity-back" disabled={locked} onClick={onBack}>
              <ArrowLeft size={14} />
              返回我的工作
            </button>
          )}
          <h1>后台运行</h1>
          <p>查看真实执行进度，在需要时接手处理。</p>
        </div>
        <button
          className="button secondary"
          disabled={loading || locked}
          onClick={() => setTick((value) => value + 1)}
        >
          <RefreshCw size={15} />
          刷新状态
        </button>
      </header>
      <section className="agent-runtime-overview" aria-label="授权范围运行概览">
        <div className="agent-runtime-identity">
          <span className="agent-runtime-mark">
            <Layers3 size={22} />
          </span>
          <div>
            <strong>会计 Agent</strong>
            <span>当前授权范围 · 持久后台任务</span>
          </div>
        </div>
        {(
          [
            ['active', '执行中', Clock3],
            ['attention', '需要介入', TriangleAlert],
            ['done', '执行记录', CheckCircle2],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            className={filter === key ? 'active' : ''}
            disabled={locked}
            onClick={() => {
              setFilter(key);
              setOffset(0);
            }}
          >
            <span>
              <Icon size={14} />
              {label}
            </span>
            <strong>{visibleSummary?.counts[key] ?? '—'}</strong>
          </button>
        ))}
      </section>
      <section className="panel agent-runs-panel">
        <div className="agent-run-toolbar">
          <div className="agent-run-tabs" role="group" aria-label="运行任务状态">
            {Object.entries(agentFilterLabels).map(([key, label]) => (
              <button
                key={key}
                className={filter === key ? 'active' : ''}
                aria-pressed={filter === key}
                disabled={locked}
                onClick={() => {
                  setFilter(key as AgentRunFilter);
                  setOffset(0);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="agent-run-filters">
            <label className="agent-search">
              <Search size={16} />
              <input
                aria-label="搜索后台任务"
                value={query}
                maxLength={200}
                placeholder="搜索任务或企业"
                disabled={locked}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <button type="button" aria-label="清空后台搜索" onClick={() => setQuery('')}>
                  <X size={14} />
                </button>
              )}
            </label>
            <AgentCompanyPicker
              session={session}
              value={company}
              onChange={(id) => {
                setCompany(id);
                setOffset(0);
              }}
              label="筛选后台企业"
              endpoint="/agent-workspace/companies"
              disabled={locked}
              revision={revision + privacyRevision}
            />
          </div>
        </div>
        {error && (
          <p className="inline-error agent-run-error" role="alert">
            {error}
          </p>
        )}
        {loading && !visibleData ? (
          <div className="agent-run-loading">
            <LoaderCircle className="spinner" size={18} />
            <span>读取后台作业事实…</span>
          </div>
        ) : rows.length ? (
          <div className="agent-runs-list">
            {rows.map((run) => {
              const progress = runProgress(run);
              const stages = runStages(run);
              const route = agentDestination(run);
              return (
                <article
                  className={'agent-run-card ' + (run.needs_attention ? 'attention' : '')}
                  key={run.key}
                  data-run-key={run.key}
                >
                  <div className="agent-run-heading">
                    <span className="agent-run-icon">
                      {run.kind === 'ocr' ? (
                        <FileScan size={19} />
                      ) : run.kind === 'proposal' ? (
                        <Layers3 size={19} />
                      ) : (
                        <ShieldCheck size={19} />
                      )}
                    </span>
                    <div className="agent-run-title">
                      <div>
                        <span className="agent-run-kind">{agentKindLabels[run.kind]}</span>
                        <span className="agent-run-company">{run.company_name}</span>
                      </div>
                      <h2>{run.title}</h2>
                    </div>
                    <span
                      className={
                        'agent-run-status ' +
                        (run.needs_attention
                          ? 'attention'
                          : run.status === 'running'
                            ? 'running'
                            : '')
                      }
                    >
                      {run.status === 'running' && <CircleDot size={12} />}
                      {runStatusLabel(run.status)}
                      {run.needs_attention &&
                      ['queued', 'waiting', 'succeeded'].includes(run.status)
                        ? ' · 需介入'
                        : ''}
                    </span>
                  </div>
                  <div className="agent-run-body">
                    <div className="agent-run-evidence">
                      <div className="agent-run-trace" aria-label="已记录的执行阶段">
                        {stages.map((stage, index) => (
                          <span key={index} className={stage.state}>
                            {stage.state === 'complete' ? (
                              <Check size={12} />
                            ) : stage.state === 'current' ? (
                              <CircleDot size={12} />
                            ) : stage.state === 'attention' ? (
                              <TriangleAlert size={12} />
                            ) : (
                              <span className="agent-unknown-dot" />
                            )}
                            {stage.label}
                            {stage.state === 'unknown' && <small>未核验</small>}
                          </span>
                        ))}
                      </div>
                      <div className="agent-run-progress">
                        <span>{progress.label}</span>
                        {progress.percent != null && (
                          <progress
                            aria-label={`${agentKindLabels[run.kind]}已处理进度`}
                            max={100}
                            value={progress.percent}
                          />
                        )}
                        <small>
                          {run.updated_at
                            ? `最近更新 ${new Date(run.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}`
                            : '更新时间待核实'}
                        </small>
                      </div>
                    </div>
                    <div className="agent-run-next">
                      <span>下一步 · {run.next_owner}</span>
                      <strong>{run.next_action}</strong>
                      <button
                        className="button secondary small"
                        disabled={locked || !route}
                        onClick={() => void open(run)}
                      >
                        {opening === run.key && <LoaderCircle className="spinner" size={14} />}
                        {run.needs_attention ? '接手处理' : '查看任务'}
                        <ArrowRight size={14} />
                      </button>
                    </div>
                  </div>
                  {run.needs_attention && (
                    <p className="agent-run-attention-note">
                      <TriangleAlert size={12} />
                      {run.status === 'stale'
                        ? '业务事实已变化，请在详情中核对版本与当前资料。'
                        : run.status === 'retry_wait'
                          ? '任务处于等待重试状态，实际重试与取消由任务详情控制。'
                          : '打开任务详情查看原因与资料，确认后继续处理。'}
                    </p>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          !error && (
            <div className="agent-runtime-empty">
              <CheckCircle2 size={27} />
              <strong>
                {filter === 'attention'
                  ? '当前筛选下没有需要介入的任务'
                  : filter === 'active'
                    ? '当前筛选下没有执行中的任务'
                    : '当前筛选下暂无执行记录'}
              </strong>
              <p>任务会在收到资料并进入后台处理后显示。</p>
            </div>
          )
        )}
        {visibleData && (
          <div className="pagination-controls">
            <span>共 {visibleData.pagination.total} 项 · 每页 20 项</span>
            <button
              className="button secondary small"
              disabled={loading || locked || offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - 20))}
            >
              上一页任务
            </button>
            <button
              className="button secondary small"
              disabled={loading || locked || !visibleData.pagination.has_more}
              onClick={() => setOffset((value) => value + 20)}
            >
              下一页任务
            </button>
          </div>
        )}
      </section>
      <p className="agent-runtime-boundary">
        <ShieldCheck size={13} />
        显示当前授权的真实任务状态。识别或草稿任务完成后，凭证仍需会计复核。
      </p>
    </main>
  );
}
