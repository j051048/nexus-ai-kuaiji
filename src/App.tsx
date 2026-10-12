import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import {
  ArrowDownLeft, ArrowRight, ArrowUpRight, Building2, Check, CheckCheck, CheckCircle2,
  ChevronRight, CircleHelp, ClipboardCheck, Download, FileCheck2, FileText, FolderOpen,
  LayoutDashboard, LoaderCircle, Plus, Search, ShieldCheck, Sparkles, Trash2, TriangleAlert,
  Upload, Users, LogOut, X, FileSearch, Layers3, Activity,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { AuthScreen, NoAuthorization } from './AuthScreen';
import { useAccountingAuth } from './useAccountingAuth';
import { AccessManagement } from './AccessManagement';
import { BusinessWorkspace } from './BusinessWorkspace';
import { IntelligencePanel } from './IntelligencePanel';
import { IntelligenceWorkbench } from './IntelligenceWorkbench';
import { QueueWorkbench } from './QueueWorkbench';
import { OperationsWorkbench } from './OperationsWorkbench';
import type { OperationsCompany } from './operations-types';
import { EXAMPLE_CSV, MAX_CSV_BYTES, downloadCsv, formatCents, formatMoney, moneyToCents, toSourceRows } from './csv';
import type { Book, BookDetail, Company, Draft, FirmRole, JournalLine, Session, Workbench } from './types';

type Filter = 'all' | 'pending' | 'exception' | 'approved';
type ModalName = 'book' | 'import' | 'help' | 'access' | null;
type Notice = { kind: 'success' | 'error'; text: string } | null;

const statusLabels: Record<Draft['status'], string> = { draft: '待审核', approved: '已审核', exported: '已导出' };
const roleLabels: Record<FirmRole, string> = { owner: '事务所负责人', admin: '事务所管理员', operator: '记账经办员', reviewer: '会计复核员', viewer: '只读成员', client_employee: '客户企业成员', client_member: '客户企业成员' };

function Spinner() { return <LoaderCircle className="spinner" size={16} aria-hidden="true" />; }

function EmptyState({ title, children, icon = <FolderOpen size={28} /> }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

function Modal({ title, subtitle, onClose, children, busy = false, error }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode; busy?: boolean; error?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="modal" aria-labelledby="modal-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="modal-header"><div><h2 id="modal-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button type="button" className="icon-button" disabled={busy} onClick={onClose} aria-label="关闭弹窗"><X size={20} /></button></div>
    {error && <div className="dialog-error" role="alert"><TriangleAlert size={16} />{error}</div>}
    {children}
  </dialog>;
}

export default function App() {
  const [businessView, setBusinessView] = useState(false);
  const [intelligenceView, setIntelligenceView] = useState(false);
  const [queueView, setQueueView] = useState(false);
  const [operationsView, setOperationsView] = useState(false);
  const [operationsCompanies, setOperationsCompanies] = useState<OperationsCompany[]>([]);
  const [queueTarget, setQueueTarget] = useState('');
  const [businessTarget, setBusinessTarget] = useState('');
  const [businessBusy, setBusinessBusy] = useState(false);
  const pendingReview = useRef<{ bookId: string; draftId: string } | null>(null);
  const changeBusinessBusy = useCallback((value: boolean) => setBusinessBusy(value), []);
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [bookId, setBookId] = useState('');
  const [storedDetail, setDetail] = useState<BookDetail | null>(null);
  const [draftId, setDraftId] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [listTab, setListTab] = useState<'drafts' | 'sources' | 'exports'>('drafts');
  const [exportHistory, setExportHistory] = useState<{ id: string; count: number; filename: string; created_at: string }[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [modal, setModal] = useState<ModalName>(null);
  const [operationBusy, setBusy] = useState('');
  const [intelligenceBusy, setIntelligenceBusy] = useState(false);
  const busy = operationBusy || (intelligenceBusy ? 'intelligence' : '');
  const changeIntelligenceBusy = useCallback((value: boolean) => setIntelligenceBusy(value), []);
  const [bootLoading, setBootLoading] = useState(true);
  const [bookLoading, setBookLoading] = useState(false);
  const [bootError, setBootError] = useState('');
  const [bookError, setBookError] = useState('');
  const [notice, setNotice] = useState<Notice>(null);
  const bookIdRef = useRef(bookId);
  const actionLock = useRef(false);
  const exportRequest = useRef<{ signature: string; key: string } | null>(null);
  bookIdRef.current = bookId;

  const clearWorkspace = useCallback(() => {
    setBusinessView(false); setIntelligenceView(false); setQueueView(false); setQueueTarget(''); setOperationsView(false); setOperationsCompanies([]); setIntelligenceBusy(false); setBusinessTarget(''); setBusinessBusy(false); pendingReview.current = null;
    setWorkbench(null); setBookId(''); setDetail(null); setDraftId(''); setSelectedIds([]);
    setExportHistory([]); setHistoryError(''); setHistoryLoading(false);
    setBookError(''); setBookLoading(false); setBootError(''); setNotice(null); setModal(null);
    setFilter('all'); setSearch(''); setListTab('drafts');
    setBusy(''); actionLock.current = false; exportRequest.current = null;
  }, []);
  const auth = useAccountingAuth(clearWorkspace);
  const { session, me } = auth;
  const sessionScope = useRef<string | null>(null);
  sessionScope.current = session?.scope_key ?? null;
  useEffect(() => { if (session?.role === 'client_employee' || session?.role === 'client_member') setBusinessView(true); }, [session?.scope_key]);

  useEffect(() => {
    setOperationsCompanies([]);
    if (!session?.firm_id) return;
    const controller = new AbortController();
    void api<{ companies: OperationsCompany[] }>('/operations/companies', session, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setOperationsCompanies(value.companies); })
      .catch(() => { if (!controller.signal.aborted) setOperationsCompanies([]); });
    return () => controller.abort();
  }, [session]);

  const initialize = useCallback(async (signal?: AbortSignal) => {
    if (!session?.firm_id) return;
    const scope = session.scope_key;
    setBootLoading(true); setBootError('');
    try {
      const data = await api<Workbench>('/workbench', session, { signal });
      if (signal?.aborted || sessionScope.current !== scope) return;
      setWorkbench(data);
      setBookId(current => data.books.some(book => book.id === current) ? current : data.books[0]?.id ?? '');
    } catch (error) {
      if (!signal?.aborted && sessionScope.current === scope) setBootError(errorMessage(error));
    } finally { if (!signal?.aborted && sessionScope.current === scope) setBootLoading(false); }
  }, [session]);

  useEffect(() => {
    const controller = new AbortController();
    void initialize(controller.signal);
    return () => controller.abort();
  }, [initialize]);

  useEffect(() => {
    if (!session || !bookId) return;
    const controller = new AbortController();
    setDetail(null);
    setDraftId('');
    setSelectedIds([]);
    setBookLoading(true);
    setBookError('');
    exportRequest.current = null;
    void api<BookDetail>(`/books/${encodeURIComponent(bookId)}`, session, { signal: controller.signal })
      .then(data => {
        if (controller.signal.aborted) return;
        setDetail(data);
        const requested = pendingReview.current?.bookId === data.book.id ? pendingReview.current.draftId : undefined;
        setDraftId(requested && data.drafts.some(draft => draft.id === requested) ? requested : data.drafts.find(draft => draft.status === 'draft')?.id ?? data.drafts[0]?.id ?? '');
        pendingReview.current = null;
      })
      .catch(error => { if (!controller.signal.aborted) setBookError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setBookLoading(false); });
    return () => controller.abort();
  }, [bookId, session]);

  useEffect(() => {
    if (!notice || notice.kind === 'error') return;
    const timeout = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    setExportHistory([]);
    setHistoryError('');
    if (!session || !bookId || listTab !== 'exports') return;
    const controller = new AbortController();
    setHistoryLoading(true);
    void api<{ exports: { id: string; count: number; filename: string; created_at: string }[] }>(`/books/${encodeURIComponent(bookId)}/exports`, session, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setExportHistory(result.exports); })
      .catch(error => { if (!controller.signal.aborted) setHistoryError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [bookId, session, listTab, storedDetail]);

  async function refresh(id: string) {
    if (!session) return;
    const [nextWorkbench, nextDetail] = await Promise.all([
      api<Workbench>('/workbench', session),
      api<BookDetail>(`/books/${encodeURIComponent(id)}`, session),
    ]);
    if (sessionScope.current !== session.scope_key) return;
    setWorkbench(nextWorkbench);
    if (bookIdRef.current !== id) return;
    setDetail(nextDetail);
    setBookError('');
    setDraftId(current => nextDetail.drafts.some(draft => draft.id === current) ? current : nextDetail.drafts[0]?.id ?? '');
    setSelectedIds(current => current.filter(value => nextDetail.drafts.some(draft => draft.id === value && draft.status === 'approved')));
  }

  async function runAction(name: string, action: () => Promise<void>) {
    if (actionLock.current || intelligenceBusy) return;
    actionLock.current = true;
    const scope = sessionScope.current;
    setBusy(name);
    setNotice(null);
    try { await action(); }
    catch (error) { if (sessionScope.current === scope && !(error instanceof DOMException && error.name === 'AbortError')) setNotice({ kind: 'error', text: errorMessage(error) }); }
    finally { if (sessionScope.current === scope) { actionLock.current = false; setBusy(''); } }
  }

  function chooseBook(id: string) {
    if (id === bookId || busy) return;
    // Clear related data synchronously, before the next request can render.
    setDetail(null);
    setDraftId('');
    setSelectedIds([]);
    setBookError('');
    setBookId(id);
    setListTab('drafts');
  }

  async function generateSuggestions() {
    if (!session || !bookId) return;
    const id = bookId;
    await runAction('suggest', async () => {
      const result = await api<{ created: number }>(`/books/${encodeURIComponent(id)}/suggestions`, session, { method: 'POST', body: {} });
      await refresh(id);
      setListTab('drafts');
      setNotice({ kind: 'success', text: result.created ? `已生成 ${result.created} 张凭证草稿，请逐张核对依据后审核。` : '没有待生成建议的流水。已有草稿不会重复生成。' });
    });
  }

  async function saveDraft(draft: Draft, description: string, lines: JournalLine[]) {
    if (!session) return;
    const id = bookId;
    await runAction('save', async () => {
      await api<Draft>(`/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}`, session, {
        method: 'PATCH', body: { expected_version: draft.version, description, lines },
      });
      await refresh(id);
      setNotice({ kind: 'success', text: '草稿已保存，金额和科目校验结果已更新。' });
    });
  }

  async function approveDraft(draft: Draft) {
    if (!session) return;
    const id = bookId;
    await runAction('approve', async () => {
      await api<Draft>(`/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}/approve`, session, {
        method: 'POST', body: { expected_version: draft.version },
      });
      await refresh(id);
      setNotice({ kind: 'success', text: '凭证已审核，可选中后导出。审核不会写入外部正式账本。' });
    });
  }

  async function exportDrafts() {
    if (!session || !selectedIds.length) return;
    const id = bookId;
    const ids = [...selectedIds].sort();
    const signature = `${id}:${ids.join(',')}`;
    if (exportRequest.current?.signature !== signature) exportRequest.current = { signature, key: crypto.randomUUID() };
    const key = exportRequest.current.key;
    await runAction('export', async () => {
      const result = await api<{ id: string; csv: string; count: number; filename: string }>(`/books/${encodeURIComponent(id)}/exports`, session, {
        method: 'POST', body: { draft_ids: ids, idempotency_key: key },
      });
      downloadCsv(result.csv, result.filename);
      await refresh(id);
      setSelectedIds([]);
      exportRequest.current = null;
      setNotice({ kind: 'success', text: `已导出 ${result.count} 张凭证。请在目标财务系统导入后核验回执与金额。` });
    });
  }

  const stats = workbench?.stats;
  const filteredBooks = (workbench?.books ?? []).filter(book => {
    const searchMatches = book.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
    return searchMatches && (filter === 'all' || (filter === 'pending' && book.pending_count > 0) || (filter === 'exception' && book.exception_count > 0) || (filter === 'approved' && book.approved_count > 0));
  });
  const detail = filteredBooks.some(book => book.id === bookId) ? storedDetail : null;
  useEffect(() => {
    if (busy || !workbench) return;
    if (!filteredBooks.some(book => book.id === bookId)) {
      setDetail(null);
      setDraftId('');
      setSelectedIds([]);
      setBookId(filteredBooks[0]?.id ?? '');
    }
  }, [filter, search, workbench, bookId, busy]);
  const drafts = (detail?.drafts ?? []).filter(draft => filter === 'all'
    || (filter === 'pending' && draft.status === 'draft')
    || (filter === 'exception' && draft.status === 'draft' && draft.issues.length > 0)
    || (filter === 'approved' && draft.status !== 'draft'));
  const activeDraft = drafts.find(draft => draft.id === draftId) ?? drafts[0];
  const approvedDraftIds = drafts.filter(draft => draft.status === 'approved').map(draft => draft.id);
  const navItems: { id: Filter; label: string; icon: typeof LayoutDashboard; count?: number }[] = [
    { id: 'all', label: '会计工作台', icon: LayoutDashboard },
    { id: 'pending', label: '待审核凭证', icon: ClipboardCheck, count: stats?.pending_count },
    { id: 'exception', label: '异常处理', icon: TriangleAlert, count: stats?.exception_count },
    { id: 'approved', label: '审核与导出', icon: FileCheck2, count: stats?.approved_count },
  ];

  if (!session) return <AuthScreen config={auth.config} loading={auth.loading} error={auth.error} onLogin={auth.login} onRetry={auth.initialize} />;
  if (!session.firm_id) return <NoAuthorization name={session.user_name} loading={auth.loading} error={auth.error} onCreate={auth.createFirm} onLogout={auth.logout} />;
  const canSeeAccounting = !['client_employee', 'client_member'].includes(session.role ?? '') || !!stats?.book_count;
  const showBusiness = !operationsView && !queueView && !intelligenceView && (businessView || !canSeeAccounting);

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); if (!businessBusy && !busy) { setOperationsView(false); setQueueView(false); setIntelligenceView(false); setBusinessView(!canSeeAccounting); setFilter('all'); } }} aria-label="衡账，返回工作台">
        <span className="brand-mark"><span /><span /><span /></span><span className="brand-name">衡账<span>HENGZHANG · AI</span></span>
      </a>
      <div className="firm-block"><span className="firm-icon"><Building2 size={18} /></span><div>{(me?.firms.length ?? 0) > 1 ? <select aria-label="切换事务所" value={session.firm_id} disabled={!!busy || businessBusy || auth.loading} onChange={event => void auth.switchFirm(event.target.value)}>{me?.firms.map(firm => <option key={firm.id} value={firm.id}>{firm.name}</option>)}</select> : <strong>{session.firm_name}</strong>}<span>事务所工作空间</span></div></div>
      <p className="nav-label">工作空间 <span>WORKSPACE</span></p>
      <nav aria-label="主导航"><button type="button" className={`nav-item ${showBusiness ? 'active' : ''}`} disabled={!!busy || businessBusy} onClick={() => { setOperationsView(false); setQueueView(false); setIntelligenceView(false); setBusinessTarget(''); setBusinessView(true); }} aria-current={showBusiness ? 'page' : undefined}><FileText size={18} /><span>业务事项</span></button><button type="button" className={`nav-item ${intelligenceView ? 'active' : ''}`} disabled={!!busy || businessBusy} onClick={() => { setOperationsView(false); setQueueView(false); setIntelligenceView(true); setBusinessView(false); }} aria-current={intelligenceView ? 'page' : undefined}><FileSearch size={18} /><span>票据核对异常</span></button><button type="button" className={`nav-item ${queueView ? 'active' : ''}`} disabled={!!busy || businessBusy} onClick={() => { setOperationsView(false); setQueueTarget(''); setQueueView(true); setIntelligenceView(false); setBusinessView(false); }} aria-current={queueView ? 'page' : undefined}><Layers3 size={18} /><span>票据处理任务</span></button>{(operationsCompanies.length > 0 || me?.permissions.manage_firm) && <button type="button" className={`nav-item ${operationsView ? 'active' : ''}`} disabled={!!busy || businessBusy} onClick={() => { setOperationsView(true); setQueueView(false); setIntelligenceView(false); setBusinessView(false); }} aria-current={operationsView ? 'page' : undefined}><Activity size={18} /><span>费用与运行监控</span></button>}{canSeeAccounting && navItems.map(item => <button key={item.id} type="button" disabled={!!busy || businessBusy} className={`nav-item ${!showBusiness && !intelligenceView && !queueView && !operationsView && filter === item.id ? 'active' : ''}`} onClick={() => { setOperationsView(false); setQueueView(false); setIntelligenceView(false); setBusinessView(false); setFilter(item.id); setSearch(''); }} aria-current={!showBusiness && !intelligenceView && !queueView && !operationsView && filter === item.id ? 'page' : undefined}>
        <item.icon size={18} /><span>{item.label}</span>{item.count !== undefined && <span className={`nav-count ${item.id === 'exception' && item.count ? 'warning' : ''}`}>{item.count}</span>}
      </button>)}</nav>
      {me?.permissions.manage_firm && <button type="button" className="nav-item access-nav" disabled={!!busy || businessBusy} onClick={() => { setNotice(null); setModal('access'); }}><Users size={18} /><span>成员与账套授权</span></button>}
      <div className="sidebar-note"><div className="note-symbol">“</div><p>让会计的时间，<br />留给需要判断的事。</p><span>从每一笔可靠的账开始</span></div>
      <div className="sidebar-bottom">{session.mode === 'local' && !!auth.config?.personas?.length && <label className="persona-switch"><span>切换验证身份</span><select aria-label="切换验证身份" value={auth.persona} disabled={!!busy || businessBusy || auth.loading} onChange={event => void auth.choosePersona(event.target.value)}>{auth.config.personas.map(persona => <option key={persona.id} value={persona.id}>{persona.label}</option>)}</select></label>}<button type="button" className="help-link" onClick={() => setModal('help')}><CircleHelp size={17} />使用说明与版本范围<ArrowUpRight size={14} /></button><div className="user-block"><span className="avatar">{session.user_name.slice(0, 1)}</span><div><strong>{session.user_name}</strong><span>{session.role ? roleLabels[session.role] : '工作空间成员'}</span></div>{session.mode === 'supabase' ? <button type="button" className="icon-button logout-button" aria-label="退出登录" disabled={businessBusy} onClick={() => void auth.logout()}><LogOut size={17} /></button> : <ShieldCheck size={17} />}</div></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div className="breadcrumbs">工作空间<ChevronRight size={13} /><span>{operationsView ? '费用与运行监控' : queueView ? '票据处理任务' : intelligenceView ? '票据核对异常' : showBusiness ? '业务事项' : navItems.find(item => item.id === filter)?.label}</span></div><div className="environment"><span className="status-dot" />{session.mode === 'local' ? '本地验证环境' : '已验证账户'}<span className="topbar-separator" />CNY 人民币</div></header>
      {operationsView ? <OperationsWorkbench key={session.scope_key} session={session} companies={operationsCompanies} onBusyChange={changeBusinessBusy} /> : queueView ? <QueueWorkbench key={`${session.scope_key}:${queueTarget}`} session={session} initialBatchId={queueTarget} onBusyChange={changeBusinessBusy} onOpenEvent={id => { setQueueView(false); setBusinessTarget(id); setBusinessView(true); }} /> : intelligenceView ? <IntelligenceWorkbench key={session.scope_key} session={session} onOpenEvent={id => { setIntelligenceView(false); setBusinessTarget(id); setBusinessView(true); }} /> : showBusiness ? <BusinessWorkspace key={session.scope_key} session={session} books={workbench?.books ?? []} initialEventId={businessTarget} onOpenBatch={id => { setQueueTarget(id); setQueueView(true); setBusinessView(false); setIntelligenceView(false); }} onBusyChange={changeBusinessBusy} onOpenDraft={(id, draft) => { pendingReview.current = { bookId: id, draftId: draft }; setOperationsView(false); setQueueView(false); setIntelligenceView(false); setBusinessView(false); setFilter('all'); setSearch(''); setBookId(id); if (bookId === id) void refresh(id).then(() => setDraftId(draft)); void initialize(); }} /> : <main>
        <section className="page-heading"><div><p className="eyebrow">ACCOUNTING, WITH CLARITY</p><h1>{filter === 'all' ? '每一笔，都有据可循。' : navItems.find(item => item.id === filter)?.label}</h1><p className="page-description">多企业协同记账，从原始流水到会计审核，在一个工作台完成。</p></div><button className="button primary" type="button" disabled={!!busy || !me?.permissions.create_book} title={!me?.permissions.create_book ? '当前账号没有新建账套权限' : undefined} onClick={() => { setNotice(null); setModal('book'); }}><Plus size={16} />新建账套</button></section>
        {bootLoading ? <div className="loading-block"><Spinner />正在载入会计工作空间…</div> : bootError ? <div className="connection-error" role="alert"><TriangleAlert size={25} /><div><h2>工作空间暂时无法连接</h2><p>{bootError}</p>{session.mode === 'local' && <code>后端默认地址：127.0.0.1:8000</code>}</div><button className="button secondary" onClick={() => void initialize()}>重新连接</button></div> : <>
          <section className="stats-grid" aria-label="事务所账务概览">
            <StatCard label="管理账套" value={stats?.book_count ?? 0} unit="家" icon={<Building2 size={19} />} hint="当前事务所客户企业" />
            <StatCard label="待审核凭证" value={stats?.pending_count ?? 0} unit="张" icon={<ClipboardCheck size={19} />} hint="等待会计核对与确认" />
            <StatCard label="待处理异常" value={stats?.exception_count ?? 0} unit="项" icon={<TriangleAlert size={19} />} hint="优先处理缺证及校验问题" warning />
            <StatCard label="已审核凭证" value={stats?.approved_count ?? 0} unit="张" icon={<CheckCheck size={19} />} hint="含已导出，交付后请核对回执" />
          </section>
          <section className="process-strip" aria-label="记账流程"><span><span className="step-number">01</span>导入原始流水</span><ArrowRight size={15} /><span><span className="step-number">02</span>规则与模型建议</span><ArrowRight size={15} /><span><span className="step-number">03</span>会计复核与导出</span><p><ShieldCheck size={14} />每一步保留来源</p></section>
          <div className="workspace-grid">
            <div className="work-column">
              <section className="panel books-panel">
                <div className="panel-heading"><div><h2>客户账套<span className="count-label">{filteredBooks.length}</span></h2><p>按企业分开处理，审核状态一眼可见</p></div><label className="search-box"><Search size={16} /><input placeholder="搜索企业名称" value={search} onChange={event => setSearch(event.target.value)} aria-label="搜索企业名称" />{search && <button type="button" className="icon-button" aria-label="清空搜索" onClick={() => setSearch('')}><X size={13} /></button>}</label></div>
                <div className="book-list" role="list" aria-label="客户企业">
                  {filteredBooks.length ? filteredBooks.map((book, index) => <button role="listitem" key={book.id} type="button" disabled={!!busy} className={`book-row ${bookId === book.id ? 'selected' : ''}`} onClick={() => chooseBook(book.id)} aria-pressed={bookId === book.id}>
                    <span className={`company-icon tone-${index % 3}`}>{book.name.slice(0, 1)}</span><span className="book-description"><strong>{book.name}</strong><span>{book.company_name && `${book.company_name} · `}{book.period} 会计期间<span className="inline-dot">·</span>人民币账套</span></span><span className="book-count"><b>{book.pending_count}</b><small>待审核</small></span><span className={`book-exception ${book.exception_count ? 'has-exception' : ''}`}>{book.exception_count ? <><TriangleAlert size={12} />{book.exception_count} 项异常</> : <><Check size={12} />无待处理异常</>}</span><ChevronRight size={16} className="book-chevron" />
                  </button>) : <EmptyState title={search || filter !== 'all' ? '没有符合条件的企业' : '从第一家客户企业开始'}>{search || filter !== 'all' ? '试试其他企业名称，或返回全部账套。' : '点击右上角“新建账套”，建立企业的独立工作空间。'}</EmptyState>}
                </div>
                <div className="panel-footnote"><span className="small-dot" />仅展示当前事务所内获授权的账套<span>独立来源 · 独立凭证</span></div>
              </section>

              <section className="panel transaction-panel">
                <div className="panel-heading transaction-heading"><div><div className="section-eyebrow">当前账套</div><h2 className="selected-book-title">{detail?.book.name ?? workbench?.books.find(book => book.id === bookId)?.name ?? '尚未选择账套'}</h2></div><button className="button secondary small" disabled={!detail?.book.permissions.edit || !!busy || bookLoading} onClick={() => { setNotice(null); setModal('import'); }}><Upload size={14} />导入流水</button></div>
                <div className="list-tabs"><div role="tablist" aria-label="账套数据"><button type="button" role="tab" aria-selected={listTab === 'drafts'} className={listTab === 'drafts' ? 'active' : ''} onClick={() => setListTab('drafts')}>凭证队列<span>{drafts.length}</span></button><button type="button" role="tab" aria-selected={listTab === 'sources'} className={listTab === 'sources' ? 'active' : ''} onClick={() => setListTab('sources')}>原始流水<span>{detail?.sources.length ?? 0}</span></button><button type="button" role="tab" aria-selected={listTab === 'exports'} className={listTab === 'exports' ? 'active' : ''} onClick={() => setListTab('exports')}>导出记录</button></div><button type="button" className="text-button" disabled={!detail?.book.permissions.edit || !detail?.sources.length || !!busy || bookLoading} onClick={() => void generateSuggestions()}>{busy === 'suggest' ? <Spinner /> : <Sparkles size={14} />}生成记账建议</button></div>
                {bookLoading ? <div className="loading-block"><Spinner />正在载入账套…</div> : bookError ? <div className="inline-error" role="alert">{bookError}<button className="text-button" disabled={!!busy} onClick={() => void runAction('refresh', () => refresh(bookId))}>重新加载</button></div> : !detail ? <EmptyState title="选择一个账套开始处理">每家企业的流水、凭证和审核记录独立保存。</EmptyState> : listTab === 'drafts' ? <>
                  {drafts.length ? <div className="transaction-table-wrap"><table className="transaction-table"><thead><tr><th className="checkbox-cell"><input type="checkbox" aria-label="选择全部可导出凭证" disabled={!detail.book.permissions.export || !approvedDraftIds.length || !!busy} checked={approvedDraftIds.length > 0 && approvedDraftIds.every(id => selectedIds.includes(id))} onChange={event => setSelectedIds(event.target.checked ? approvedDraftIds : [])} /></th><th>摘要 / 交易日期</th><th className="number-cell">金额（元）</th><th>审核状态</th><th /></tr></thead><tbody>{drafts.map(draft => {
                    const source = detail.sources.find(item => item.id === draft.source_id);
                    return <tr key={draft.id} className={draft.id === activeDraft?.id ? 'active' : ''}>
                      <td className="checkbox-cell"><input type="checkbox" aria-label={`选择凭证 ${draft.description}`} disabled={!detail.book.permissions.export || draft.status !== 'approved' || !!busy} checked={selectedIds.includes(draft.id)} onChange={event => setSelectedIds(ids => event.target.checked ? [...ids, draft.id] : ids.filter(id => id !== draft.id))} /></td>
                      <td><button type="button" className="draft-title" disabled={!!busy} onClick={() => setDraftId(draft.id)}><strong>{draft.description || source?.description || '未命名凭证'}</strong><span>{source?.date ?? '—'}<span className="inline-dot">·</span>{source?.counterparty || '未填写交易对方'}</span></button></td>
                      <td className="number-cell"><span className="amount">{source ? formatMoney(source.amount) : '—'}</span><span className="direction-label">{source?.direction === 'income' ? '收入' : '支出'}</span></td>
                      <td><span className={`badge ${draft.status === 'draft' && draft.issues.length ? 'exception' : draft.status}`}>{draft.status === 'draft' && draft.issues.length ? '需处理' : statusLabels[draft.status]}</span></td>
                      <td><button type="button" className="icon-button review-arrow" aria-label={`复核 ${draft.description}`} disabled={!!busy} onClick={() => setDraftId(draft.id)}><ChevronRight size={16} /></button></td>
                    </tr>;
                  })}</tbody></table></div> : <EmptyState title={filter === 'all' ? '还没有凭证草稿' : '当前账套没有符合条件的凭证'} icon={<FileText size={28} />}>{detail.sources.length ? '点击“生成记账建议”，将原始流水转换为可审核草稿。' : '先导入企业流水，再生成记账建议。'}</EmptyState>}
                  <div className="export-bar"><span>已选 <strong>{selectedIds.length}</strong> 张已审核凭证</span><button className="button secondary small" disabled={!detail.book.permissions.export || !selectedIds.length || !!busy} onClick={() => void exportDrafts()}>{busy === 'export' ? <Spinner /> : <Download size={14} />}导出凭证 CSV</button></div>
                </> : listTab === 'exports' ? historyLoading ? <div className="loading-block"><Spinner />正在读取导出记录…</div> : historyError ? <div className="inline-error" role="alert">{historyError}<button type="button" className="text-button" onClick={() => { setListTab('drafts'); }}>返回凭证队列</button></div> : exportHistory.length ? <div className="source-list">{exportHistory.map(record => <div className="source-row export-history-row" key={record.id}><span className="source-direction income"><FileCheck2 size={18} /></span><div><strong>{record.filename}</strong><span>{record.count} 张凭证 · {new Date(record.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</span></div><button type="button" className="button secondary small" disabled={!detail.book.permissions.export || !!busy} aria-label={`重新下载 ${record.filename}`} onClick={() => void runAction('download', async () => {
                  if (!session) return;
                  const receipt = await api<{ csv: string; filename: string }>(`/books/${encodeURIComponent(bookId)}/exports/${encodeURIComponent(record.id)}`, session);
                  downloadCsv(receipt.csv, receipt.filename);
                })}>{busy === 'download' ? <Spinner /> : <Download size={14} />}重新下载</button></div>)}</div> : <EmptyState title="还没有导出记录" icon={<Download size={26} />}>审核并导出凭证后，可在这里重新下载历史文件。</EmptyState> : detail.sources.length ? <div className="source-list">{detail.sources.map(source => <div className="source-row" key={source.id}><span className={`source-direction ${source.direction}`}>{source.direction === 'income' ? <ArrowDownLeft size={18} /> : <ArrowUpRight size={18} />}</span><div><strong>{source.description}</strong><span>{source.date} · {source.external_id}</span><small>{source.evidence_ref ? `凭据引用：${source.evidence_ref}` : '尚未补充凭据引用'}</small></div><div className="source-amount"><strong>{formatMoney(source.amount)}</strong><span>{source.direction === 'income' ? '收入' : '支出'}</span></div></div>)}</div> : <EmptyState title="还没有原始流水">支持带表头的 UTF-8 CSV，点击“导入流水”开始。</EmptyState>}
              </section>
            </div>

            <aside className="panel review-panel" aria-label="凭证复核"><div className="review-panel-header"><div className="review-heading"><span className="review-icon"><ClipboardCheck size={18} /></span><div><h2>凭证复核</h2><p>核对来源，再确认入账建议</p></div></div><span className="review-step">REVIEW</span></div>
              {!bookLoading && detail && activeDraft ? <Reviewer key={`${bookId}:${activeDraft.id}`} session={session} draft={activeDraft} detail={detail} busy={busy} externalBusy={!!operationBusy} onIntelligenceBusyChange={changeIntelligenceBusy} onIntelligenceChanged={() => refresh(bookId)} onOpenBatch={id => { setQueueTarget(id); setQueueView(true); setIntelligenceView(false); setBusinessView(false); }} onSave={saveDraft} onApprove={approveDraft} onBusinessEvidence={id => { setBusinessTarget(id); setIntelligenceView(false); setBusinessView(true); }} onEvidence={async (evidenceRef) => {
                if (!session) return;
                const id = bookId;
                await runAction('evidence', async () => {
                  await api(`/books/${encodeURIComponent(id)}/sources/${encodeURIComponent(activeDraft.source_id)}/evidence`, session, { method: 'POST', body: { evidence_ref: evidenceRef } });
                  await refresh(id);
                  setNotice({ kind: 'success', text: '凭据引用已补充，请人工核对对应文件的真实性与完整性。' });
                });
              }} /> : <EmptyState title={bookLoading ? '正在载入凭证' : '选择一张凭证开始复核'} icon={<ClipboardCheck size={30} />}>这里将展示原始流水、建议分录和需要处理的问题。</EmptyState>}
            </aside>
          </div>
          <footer className="workspace-footer"><span>衡账 · AI 辅助记账工作台</span><span>中国大陆企业 / 人民币 / 会计审核后导出</span></footer>
        </>}
      </main>}
    </div>

    {notice && <div className={`toast ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'error' ? <TriangleAlert size={18} /> : <CheckCircle2 size={18} />}<span>{notice.text}</span><button type="button" onClick={() => setNotice(null)} className="icon-button" aria-label="关闭消息"><X size={16} /></button></div>}
    {modal === 'book' && <NewBookModal session={session} canCreateCompany={!!me?.permissions.create_company} serverError={notice?.kind === 'error' ? notice.text : undefined} busy={!!busy} onClose={() => setModal(null)} onSubmit={async (name, period, companyId) => {
      if (!session) return;
      await runAction('book', async () => {
        const book = await api<Book>('/books', session, { method: 'POST', body: { name, period, company_id: companyId } });
        const data = await api<Workbench>('/workbench', session);
        setWorkbench(data); setBookId(book.id); setDetail(null); setSelectedIds([]); setFilter('all'); setSearch(''); setModal(null);
        setNotice({ kind: 'success', text: `账套“${book.name}”已创建，可以导入流水。` });
      });
    }} />}
    {modal === 'access' && me?.permissions.manage_firm && <AccessManagement session={session} books={workbench?.books ?? []} onClose={() => setModal(null)} onChanged={async () => { await initialize(); }} />}
    {modal === 'import' && detail && <ImportModal serverError={notice?.kind === 'error' ? notice.text : undefined} book={detail.book} busy={!!busy} onClose={() => setModal(null)} onSubmit={async (sourceName, text) => {
      if (!session) return;
      const rows = toSourceRows(text);
      const id = bookId;
      await runAction('import', async () => {
        const result = await api<{ imported: number; duplicates: number }>(`/books/${encodeURIComponent(id)}/imports`, session, { method: 'POST', body: { source_name: sourceName, rows } });
        await refresh(id); setModal(null); setListTab('sources');
        setNotice({ kind: 'success', text: `成功导入 ${result.imported} 条流水，跳过 ${result.duplicates} 条重复记录。接下来可生成记账建议。` });
      });
    }} />}
    {modal === 'help' && <Modal title="从可靠的第一笔账开始" subtitle={session.mode === 'local' ? '当前版本 · 本地辅助记账验证' : '当前版本 · 企业试点辅助记账'} onClose={() => setModal(null)}><div className="help-content"><p>适用于中国大陆企业、人民币账套。支持员工报销、采购 / 费用、日常收支录入与附件上传；经部门审批和企业财务核验后交接凭证草稿。也支持 CSV 流水导入、规则建议、人工修改与审核、凭证导出。</p><ol><li><strong>创建账套并导入流水</strong><span>每条流水须有稳定的外部编号。重复导入相同记录不会重复创建。</span></li><li><strong>检查建议与原始依据</strong><span>建议来自规则或管理员配置的模型。模型结果会与业务事项、票据及关联流水核对；实际会计处理须由会计确认。</span></li><li><strong>审核后导出</strong><span>补全借贷分录与凭据引用后审核，再导出到目标财务系统并核对回执。</span></li></ol><div className="info-callout"><ShieldCheck size={18} /><p>{session.mode === 'local' ? '首次启动的三家企业与流水均为虚构示例。' : '仅可访问当前账户获授权的客户企业与账套。'}凭据引用是人工填写的索引，不代表附件已经验证。票据字段为模型识别转录，须核对原件。当前版本不自动过账，也不生成正式财务报表。</p></div></div><div className="modal-footer"><button className="button primary" onClick={() => setModal(null)}>开始处理</button></div></Modal>}
  </div>;
}

function StatCard({ label, value, unit, icon, hint, warning = false }: { label: string; value: number; unit: string; icon: ReactNode; hint: string; warning?: boolean }) {
  return <article className={`stat-card ${warning ? 'warning' : ''}`}><div className="stat-label">{label}<span>{icon}</span></div><div className="stat-value">{value.toLocaleString('zh-CN')}<span>{unit}</span></div><p>{warning && value > 0 ? <span className="warning-dot" /> : null}{hint}</p></article>;
}

function Reviewer({ session, draft, detail, busy, externalBusy, onIntelligenceBusyChange, onIntelligenceChanged, onSave, onApprove, onEvidence, onBusinessEvidence, onOpenBatch }: {
  session: Session; draft: Draft; detail: BookDetail; busy: string; externalBusy: boolean; onIntelligenceBusyChange: (busy: boolean) => void; onIntelligenceChanged: () => Promise<void>;
  onSave: (draft: Draft, description: string, lines: JournalLine[]) => Promise<void>;
  onApprove: (draft: Draft) => Promise<void>;
  onEvidence: (evidenceRef: string) => Promise<void>;
  onBusinessEvidence: (eventId: string) => void; onOpenBatch: (batchId: string) => void;
}) {
  const [description, setDescription] = useState(draft.description);
  const [intelligenceOpen, setIntelligenceOpen] = useState(true);
  const [lines, setLines] = useState<JournalLine[]>(draft.lines.map(line => ({ ...line })));
  const source = detail.sources.find(item => item.id === draft.source_id);
  const [evidenceRef, setEvidenceRef] = useState(source?.evidence_ref ?? '');
  const locked = draft.status !== 'draft';
  const readOnly = locked || !detail.book.permissions.edit;
  useEffect(() => {
    setDescription(draft.description);
    setLines(draft.lines.map(line => ({ ...line })));
  }, [draft.id, draft.version, draft.description, draft.lines]);
  useEffect(() => { setEvidenceRef(source?.evidence_ref ?? ''); }, [source?.evidence_ref]);
  const dirty = description !== draft.description || JSON.stringify(lines) !== JSON.stringify(draft.lines);
  let debit = 0n;
  let credit = 0n;
  let amountError = '';
  try { for (const line of lines) { debit += moneyToCents(line.debit); credit += moneyToCents(line.credit); } }
  catch { amountError = '金额须为非负数字，最多保留两位小数。'; }
  const balanced = !amountError && debit === credit && debit > 0n;
  function editLine(index: number, field: keyof JournalLine, value: string) {
    setLines(current => current.map((line, currentIndex) => currentIndex === index ? { ...line, [field]: value } : line));
  }
  return <div className="review-content">
    <div className="review-status"><span className={`badge ${draft.status}`}>{statusLabels[draft.status]}</span><span>版本 {draft.version}<span className="inline-dot">·</span>{detail.book.period}</span></div>
    {!locked && !detail.book.permissions.review && <p className="permission-note"><ShieldCheck size={14} />{detail.book.permissions.edit ? '你可以准备草稿，审核与导出由获授权复核员完成。' : '当前账套为只读权限。'}</p>}
    <section className="review-section source-card"><h3><FileText size={14} />原始流水<span>来源可追溯</span></h3>{source ? <><div className="source-card-title">{source.description}</div><div className="source-main-amount"><span>¥</span>{formatMoney(source.amount)}<small>{source.direction === 'expense' ? '支出' : '收入'}</small></div><dl><div><dt>交易对方</dt><dd>{source.counterparty || '未填写'}</dd></div><div><dt>交易日期</dt><dd>{source.date}</dd></div><div><dt>流水编号</dt><dd className="mono">{source.external_id}</dd></div><div><dt>凭据引用</dt><dd>{source.evidence_ref || <span className="text-warning">待补充</span>}</dd></div></dl>{!source.evidence_ref && !readOnly && <div className="evidence-editor"><label htmlFor="evidence-ref">补充凭据引用</label><div><input id="evidence-ref" value={evidenceRef} maxLength={500} placeholder="例如：发票号码或归档文件编号" onChange={event => setEvidenceRef(event.target.value)} disabled={!!busy} /><button type="button" className="button secondary small" disabled={!!busy || !evidenceRef.trim()} onClick={() => void onEvidence(evidenceRef.trim())}>{busy === 'evidence' ? <Spinner /> : '补充'}</button></div></div>}{source.evidence_ref.startsWith('business:') && <button className="text-button" type="button" disabled={!!busy} onClick={() => onBusinessEvidence(source.evidence_ref.split(':')[1])}><FolderOpen size={14} />查看业务事项与附件</button>}<p className="evidence-note">凭据引用待人工核对，不代表附件已验证。</p></> : <p>来源记录暂不可用，请重新加载账套。</p>}</section>
    {source?.evidence_ref.startsWith('business:') && !intelligenceOpen && <button className="text-button" type="button" disabled={!!busy} onClick={() => setIntelligenceOpen(true)}><FileSearch size={14} />查看票据核对与模型建议</button>}
    {source?.evidence_ref.startsWith('business:') && intelligenceOpen && <IntelligencePanel key={source.evidence_ref.split(':')[1]} session={session} eventId={source.evidence_ref.split(':')[1]} book={detail.book} draft={draft} accounts={detail.accounts} externalBusy={externalBusy} onBusyChange={onIntelligenceBusyChange} onChanged={onIntelligenceChanged} onDismiss={() => setIntelligenceOpen(false)} onOpenBatch={onOpenBatch} disableProposal={dirty} />}
    <section className="review-section rationale"><h3><Sparkles size={14} />建议依据</h3><p>{draft.explanation || '请根据原始流水核对会计处理。'}</p><span className="rule-label">建议依据 · 会计确认</span></section>
    {draft.issues.length > 0 && <section className="issues-box" aria-label="待处理问题"><h3><TriangleAlert size={14} />{draft.issues.length} 项待处理</h3><ul>{draft.issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul></section>}
    <section className="review-section journal-section"><div className="journal-heading"><h3>凭证分录</h3><span>示例科目 · CNY</span></div><label className="field-label" htmlFor="journal-description">凭证摘要</label><textarea id="journal-description" className="description-input" value={description} maxLength={500} rows={2} onChange={event => setDescription(event.target.value)} disabled={readOnly || !!busy} />
      <div className="journal-lines">{lines.map((line, index) => <div className="journal-line" key={index}><div className="line-top"><span className="line-index">{String(index + 1).padStart(2, '0')}</span><select value={line.account_code} onChange={event => editLine(index, 'account_code', event.target.value)} disabled={readOnly || !!busy} aria-label={`第 ${index + 1} 行会计科目`}><option value="">选择会计科目</option>{detail.accounts.map(account => <option value={account.code} key={account.code}>{account.code} · {account.name}</option>)}</select>{!readOnly && <button className="icon-button" type="button" aria-label={`删除第 ${index + 1} 行分录`} disabled={!!busy || lines.length <= 1} onClick={() => setLines(current => current.filter((_, currentIndex) => currentIndex !== index))}><Trash2 size={14} /></button>}</div><div className="line-amounts"><label>借方<input aria-label={`第 ${index + 1} 行借方金额`} inputMode="decimal" value={line.debit} onChange={event => editLine(index, 'debit', event.target.value)} onBlur={() => { if (line.debit === '') editLine(index, 'debit', '0.00'); }} disabled={readOnly || !!busy} /></label><label>贷方<input aria-label={`第 ${index + 1} 行贷方金额`} inputMode="decimal" value={line.credit} onChange={event => editLine(index, 'credit', event.target.value)} onBlur={() => { if (line.credit === '') editLine(index, 'credit', '0.00'); }} disabled={readOnly || !!busy} /></label></div></div>)}</div>
      {!readOnly && <button type="button" className="add-line" disabled={!!busy || lines.length >= 20} onClick={() => setLines(current => [...current, { account_code: '', debit: '0.00', credit: '0.00' }])}><Plus size={14} />添加分录</button>}
      <div className={`journal-totals ${balanced ? 'balanced' : 'unbalanced'}`}><div><span>借方合计</span><strong>{amountError ? '—' : formatCents(debit)}</strong></div><div><span>贷方合计</span><strong>{amountError ? '—' : formatCents(credit)}</strong></div><p>{balanced ? <><CheckCircle2 size={13} />借贷平衡</> : <><TriangleAlert size={13} />{amountError || (debit === 0n && credit === 0n ? '请填写有效金额' : `借贷差额 ¥ ${formatCents(debit - credit)}`)}</>}</p></div>
    </section>
    <div className="review-actions">{locked ? <div className="approved-note"><ShieldCheck size={18} /><p>{draft.status === 'exported' ? '此凭证已导出，修改入口已锁定。' : '此凭证已审核，选中后即可导出。'}<span>如需更正，请在后续调整流程中处理。</span></p></div> : <><p className="review-action-note">{dirty ? '有未保存的修改，请先保存后审核。' : '审核前，请核对凭据、金额与会计科目。'}</p><div><button className="button secondary" disabled={readOnly || !!busy || !dirty || !description.trim() || !!amountError} onClick={() => void onSave(draft, description.trim(), lines)}>{busy === 'save' ? <Spinner /> : <FileText size={15} />}保存草稿</button><button className="button primary" disabled={!detail.book.permissions.review || !!busy || dirty || !balanced || draft.issues.length > 0 || !source?.evidence_ref} onClick={() => void onApprove(draft)}>{busy === 'approve' ? <Spinner /> : <Check size={16} />}确认审核</button></div></>}</div>
  </div>;
}

function NewBookModal({ session, canCreateCompany, busy, onClose, onSubmit, serverError }: { session: Session; canCreateCompany: boolean; serverError?: string; busy: boolean; onClose: () => void; onSubmit: (name: string, period: string, companyId: string) => Promise<void> }) {
  const [name, setName] = useState('');
  const [period, setPeriod] = useState('2026-10');
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [taxId, setTaxId] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api<{ companies: Company[] }>('/companies', session, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { setCompanies(result.companies); if (!canCreateCompany) { setCompanyId(result.companies[0]?.id ?? ''); setName(result.companies[0]?.name ?? ''); } } })
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [session, canCreateCompany]);
  const submitting = busy || creating;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || loading) return;
    setError(''); setCreating(true);
    try {
      let target = companyId;
      if (!target) {
        if (!canCreateCompany || !companyName.trim()) return;
        const company = await api<Company>('/companies', session, { method: 'POST', body: { name: companyName.trim(), tax_id: taxId.trim() || undefined } });
        target = company.id; setCompanyId(target); setCompanies(current => [...current, company]);
      }
      await onSubmit(name.trim(), period, target);
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setCreating(false); }
  }
  return <Modal error={error || serverError} title="创建客户账套" subtitle="先关联客户企业，再建立独立的来源与凭证空间" onClose={onClose} busy={submitting}><form onSubmit={event => void submit(event)}><div className="form-content"><label className="field-label" htmlFor="book-company">客户企业</label><select id="book-company" value={companyId} disabled={submitting || loading} onChange={event => { setCompanyId(event.target.value); const company = companies.find(item => item.id === event.target.value); setName(company?.name ?? companyName); }}>{canCreateCompany ? <option value="">新建客户企业</option> : <option value="">选择获授权企业</option>}{companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select>{loading && <p className="field-help">正在载入客户企业…</p>}{!companyId && canCreateCompany && <div className="new-company-fields"><label className="field-label" htmlFor="company-name">企业名称</label><input id="company-name" autoFocus value={companyName} onChange={event => { setCompanyName(event.target.value); if (!name || name === companyName) setName(event.target.value); }} maxLength={120} placeholder="请输入企业完整名称" required disabled={submitting} /><label className="field-label" htmlFor="company-tax-id">统一社会信用代码（选填）</label><input id="company-tax-id" value={taxId} onChange={event => setTaxId(event.target.value)} maxLength={30} placeholder="用于区分名称相近的客户企业" disabled={submitting} /></div>}<label className="field-label" htmlFor="book-name">账套名称</label><input id="book-name" value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="例如：企业名称 · 2026 年账套" required disabled={submitting} /><label className="field-label" htmlFor="book-period">会计期间</label><input id="book-period" type="month" value={period} onChange={event => setPeriod(event.target.value)} min="2000-01" max="2099-12" required disabled={submitting} /><div className="info-callout"><Building2 size={18} /><p>当前支持中国大陆企业及人民币账套。预置科目为验证示例，请由会计核对适用性。</p></div></div><div className="modal-footer"><button className="button secondary" type="button" onClick={onClose} disabled={submitting}>取消</button><button className="button primary" type="submit" disabled={submitting || loading || !name.trim() || !period || (!companyId && (!canCreateCompany || !companyName.trim()))}>{submitting ? <Spinner /> : <Plus size={15} />}创建账套</button></div></form></Modal>;
}

function ImportModal({ book, busy, onClose, onSubmit, serverError }: { serverError?: string; book: Book; busy: boolean; onClose: () => void; onSubmit: (sourceName: string, text: string) => Promise<void> }) {
  const [text, setText] = useState('');
  const [sourceName, setSourceName] = useState('手动粘贴流水.csv');
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  async function readFile(file?: File) {
    if (!file) return;
    setError(''); setPreview(null); setText(''); setReading(true);
    try {
      if (file.size > MAX_CSV_BYTES) throw new Error('文件不能超过 2 MB，请拆分后重试。');
      const contents = await file.text();
      if (contents.includes('\uFFFD')) throw new Error('文件编码无法识别，请另存为 UTF-8 CSV 后重试。');
      setText(contents); setSourceName(file.name); setPreview(toSourceRows(contents).length);
    } catch (fileError) { setError(errorMessage(fileError)); }
    finally { setReading(false); if (input.current) input.current.value = ''; }
  }
  return <Modal error={serverError} title="导入原始流水" subtitle={`目标账套：${book.name} · ${book.period}`} busy={busy || reading} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); setError('');
    try { setPreview(toSourceRows(text).length); await onSubmit(sourceName, text); }
    catch (importError) { setError(errorMessage(importError)); }
  }}><div className="form-content import-content"><div className="import-instructions"><span>UTF-8 CSV · 最大 2 MB · 每次最多 1,000 行</span><button type="button" className="text-button" onClick={() => downloadCsv(EXAMPLE_CSV.replace(/2026-10/g, book.period), '流水导入示例.csv')}><Download size={14} />下载示例</button></div><input ref={input} type="file" accept=".csv,text/csv" hidden aria-label="选择流水 CSV 文件" onChange={event => void readFile(event.target.files?.[0])} disabled={busy || reading} /><button className="upload-zone" type="button" disabled={busy || reading} onClick={() => input.current?.click()}>{reading ? <Spinner /> : <Upload size={26} />}<strong>{text && sourceName !== '手动粘贴流水.csv' ? sourceName : '选择 CSV 文件'}</strong><span>保留原始流水编号，用于防止重复导入</span></button><label className="field-label" htmlFor="csv-text">或直接粘贴 CSV 内容（含表头）</label><textarea id="csv-text" className="csv-input" value={text} rows={7} spellCheck={false} placeholder="external_id,date,description,counterparty,amount,direction,evidence_ref" onChange={event => { setText(event.target.value); setPreview(null); setError(''); setSourceName('手动粘贴流水.csv'); }} disabled={busy || reading} /><p className="field-help">金额填写正数，支出方向为 expense，收入为 income。包含逗号的摘要须用双引号包裹。凭据引用可在复核时补充。</p>{error && <p className="form-error" role="alert"><TriangleAlert size={15} />{error}</p>}{preview !== null && !error && <p className="form-success"><CheckCircle2 size={15} />已识别 {preview} 条流水，最终去重与校验由服务端完成。</p>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={busy || reading}>取消</button><button type="submit" className="button primary" disabled={busy || reading || !text.trim()}>{busy ? <Spinner /> : <Upload size={15} />}确认导入</button></div></form></Modal>;
}
