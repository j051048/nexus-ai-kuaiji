import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Clock3, Pause, Play, RefreshCw, Timer, TriangleAlert } from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import { canAcknowledgeActivity, durationLabel, measuredRate, workActivities, workStates } from './efficiency-helpers';
import type { EfficiencyOverview, WorkActivity, WorkSession } from './efficiency-helpers';
import './efficiency.css';

const encoded = encodeURIComponent;
const message = (error: unknown) => error instanceof Error ? error.message : '计时状态未确认，请刷新读取当前记录。';

export function EfficiencyWorkbench({ session, bookId, eventId, onBack, onBusyChange }: {
  session: Session; bookId?: string; eventId?: string; onBack?: () => void; onBusyChange?: (busy: boolean) => void;
}) {
  const [overview, setOverview] = useState<EfficiencyOverview | null>(null), [sessions, setSessions] = useState<WorkSession[]>([]);
  const [activity, setActivity] = useState<WorkActivity>(eventId ? 'business_entry' : 'review');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const scope = `${session.scope_key}:${bookId ?? ''}:${eventId ?? ''}`, scopeRef = useRef(scope); scopeRef.current = scope;
  const epoch = useRef(0), commandEpoch = useRef(0), active = useRef(true), locked = useRef(false), lastInput = useRef(performance.now());
  const current = sessions.find(item => item.state !== 'completed' && (bookId ? item.book_id === bookId : item.event_id === eventId));
  const unrelatedActive = sessions.find(item => item.state === 'active' && item.id !== current?.id);
  useEffect(() => { active.current = true; return () => { active.current = false; epoch.current += 1; }; }, [scope]);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const stamp = ++epoch.current, captured = scope; setLoading(true);
    try {
      const [own, summary] = await Promise.all([api<{ sessions: WorkSession[] }>('/efficiency/sessions?limit=100', session, { signal }), bookId ? api<EfficiencyOverview>(`/books/${encoded(bookId)}/efficiency`, session, { signal }) : Promise.resolve(null)]);
      if (active.current && !signal?.aborted && epoch.current === stamp && captured === scopeRef.current) { setSessions(own.sessions); setOverview(summary); setError(''); }
    } catch (failure) { if (active.current && !signal?.aborted && epoch.current === stamp && captured === scopeRef.current) { setSessions([]); setOverview(null); setError(message(failure)); } }
    finally { if (active.current && epoch.current === stamp && captured === scopeRef.current) setLoading(false); }
  }, [bookId, session, scope]);
  useEffect(() => { epoch.current += 1; commandEpoch.current += 1; locked.current = false; setBusy(false); setOverview(null); setSessions([]); setError(''); setActivity(eventId ? 'business_entry' : 'review'); }, [scope, eventId]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  const run = useCallback(async (action: () => Promise<unknown>, quiet = false) => {
    if (locked.current) return; const captured = scope, commandStamp = ++commandEpoch.current;
    locked.current = true; setBusy(true); if (!quiet) setError('');
    try { await action(); if (active.current && captured === scopeRef.current) await load(); }
    catch (failure) { if (active.current && captured === scopeRef.current) { await load(); setError(message(failure)); } }
    finally { if (commandEpoch.current === commandStamp) locked.current = false; if (active.current && captured === scopeRef.current && commandEpoch.current === commandStamp) setBusy(false); }
  }, [load, scope]);
  const command = useCallback((row: WorkSession, action: 'heartbeat' | 'pause' | 'resume' | 'complete', quiet = false) => run(() => api(`/efficiency/sessions/${encoded(row.id)}/commands`, session, { method: 'POST', body: { action, expected_version: row.version, idempotency_key: crypto.randomUUID() } }), quiet), [run, session]);
  useEffect(() => {
    const interacted = () => { lastInput.current = performance.now(); };
    window.addEventListener('pointerdown', interacted, { passive: true }); window.addEventListener('keydown', interacted);
    return () => { window.removeEventListener('pointerdown', interacted); window.removeEventListener('keydown', interacted); };
  }, []);
  useEffect(() => {
    if (current?.state !== 'active') return;
    const timer = window.setInterval(() => {
      if (locked.current || !canAcknowledgeActivity(document.visibilityState === 'visible', document.hasFocus(), performance.now() - lastInput.current)) return;
      void command(current, 'heartbeat', true);
    }, 20_000);
    return () => window.clearInterval(timer);
  }, [current, command]);
  function start() {
    lastInput.current = performance.now();
    void run(() => api('/efficiency/sessions', session, { method: 'POST', body: { activity, ...(bookId ? { book_id: bookId } : { event_id: eventId }), idempotency_key: crypto.randomUUID() } }));
  }
  return <main className="efficiency-workbench"><section className="page-heading"><div><p className="eyebrow">HUMAN EFFORT / MEASURED</p><h1>用真实处理时间，衡量自动化。</h1><p className="page-description">主动开始、暂停和完成计时；未计时的工作不推算，等待员工补件单独记录。</p></div><div className="efficiency-actions">{onBack && <button className="button secondary" disabled={busy} onClick={onBack}>返回工作台</button>}<button className="button secondary" disabled={busy || loading} onClick={() => void load()}><RefreshCw size={15} />读取最新记录</button></div></section>
    {error && <p className="inline-error" role="alert"><TriangleAlert size={16} />{error}</p>}
    <section className="efficiency-timer" aria-label="人工工作计时"><div className="efficiency-clock"><Timer size={25} /><div><span>{current ? workStates[current.state] : '尚未开启'}</span><strong>{durationLabel(current?.credited_seconds ?? 0)}</strong><small>仅展示服务器已确认时间</small></div></div><div className="efficiency-controls"><label>正在处理<select aria-label="计时工作类型" value={activity} disabled={busy || !!current || !!eventId} onChange={event => setActivity(event.target.value as WorkActivity)}>{Object.entries(workActivities).filter(([value]) => eventId ? value === 'business_entry' : value !== 'business_entry').map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{!current ? <button className="button primary" disabled={busy || loading || !!unrelatedActive || !(bookId || eventId)} onClick={start}><Play size={15} />开始计时</button> : <><button className="button secondary" disabled={busy || loading} onClick={() => { lastInput.current = performance.now(); void command(current, current.state === 'active' ? 'pause' : 'resume'); }}>{current.state === 'active' ? <Pause size={15} /> : <Play size={15} />}{current.state === 'active' ? '暂停' : '继续计时'}</button><button className="button primary" disabled={busy || loading} onClick={() => void command(current, 'complete')}><CheckCircle2 size={15} />完成此次工作</button></>}</div><p>每次活跃确认最多计入 60 秒；返回同账套复核页后由顶部计时条继续确认活跃操作；切换账套、失去焦点或长期无操作不会持续累加。单次最多 8 小时。</p>{unrelatedActive && <div className="efficiency-other">本人另一项工作正在计时，请先处理已有计时。<button className="button secondary" disabled={busy} onClick={() => void command(unrelatedActive, 'pause')}>暂停已有计时</button></div>}</section>
    {overview && <><section className="efficiency-summary" aria-label="账套效率统计"><article><span>明确记录的人工时间</span><strong>{durationLabel(overview.human.credited_seconds)}</strong><small>{overview.human.session_count} 次记录 · 不是估算节省时间</small></article><article><span>已审核凭证</span><strong>{overview.review.reviewed_drafts}</strong><small>首次审核前有修正：{overview.review.with_recorded_correction}</small></article><article><span>未记录修正的审核比例</span><strong>{measuredRate(overview.review.without_recorded_correction, overview.review.reviewed_drafts)}</strong><small>不等同于独立正确率</small></article><article><span>企业规则应用覆盖</span><strong>{measuredRate(overview.review.company_rule_covered, overview.review.reviewed_drafts)}</strong><small>仅计算有应用审计的已审核凭证</small></article></section><section className="efficiency-evidence"><div><Clock3 size={18} /><h2>把等待与操作分开</h2></div><p>{overview.supplement.completed_first_response_intervals} 次首次补件响应，累计等待 {durationLabel(overview.supplement.calendar_wait_seconds)}。这是日历时间，不计入人工工时。</p><dl>{Object.entries(overview.definitions).map(([key, value]) => <div key={key}><dt>{key === 'human' ? '人工时间' : key === 'company_rule_covered' ? '企业规则覆盖' : '未记录修正'}</dt><dd>{value}</dd></div>)}</dl></section></>}
    <section className="efficiency-history"><h2>本人当前范围的计时记录</h2>{loading && <p role="status">正在读取已授权记录…</p>}<div className="efficiency-table"><table><thead><tr><th>工作</th><th>状态</th><th>累计确认</th><th>开始时间</th><th>版本</th></tr></thead><tbody>{sessions.filter(item => bookId ? item.book_id === bookId : item.event_id === eventId).map(item => <tr key={item.id}><td>{workActivities[item.activity]}</td><td>{workStates[item.state]}</td><td>{durationLabel(item.credited_seconds)}</td><td>{new Date(item.started_at).toLocaleString('zh-CN')}</td><td>v{item.version}</td></tr>)}</tbody></table></div>{!loading && !sessions.some(item => bookId ? item.book_id === bookId : item.event_id === eventId) && <p className="efficiency-empty">尚无计时样本。开启明确计时后再评估人工负担。</p>}</section>
  </main>;
}
