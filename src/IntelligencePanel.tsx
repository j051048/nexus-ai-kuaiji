import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CheckCircle2,
  FileSearch,
  LoaderCircle,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { OcrEvidenceViewer, type OcrEvidenceSelection } from './OcrEvidenceViewer';
import { bindingLabel, fieldBinding, OCR_FIELD_LABELS } from './ocr-evidence';
import { QueueJobRecord } from './QueueJobRecord';
import { canRetryJob, jobActive, latestQueueJobs, QUEUE_POLL_MS } from './queue-helpers';
import type { QueueBatch } from './queue-types';
import { formatMoney } from './csv';
import {
  canApplyProposal,
  canResolveFinding,
  canStartIntelligenceAction,
  journalResult,
  latestRuns,
  ocrResult,
  processingUnavailable,
  recognizedMoney,
  runPending,
  runStatusLabel,
  runWaitingTimedOut,
  shouldReadAfterFailure,
} from './intelligence-helpers';
import type { BusinessEvent } from './business-types';
import type { Book, Draft, Session } from './types';
import type {
  IntelligenceConfig,
  IntelligenceDetail,
  IntelligenceRun,
  IntelligenceSettings,
  IndependentOcrEvidence,
  OcrDocument,
} from './intelligence-types';

const encoded = encodeURIComponent;
const documentLabels: Record<string, string> = {
  invoice: '发票',
  receipt: '收据',
  bank_receipt: '银行回单',
  other: '其他票据',
  unreadable: '无法识别',
};
const fieldLabels = OCR_FIELD_LABELS;

export function RecognizedDocument({
  document,
  ocrEvidence,
  documentIndex = 0,
  previewContext,
}: {
  document: OcrDocument;
  ocrEvidence?: IndependentOcrEvidence;
  documentIndex?: number;
  previewContext?: { session: Session; eventId: string; runId: string };
}) {
  const [selection, setSelection] = useState<OcrEvidenceSelection | null>(null);
  const independentEvidence =
    ocrEvidence?.schema_version === 'independent-ocr-v1' ? ocrEvidence : undefined;
  return (
    <article className="recognized-document">
      <h5>
        {documentLabels[document.document_type] ?? document.document_type}
        <span>
          {independentEvidence ? '字段提取 · 可核对独立文字证据' : '识别转录 · 待核对原件'}
        </span>
      </h5>
      <dl className="recognized-fields">
        {Object.entries(fieldLabels).map(([field, label]) => {
          const value = document[field as keyof OcrDocument];
          const missing = value === null || value === undefined || value === '';
          const binding = fieldBinding(independentEvidence, documentIndex, field);
          return (
            <div key={field}>
              <dt>{label}</dt>
              <dd className={missing ? 'text-warning' : undefined}>
                {field.endsWith('_amount')
                  ? recognizedMoney(typeof value === 'string' ? value : null, document.currency)
                  : typeof value === 'string' && value
                    ? value
                    : '待核对'}
              </dd>
              {independentEvidence &&
                previewContext &&
                !missing &&
                independentEvidence.pages.length > 0 && (
                  <dd className="ocr-field-action">
                    <button
                      type="button"
                      className={`ocr-locate-button ${!binding || binding.status !== 'matched' ? 'needs-review' : ''}`}
                      aria-label={`核对${label}的文字证据`}
                      aria-pressed={selection?.field === field}
                      onClick={() =>
                        setSelection({
                          field,
                          page: binding?.page ?? independentEvidence.pages[0].page,
                        })
                      }
                    >
                      <MapPin size={12} />
                      {bindingLabel(binding)}
                    </button>
                  </dd>
                )}
            </div>
          );
        })}
      </dl>
      {independentEvidence && previewContext && (
        <OcrEvidenceViewer
          {...previewContext}
          documentIndex={documentIndex}
          evidence={independentEvidence}
          selection={selection}
          onSelect={setSelection}
        />
      )}
      <details className="recognition-evidence">
        <summary>识别依据与页码</summary>
        {document.evidence.length ? (
          document.evidence.map((evidence, index) => (
            <div key={index}>
              <strong>
                {fieldLabels[evidence.field] ?? evidence.field} · 第 {evidence.page} 页
              </strong>
              <blockquote>{evidence.quote}</blockquote>
            </div>
          ))
        ) : (
          <p>未返回字段定位依据，请人工核对原始附件。</p>
        )}
        {document.pages.map((page) => (
          <details key={page.page}>
            <summary>第 {page.page} 页转录文本</summary>
            <pre>{page.raw_text || '未返回转录文本'}</pre>
          </details>
        ))}
      </details>
    </article>
  );
}

export function IntelligencePanel({
  session,
  eventId,
  event: suppliedEvent,
  book,
  draft,
  accounts = [],
  externalBusy = false,
  onBusyChange,
  onChanged,
  onDismiss,
  onOpenBatch,
  disableProposal = false,
}: {
  session: Session;
  eventId: string;
  event?: BusinessEvent;
  book?: Book;
  draft?: Draft;
  accounts?: { code: string; name: string }[];
  externalBusy?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => Promise<void>;
  onDismiss?: () => void;
  onOpenBatch?: (batchId: string) => void;
  disableProposal?: boolean;
}) {
  const [event, setEvent] = useState<BusinessEvent | null>(suppliedEvent ?? null);
  const [detail, setDetail] = useState<IntelligenceDetail | null>(null);
  const [config, setConfig] = useState<IntelligenceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [bankIds, setBankIds] = useState<string[]>([]);
  const [bankReason, setBankReason] = useState('');
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const active = useRef(true);
  const lock = useRef(false);
  const epoch = useRef(0);
  const requestKeys = useRef(new Map<string, string>());
  const callbacks = useRef({ onChanged, onBusyChange });
  callbacks.current = { onChanged, onBusyChange };
  const target = `${session.scope_key}:${eventId}`;
  const targetRef = useRef(target);
  targetRef.current = target;
  const loadedTarget = useRef<string | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
      callbacks.current.onBusyChange?.(false);
    };
  }, [target]);
  useEffect(() => {
    callbacks.current.onBusyChange?.(!!busy);
  }, [busy]);
  useEffect(() => {
    const revoke = () => {
      epoch.current += 1;
      setDetail(null);
      setEvent(null);
      setConfig(null);
      setError('票据授权已更新，已清除识别资料。请刷新后重新核对权限。');
      setLoading(false);
    };
    window.addEventListener('accounting:unauthorized', revoke);
    window.addEventListener('accounting:forbidden', revoke);
    return () => {
      window.removeEventListener('accounting:unauthorized', revoke);
      window.removeEventListener('accounting:forbidden', revoke);
    };
  }, []);
  useEffect(() => {
    if (suppliedEvent) setEvent(suppliedEvent);
  }, [suppliedEvent]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const version = ++epoch.current;
      const [nextDetail, nextConfig, nextEvent] = await Promise.all([
        api<IntelligenceDetail>(`/business/events/${encoded(eventId)}/intelligence`, session, {
          signal,
        }),
        api<IntelligenceConfig>('/intelligence/config', session, { signal }),
        api<BusinessEvent>(`/business/events/${encoded(eventId)}`, session, { signal }),
      ]);
      if (!active.current || signal?.aborted || version !== epoch.current) return;
      setDetail(nextDetail);
      setConfig(nextConfig);
      setEvent(nextEvent);
    },
    [eventId, session, suppliedEvent],
  );
  useEffect(() => {
    const controller = new AbortController();
    if (loadedTarget.current !== target) {
      loadedTarget.current = target;
      setDetail(null);
      setConfig(null);
      setEvent(suppliedEvent ?? null);
      setReasons({});
      setBankIds([]);
      setBankReason('');
      setError('');
      setNotice('');
    }
    setLoading(true);
    void load(controller.signal)
      .catch((failure) => {
        if (!controller.signal.aborted && active.current) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted && active.current) setLoading(false);
      });
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    if (!detail?.runs.some((run) => run.status === 'running')) return;
    const timer = window.setInterval(() => setCurrentTime(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [detail]);

  useEffect(() => {
    if (!detail?.queue_jobs?.some(jobActive)) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      try {
        await load(controller.signal);
      } catch (failure) {
        if (!controller.signal.aborted && active.current) setError(errorMessage(failure));
      }
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), QUEUE_POLL_MS);
    };
    timer = window.setTimeout(() => void poll(), QUEUE_POLL_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [load, detail?.queue_jobs?.some(jobActive)]);

  async function act(
    name: string,
    action: () => Promise<unknown>,
    success: string,
    changed = true,
  ) {
    if (!canStartIntelligenceAction(name, disableProposal, externalBusy, lock.current)) return;
    lock.current = true;
    setBusy(name);
    setError('');
    setNotice('');
    const actionTarget = target;
    const current = () => active.current && targetRef.current === actionTarget;
    try {
      const result = await action();
      if (!current()) return;
      const run = result as IntelligenceRun | null;
      if (changed) await callbacks.current.onChanged?.();
      if (!current()) return;
      await load();
      if (!current()) return;
      if (run && 'status' in run && run.status === 'failed')
        setError(run.error_detail || '模型处理失败，请核对配置或附件后重新处理。');
      else if (run && 'status' in run && run.status === 'stale')
        setError('依据已发生变化，本次结果未应用。请刷新后重新处理。');
      else setNotice(success);
    } catch (failure) {
      if (!current() || (failure instanceof DOMException && failure.name === 'AbortError')) return;
      let message = errorMessage(failure);
      // A network failure does not prove whether the persisted job completed. Read it before a retry.
      try {
        if (shouldReadAfterFailure(failure) && name !== 'refresh') {
          await callbacks.current.onChanged?.();
          if (!current()) return;
          message += ' 已重新读取最新版本与处理状态，请核对后操作。';
        }
        await load();
      } catch {
        message += ' 最新状态暂未读取成功，请刷新后核对。';
      }
      if (current()) setError(message);
    } finally {
      if (current()) {
        lock.current = false;
        setBusy('');
      }
    }
  }
  function idempotencyKey(signature: string) {
    let key = requestKeys.current.get(signature);
    if (!key) {
      key = crypto.randomUUID();
      requestKeys.current.set(signature, key);
    }
    return key;
  }
  async function processAttachments(attachmentIds: string[]) {
    if (!event || !attachmentIds.length) return;
    const signature = `queue:${event.id}:${event.version}:${[...attachmentIds].sort().join(',')}`;
    await act(
      `ocr:${attachmentIds.length === 1 ? attachmentIds[0] : 'all'}`,
      async () => {
        const batch = await api<QueueBatch>('/intelligence/batches', session, {
          method: 'POST',
          body: {
            company_id: event.company_id,
            items: attachmentIds.map((attachmentId) => ({
              event_id: event.id,
              attachment_id: attachmentId,
              expected_version: event.version,
            })),
            idempotency_key: idempotencyKey(signature),
          },
        });
        requestKeys.current.delete(signature);
        return batch;
      },
      '识别任务已保存，可离开页面，在“票据处理任务”查看进度。',
    );
  }
  async function proposeJournal() {
    if (!book || !draft) return;
    const signature = `proposal:${book.id}:${draft.id}:${draft.version}`;
    await act(
      'proposal',
      async () => {
        const latest = latestRuns(detail?.runs ?? [], 'proposal').find(
          (run) => run.draft_id === draft.id,
        );
        if (latest && !runPending(latest)) requestKeys.current.delete(signature);
        const run = await api<IntelligenceRun>(
          `/books/${encoded(book.id)}/drafts/${encoded(draft.id)}/ai-proposals`,
          session,
          {
            method: 'POST',
            body: { expected_version: draft.version, idempotency_key: idempotencyKey(signature) },
          },
        );
        if (run.status !== 'running') requestKeys.current.delete(signature);
        return run;
      },
      '模型建议已保存，采用后仍须会计复核。',
    );
  }
  const proposalUnavailable = processingUnavailable(config, event?.company_id ?? '', 'proposal');
  const companyEnabled = !!config?.enabled_company_ids.includes(event?.company_id ?? '');
  const disabled = !!busy || externalBusy || disableProposal;
  const activeAttachments = event?.attachments.filter((attachment) => !attachment.superseded) ?? [];
  const availableAttachments = activeAttachments.filter(
    (attachment) =>
      !processingUnavailable(config, event?.company_id ?? '', 'ocr', attachment.media_type),
  );
  const unavailable = availableAttachments.length
    ? ''
    : processingUnavailable(config, event?.company_id ?? '', 'ocr');
  const activeAttachmentIds = new Set(activeAttachments.map((attachment) => attachment.id));
  const latestProcessing = [
    ...latestRuns(detail?.runs ?? [], 'ocr').filter(
      (run) => !!run.attachment_id && activeAttachmentIds.has(run.attachment_id),
    ),
    ...latestRuns(detail?.runs ?? [], 'proposal'),
  ];
  const queueJobs = latestQueueJobs(detail?.queue_jobs ?? []).filter((job) =>
    activeAttachmentIds.has(job.attachment_id),
  );
  const queueRunning = queueJobs.some(jobActive);
  const running = queueRunning || latestProcessing.some((run) => runPending(run, currentTime));
  const waitingTimedOut = latestProcessing.some((run) => runWaitingTimedOut(run, currentTime));
  const canPropose =
    !!book?.permissions.edit &&
    draft?.status === 'draft' &&
    (detail?.permissions.generate_proposal ?? detail?.permissions.apply_proposal);
  const currentProposals = latestRuns(detail?.runs ?? [], 'proposal').filter(
    (run) => run.draft_id === draft?.id,
  );

  return (
    <section className="intelligence-panel" aria-label="票据识别与交叉核对">
      <div className="intelligence-heading">
        <div>
          <FileSearch size={17} />
          <h4>票据识别与交叉核对</h4>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="刷新票据核对结果"
          disabled={!!busy || externalBusy || loading}
          onClick={() =>
            void act('refresh', () => Promise.resolve(), '已读取最新处理状态。', false)
          }
        >
          <RefreshCw size={14} />
        </button>
      </div>
      <p className="intelligence-caption">
        金额、税额与交易对方来自附件转录；核对业务事项和流水后，由人员确认。
      </p>
      {loading || loadedTarget.current !== target ? (
        <div className="loading-block">
          <LoaderCircle className="spinner" size={16} />
          正在读取票据处理记录…
        </div>
      ) : (
        <>
          {error && (
            <p className="form-error" role="alert">
              <TriangleAlert size={15} />
              {error}
            </p>
          )}
          {notice && (
            <p className="form-success" role="status">
              <CheckCircle2 size={15} />
              {notice}
            </p>
          )}
          {busy && (
            <div className="intelligence-processing">
              <p role="status">
                <LoaderCircle className="spinner" size={15} />
                正在处理，请稍候。结果会保存，可稍后刷新查看。
              </p>
              {onDismiss && (
                <button type="button" className="text-button" onClick={onDismiss}>
                  收起处理详情
                </button>
              )}
            </div>
          )}
          {disableProposal && (
            <p className="field-help text-warning">
              当前草稿有未保存的修改，请先保存，再进行票据处理或核对。
            </p>
          )}
          {running && !busy && (
            <p className="field-help">
              有任务正在排队或处理中，页面会读取最新进度；可离开页面后继续查看。
            </p>
          )}
          {waitingTimedOut && !busy && (
            <p className="field-help text-warning">
              处理等待超时，可重新发起。原任务仍显示处理中；重新发起会使用新的请求编号。
            </p>
          )}
          {unavailable && (
            <div className="intelligence-unconfigured">
              <ShieldCheck size={15} />
              <span>
                {unavailable}
                <small>企业管理员可在“企业处理设置”开启；模型服务由系统管理员配置。</small>
              </span>
            </div>
          )}
          {detail && event && (
            <>
              {detail.permissions.process && activeAttachments.length > 1 && (
                <div className="intelligence-batch-action">
                  <button
                    className="button secondary small"
                    type="button"
                    disabled={
                      disabled || !!unavailable || queueRunning || queueJobs.some(canRetryJob)
                    }
                    title={
                      queueJobs.some(canRetryJob) ? '请在任务中心核对失败记录后明确重试' : undefined
                    }
                    onClick={() =>
                      void processAttachments(
                        availableAttachments.map((attachment) => attachment.id),
                      )
                    }
                  >
                    <FileSearch size={14} />
                    处理 {availableAttachments.length} 份可识别附件
                  </button>
                  <span>逐份处理，可离开页面</span>
                </div>
              )}
              <div className="intelligence-attachment-list">
                {event.attachments.map((attachment) => {
                  const run = latestRuns(detail.runs, 'ocr').find(
                    (item) => item.attachment_id === attachment.id,
                  );
                  const result = run ? ocrResult(run) : null;
                  const job = queueJobs.find((item) => item.attachment_id === attachment.id);
                  return (
                    <article className="intelligence-attachment" key={attachment.id}>
                      <div className="intelligence-file-head">
                        <strong>{attachment.filename}</strong>
                        {detail.permissions.process && (
                          <button
                            className="button secondary small"
                            type="button"
                            disabled={
                              !!attachment.superseded ||
                              disabled ||
                              !!processingUnavailable(
                                config,
                                event.company_id,
                                'ocr',
                                attachment.media_type,
                              ) ||
                              (!!job && (jobActive(job) || canRetryJob(job))) ||
                              (!!run && runPending(run, currentTime))
                            }
                            onClick={() => void processAttachments([attachment.id])}
                          >
                            {busy === `ocr:${attachment.id}` ? (
                              <LoaderCircle className="spinner" size={14} />
                            ) : (
                              <FileSearch size={14} />
                            )}
                            {attachment.media_type === 'application/xml'
                              ? '解析数电票 XML'
                              : run || job
                                ? '重新识别'
                                : '识别票据'}
                          </button>
                        )}
                      </div>
                      {attachment.superseded && (
                        <p className="field-help">
                          已由核验后的补件替代；保留历史结果，不计入当前核对。
                        </p>
                      )}
                      {job && <QueueJobRecord job={job} compact onOpenBatch={onOpenBatch} />}
                      {result && job && jobActive(job) && (
                        <p className="field-help">
                          新任务完成前，以下为历史识别结果，请勿作为当前核对依据。
                        </p>
                      )}
                      {run && (
                        <p className={`intelligence-run-status ${run.status}`}>
                          {runStatusLabel(run, currentTime)}
                          <span>
                            {run.provider} · {run.model}
                          </span>
                        </p>
                      )}
                      {run?.status === 'failed' && (
                        <p className="form-error">{run.error_detail || '未返回可用识别结果。'}</p>
                      )}
                      {run?.status === 'stale' && (
                        <p className="field-help">以下是历史结果，不作为当前事项的核对依据。</p>
                      )}
                      {result?.documents.map((document, index) => (
                        <RecognizedDocument
                          key={`${target}:${run?.id}:${index}`}
                          document={document}
                          documentIndex={index}
                          ocrEvidence={result.ocr_evidence}
                          previewContext={run ? { session, eventId, runId: run.id } : undefined}
                        />
                      ))}
                      {!run && !job && (
                        <p className="field-help">尚未识别，原始附件仍可从事项详情下载。</p>
                      )}
                    </article>
                  );
                })}
              </div>
              {!event.attachments.length && (
                <p className="field-help">上传原始附件后，可提取票据字段。</p>
              )}
              <div className="intelligence-check-heading">
                <h5>核对结果</h5>
                {detail.permissions.process && (
                  <button
                    className="text-button"
                    type="button"
                    disabled={disabled || running || !companyEnabled}
                    title={!companyEnabled ? '该企业尚未开启票据与模型处理' : undefined}
                    onClick={() =>
                      void act(
                        'reconcile',
                        () =>
                          api(`/business/events/${encoded(eventId)}/reconcile`, session, {
                            method: 'POST',
                            body: {},
                          }),
                        '已重新核对票据、业务事项与已关联流水。',
                        true,
                      )
                    }
                  >
                    <RefreshCw size={13} />
                    重新核对
                  </button>
                )}
              </div>
              {!detail.findings.length && (
                <p className="field-help">当前没有已记录的异常。票据与流水仍需人工核对。</p>
              )}
              {detail.findings.map((finding) => (
                <article
                  className={`intelligence-finding ${finding.resolved ? 'resolved' : finding.severity}`}
                  key={finding.id ?? `${finding.code}:${JSON.stringify(finding.payload)}`}
                >
                  <div>
                    {finding.resolved ? <CheckCircle2 size={15} /> : <TriangleAlert size={15} />}
                    <strong>
                      {finding.resolved
                        ? '已人工处理'
                        : finding.severity === 'blocking'
                          ? '待处理 · 阻断'
                          : '待核对 · 提醒'}
                    </strong>
                  </div>
                  <p>{finding.payload.message}</p>
                  {finding.resolved ? (
                    <p className="finding-resolution">处理依据：{finding.resolution_reason}</p>
                  ) : canResolveFinding(detail, finding) ? (
                    <div className="finding-editor">
                      <textarea
                        aria-label={`异常处理依据 ${finding.id}`}
                        rows={2}
                        maxLength={1000}
                        disabled={disabled}
                        placeholder="填写核对原件、补件或差异处理依据"
                        value={reasons[finding.id!] ?? ''}
                        onChange={(input) =>
                          setReasons((current) => ({
                            ...current,
                            [finding.id!]: input.target.value,
                          }))
                        }
                      />
                      <button
                        className="button secondary small"
                        type="button"
                        disabled={disabled || !(reasons[finding.id!] ?? '').trim()}
                        onClick={() =>
                          void act(
                            `resolve:${finding.id}`,
                            () =>
                              api(
                                `/intelligence/findings/${encoded(finding.id!)}/resolve`,
                                session,
                                { method: 'POST', body: { reason: reasons[finding.id!].trim() } },
                              ),
                            '已记录人工处理依据；凭证仍须会计审核。',
                            true,
                          )
                        }
                      >
                        记录人工处理
                      </button>
                    </div>
                  ) : finding.non_overridable || finding.payload.non_overridable === true ? (
                    <p className="field-help">
                      等待后台任务结束，或在任务中心取消任务后再核对。处理中状态不能用人工确认跳过。
                    </p>
                  ) : (
                    !finding.id && (
                      <p className="field-help">
                        这是最新资料的即时核对发现，请先“重新核对”登记后再记录人工处理。
                      </p>
                    )
                  )}
                </article>
              ))}
              <details className="intelligence-bank">
                <summary>
                  银行流水关联与防重复记账<span>{detail.bank_links.length} 条已关联</span>
                </summary>
                <p className="field-help">
                  由人员核对后关联同账套流水。已关联的银行流水草稿会被阻断，避免事项与付款重复计入。
                </p>
                {detail.bank_links.map((link) => (
                  <div className="bank-linked" key={link.bank_source_id}>
                    <strong>{link.external_id ?? link.bank_source_id}</strong>
                    <p>{link.reason || '已人工关联'}</p>
                    {detail.permissions.link_bank && (
                      <>
                        <input
                          aria-label={`解除流水关联依据 ${link.bank_source_id}`}
                          maxLength={1000}
                          placeholder="填写解除依据"
                          disabled={disabled}
                          value={reasons[link.bank_source_id] ?? ''}
                          onChange={(input) =>
                            setReasons((current) => ({
                              ...current,
                              [link.bank_source_id]: input.target.value,
                            }))
                          }
                        />
                        <button
                          className="text-button"
                          type="button"
                          disabled={disabled || !(reasons[link.bank_source_id] ?? '').trim()}
                          onClick={() =>
                            void act(
                              'unlink',
                              () =>
                                api(
                                  `/business/events/${encoded(eventId)}/bank-links/${encoded(link.bank_source_id)}`,
                                  session,
                                  {
                                    method: 'DELETE',
                                    body: { reason: reasons[link.bank_source_id].trim() },
                                  },
                                ),
                              '关联已解除，请核对事项与流水草稿。',
                              true,
                            )
                          }
                        >
                          解除关联
                        </button>
                      </>
                    )}
                  </div>
                ))}
                {detail.permissions.link_bank && (
                  <>
                    {detail.bank_candidates
                      .filter(
                        (candidate) =>
                          !detail.bank_links.some((link) => link.bank_source_id === candidate.id),
                      )
                      .map((candidate) => (
                        <label className="bank-candidate" key={candidate.id}>
                          <input
                            type="checkbox"
                            aria-label={`关联流水 ${candidate.external_id}`}
                            checked={bankIds.includes(candidate.id)}
                            disabled={disabled}
                            onChange={(input) =>
                              setBankIds((current) =>
                                input.target.checked
                                  ? [...current, candidate.id]
                                  : current.filter((id) => id !== candidate.id),
                              )
                            }
                          />
                          <span>
                            <strong>{candidate.description}</strong>
                            <small>
                              {candidate.date} · {candidate.counterparty} · {candidate.external_id}
                            </small>
                          </span>
                          <b>
                            {candidate.direction === 'expense' ? '支出' : '收入'} ¥{' '}
                            {formatMoney(candidate.amount)}
                          </b>
                        </label>
                      ))}
                    {!detail.bank_candidates.length && (
                      <p className="field-help">
                        暂无可关联流水。已收付事项交接到账套后，可从导入的流水中核对。
                      </p>
                    )}
                    {bankIds.length > 0 && (
                      <div className="bank-link-editor">
                        <textarea
                          aria-label="流水关联依据"
                          rows={2}
                          maxLength={1000}
                          disabled={disabled}
                          placeholder="填写付款人、金额与日期等关联依据"
                          value={bankReason}
                          onChange={(input) => setBankReason(input.target.value)}
                        />
                        <button
                          className="button secondary small"
                          type="button"
                          disabled={disabled || !bankReason.trim()}
                          onClick={() =>
                            void act(
                              'link',
                              async () => {
                                await api(
                                  `/business/events/${encoded(eventId)}/bank-links`,
                                  session,
                                  {
                                    method: 'POST',
                                    body: { bank_source_ids: bankIds, reason: bankReason.trim() },
                                  },
                                );
                                setBankIds([]);
                                setBankReason('');
                              },
                              '流水已关联，重复记账保护已更新。',
                              true,
                            )
                          }
                        >
                          确认关联 {bankIds.length} 条流水
                        </button>
                      </div>
                    )}
                  </>
                )}
              </details>
              {draft && book && (
                <section className="intelligence-proposals">
                  <div className="intelligence-check-heading">
                    <h5>
                      <Sparkles size={14} />
                      模型记账建议
                    </h5>
                    {canPropose && (
                      <button
                        className="button secondary small"
                        type="button"
                        disabled={disabled || running || !!proposalUnavailable || disableProposal}
                        title={
                          disableProposal
                            ? '请先保存当前草稿修改'
                            : proposalUnavailable || undefined
                        }
                        onClick={() => void proposeJournal()}
                      >
                        {busy === 'proposal' ? (
                          <LoaderCircle className="spinner" size={14} />
                        ) : (
                          <Sparkles size={14} />
                        )}
                        生成分录建议
                      </button>
                    )}
                  </div>
                  <p className="field-help">
                    {proposalUnavailable ||
                      '仅使用本企业事项、票据和当前账套科目生成建议。采用到草稿后，还需确认审核才能导出。'}
                  </p>
                  {currentProposals.map((run) => {
                    const proposal = journalResult(run);
                    return (
                      <article className="journal-proposal" key={run.id}>
                        <p className={`intelligence-run-status ${run.status}`}>
                          {runStatusLabel(run, currentTime)}
                          <span>
                            草稿版本 {run.input_version} · {run.model}
                          </span>
                        </p>
                        {run.status === 'failed' && (
                          <p className="form-error">{run.error_detail || '未返回可用分录建议。'}</p>
                        )}
                        {proposal && (
                          <>
                            <strong>{proposal.description}</strong>
                            <div className="proposal-table-wrap">
                              <table className="proposal-table">
                                <thead>
                                  <tr>
                                    <th>会计科目</th>
                                    <th>借方</th>
                                    <th>贷方</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {proposal.lines.map((line, index) => (
                                    <tr key={index}>
                                      <td>
                                        {line.account_code}{' '}
                                        {
                                          accounts.find(
                                            (account) => account.code === line.account_code,
                                          )?.name
                                        }
                                      </td>
                                      <td>{formatMoney(line.debit)}</td>
                                      <td>{formatMoney(line.credit)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                            <p>{proposal.reasoning}</p>
                            {proposal.evidence_refs.length > 0 && (
                              <p className="proposal-evidence">
                                依据：
                                {proposal.evidence_refs
                                  .map(
                                    (reference) =>
                                      `${event.attachments.find((attachment) => attachment.id === reference.attachment_id)?.filename ?? reference.attachment_id} · ${fieldLabels[reference.field] ?? reference.field} · 第 ${reference.page} 页`,
                                  )
                                  .join('；')}
                              </p>
                            )}
                            {proposal.warnings.length > 0 && (
                              <ul className="proposal-warnings">
                                {proposal.warnings.map((warning, index) => (
                                  <li key={index}>{warning}</li>
                                ))}
                              </ul>
                            )}
                            {detail.permissions.apply_proposal && draft.status === 'draft' && (
                              <button
                                className="button secondary small"
                                type="button"
                                disabled={
                                  disabled ||
                                  disableProposal ||
                                  !canApplyProposal(detail, run, draft.version)
                                }
                                title={
                                  !canApplyProposal(detail, run, draft.version)
                                    ? '请先处理阻断异常，并使用当前草稿版本的有效建议'
                                    : disableProposal
                                      ? '请先保存当前草稿修改'
                                      : undefined
                                }
                                onClick={() =>
                                  void act(
                                    'apply',
                                    () =>
                                      api(
                                        `/intelligence/proposals/${encoded(run.id)}/apply`,
                                        session,
                                        {
                                          method: 'POST',
                                          body: { expected_version: draft.version },
                                        },
                                      ),
                                    '已采用到凭证草稿，请核对后确认审核。',
                                    true,
                                  )
                                }
                              >
                                采用到草稿
                              </button>
                            )}
                          </>
                        )}
                      </article>
                    );
                  })}
                  {!currentProposals.length && (
                    <p className="field-help">尚未生成模型建议，当前凭证草稿保留原有规则分录。</p>
                  )}
                </section>
              )}
              {detail.runs.length > 0 && (
                <details className="intelligence-history">
                  <summary>
                    模型处理记录<span>{detail.runs.length} 次</span>
                  </summary>
                  {[...detail.runs].reverse().map((run) => (
                    <div key={run.id}>
                      <strong>
                        {run.kind === 'ocr' ? '票据识别' : '分录建议'} ·{' '}
                        {runStatusLabel(run, currentTime)}
                      </strong>
                      <small>
                        {run.created_at} · {run.provider} / {run.model} · 提示版本{' '}
                        {run.prompt_version}
                      </small>
                      <small>任务编号 {run.id}</small>
                    </div>
                  ))}
                </details>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

export function IntelligenceCompanySettings({
  session,
  companyId,
  onBusyChange,
}: {
  session: Session;
  companyId: string;
  onBusyChange: (busy: boolean) => void;
}) {
  const [settings, setSettings] = useState<IntelligenceSettings | null>(null);
  const [config, setConfig] = useState<IntelligenceConfig | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const lock = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      onBusyChange(false);
    };
  }, [companyId, session.scope_key, onBusyChange]);
  useEffect(() => {
    const controller = new AbortController();
    setSettings(null);
    setConfig(null);
    setError('');
    if (!companyId) return () => controller.abort();
    void Promise.all([
      api<IntelligenceSettings>(`/companies/${encoded(companyId)}/intelligence/settings`, session, {
        signal: controller.signal,
      }),
      api<IntelligenceConfig>('/intelligence/config', session, { signal: controller.signal }),
    ])
      .then(([value, runtime]) => {
        if (!controller.signal.aborted) {
          setSettings(value);
          setConfig(runtime);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [companyId, session]);
  async function change() {
    if (!settings?.can_manage || lock.current) return;
    lock.current = true;
    setBusy(true);
    onBusyChange(true);
    setError('');
    try {
      const value = await api<IntelligenceSettings>(
        `/companies/${encoded(companyId)}/intelligence/settings`,
        session,
        { method: 'PUT', body: { enabled: !settings.enabled } },
      );
      if (active.current) setSettings(value);
    } catch (failure) {
      if (active.current && !(failure instanceof DOMException && failure.name === 'AbortError'))
        setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (active.current) {
        setBusy(false);
        onBusyChange(false);
      }
    }
  }
  if (!companyId) return null;
  return (
    <section className="intelligence-company-settings">
      <div>
        <ShieldCheck size={17} />
        <h3>企业处理设置</h3>
      </div>
      <strong>票据与模型处理</strong>
      <p>
        开启后，获授权人员可将该企业上传的附件与业务事项发送至系统管理员配置的模型服务，提取票据字段并生成分录建议。
      </p>
      {settings?.can_manage && (
        <p className="field-help">
          企业默认开启 AI。暂停、恢复与费用限制可在
          <Link to={`/admin/organizations/${encoded(companyId)}?tab=ai`}>企业 AI 管理</Link>
          中统一设置；管理操作需完成二次验证。
        </p>
      )}
      <p className="field-help">
        当前服务：{config?.configured ? config.provider_label : '等待管理员配置'}
        。所有结果均需人工核对，会计审核后方可导出。
      </p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {settings ? (
        <div className="intelligence-setting-action">
          <span className={`badge ${settings.enabled ? 'approved' : 'draft'}`}>
            {settings.enabled ? '该企业已开启' : '管理员已暂停'}
          </span>
          {settings.can_manage && (
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => void change()}
            >
              {busy ? <LoaderCircle className="spinner" size={14} /> : <ShieldCheck size={14} />}
              {settings.enabled ? '关闭票据与模型处理' : '开启票据与模型处理'}
            </button>
          )}
        </div>
      ) : (
        !error && <p className="field-help">正在读取企业设置…</p>
      )}
    </section>
  );
}
