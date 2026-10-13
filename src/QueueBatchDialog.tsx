import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileSearch, LoaderCircle, Paperclip, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import { api, errorMessage } from './api';
import { processingUnavailable, shouldReadAfterFailure } from './intelligence-helpers';
import { batchSelectionError, MAX_BATCH_ATTACHMENTS, selectedQueueItems } from './queue-helpers';
import type { QueueBatch } from './queue-types';
import type { BusinessEvent } from './business-types';
import type { IntelligenceConfig } from './intelligence-types';
import type { Session } from './types';

export function QueueBatchDialog({ session, events, initialCompanyId = '', onClose, onCreated, onBusyChange }: {
  session: Session; events: BusinessEvent[]; initialCompanyId?: string;
  onClose: () => void; onCreated: (batch: QueueBatch) => void; onBusyChange: (value: boolean) => void;
}) {
  const eligible = events.map(event => ({ ...event, attachments: event.attachments.filter(attachment => !attachment.superseded) }))
    .filter(event => event.permissions.process_intelligence && event.attachments.length > 0);
  const companies = [...new Map(eligible.map(event => [event.company_id, { id: event.company_id, name: event.company_name || event.company_id }])).values()];
  const [companyId, setCompanyId] = useState(() => companies.some(company => company.id === initialCompanyId) ? initialCompanyId : companies[0]?.id ?? '');
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [config, setConfig] = useState<IntelligenceConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(true);
  const lock = useRef(false);
  const request = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => { dialog.current?.showModal(); active.current = true; return () => { active.current = false; onBusyChange(false); }; }, [session.scope_key, onBusyChange]);
  useEffect(() => {
    const controller = new AbortController(); setConfig(null);
    void api<IntelligenceConfig>('/intelligence/config', session, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setConfig(value); }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [session]);
  const currentEvents = eligible.filter(event => event.company_id === companyId);
  const items = selectedQueueItems(currentEvents, companyId, attachmentIds);
  const unavailable = processingUnavailable(config, companyId, 'ocr');
  async function submit() {
    if (lock.current || unavailable) return;
    const validation = batchSelectionError(items); if (validation) { setError(validation); return; }
    const signature = JSON.stringify({ companyId, items });
    if (request.current?.signature !== signature) request.current = { signature, key: crypto.randomUUID() };
    lock.current = true; setBusy(true); onBusyChange(true); setError('');
    try {
      const batch = await api<QueueBatch>('/intelligence/batches', session, { method: 'POST', body: { company_id: companyId, items, idempotency_key: request.current.key } });
      if (active.current) onCreated(batch);
    } catch (failure) {
      if (active.current && !(failure instanceof DOMException && failure.name === 'AbortError')) setError(errorMessage(failure) + (shouldReadAfterFailure(failure) ? ' 提交状态未确认。可先关闭并查看处理任务；保持当前选择再次提交将复用请求编号。' : ''));
    } finally { lock.current = false; if (active.current) { setBusy(false); onBusyChange(false); } }
  }
  return <dialog ref={dialog} className="modal queue-batch-modal" aria-labelledby="queue-batch-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}><div className="modal-header"><div><h2 id="queue-batch-title">批量识别本页票据</h2><p>选择同一企业的原始附件，提交后由后台逐份处理。</p></div><button type="button" className="icon-button" aria-label="关闭批量识别" disabled={busy} onClick={onClose}><X size={20} /></button></div><div className="form-content queue-batch-content"><label><span className="field-label">客户企业</span><select aria-label="批量识别企业" value={companyId} disabled={busy} onChange={event => { setCompanyId(event.target.value); setAttachmentIds([]); setError(''); }}>{companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label><p className="field-help"><ShieldCheck size={13} />只列出本页当前账号可处理的事项附件，每批最多 {MAX_BATCH_ATTACHMENTS} 份。</p>{unavailable && <p className="intelligence-unconfigured">{unavailable}</p>}
      {currentEvents.length > 0 && <div className="queue-batch-selection"><label><input type="checkbox" aria-label="选择本企业全部可处理附件" disabled={busy || !!unavailable || currentEvents.reduce((sum, event) => sum + event.attachments.length, 0) > MAX_BATCH_ATTACHMENTS} checked={items.length > 0 && items.length === currentEvents.reduce((sum, event) => sum + event.attachments.length, 0)} onChange={event => setAttachmentIds(event.target.checked ? currentEvents.flatMap(item => item.attachments.map(attachment => attachment.id)) : [])} /><span>选择本页该企业全部附件</span></label><strong>{items.length} 份已选</strong></div>}
      <div className="queue-batch-events">{currentEvents.map(event => <article key={event.id}><h3>{event.payload.description}</h3><p>{event.payload.occurred_on} · 第 {event.version} 版</p>{event.attachments.filter(attachment => !attachment.superseded).map(attachment => <label key={attachment.id}><input type="checkbox" aria-label={`识别附件 ${attachment.filename} ${event.payload.description}`} disabled={busy || !!unavailable || !attachmentIds.includes(attachment.id) && items.length >= MAX_BATCH_ATTACHMENTS} checked={attachmentIds.includes(attachment.id)} onChange={input => setAttachmentIds(ids => input.target.checked ? [...ids, attachment.id] : ids.filter(id => id !== attachment.id))} /><Paperclip size={14} /><span>{attachment.filename}</span><small>{(attachment.size_bytes / 1024).toFixed(0)} KB</small></label>)}</article>)}</div>
      {!currentEvents.length && <div className="empty-state"><FileSearch size={25} /><strong>本页没有可处理的附件</strong><p>先上传票据，并确认当前账号有事项处理权限。</p></div>}{error && <p className="form-error" role="alert"><TriangleAlert size={15} />{error}</p>}<p className="field-help">任务保存后可离开页面，进度与失败记录在“票据处理任务”查看。企业间轮流调度，识别结果不会自动审核凭证。</p></div><div className="modal-footer"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="button" className="button primary" disabled={busy || !!unavailable || !items.length} onClick={() => void submit()}>{busy ? <LoaderCircle className="spinner" size={15} /> : <CheckCircle2 size={15} />}提交 {items.length} 份识别任务</button></div></dialog>;
}
