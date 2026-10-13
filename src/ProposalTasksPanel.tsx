import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Clock3, LoaderCircle, RefreshCw, TriangleAlert, X } from 'lucide-react';
import { api, ApiError } from './api';
import { activeProposalStatuses, hasNewProposalDraft, proposalOperationReason, proposalStatusLabels } from './proposal-task-helpers';
import type { ProposalTask } from './proposal-task-helpers';
import type { Session } from './types';
import './proposal-tasks.css';
import { QueueAutomationPanel } from './QueueAutomationPanel';

const encoded = encodeURIComponent;
const message = (failure: unknown) => failure instanceof Error ? failure.message : '任务状态未能读取，请稍后重试。';
const dateLabel = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';

export function ProposalTasksPanel({ session, bookId, companyId, paused = false, onBusyChange, onChanged }: {
  session: Session; bookId: string; companyId?: string; paused?: boolean; onBusyChange?: (value: boolean) => void; onChanged?: () => void;
}) {
  const [jobs, setJobs] = useState<ProposalTask[]>([]); const [total, setTotal] = useState(0); const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<ProposalTask | null>(null); const [detailBusy, setDetailBusy] = useState(false);
  const [operation, setOperation] = useState<{ kind: 'retry' | 'cancel'; job: ProposalTask } | null>(null); const [reason, setReason] = useState('');
  const [refreshNeeded, setRefreshNeeded] = useState(false);
  const active = useRef(true); const listEpoch = useRef(0); const detailEpoch = useRef(0); const lock = useRef(false);
  const knownResults = useRef(new Map<string, number>()); const requestKeys = useRef(new Map<string, string>());
  const target = `${session.scope_key}:${bookId}`; const targetRef = useRef(target); targetRef.current = target;
  useEffect(() => { active.current = true; return () => { active.current = false; listEpoch.current += 1; detailEpoch.current += 1; }; }, [session.scope_key]);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useEffect(() => { listEpoch.current += 1; detailEpoch.current += 1; setJobs([]); setTotal(0); setOffset(0); setSelected(null); setOperation(null); setReason(''); setError(''); setNotice(''); setRefreshNeeded(false); setDetailBusy(false); setBusy(false); knownResults.current.clear(); requestKeys.current.clear(); }, [target]);
  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    if (!bookId) return;
    const epoch = ++listEpoch.current; if (!quiet) setLoading(true);
    try {
      const query = new URLSearchParams({ book_id: bookId, limit: '10', offset: String(offset) });
      const result = await api<{ jobs: ProposalTask[]; total: number }>(`/intelligence/proposal-jobs?${query}`, session, { signal });
      if (signal?.aborted || !active.current || listEpoch.current !== epoch) return;
      if (hasNewProposalDraft(knownResults.current, result.jobs)) setRefreshNeeded(true);
      for (const job of result.jobs) if (job.status === 'succeeded' && job.result_draft_version !== null) knownResults.current.set(job.id, job.result_draft_version);
      setJobs(result.jobs); setTotal(result.total); setError('');
      setSelected(previous => { const latest = previous && result.jobs.find(job => job.id === previous.id); return previous && latest ? { ...previous, ...latest } : previous; });
    } catch (failure) { if (!signal?.aborted && active.current && listEpoch.current === epoch) { setError(message(failure)); if (!quiet || failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { setJobs([]); setTotal(0); setSelected(null); setOperation(null); setReason(''); } } }
    finally { if (!signal?.aborted && active.current && listEpoch.current === epoch && !quiet) setLoading(false); }
  }, [session, bookId, offset]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  useEffect(() => {
    if (!bookId || paused || busy || operation || detailBusy) return;
    const controller = new AbortController(); let timer: number | undefined;
    const poll = async () => { await load(controller.signal, true); if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 5000); };
    timer = window.setTimeout(() => void poll(), 5000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [bookId, paused, busy, operation, detailBusy, load]);

  async function detail(job: ProposalTask) {
    if (busy || paused) return;
    const epoch = ++detailEpoch.current; const startedTarget = target; setDetailBusy(true); setSelected(null); setError('');
    try { const result = await api<ProposalTask>(`/intelligence/proposal-jobs/${encoded(job.id)}`, session); if (active.current && startedTarget === targetRef.current && detailEpoch.current === epoch) setSelected({ ...job, ...result }); }
    catch (failure) { if (active.current && startedTarget === targetRef.current && detailEpoch.current === epoch) setError(message(failure)); }
    finally { if (active.current && startedTarget === targetRef.current && detailEpoch.current === epoch) setDetailBusy(false); }
  }
  async function perform() {
    if (lock.current || !operation || paused) return;
    const validation = proposalOperationReason(reason); if (validation) { setError(validation); return; }
    const { kind, job } = operation; const startedTarget = target; const current = () => active.current && startedTarget === targetRef.current;
    const signature = `${target}:${kind}:${job.id}:${job.attempt_count}:${reason.trim()}`;
    let key = requestKeys.current.get(signature); if (!key) { key = crypto.randomUUID(); requestKeys.current.set(signature, key); }
    lock.current = true; setBusy(true); setError('');
    try {
      await api(`/intelligence/proposal-jobs/${encoded(job.id)}/${kind}`, session, { method: 'POST', body: kind === 'retry' ? { idempotency_key: key, reason: reason.trim() } : { reason: reason.trim() } });
      if (!current()) return;
      requestKeys.current.delete(signature); setOperation(null); setReason(''); setSelected(null);
      setNotice(kind === 'retry' ? '已记录重试依据并重新排队，后台会重新检查事实、科目与草稿版本。' : '已记录取消依据；已经发出的模型请求仍可能产生费用，请核对费用记录。');
      await load(undefined, true);
    } catch (failure) { if (current()) setError(message(failure)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  }

  return <section className="panel proposal-tasks-panel" aria-label="后台分录任务"><div className="panel-heading"><div><h2>后台分录任务<span className="count-label">{total}</span></h2><p>处理进度持续保存，完成后由会计复核草稿。</p></div><button className="text-button" disabled={loading || busy || paused || !!operation} onClick={() => void load()}><RefreshCw size={13} />读取任务进度</button></div>
    {(companyId || jobs[0]?.company_id) && <QueueAutomationPanel key={target} session={session} bookId={bookId} companyId={companyId || jobs[0].company_id} paused={paused || busy || !!operation} />}
    {paused && <p className="proposal-pause">正在编辑或处理账务，任务进度暂缓刷新；后台任务继续运行。</p>}
    {refreshNeeded && <div className="proposal-ready"><CheckCircle2 size={16} /><p>后台草稿有更新。保存当前编辑后，刷新凭证待办并核对。</p>{onChanged && <button className="button secondary small" disabled={paused || busy} onClick={() => { setRefreshNeeded(false); onChanged(); }}>刷新凭证待办</button>}</div>}
    {error && <p className="inline-error" role="alert"><TriangleAlert size={15} />{error}</p>}{notice && <p className="form-success proposal-task-notice" role="status">{notice}</p>}
    {loading ? <div className="loading-block"><LoaderCircle size={15} className="spinner" />正在读取分录任务…</div> : jobs.length ? <div className="proposal-task-list">{jobs.map(job => <article key={job.id}><div className="proposal-task-heading">{activeProposalStatuses.has(job.status) ? <Clock3 size={17} /> : job.status === 'succeeded' ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}<strong>{job.draft_description || '业务事项分录处理'}</strong><span className={`badge ${job.status === 'succeeded' ? 'approved' : activeProposalStatuses.has(job.status) ? 'draft' : 'exception'}`}>{proposalStatusLabels[job.status] ?? '状态待核对'}</span></div><p className="proposal-task-meta">事实 v{job.expected_fact_version} · 入队草稿 v{job.expected_draft_version} · 尝试 {job.attempt_count} 次 · {dateLabel(job.updated_at)}</p>{job.error_detail && <p className="proposal-task-issue">{job.error_detail}</p>}{job.status === 'succeeded' && <p className="proposal-task-meta">{job.result_rule_id ? `已发布企业规则 v${job.result_rule_version}` : job.result_template_id ? `企业模板 v${job.result_template_version}` : '候选分录'} · 生成草稿 v{job.result_draft_version ?? '待核对'} · 尚需会计复核</p>}<div className="proposal-task-actions"><button className="text-button" disabled={busy || paused || detailBusy || !!operation} onClick={() => void detail(job)}>读取尝试记录</button>{job.permissions.retry && <button className="text-button" disabled={busy || paused || !!operation} onClick={() => { setOperation({ kind: 'retry', job }); setReason(''); setError(''); }}>重试分录任务</button>}{job.permissions.cancel && <button className="text-button" disabled={busy || paused || !!operation} onClick={() => { setOperation({ kind: 'cancel', job }); setReason(''); setError(''); }}>取消分录任务</button>}</div></article>)}</div> : !error && <div className="proposal-task-empty"><p>本账套暂没有后台分录任务</p><small>企业财务移交已审批事项后创建任务，模型服务缺失或核对异常会暂停并提示人工处理。</small></div>}
    {selected && <div className="proposal-attempts" aria-label="分录任务尝试记录"><div><strong>处理尝试 · {selected.attempts?.length ?? 0} 次</strong><button className="icon-button" aria-label="关闭分录尝试记录" onClick={() => setSelected(null)}><X size={14} /></button></div>{selected.attempts?.length ? selected.attempts.map(attempt => <article key={attempt.attempt_no}><strong>第 {attempt.attempt_no} 次 · {proposalStatusLabels[attempt.status as keyof typeof proposalStatusLabels] ?? attempt.status}</strong><span>{dateLabel(attempt.created_at)} → {dateLabel(attempt.finished_at)}</span>{attempt.error_detail && <p>{attempt.error_detail}</p>}</article>) : <p className="field-help">尚未开始模型请求，先检查任务列出的待人工原因。</p>}</div>}
    {operation && <div className="proposal-operation" aria-label="分录任务处理依据"><div><strong>{operation.kind === 'retry' ? '重新生成分录草稿' : '取消后台分录任务'}</strong><button className="icon-button" disabled={busy} aria-label="关闭分录任务操作" onClick={() => { setOperation(null); setReason(''); }}><X size={15} /></button></div><p>{operation.kind === 'retry' ? '重试可能再次调用模型并产生费用。后台仍检查企业授权、事实和草稿版本，不覆盖会计已经修改的草稿。' : '取消不会删除原件或已保存草稿。已经发送的模型请求可能继续产生费用。'}</p><textarea aria-label="分录任务处理依据" rows={2} maxLength={1000} disabled={busy} value={reason} onChange={event => setReason(event.target.value)} placeholder="填写已核对的问题及处理依据" /><button className="button secondary small" disabled={busy || paused || !!proposalOperationReason(reason)} onClick={() => void perform()}>{busy ? <LoaderCircle className="spinner" size={14} /> : <RefreshCw size={14} />}{operation.kind === 'retry' ? '确认重试分录任务' : '确认取消分录任务'}</button></div>}
    <div className="proposal-task-pagination"><span>{total} 个分录任务 · 每页 10 项</span><div><button className="text-button" disabled={loading || busy || paused || !!operation || offset === 0} onClick={() => { setJobs([]); setSelected(null); setOffset(value => Math.max(0, value - 10)); }}>上一页分录任务</button><button className="text-button" disabled={loading || busy || paused || !!operation || offset + 10 >= total} onClick={() => { setJobs([]); setSelected(null); setOffset(value => value + 10); }}>下一页分录任务</button></div></div>
  </section>;
}
