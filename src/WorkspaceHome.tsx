import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, CircleDot, Clock3, FilePlus2, Layers3, List, LoaderCircle, Play, RefreshCw, Save, Search, Table2, TriangleAlert, X } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { AgentCompanyPicker } from './AgentCompanyPicker';
import { formatTaskAmount, roleCopy, savedWorkspaceViews, taskLabels, taskStateLabel, viewStorageKey, workspaceSortLabels } from './workspace-types';
import type { SavedWorkspaceView, WorkspaceDensity, WorkspaceProfile, WorkspaceSort, WorkspaceTask, WorkspaceTasks, TaskKind } from './workspace-types';
import type { AgentSummary } from './agent-workspace-types';
import type { Session } from './types';
import type { BusinessTemplate } from './business-types';
import './workspace.css';

export function WorkspaceHome({ session, profile, profileError, onRetryProfile, onOpen, onNew, onAccounting, onReview, onRuns, revision, paused = false }: {
  session: Session; profile: WorkspaceProfile | null; profileError: string; onRetryProfile: () => void;
  onOpen: (task: WorkspaceTask) => void; onNew: (template: BusinessTemplate) => void; onAccounting: () => void; onReview: () => void; onRuns?: () => void; revision: number; paused?: boolean;
}) {
  const [bucket, setBucket] = useState<'action' | 'waiting'>('action'); const [kind, setKind] = useState<TaskKind | ''>('');
  const [company, setCompany] = useState(''); const [offset, setOffset] = useState(0); const [sort, setSort] = useState<WorkspaceSort>('priority');
  const [density, setDensity] = useState<WorkspaceDensity>('list'); const [query, setQuery] = useState(''); const [search, setSearch] = useState('');
  const [views, setViews] = useState<SavedWorkspaceView[]>([]); const [viewNotice, setViewNotice] = useState('');
  const [value, setValue] = useState<WorkspaceTasks | null>(null); const [summary, setSummary] = useState<AgentSummary | null>(null);
  const [error, setError] = useState(''); const [loading, setLoading] = useState(false); const [opening, setOpening] = useState(''); const [openError, setOpenError] = useState(''); const [privacyRevision, setPrivacyRevision] = useState(0); const [refreshTick, setRefreshTick] = useState(0);
  const epoch = useRef(0); const alive = useRef(true); const scope = useRef(session.scope_key); scope.current = session.scope_key;
  const dataScope = useRef(''); const dataFilter = useRef(''); const summaryScope = useRef(''); const openingLock = useRef(false); const openingController = useRef<AbortController | null>(null);
  const filterKey = JSON.stringify([session.scope_key, bucket, kind, company, offset, sort, search]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const request = ++epoch.current; const started = session.scope_key; setLoading(true); setError('');
    try {
      const params = new URLSearchParams({ bucket, sort, limit: '20', offset: String(offset), ...(kind ? { kind } : {}), ...(company ? { company_id: company } : {}), ...(search ? { q: search } : {}) });
      const next = await api<WorkspaceTasks>(`/workspace/tasks?${params}`, session, { signal });
      if (alive.current && scope.current === started && !signal?.aborted && request === epoch.current) { dataScope.current = started; dataFilter.current = JSON.stringify([started, bucket, kind, company, offset, sort, search]); setValue(next); }
    } catch (caught) {
      if (alive.current && scope.current === started && !signal?.aborted && request === epoch.current) { setValue(null); setSummary(null); if (caught instanceof ApiError && [401, 403, 404].includes(caught.status)) setPrivacyRevision(value => value + 1); setError(errorMessage(caught)); }
    } finally { if (alive.current && scope.current === started && !signal?.aborted && request === epoch.current) setLoading(false); }
  }, [session, bucket, kind, company, offset, sort, search]);

  useEffect(() => {
    alive.current = true; setValue(null); setSummary(null); setOpenError(''); setViews([]); setBucket('action'); setKind(''); setCompany(''); setOffset(0); setSort('priority'); setDensity('list'); setSearch(''); setQuery(''); setViewNotice('');
    try { setViews(savedWorkspaceViews(localStorage.getItem(viewStorageKey(session)))); } catch { /* Privacy mode may disable local storage. */ }
    return () => { alive.current = false; epoch.current++; openingController.current?.abort(); };
  }, [session.scope_key]);
  useEffect(() => { const timeout = setTimeout(() => { setSearch(query.trim()); setOffset(0); }, 220); return () => clearTimeout(timeout); }, [query]);
  useEffect(() => { const controller = new AbortController(); setValue(null); void load(controller.signal); return () => { controller.abort(); epoch.current++; }; }, [load, revision]);
  useEffect(() => {
    const controller = new AbortController(); const started = session.scope_key; setSummary(null);
    void api<AgentSummary>('/agent-workspace/summary', session, { signal: controller.signal })
      .then(next => { if (alive.current && scope.current === started && !controller.signal.aborted) { summaryScope.current = started; setSummary(next); } })
      .catch(caught => { if (alive.current && scope.current === started && !controller.signal.aborted) { setSummary(null); if (caught instanceof ApiError && [401, 403, 404].includes(caught.status)) { setValue(null); setPrivacyRevision(value => value + 1); setError(errorMessage(caught)); } } });
    return () => controller.abort();
  }, [session, revision, refreshTick]);

  async function open(task: WorkspaceTask) {
    if (paused || openingLock.current) return;
    openingLock.current = true; setOpening(task.key); setOpenError(''); const started = session.scope_key;
    const controller = new AbortController(); openingController.current?.abort(); openingController.current = controller;
    try {
      const current = await api<WorkspaceTask>(`/workspace/tasks/${encodeURIComponent(task.kind)}/${encodeURIComponent(task.ref)}`, session, { signal: controller.signal });
      if (alive.current && scope.current === started && !controller.signal.aborted) onOpen(current);
    } catch (caught) {
      if (alive.current && scope.current === started && !controller.signal.aborted) { setValue(null); setSummary(null); if (caught instanceof ApiError && [401, 403, 404].includes(caught.status)) setPrivacyRevision(value => value + 1); setOpenError(errorMessage(caught)); void load(); }
    } finally { if (alive.current && scope.current === started) setOpening(''); openingLock.current = false; }
  }
  function saveView() {
    const next = [...views.slice(-2), { bucket, kind, company_id: company, sort, density }];
    setViews(next);
    try { localStorage.setItem(viewStorageKey(session), JSON.stringify(next)); setViewNotice('已保存筛选。企业权限在每次读取时核验。'); }
    catch { setViewNotice('当前浏览器无法保存筛选，仍可继续工作。'); }
  }
  function restoreView(view: SavedWorkspaceView) {
    setBucket(view.bucket); setKind(view.kind); setCompany(view.company_id); setSort(view.sort); setDensity(view.density); setQuery(''); setSearch(''); setOffset(0); setViewNotice(view.company_id ? '已恢复指定企业筛选；不会切换到其他企业。' : '已恢复常用筛选。');
  }
  function removeView(index: number) {
    const next = views.filter((_, position) => position !== index); setViews(next);
    try { localStorage.setItem(viewStorageKey(session), JSON.stringify(next)); } catch { /* No private records are stored. */ }
  }
  const copy = roleCopy[profile?.role ?? 'viewer']; const visibleValue = dataScope.current === session.scope_key && dataFilter.current === filterKey ? value : null;
  const visibleSummary = summaryScope.current === session.scope_key ? summary : null;
  const rows = visibleValue?.tasks ?? []; const locked = paused || !!opening; const first = bucket === 'action' ? rows[0] : undefined;
  const waiting = visibleSummary ? visibleSummary.counts.active : null;
  return <main className="workspace-home">
    <header className="workspace-heading"><div><p className="workspace-kicker"><CircleDot size={13} /> HENGZHANG · ACCOUNTING AGENT</p><h1>{copy.name}</h1><p>{copy.subtitle}</p></div><div className="workspace-header-actions"><button className="button secondary workspace-refresh" disabled={loading || locked} onClick={() => { onRetryProfile(); setRefreshTick(value => value + 1); void load(); }}><RefreshCw size={15} /><span>刷新工作台</span></button>{profile?.can_review && <button className="button primary" disabled={locked} onClick={onReview}>开始批量复核<ArrowRight size={15} /></button>}</div></header>
    {profileError && <p className="inline-error" role="alert">{profileError}<button className="text-button" onClick={onRetryProfile}>重新读取权限</button></p>}
    {!profile && !profileError && <p className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取我的工作范围…</p>}
    {profile?.can_submit && <section className="workspace-intake-actions" aria-label="快速录入"><div><FilePlus2 size={21} /><strong>记录一笔业务</strong><span>附件与事项一起提交，AI 协助填写。</span></div>{([['reimbursement', '报销'], ['procurement_expense', '采购 / 费用'], ['daily_cashflow', '日常收支']] as const).map(([template, label]) => <button key={template} className="button secondary" disabled={locked} onClick={() => onNew(template)}>{label}<ArrowRight size={15} /></button>)}</section>}
    <section className="workspace-summary" aria-label="我的工作概览">
      <button className={bucket === 'action' ? 'active' : ''} disabled={locked} onClick={() => { setBucket('action'); setOffset(0); }}><span><TriangleAlert size={16} />需要我处理</span><strong>{visibleValue ? visibleValue.counts.action ?? 0 : '—'}</strong><small>审批 · 异常 · 复核</small></button>
      <button className={bucket === 'waiting' ? 'active' : ''} disabled={locked} onClick={() => { setBucket('waiting'); setOffset(0); }}><span><Clock3 size={16} />等待他人 / 后台</span><strong>{visibleValue ? visibleValue.counts.waiting ?? 0 : '—'}</strong><small>保留当前办理进度</small></button>
      <button className="workspace-runtime-link" disabled={locked || !onRuns} onClick={onRuns}><span><Layers3 size={16} />Agent 后台</span><strong>{waiting ?? '—'}</strong><small>{visibleSummary ? `${visibleSummary.counts.attention} 项需要介入` : '读取真实运行状态'}<ArrowRight size={13} /></small></button>
      {profile?.can_accounting && <button className="workspace-accounting-shortcuts" disabled={locked} onClick={onAccounting}><span>当前授权工作范围</span><strong>{profile.book_count}<small> 个会计期间</small></strong><span>查看企业账套<ArrowRight size={14} /></span></button>}
    </section>
    <section className="panel workspace-inbox">
      <div className="workspace-inbox-heading"><div><p className="workspace-section-eyebrow">集中分诊</p><h2>{bucket === 'action' ? '需要我处理' : '办理进度'}<span>{visibleValue?.pagination.total ?? '—'}</span></h2></div><div className="workspace-view-tools"><div className="workspace-density" role="group" aria-label="待办显示密度"><button className={density === 'list' ? 'active' : ''} aria-label="清单视图" aria-pressed={density === 'list'} disabled={locked} onClick={() => setDensity('list')}><List size={16} /></button><button className={density === 'table' ? 'active' : ''} aria-label="紧凑表格视图" aria-pressed={density === 'table'} disabled={locked} onClick={() => setDensity('table')}><Table2 size={16} /></button></div><button className="button secondary small" disabled={locked} onClick={saveView}><Save size={14} />保存视图</button>{first && <button className="button primary small" disabled={locked || loading} onClick={() => void open(first)}><Play size={14} />处理下一项</button>}</div></div>
      <div className="workspace-inbox-filters"><label className="agent-search workspace-task-search"><Search size={16} /><input aria-label="搜索工作事项" placeholder="搜索事项或企业" maxLength={200} value={query} disabled={locked} onChange={event => setQuery(event.target.value)} />{query && <button type="button" aria-label="清空事项搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label><label className="workspace-select"><span className="sr-only">事项类型</span><select aria-label="筛选待办类型" value={kind} disabled={locked} onChange={event => { setKind(event.target.value as TaskKind | ''); setOffset(0); }}><option value="">全部事项类型</option>{Object.entries(taskLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><AgentCompanyPicker session={session} value={company} disabled={locked} revision={revision + privacyRevision} onChange={id => { setCompany(id); setOffset(0); }} /><label className="workspace-select"><span className="sr-only">排序</span><select aria-label="待办排序" value={sort} disabled={locked} onChange={event => { setSort(event.target.value as WorkspaceSort); setOffset(0); }}>{Object.entries(workspaceSortLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      {!!views.length && <div className="workspace-saved-views" aria-label="常用工作视图"><span>常用视图</span>{views.map((view, index) => <div key={index}><button disabled={locked} onClick={() => restoreView(view)}>{index + 1} · {view.kind ? taskLabels[view.kind] : view.bucket === 'action' ? '待处理' : '办理进度'}{view.company_id ? ' · 指定企业' : ''}</button><button aria-label={`删除常用视图 ${index + 1}`} disabled={locked} onClick={() => removeView(index)}><X size={12} /></button></div>)}</div>}
      {viewNotice && <p className="workspace-view-notice" role="status">{viewNotice}</p>}
      {(openError || error) && <p className="inline-error workspace-load-error" role="alert">{openError || error}</p>}
      {loading ? <p className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取待办…</p> : rows.length ? <div className={'workspace-task-list ' + (density === 'table' ? 'workspace-table-view' : '')}>
        {density === 'table' && <div className="workspace-table-labels" aria-hidden="true"><span>事项 / 企业</span><span>状态 / 期限</span><span>金额</span><span>下一步</span></div>}
        {rows.map(task => <article className={'workspace-task ' + (task.overdue ? 'overdue' : '')} key={task.key} data-task-key={task.key}>
          <div className="workspace-task-main"><div className="workspace-task-meta"><span className="workspace-task-kind">{taskLabels[task.kind]}</span><span>{task.company_name}</span>{task.overdue && <span className="case-overdue">已逾期</span>}<span className="workspace-task-mobile-amount">{formatTaskAmount(task.amount)}</span></div><h3>{task.title}</h3><small>{task.responsible}</small></div>
          <div className="workspace-task-state"><span className={'workspace-state-pill ' + (task.bucket === 'action' ? 'attention' : '')}>{taskStateLabel(task)}</span><small>{task.due_on ? `期限 ${task.due_on}` : '未设置期限'}</small></div>
          <span className="workspace-task-amount">{formatTaskAmount(task.amount)}</span>
          <button className="button secondary" disabled={locked} onClick={() => void open(task)}>{opening === task.key ? <LoaderCircle className="spinner" size={15} /> : null}{task.action_label}<ArrowRight size={15} /></button>
        </article>)}
      </div> : !error && <div className="workspace-clear"><CheckCircle2 size={27} /><strong>{bucket === 'action' ? '当前筛选下没有待处理事项' : '当前筛选下暂无等待事项'}</strong><p>{company ? '保持指定企业范围，可调整事项类型或搜索条件。' : '新审批、补件、会计异常和迁移事项会集中显示在这里。'}</p></div>}
      {visibleValue && <div className="pagination-controls"><span>共 {visibleValue.pagination.total} 项 · 每页 20 项</span><button className="button secondary small" disabled={loading || locked || offset === 0} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页待办</button><button className="button secondary small" disabled={loading || locked || !visibleValue.pagination.has_more} onClick={() => setOffset(n => n + 20)}>下一页待办</button></div>}
    </section><p className="workspace-boundary"><CircleDot size={12} />AI 在后台识别、核对与生成草稿；会计复核后导出。</p>
  </main>;
}
