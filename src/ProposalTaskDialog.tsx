import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, Clock3, LoaderCircle, RefreshCw, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { activeProposalStatuses, proposalOperationReason, proposalStatusLabels } from './proposal-task-helpers';
import type { ProposalTask } from './proposal-task-helpers';
import type { Session } from './types';
import './proposal-task-dialog.css';

const dateLabel = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
export function ProposalTaskDialog({ session, jobId, onClose, onBusyChange, onChanged, onOpenDraft }: {
  session: Session; jobId: string; onClose: () => void; onBusyChange?: (busy: boolean) => void;
  onChanged?: () => void; onOpenDraft?: (bookId: string, draftId: string) => void;
}) {
  const [job, setJob] = useState<ProposalTask | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [operation, setOperation] = useState<'retry' | 'cancel' | null>(null), [reason, setReason] = useState(''), [acknowledged, setAcknowledged] = useState(false);
  const epoch = useRef(0), active = useRef(true), locked = useRef(false), keys = useRef(new Map<string, string>()), panel = useRef<HTMLDivElement>(null);
  const target = `${session.scope_key}:${jobId}`, targetRef = useRef(target); targetRef.current = target;
  useEffect(() => { active.current = true; return () => { active.current = false; epoch.current++; }; }, []);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    const request = ++epoch.current, started = target;
    if (!quiet) setLoading(true);
    try {
      const value = await api<ProposalTask>(`/intelligence/proposal-jobs/${encodeURIComponent(jobId)}`, session, { signal });
      if (!active.current || request !== epoch.current || started !== targetRef.current || signal?.aborted) return;
      if (value.id !== jobId) throw new Error('任务返回与指定待办不一致，请重新读取。');
      setJob(value); setError('');
    } catch (failure) {
      if (!active.current || request !== epoch.current || started !== targetRef.current || signal?.aborted) return;
      setError(errorMessage(failure));
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { setJob(null); setOperation(null); setReason(''); keys.current.clear(); }
    } finally { if (active.current && request === epoch.current && started === targetRef.current && !signal?.aborted) setLoading(false); }
  }, [session, jobId, target]);
  useEffect(() => {
    const controller = new AbortController(); setJob(null); setOperation(null); setReason(''); setAcknowledged(false); setNotice(''); setError(''); keys.current.clear(); locked.current = false; setBusy(false); void load(controller.signal);
    return () => { controller.abort(); epoch.current++; };
  }, [load]);
  useEffect(() => {
    if (!job || !activeProposalStatuses.has(job.status) || busy || operation) return;
    const controller = new AbortController(); let timer: number | undefined;
    const poll = async () => { await load(controller.signal, true); if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 5000); };
    timer = window.setTimeout(() => void poll(), 5000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [job?.status, busy, operation, load]);
  useEffect(() => {
    const revoke = () => { epoch.current++; locked.current = false; keys.current.clear(); setBusy(false); setJob(null); setOperation(null); setReason(''); setLoading(false); setError('当前任务授权已更新，请返回待办重新读取。'); };
    window.addEventListener('accounting:forbidden', revoke); window.addEventListener('accounting:unauthorized', revoke);
    return () => { window.removeEventListener('accounting:forbidden', revoke); window.removeEventListener('accounting:unauthorized', revoke); };
  }, []);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; panel.current?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) { event.preventDefault(); onClose(); }
      if (event.key !== 'Tab') return;
      const elements = panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]');
      if (!elements?.length) return;
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    window.addEventListener('keydown', keydown); return () => window.removeEventListener('keydown', keydown);
  }, [busy, onClose]);
  const uncertain = !!job && (job.status === 'interrupted' || job.error_code === 'result_uncertain' || job.attempts?.some(attempt => attempt.error_code === 'result_uncertain'));
  async function perform() {
    if (!job || !operation || locked.current || !job.permissions[operation] || proposalOperationReason(reason) || operation === 'retry' && uncertain && !acknowledged) return;
    const kind = operation, signature = JSON.stringify([target, kind, job.attempt_count, reason.trim()]);
    let key = keys.current.get(signature); if (!key) { key = crypto.randomUUID(); keys.current.set(signature, key); }
    const started = target, generation = epoch.current;
    locked.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const value = await api<ProposalTask>(`/intelligence/proposal-jobs/${encodeURIComponent(jobId)}/${kind}`, session, { method: 'POST', body: kind === 'retry' ? { idempotency_key: key, reason: reason.trim() } : { reason: reason.trim() } });
      if (!active.current || started !== targetRef.current || generation !== epoch.current) return;
      keys.current.delete(signature); setJob(value); setOperation(null); setReason(''); setAcknowledged(false);
      setNotice(kind === 'retry' ? '已记录处理依据并重新排队，后台会检查当前事实和草稿版本。' : '已记录取消依据。原件和已保存草稿继续保留。'); onChanged?.();
    } catch (failure) { if (active.current && started === targetRef.current && generation === epoch.current) setError(`${errorMessage(failure)}${kind === 'retry' ? ' 如结果不确定，再次确认会使用同一重试键核验结果。' : ''}`); }
    finally { if (active.current && started === targetRef.current && generation === epoch.current) { locked.current = false; setBusy(false); } }
  }
  return <div className="proposal-dialog-backdrop"><div className="proposal-dialog" ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="proposal-dialog-title"><header><div><p>后台自动生成 · 人工处理</p><h2 id="proposal-dialog-title">分录任务详情</h2></div><button className="icon-button" aria-label="关闭分录任务详情" disabled={busy} onClick={onClose}><X size={20} /></button></header>
    <div className="proposal-dialog-content">{error && <p className="inline-error" role="alert"><TriangleAlert size={16} />{error}</p>}{notice && <p className="form-success" role="status"><CheckCircle2 size={16} />{notice}</p>}
    {loading ? <div className="loading-block"><LoaderCircle size={20} className="spinner" />正在读取此任务的当前权限与处理记录…</div> : job ? <><section className="proposal-dialog-summary"><div>{activeProposalStatuses.has(job.status) ? <Clock3 size={24} /> : job.status === 'succeeded' ? <CheckCircle2 size={24} /> : <TriangleAlert size={24} />}<strong>{proposalStatusLabels[job.status] || '状态待核对'}</strong></div><p>{job.error_detail || (job.status === 'succeeded' ? '后台草稿已生成，仍须会计核对原件和分录。' : activeProposalStatuses.has(job.status) ? '任务进度持续保存，完成后进入会计复核。' : '请核对当前任务和业务资料，再决定下一步。')}</p><dl><div><dt>处理尝试</dt><dd>{job.attempt_count} 次</dd></div><div><dt>最近更新</dt><dd>{dateLabel(job.updated_at)}</dd></div></dl></section>
      <section className="proposal-dialog-next"><h3>现在可以做什么</h3>{onOpenDraft && <button className="button secondary" disabled={busy || !!operation} onClick={() => onOpenDraft(job.book_id, job.draft_id)}>核对对应凭证<ArrowRight size={15} /></button>}{job.permissions.retry && <button className="button secondary" disabled={busy || !!operation} onClick={() => { setOperation('retry'); setReason(''); setAcknowledged(false); setError(''); }}>重试分录任务<RefreshCw size={15} /></button>}{job.permissions.cancel && <button className="text-button" disabled={busy || !!operation} onClick={() => { setOperation('cancel'); setReason(''); setAcknowledged(false); setError(''); }}>取消分录任务</button>}{!job.permissions.retry && !job.permissions.cancel && <p>当前状态无重试或取消动作；可以核对已保存的处理结果。</p>}</section>
      {operation && <section className="proposal-dialog-operation" aria-label="分录任务处理依据"><h3>{operation === 'retry' ? '确认重新生成草稿' : '确认取消任务'}</h3><p>{operation === 'retry' ? '重试可能再次调用模型并产生费用。后台仍检查授权、事实及草稿版本，不覆盖会计已修改的草稿。' : '取消保留原件与草稿。已发送的模型请求仍可能产生费用，请按任务记录核对。'}</p><label>处理依据<textarea aria-label="分录任务处理依据" rows={3} maxLength={1000} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} placeholder="说明已核对的问题以及处理依据" /></label>{operation === 'retry' && uncertain && <label className="proposal-dialog-ack"><input type="checkbox" aria-label="已核对不确定调用及可能重复费用" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} />我已核对不确定调用，知晓重试可能再次产生费用</label>}<div><button className="button primary" disabled={busy || !!proposalOperationReason(reason) || !job.permissions[operation] || operation === 'retry' && uncertain && !acknowledged} onClick={() => void perform()}>{busy ? <LoaderCircle className="spinner" size={15} /> : <ShieldCheck size={15} />}{operation === 'retry' ? '确认重试分录任务' : '确认取消分录任务'}</button><button className="text-button" disabled={busy} onClick={() => { setOperation(null); setReason(''); setAcknowledged(false); }}>暂不处理</button></div></section>}
      <details className="proposal-dialog-technical"><summary>查看版本与尝试记录</summary><dl><div><dt>事实版本</dt><dd>{job.expected_fact_version}</dd></div><div><dt>入队草稿版本</dt><dd>{job.expected_draft_version}</dd></div><div><dt>结果草稿版本</dt><dd>{job.result_draft_version ?? '尚未生成'}</dd></div><div><dt>任务编号</dt><dd>{job.id}</dd></div></dl>{job.attempts?.length ? job.attempts.map(attempt => <article key={attempt.attempt_no}><strong>第 {attempt.attempt_no} 次 · {proposalStatusLabels[attempt.status as keyof typeof proposalStatusLabels] || attempt.status}</strong><p>{dateLabel(attempt.created_at)} → {dateLabel(attempt.finished_at)}</p>{attempt.error_detail && <p>{attempt.error_detail}</p>}</article>) : <p>尚未进入模型调用，先处理上方列出的等待原因。</p>}</details>
    </> : !error && <p>此任务暂不可读取，请重新核对当前授权。</p>}</div><footer><button className="button secondary" disabled={busy || loading || !!operation} onClick={() => void load()}><RefreshCw size={15} />读取最新进度</button><button className="button primary" disabled={busy} onClick={onClose}>返回待办</button></footer>
  </div></div>;
}
