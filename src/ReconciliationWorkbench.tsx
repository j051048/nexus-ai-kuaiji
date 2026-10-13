import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRightLeft, CheckCircle2, FileCheck2, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import type { Session } from './types';
import type { BankCandidate, CandidatePage, MatchReceipt, ReconciliationEvent, ReconciliationPage } from './reconciliation-types';
import { reconciliationMatchItem, reconciliationSelectionIssue, reconciliationSelectionTotal } from './reconciliation-helpers';
import './reconciliation.css';

export function ReconciliationWorkbench({ session, bookId, onBack, onBusyChange, onChanged }: { session: Session; bookId: string; onBack: () => void; onBusyChange?: (busy: boolean) => void; onChanged?: () => void }) {
  const [page, setPage] = useState<ReconciliationPage | null>(null); const [eventsOffset, setEventsOffset] = useState(0); const [status, setStatus] = useState<'unmatched' | 'matched' | 'all'>('unmatched');
  const [selectedEvent, setSelectedEvent] = useState<ReconciliationEvent | null>(null); const [candidates, setCandidates] = useState<CandidatePage | null>(null); const [candidateOffset, setCandidateOffset] = useState(0);
  const [selected, setSelected] = useState<Map<string, BankCandidate>>(new Map()); const [reason, setReason] = useState(''); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false); const [candidateLoading, setCandidateLoading] = useState(false); const [busy, setBusy] = useState(false); const [refreshRequired, setRefreshRequired] = useState(false);
  const listEpoch = useRef(0); const candidateEpoch = useRef(0); const mounted = useRef(true); const lock = useRef(false); const keys = useRef(new Map<string, string>());
  const target = `${session.scope_key}:${bookId}`; const targetRef = useRef(target); targetRef.current = target;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; listEpoch.current += 1; candidateEpoch.current += 1; }; }, [session.scope_key]);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useEffect(() => { listEpoch.current += 1; candidateEpoch.current += 1; setPage(null); setEventsOffset(0); setSelectedEvent(null); setCandidates(null); setCandidateOffset(0); setSelected(new Map()); setReason(''); setError(''); setNotice(''); setBusy(false); setRefreshRequired(false); keys.current.clear(); }, [target]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const epoch = ++listEpoch.current; const started = target; setLoading(true);
    try { const result = await api<ReconciliationPage>(`/reconciliation/books/${encodeURIComponent(bookId)}/events?status=${status}&limit=20&offset=${eventsOffset}`, session, { signal }); if (!signal?.aborted && mounted.current && targetRef.current === started && epoch === listEpoch.current) { setPage(result); setError(''); } }
    catch (failure) { if (!signal?.aborted && mounted.current && epoch === listEpoch.current) { setPage(null); setCandidates(null); setSelectedEvent(null); setSelected(new Map()); setError(errorMessage(failure)); } }
    finally { if (!signal?.aborted && mounted.current && epoch === listEpoch.current) setLoading(false); }
  }, [session, bookId, status, eventsOffset, target]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  const loadCandidates = useCallback(async (event: ReconciliationEvent, offset: number, signal?: AbortSignal) => {
    const epoch = ++candidateEpoch.current; const started = target; setCandidateLoading(true);
    try { const result = await api<CandidatePage>(`/reconciliation/books/${encodeURIComponent(bookId)}/events/${encodeURIComponent(event.event_id)}/candidates?limit=20&offset=${offset}`, session, { signal });
      if (!signal?.aborted && mounted.current && targetRef.current === started && epoch === candidateEpoch.current) {
        setCandidates(result); setSelectedEvent(result.event); setCandidateOffset(offset); setError('');
        if (result.event.event_version !== event.event_version || result.event.fact_version !== event.fact_version || result.event.business_draft_version !== event.business_draft_version || result.event.business_source_fingerprint !== event.business_source_fingerprint) {
          setSelected(new Map()); setReason(''); setRefreshRequired(true); setError('业务事实或凭证版本已有变化，请重新读取候选并核对。');
        }
      } }
    catch (failure) { if (!signal?.aborted && mounted.current && targetRef.current === started && epoch === candidateEpoch.current) { setCandidates(null); setSelected(new Map()); setError(errorMessage(failure)); if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { setSelectedEvent(null); setPage(null); } } }
    finally { if (!signal?.aborted && mounted.current && targetRef.current === started && epoch === candidateEpoch.current) setCandidateLoading(false); }
  }, [session, bookId, target]);
  function open(event: ReconciliationEvent) { setSelectedEvent(event); setCandidates(null); setSelected(new Map()); setCandidateOffset(0); setReason(''); setNotice(''); setRefreshRequired(false); void loadCandidates(event, 0); }
  function toggle(bank: BankCandidate, checked: boolean) { setSelected(previous => { const next = new Map(previous); if (checked) next.set(bank.source_id, bank); else next.delete(bank.source_id); return next; }); }
  async function confirm() {
    if (!selectedEvent || lock.current || refreshRequired) return;
    const rows = [...selected.values()]; const issue = reconciliationSelectionIssue(selectedEvent, rows, reason); if (issue) { setError(issue); return; }
    const command = { items: [reconciliationMatchItem(selectedEvent, rows)], reason: reason.trim() }; const signature = JSON.stringify({ target, command });
    let key = keys.current.get(signature); if (!key) { key = crypto.randomUUID(); keys.current.set(signature, key); }
    const started = target; lock.current = true; setBusy(true); setError('');
    try { const receipt = await api<MatchReceipt>('/reconciliation/confirm', session, { method: 'POST', body: { ...command, idempotency_key: key } });
      if (!mounted.current || targetRef.current !== started) return;
      keys.current.delete(signature); setSelected(new Map()); setCandidates(null); setSelectedEvent(null); setReason('');
      setNotice(receipt.items.every(item => item.links_current) ? '关联回执已保存。银行流水与业务事项合并处理，凭证仍须会计复核后导出。' : '已恢复历史回执，当前关联有所变化，请重新核对实际关联。');
      await load(); onChanged?.();
    } catch (failure) { if (mounted.current && targetRef.current === started) { setError(errorMessage(failure)); if (failure instanceof ApiError && [409, 422].includes(failure.status)) { setRefreshRequired(true); } if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { setPage(null); setCandidates(null); setSelectedEvent(null); setSelected(new Map()); } } }
    finally { lock.current = false; if (mounted.current && targetRef.current === started) setBusy(false); }
  }
  const selectedRows = [...selected.values()]; const amount = reconciliationSelectionTotal(selectedRows); const selectionIssue = selectedEvent ? reconciliationSelectionIssue(selectedEvent, selectedRows, reason) : '请选择业务事项';
  return <main className="reconciliation-workbench"><div className="reconciliation-heading"><div><p className="reconciliation-kicker">BANK RECONCILIATION / 业务与资金</p><h1>让一笔业务，对上一笔付款。</h1><p>{page?.book.company_name ?? '当前授权账套'} · {page?.book.period ?? ''} · 关联核对与会计复核分别确认</p></div><button className="text-button" disabled={busy} onClick={onBack}><ArrowLeft size={14} />返回会计工作台</button></div>
    {error && <p className="inline-error" role="alert"><TriangleAlert size={15} />{error}</p>}{notice && <p className="form-success" role="status">{notice}</p>}
    <div className="reconciliation-columns"><section className="panel reconciliation-events" aria-label="待匹配业务事项"><div className="panel-heading"><div><h2>业务事项<span className="count-label">{page?.total ?? 0}</span></h2><p>只显示本账套已审批、已移交且实际收付的事项。</p></div><button className="icon-button" aria-label="刷新核对事项" disabled={loading || busy || !!selectedEvent} onClick={() => void load()}><RefreshCw size={15} /></button></div><label className="reconciliation-filter">查看范围<select aria-label="流水核对范围" value={status} disabled={busy || !!selectedEvent} onChange={event => { setStatus(event.target.value as typeof status); setEventsOffset(0); }}><option value="unmatched">尚未关联</option><option value="matched">已关联 · 保留回执</option><option value="all">全部已收付事项</option></select></label>
      {loading ? <div className="loading-block"><LoaderCircle size={16} className="spinner" />正在读取业务事项…</div> : page?.events.length ? <div className="reconciliation-event-list">{page.events.map(event => <button key={event.event_id} className={`reconciliation-event ${selectedEvent?.event_id === event.event_id ? 'selected' : ''}`} disabled={busy || !!event.links.length || !event.permissions.confirm} onClick={() => open(event)}><div><strong>{event.description}</strong><span>{event.direction === 'expense' ? '支出' : '收入'} ¥{event.amount}</span></div><small>{event.date} · {event.counterparty}</small>{!!event.links.length && <p><CheckCircle2 size={12} />已关联 {event.links.length} 笔流水，凭证{event.draft_status === 'draft' ? '待会计复核' : '已复核'}</p>}</button>)}</div> : <div className="reconciliation-empty"><FileCheck2 size={22} /><strong>当前范围没有待匹配事项</strong><p>企业财务移交已收付事项后，回到这里核对银行流水。</p></div>}
      <div className="reconciliation-pagination"><span>每页 20 项</span><div><button className="text-button" disabled={busy || loading || !!selectedEvent || eventsOffset === 0} onClick={() => setEventsOffset(value => Math.max(0, value - 20))}>上一页业务事项</button><button className="text-button" disabled={busy || loading || !!selectedEvent || !page?.has_more} onClick={() => setEventsOffset(value => value + 20)}>下一页业务事项</button></div></div>
    </section><section className="panel reconciliation-bank" aria-label="银行流水候选"><div className="panel-heading"><div><h2>银行流水候选</h2><p>同账套、同交易对方和方向，日期相差最多 7 天。</p></div>{selectedEvent && <button className="text-button" disabled={busy} onClick={() => { candidateEpoch.current += 1; setSelectedEvent(null); setCandidates(null); setSelected(new Map()); setReason(''); setRefreshRequired(false); }}>结束本次核对</button>}</div>
      {!selectedEvent ? <div className="reconciliation-empty"><ArrowRightLeft size={25} /><strong>从左侧选择一个业务事项</strong><p>系统给出确定性候选与核对理由，实际收付关系由会计确认。</p></div> : <><div className="reconciliation-target"><strong>{selectedEvent.description}</strong><span>¥{selectedEvent.amount}</span><small>{selectedEvent.counterparty} · 事实 v{selectedEvent.fact_version} / 凭证 v{selectedEvent.business_draft_version}</small></div>
        {candidates?.ambiguous && <p className="reconciliation-warning"><TriangleAlert size={14} />发现 {candidates.exact_amount_count} 笔同金额流水，请核对回单和真实收付，系统不会自动选择。</p>}
        {refreshRequired && <div className="reconciliation-warning"><p>事实、版本或异常状态有变化，当前选择已停止确认。</p><button className="text-button" disabled={busy} onClick={() => { setSelected(new Map()); setReason(''); setRefreshRequired(false); void loadCandidates(selectedEvent, 0); }}>重新读取当前候选</button></div>}
        {candidateLoading ? <div className="loading-block"><LoaderCircle size={16} className="spinner" />正在查询本账套候选…</div> : candidates?.candidates.length ? <div className="reconciliation-candidate-list">{candidates.candidates.map(bank => <label className={`reconciliation-candidate ${selected.has(bank.source_id) ? 'selected' : ''}`} key={bank.source_id}><input aria-label={`选择核对流水 ${bank.external_id}`} type="checkbox" disabled={busy || refreshRequired || !bank.selectable || !selected.has(bank.source_id) && selected.size >= 20} checked={selected.has(bank.source_id)} onChange={event => toggle(bank, event.target.checked)} /><div><div><strong>{bank.description}</strong><span>¥{bank.amount}</span></div><small>{bank.date} · {bank.counterparty} · {bank.external_id}</small><p className="reconciliation-reasons">{bank.reasons.join(' · ')}</p>{bank.risks.map((risk, index) => <p className="reconciliation-risk" key={index}><TriangleAlert size={12} />{risk}</p>)}</div></label>)}</div> : <div className="reconciliation-empty"><strong>未找到符合条件的流水</strong><p>核对是否已经导入银行流水、交易对方名称和日期，并在流水待办中生成草稿。</p></div>}
        {!!candidates && <div className="reconciliation-pagination"><span>{candidates.total} 笔候选 · 可跨页选择</span><div><button className="text-button" disabled={busy || candidateLoading || candidateOffset === 0} onClick={() => void loadCandidates(selectedEvent, Math.max(0, candidateOffset - 20))}>上一页银行候选</button><button className="text-button" disabled={busy || candidateLoading || !candidates.has_more} onClick={() => void loadCandidates(selectedEvent, candidateOffset + 20)}>下一页银行候选</button></div></div>}
        <div className="reconciliation-confirm"><div><span>已选 {selected.size} 笔</span><strong>¥{amount} <small>/ ¥{selectedEvent.amount}</small></strong></div><label>核对依据<textarea aria-label="流水关联核对依据" rows={3} maxLength={1000} disabled={busy || refreshRequired} value={reason} onChange={event => setReason(event.target.value)} placeholder="说明已核对回单、交易对方和实际收付；分笔收付请写明对应关系" /></label><p className="field-help">确认后保留来源和关联回执，关联的银行流水不再单独审核入账。凭证仍需处理异常并由会计复核后导出。</p><button className="button secondary" disabled={busy || candidateLoading || refreshRequired || !!selectionIssue} title={selectionIssue || undefined} onClick={() => void confirm()}>{busy ? <LoaderCircle size={14} className="spinner" /> : <ArrowRightLeft size={14} />}确认关联并保存回执</button></div>
      </>}
    </section></div>
  </main>;
}
