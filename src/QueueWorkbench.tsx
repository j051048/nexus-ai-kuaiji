import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, Clock3, FileSearch, Layers3, LoaderCircle, RefreshCw, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import { api, errorMessage } from './api';
import { shouldReadAfterFailure } from './intelligence-helpers';
import { batchActive, batchStatusLabels, canCancelJob, canRetryJob, displayedPercent, QUEUE_POLL_MS, RETRY_NOTICE } from './queue-helpers';
import { QueueJobRecord } from './QueueJobRecord';
import type { BusinessContext } from './business-types';
import type { QueueBatch, QueueJob } from './queue-types';
import type { Session } from './types';

const encoded = encodeURIComponent;
function queueDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }); }
export function QueueBatchProgress({ batch }: { batch: QueueBatch }) {
  const { progress } = batch;
  return <div className="queue-progress"><div><strong>{progress.completed} <span>/ {progress.total} 份已结束</span></strong><span>{Math.round(displayedPercent(progress))}%</span></div><progress aria-label="批次处理进度" max={100} value={displayedPercent(progress)} /><p><span>成功 {progress.succeeded}</span><span>待处理 {progress.pending}</span><span>处理中 {progress.running}</span><span className={progress.failed + progress.interrupted + progress.stale ? 'text-warning' : undefined}>需处理 {progress.failed + progress.interrupted + progress.stale}</span>{progress.cancelled > 0 && <span>取消 {progress.cancelled}</span>}</p></div>;
}

export function QueueWorkbench({ session, initialBatchId = '', onOpenEvent, onBusyChange }: { session: Session; initialBatchId?: string; onOpenEvent: (id: string) => void; onBusyChange?: (value: boolean) => void }) {
  const [batches, setBatches] = useState<QueueBatch[]>([]);
  const [total, setTotal] = useState(0);
  const [companyId, setCompanyId] = useState('');
  const [companies, setCompanies] = useState<BusinessContext['companies']>([]);
  const [offset, setOffset] = useState(0);
  const [batchId, setBatchId] = useState(initialBatchId);
  const [batch, setBatch] = useState<QueueBatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [confirmation, setConfirmation] = useState<{ kind: 'retry' | 'retry-batch' | 'cancel'; job?: QueueJob } | null>(null);
  const [reason, setReason] = useState('');
  useEffect(() => { onBusyChange?.(!!busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  const active = useRef(true);
  const listEpoch = useRef(0);
  const detailEpoch = useRef(0);
  const lock = useRef(false);
  const requestKeys = useRef(new Map<string, string>());
  const target = `${session.scope_key}:${batchId}`;
  const targetRef = useRef(target); targetRef.current = target;
  useEffect(() => { active.current = true; return () => { active.current = false; listEpoch.current += 1; detailEpoch.current += 1; }; }, [session.scope_key]);
  useEffect(() => {
    const controller = new AbortController(); setCompanies([]);
    void api<BusinessContext>('/business/context', session, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setCompanies(value.companies); }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [session]);
  const loadList = useCallback(async (signal?: AbortSignal, quiet = false) => {
    const epoch = ++listEpoch.current;
    if (!quiet) setLoading(true);
    const query = new URLSearchParams({ limit: '20', offset: String(offset) }); if (companyId) query.set('company_id', companyId);
    try {
      const result = await api<{ batches: QueueBatch[]; total: number }>(`/intelligence/batches?${query}`, session, { signal });
      if (!active.current || signal?.aborted || epoch !== listEpoch.current) return;
      setBatches(result.batches); setTotal(result.total); setError('');
    } catch (failure) { if (active.current && !signal?.aborted && epoch === listEpoch.current) setError(errorMessage(failure)); }
    finally { if (active.current && !signal?.aborted && epoch === listEpoch.current && !quiet) setLoading(false); }
  }, [session, companyId, offset]);
  const loadDetail = useCallback(async (signal?: AbortSignal, quiet = false) => {
    if (!batchId) return;
    const epoch = ++detailEpoch.current;
    if (!quiet) setDetailLoading(true);
    try {
      const result = await api<QueueBatch>(`/intelligence/batches/${encoded(batchId)}`, session, { signal });
      if (!active.current || signal?.aborted || epoch !== detailEpoch.current) return;
      setBatch(result); setDetailError('');
    } catch (failure) { if (active.current && !signal?.aborted && epoch === detailEpoch.current) { setBatch(null); setDetailError(errorMessage(failure)); } }
    finally { if (active.current && !signal?.aborted && epoch === detailEpoch.current && !quiet) setDetailLoading(false); }
  }, [session, batchId]);
  useEffect(() => { const controller = new AbortController(); setBatches([]); setTotal(0); void loadList(controller.signal); return () => controller.abort(); }, [loadList]);
  useEffect(() => { const controller = new AbortController(); setBatch(null); setDetailError(''); setConfirmation(null); setReason(''); setNotice(''); void loadDetail(controller.signal); return () => controller.abort(); }, [loadDetail]);
  const polling = batches.some(batchActive) || !!batch && batchActive(batch);
  useEffect(() => {
    if (!polling || busy) return;
    const controller = new AbortController(); let timer: number | undefined;
    const poll = async () => { await Promise.all([loadList(controller.signal, true), loadDetail(controller.signal, true)]); if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), QUEUE_POLL_MS); };
    timer = window.setTimeout(() => void poll(), QUEUE_POLL_MS);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [polling, busy, loadList, loadDetail]);
  function chooseBatch(id: string) { if (busy) return; detailEpoch.current += 1; setBatch(null); setBatchId(id); setConfirmation(null); setReason(''); setNotice(''); }
  function key(signature: string) { let value = requestKeys.current.get(signature); if (!value) { value = crypto.randomUUID(); requestKeys.current.set(signature, value); } return value; }
  async function perform() {
    if (!batch || !confirmation || lock.current || confirmation.kind === 'cancel' && !reason.trim()) return;
    const { kind, job } = confirmation;
    if (kind === 'retry' && (!job || !canRetryJob(job)) || kind === 'cancel' && (!job || !canCancelJob(job))) return;
    const signature = `${kind}:${job?.id ?? batch.id}:${job?.attempt_count ?? batch.jobs.map(item => `${item.id}:${item.attempt_count}:${item.status}`).join(',')}`;
    lock.current = true; setBusy(signature); setDetailError(''); setNotice('');
    const startedTarget = target; const current = () => active.current && startedTarget === targetRef.current;
    try {
      const result = await api<QueueBatch>(kind === 'retry-batch' ? `/intelligence/batches/${encoded(batch.id)}/retry` : `/intelligence/jobs/${encoded(job!.id)}/${kind}`, session, { method: 'POST', body: kind === 'cancel' ? { reason: reason.trim() } : { idempotency_key: key(signature) } });
      if (!current()) return;
      setBatch(result); setConfirmation(null); setReason(''); requestKeys.current.delete(signature);
      setNotice(kind === 'cancel' ? '已记录取消请求。后台正在发送的请求可能仍产生费用；后续结果以任务记录为准。' : '失败条目已重新排队。后台会继续检查权限与当前附件版本。');
      await loadList(undefined, true);
    } catch (failure) {
      if (!current() || failure instanceof DOMException && failure.name === 'AbortError') return;
      let message = errorMessage(failure);
      if (shouldReadAfterFailure(failure)) { await loadDetail(undefined, true); await loadList(undefined, true); message += ' 已重新读取任务状态，请核对后再操作。'; }
      if (current()) setDetailError(message);
    } finally { if (current()) { setBusy(''); lock.current = false; } }
  }
  return <main className="queue-workbench"><section className="page-heading"><div><p className="eyebrow">DOCUMENTS, IN MOTION</p><h1>票据交给后台，判断留给会计。</h1><p className="page-description">批量任务持续保存，离开页面后继续处理；企业轮流获得处理机会。</p></div><button type="button" className="button secondary" disabled={loading || !!busy} onClick={() => void Promise.all([loadList(), loadDetail()])}><RefreshCw size={16} />刷新处理任务</button></section><section className="queue-intro"><Layers3 size={24} /><div><strong>票据处理任务</strong><p>任务完成只代表识别结束，异常处理与会计复核仍由人员完成。</p></div><span><ShieldCheck size={15} />进度仅计入获授权条目</span></section><div className="queue-workbench-grid"><section className="panel queue-batches-panel"><div className="panel-heading"><div><h2>识别批次<span className="count-label">{total}</span></h2><p>按企业查看排队、处理中和待处理任务</p></div></div><div className="queue-filter"><label><span>客户企业</span><select aria-label="筛选处理任务企业" value={companyId} disabled={!!busy} onChange={event => { listEpoch.current += 1; detailEpoch.current += 1; setBatches([]); setBatch(null); setBatchId(''); setCompanyId(event.target.value); setOffset(0); }}><option value="">全部获授权企业</option>{companies.map(company => <option value={company.id} key={company.id}>{company.name}</option>)}</select></label></div>{error && <p className="inline-error" role="alert"><TriangleAlert size={15} />{error}</p>}{loading ? <div className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取批次…</div> : batches.length ? <div className="queue-batch-list">{batches.map(item => <button type="button" key={item.id} aria-label={`查看识别批次 ${item.company_name} ${queueDate(item.created_at)}`} className={`queue-batch-row ${batchId === item.id ? 'selected' : ''}`} disabled={!!busy} onClick={() => chooseBatch(item.id)}><div><span className={`queue-batch-icon ${item.status}`}>{batchActive(item) ? <Clock3 size={20} /> : item.status === 'completed' ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</span><strong>{item.company_name}<small>{queueDate(item.created_at)}</small></strong><span className={`badge ${item.status === 'completed' ? 'approved' : 'draft'}`}>{batchStatusLabels[item.status]}</span></div><QueueBatchProgress batch={item} /></button>)}</div> : !error && <div className="empty-state"><div className="empty-icon"><FileSearch size={27} /></div><strong>当前没有可见处理任务</strong><p>在业务事项中上传票据，使用单份或批量识别创建任务。</p></div>}<div className="business-pagination"><span>{total} 个批次{total > 0 && ` · 第 ${Math.floor(offset / 20) + 1} 页`}</span><div><button type="button" className="text-button" disabled={loading || !!busy || offset === 0} onClick={() => { setBatches([]); setOffset(value => Math.max(0, value - 20)); }}>上一页</button><button type="button" className="text-button" disabled={loading || !!busy || offset + 20 >= total} onClick={() => { setBatches([]); setOffset(value => value + 20); }}>下一页</button></div></div></section>
    <aside className="panel queue-detail-panel" aria-label="票据处理任务详情"><div className="review-panel-header"><div className="review-heading"><span className="review-icon"><Layers3 size={18} /></span><div><h2>处理进度与恢复</h2><p>逐份查看结果和尝试记录</p></div></div><span className="review-step">PROCESS</span></div>{detailError && <p className="inline-error" role="alert"><TriangleAlert size={15} />{detailError}</p>}{notice && <p className="form-success queue-notice" role="status">{notice}</p>}{detailLoading ? <div className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取任务…</div> : batch ? <div className="queue-detail-content"><div className="queue-detail-title"><span className={`badge ${batch.status === 'completed' ? 'approved' : 'draft'}`}>{batchStatusLabels[batch.status]}</span><h3>{batch.company_name}</h3><p>{queueDate(batch.created_at)}</p></div><QueueBatchProgress batch={batch} /><p className="field-help">仅显示当前授权的任务，进度按这些条目计算。成功识别的票据仍须回到事项核对。</p>{batch.jobs.some(canRetryJob) && <button type="button" className="button secondary small" disabled={!!busy} onClick={() => setConfirmation({ kind: 'retry-batch' })}><RefreshCw size={14} />重试可处理的失败条目</button>}{confirmation && <section className="queue-confirmation" aria-label="确认任务操作"><div><strong>{confirmation.kind === 'cancel' ? '取消当前任务' : '确认重新识别'}</strong><button type="button" className="icon-button" aria-label="收起任务操作" disabled={!!busy} onClick={() => { setConfirmation(null); setReason(''); }}><X size={14} /></button></div><p>{confirmation.kind === 'cancel' ? '取消保留原附件和处理记录。已经发送的请求可能无法撤回，结果不会替代会计审核。' : RETRY_NOTICE}</p>{confirmation.kind === 'cancel' && <textarea aria-label="取消识别任务依据" rows={2} maxLength={1000} value={reason} disabled={!!busy} onChange={event => setReason(event.target.value)} placeholder="填写取消原因" />}<button type="button" className="button secondary small" disabled={!!busy || confirmation.kind === 'cancel' && !reason.trim()} onClick={() => void perform()}>{busy ? <LoaderCircle className="spinner" size={14} /> : <RefreshCw size={14} />}{confirmation.kind === 'cancel' ? '确认取消任务' : '确认重新排队'}</button></section>}
      <div className="queue-jobs">{batch.jobs.map(job => <article className="queue-job" key={job.id}><div className="queue-job-heading"><strong>{job.filename}</strong><small>{job.event_description}</small></div><QueueJobRecord job={job} /><div className="queue-job-actions"><button type="button" className="text-button" disabled={!!busy} onClick={() => onOpenEvent(job.event_id)}>核对业务事项<ArrowRight size={13} /></button>{canRetryJob(job) && <button type="button" className="text-button" disabled={!!busy} onClick={() => setConfirmation({ kind: 'retry', job })}>重试此任务</button>}{canCancelJob(job) && <button type="button" className="text-button" disabled={!!busy} onClick={() => { setReason(''); setConfirmation({ kind: 'cancel', job }); }}>取消此任务</button>}</div>{!!job.attempts?.length && <details className="queue-attempts"><summary>处理尝试记录 · {job.attempts.length} 次</summary>{job.attempts.map(attempt => <div key={attempt.attempt_no}><strong>第 {attempt.attempt_no} 次 · {attempt.status === 'succeeded' ? '完成' : attempt.status === 'running' ? '处理中' : attempt.status === 'interrupted' ? '中断' : attempt.status === 'failed' ? '失败' : attempt.status}</strong><span>{queueDate(attempt.created_at)}{attempt.finished_at && ` → ${queueDate(attempt.finished_at)}`}</span>{attempt.error_detail && <p>{attempt.error_detail}</p>}</div>)}</details>}</article>)}</div><details className="queue-identifiers"><summary>批次编号</summary><code>{batch.id}</code></details></div> : !detailError && <div className="empty-state"><div className="empty-icon"><Layers3 size={27} /></div><strong>选择一个处理批次</strong><p>处理进度、失败原因与恢复操作将在这里展示。</p></div>}</aside></div></main>;
}
