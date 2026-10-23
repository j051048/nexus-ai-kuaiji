import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Download,
  FileText,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import { formatCents, formatMoney, moneyToCents } from './csv';
import { EvidenceViewer, type EvidenceFocus, type ReviewAttachment } from './EvidenceViewer';
import { JournalEntryGrid } from './JournalEntryGrid';
import {
  AccountingContextAssistant,
  type AccountingContextAction,
} from './AccountingContextAssistant';
import { clearReviewSession, inputOwnsKeyboard } from './review-session';
import { WorkflowGuide } from './WorkflowGuide';
import { reviewGuidance } from './review-guidance';
import type { BusinessEvent } from './business-types';
import type { SourceHistory } from './SourceHistoryPanel';
import type { BookDetail, Draft, JournalLine, Session } from './types';
import './inline-review.css';

export interface InlineReviewTarget {
  book_id: string;
  draft_id: string;
}
interface BankOriginal {
  file_id: string;
  filename: string;
  sha256: string;
  row_number: number;
  original_position?: number;
  raw: string[];
  headers?: string[];
}
export function InlineReviewDrawer({
  session,
  bookId,
  draftId,
  queue = [],
  onClose,
  onNavigate,
  onChanged,
  onBusyChange,
}: {
  session: Session;
  bookId: string;
  draftId: string;
  queue?: InlineReviewTarget[];
  onClose: () => void;
  onNavigate?: (bookId: string, draftId: string) => void;
  onChanged?: (bookId: string, draft: Draft) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [detail, setDetail] = useState<BookDetail | null>(null),
    [draft, setDraft] = useState<Draft | null>(null);
  const [attachments, setAttachments] = useState<ReviewAttachment[]>([]),
    [evidenceError, setEvidenceError] = useState('');
  const [bankOriginals, setBankOriginals] = useState<BankOriginal[]>([]);
  const [description, setDescription] = useState(''),
    [lines, setLines] = useState<JournalLine[]>([]),
    [reason, setReason] = useState('');
  const [acknowledged, setAcknowledged] = useState(false),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState('');
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [keyboardHelp, setKeyboardHelp] = useState(false);
  const [leaveTarget, setLeaveTarget] = useState<InlineReviewTarget | 'close' | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(
    () => window.matchMedia('(min-width: 1440px)').matches,
  );
  const assistantWasOpen = useRef(assistantOpen);
  const [evidenceFocus, setEvidenceFocus] = useState<EvidenceFocus | undefined>();
  const panel = useRef<HTMLDivElement>(null),
    facts = useRef<HTMLElement>(null),
    evidenceColumn = useRef<HTMLDivElement>(null),
    assistantButton = useRef<HTMLButtonElement>(null),
    epoch = useRef(0),
    active = useRef(true),
    command = useRef(false);
  const scope = `${session.scope_key}:${bookId}:${draftId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const source = detail?.sources.find((row) => row.id === draft?.source_id);
  const dirty =
    !!draft &&
    (description !== draft.description || JSON.stringify(lines) !== JSON.stringify(draft.lines));
  const readonly =
    !detail?.book.permissions.edit ||
    draft?.status !== 'draft' ||
    !!draft?.source_inactive ||
    detail?.book.period_status === 'closed';
  const position = queue.findIndex((row) => row.book_id === bookId && row.draft_id === draftId);
  let debit = 0n,
    credit = 0n,
    amountError = '';
  try {
    for (const line of lines) {
      debit += moneyToCents(line.debit);
      credit += moneyToCents(line.credit);
    }
  } catch {
    amountError = '金额须为非负数字，最多保留两位小数。';
  }
  const balanced = !amountError && debit > 0n && debit === credit;
  const canSave = !readonly && !busy && dirty && !!description.trim() && !amountError;
  const canApprove =
    !readonly &&
    !!detail?.book.permissions.review &&
    !busy &&
    !!draft &&
    !dirty &&
    acknowledged &&
    balanced &&
    !draft.issues.length &&
    !!source?.evidence_ref &&
    !evidenceError &&
    !error;
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('accounting:review-dirty', { detail: dirty }));
    return () => {
      window.dispatchEvent(new CustomEvent('accounting:review-dirty', { detail: false }));
    };
  }, [dirty]);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(!!busy || dirty);
    return () => onBusyChange?.(false);
  }, [busy, dirty, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const request = ++epoch.current,
        target = scope;
      setLoading(true);
      setError('');
      setEvidenceError('');
      setAttachments([]);
      setBankOriginals([]);
      setDetail(null);
      setDraft(null);
      setAcknowledged(false);
      setLeaveTarget(null);
      setEvidenceFocus(undefined);
      try {
        const value = await api<BookDetail>(
          `/books/${encodeURIComponent(bookId)}?focus_draft_id=${encodeURIComponent(draftId)}&limit=1`,
          session,
          { signal },
        );
        if (
          !active.current ||
          target !== scopeRef.current ||
          request !== epoch.current ||
          signal?.aborted
        )
          return;
        const item = value.drafts.find((row) => row.id === draftId);
        if (!item) throw new Error('当前凭证不存在或无权访问，请返回待办重新读取。');
        setDetail(value);
        setDraft(item);
        setDescription(item.description);
        setLines(structuredClone(item.lines));
        setReason('');
        if (item.business_origin) {
          const historyBase = `/books/${encodeURIComponent(bookId)}/sources/${encodeURIComponent(item.source_id)}/history`;
          try {
            const evidence = item.source_inactive
              ? await api<SourceHistory>(historyBase, session, { signal })
              : await api<BusinessEvent>(
                  `/business/events/${encodeURIComponent(item.business_origin.event_id)}`,
                  session,
                  { signal },
                );
            if (
              !active.current ||
              target !== scopeRef.current ||
              request !== epoch.current ||
              signal?.aborted
            )
              return;
            if (!item.source_inactive) {
              const mapping = (evidence as BusinessEvent).mapping;
              if (
                !mapping ||
                mapping.book_id !== bookId ||
                mapping.source_id !== item.source_id ||
                mapping.draft_id !== item.id
              ) {
                throw new Error('业务事实已移交或修订，请重新读取当前来源，再核对对应原件。');
              }
            }
            setAttachments(
              evidence.attachments
                .map<ReviewAttachment>((row) => ({
                  ...row,
                  contentPath: item.source_inactive
                    ? `${historyBase}/attachments/${encodeURIComponent(row.id)}/content`
                    : `/business/attachments/${encodeURIComponent(row.id)}/content`,
                }))
                .sort((a, b) => Number(!!a.superseded) - Number(!!b.superseded)),
            );
          } catch (failure) {
            if (
              !active.current ||
              target !== scopeRef.current ||
              request !== epoch.current ||
              signal?.aborted
            )
              return;
            setEvidenceError(errorMessage(failure));
            if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
              setAcknowledged(false);
          }
        } else {
          const originals = await api<{ originals: BankOriginal[] }>(
            `/books/${encodeURIComponent(bookId)}/import-wizard/sources/${encodeURIComponent(item.source_id)}/evidence`,
            session,
            { signal },
          );
          if (
            !active.current ||
            target !== scopeRef.current ||
            request !== epoch.current ||
            signal?.aborted
          )
            return;
          setBankOriginals(originals.originals);
        }
      } catch (failure) {
        if (
          active.current &&
          target === scopeRef.current &&
          request === epoch.current &&
          !signal?.aborted
        ) {
          setError(errorMessage(failure));
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            clearReviewSession();
            setDetail(null);
            setDraft(null);
            setAttachments([]);
            setBankOriginals([]);
          }
        }
      } finally {
        if (
          active.current &&
          target === scopeRef.current &&
          request === epoch.current &&
          !signal?.aborted
        )
          setLoading(false);
      }
    },
    [bookId, draftId, session, scope],
  );
  useEffect(() => {
    const controller = new AbortController();
    setNotice('');
    command.current = false;
    setBusy('');
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current++;
    };
  }, [load]);
  useEffect(() => {
    const revoke = () => {
      clearReviewSession();
      epoch.current++;
      command.current = false;
      setBusy('');
      setDetail(null);
      setDraft(null);
      setAttachments([]);
      setBankOriginals([]);
      setLoading(false);
      setError('当前授权已更新，请返回待办重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, []);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.focus();
    return () => {
      document.body.style.overflow = oldOverflow;
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    if (assistantOpen && window.matchMedia('(max-width: 1100px)').matches)
      panel.current?.querySelector<HTMLButtonElement>('.context-assistant-close')?.focus();
    else if (!assistantOpen && assistantWasOpen.current) assistantButton.current?.focus();
    assistantWasOpen.current = assistantOpen;
  }, [assistantOpen, draft?.id]);

  function navigate(target: InlineReviewTarget | 'close') {
    if (busy) return;
    if (dirty) {
      setLeaveTarget(target);
      return;
    }
    if (target === 'close') onClose();
    else onNavigate?.(target.book_id, target.draft_id);
  }
  function closeAssistant() {
    setAssistantOpen(false);
    assistantButton.current?.focus();
  }
  function openEvidence(evidenceId: string) {
    const attachment = evidenceId.startsWith('attachment:')
      ? attachments.find((row) => row.id === evidenceId.slice('attachment:'.length))
      : undefined;
    if (attachment) setEvidenceFocus({ attachment_id: attachment.id });
    else if (evidenceId.startsWith('source:'))
      setEvidenceFocus({ reference: source?.evidence_ref || '当前来源尚未填写凭据引用' });
    else {
      facts.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
      facts.current?.focus();
    }
    if (attachment || evidenceId.startsWith('source:'))
      evidenceColumn.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
    if (window.matchMedia('(max-width: 1100px)').matches) closeAssistant();
  }
  function assistantAction(action: AccountingContextAction) {
    if (action.book_id !== bookId || action.draft_id !== draftId || !action.enabled) return;
    // These callbacks reveal the existing manual controls; no journal mutation occurs here.
    if (action.type === 'review' || action.type === 'edit' || action.type === 'history') {
      const target =
        action.type === 'edit'
          ? facts.current?.querySelector<HTMLTextAreaElement>('textarea')
          : facts.current;
      target?.scrollIntoView({ block: 'center', behavior: 'auto' });
      target?.focus();
      if (window.matchMedia('(max-width: 1100px)').matches) closeAssistant();
    }
  }
  function discardAndNavigate() {
    const target = leaveTarget;
    setLeaveTarget(null);
    if (target === 'close') onClose();
    else if (target) onNavigate?.(target.book_id, target.draft_id);
  }
  async function downloadOriginal(original: BankOriginal) {
    if (command.current) return;
    command.current = true;
    setBusy('download');
    const target = scope,
      generation = epoch.current;
    try {
      const blob = await apiBlob(
        `/books/${encodeURIComponent(bookId)}/import-wizard/files/${encodeURIComponent(original.file_id)}`,
        session,
      );
      if (!active.current || target !== scopeRef.current || generation !== epoch.current) return;
      const url = URL.createObjectURL(blob),
        link = document.createElement('a');
      link.href = url;
      link.download = original.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (failure) {
      if (active.current && target === scopeRef.current && generation === epoch.current)
        setError(errorMessage(failure));
    } finally {
      if (active.current && target === scopeRef.current && generation === epoch.current) {
        command.current = false;
        setBusy('');
      }
    }
  }
  async function mutate(action: 'save' | 'approve', continueTo?: InlineReviewTarget | 'close') {
    if (!draft || command.current || !detail) return;
    command.current = true;
    setBusy(action);
    setError('');
    setNotice('');
    const target = scope,
      generation = epoch.current;
    try {
      const value = await api<Draft>(
        `/books/${encodeURIComponent(bookId)}/drafts/${encodeURIComponent(draft.id)}${action === 'approve' ? '/approve' : ''}`,
        session,
        {
          method: action === 'approve' ? 'POST' : 'PATCH',
          body:
            action === 'approve'
              ? { expected_version: draft.version }
              : {
                  expected_version: draft.version,
                  description: description.trim(),
                  lines,
                  correction_reason: reason.trim(),
                },
        },
      );
      if (!active.current || target !== scopeRef.current || generation !== epoch.current) return;
      setDraft(value);
      setDescription(value.description);
      setLines(structuredClone(value.lines));
      setReason('');
      setAcknowledged(false);
      setNotice(
        action === 'approve'
          ? '凭证已审核，后续仍由会计执行导出。'
          : '草稿已保存，原批量预览需按最新版本重新准备。',
      );
      onChanged?.(bookId, value);
      if (continueTo === 'close') onClose();
      else if (continueTo) onNavigate?.(continueTo.book_id, continueTo.draft_id);
    } catch (failure) {
      if (active.current && target === scopeRef.current && generation === epoch.current) {
        setError(
          `${errorMessage(failure)}${failure instanceof ApiError && failure.status === 409 ? ' 请重新读取当前版本，再核对后提交。' : ''}`,
        );
        setAcknowledged(false);
      }
    } finally {
      if (active.current && target === scopeRef.current && generation === epoch.current) {
        command.current = false;
        setBusy('');
      }
    }
  }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (document.querySelector('dialog[open]')) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (
          canSave &&
          !leaveTarget &&
          !(
            event.target instanceof Element && event.target.closest('.accounting-context-assistant')
          )
        )
          void mutate('save');
        return;
      }
      if (event.key === 'Tab') {
        const focusable = Array.from(
          panel.current?.querySelectorAll<HTMLElement>(
            'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], iframe, [tabindex="0"]',
          ) ?? [],
        ).filter(
          (element) =>
            element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden',
        );
        if (!focusable?.length) return;
        const first = focusable[0],
          last = focusable[focusable.length - 1];
        if (
          event.shiftKey &&
          (document.activeElement === first || document.activeElement === panel.current)
        ) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        if (assistantOpen && window.matchMedia('(max-width: 1100px)').matches) closeAssistant();
        else navigate('close');
        return;
      }
      if (
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        (event.shiftKey && event.key !== '?') ||
        inputOwnsKeyboard(event.target) ||
        busy ||
        loading ||
        leaveTarget ||
        (event.target instanceof Element && !!event.target.closest('.accounting-context-assistant'))
      )
        return;
      if (event.key === '?') {
        event.preventDefault();
        setKeyboardHelp((value) => !value);
        return;
      }
      if (event.key.toLowerCase() === 'e' && !readonly) {
        event.preventDefault();
        facts.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
        return;
      }
      if (event.key.toLowerCase() === 'a' && canApprove) {
        event.preventDefault();
        void mutate('approve');
        return;
      }
      if (
        (event.key.toLowerCase() === 'j' || event.key === 'ArrowRight') &&
        position >= 0 &&
        position < queue.length - 1
      ) {
        event.preventDefault();
        navigate(queue[position + 1]);
      }
      if ((event.key.toLowerCase() === 'k' || event.key === 'ArrowLeft') && position > 0) {
        event.preventDefault();
        navigate(queue[position - 1]);
      }
    }
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  });
  return (
    <div className="inline-review-backdrop">
      <div
        className={`inline-review-drawer ${assistantOpen ? 'assistant-open' : ''}`}
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="inline-review-title"
      >
        <header className="inline-review-header">
          <div>
            <p>同屏复核{position >= 0 && ` · ${position + 1} / ${queue.length}`}</p>
            <h2 id="inline-review-title">原件与凭证，一起核对</h2>
            <span className="inline-review-scope">
              {detail?.book.company_name} · {detail?.book.name} · {detail?.book.period}
            </span>
            {draft && (
              <div className="inline-review-status-axes" aria-label="审核与外部交付状态">
                <span className={'review-axis review-axis-' + draft.status}>
                  审核：
                  {draft.status === 'draft'
                    ? '待会计审核'
                    : draft.status === 'approved'
                      ? '已审核'
                      : '已导出'}
                </span>
                <span className="review-axis">
                  外部交付：{draft.status === 'exported' ? '已导出，待回执核验' : '未导出'}
                </span>
                <span className="review-axis">事实版本：v{draft.version}</span>
              </div>
            )}
          </div>
          <div className="inline-review-header-actions">
            <button
              ref={assistantButton}
              className={`button secondary small inline-assistant-toggle ${assistantOpen ? 'active' : ''}`}
              aria-expanded={assistantOpen}
              disabled={loading || !draft}
              onClick={() => setAssistantOpen((value) => !value)}
            >
              <Sparkles size={15} />
              会计助手
            </button>
            <button
              className="icon-button"
              aria-label="关闭同屏复核"
              disabled={!!busy}
              onClick={() => navigate('close')}
            >
              <X size={20} />
            </button>
          </div>
        </header>
        {error && (
          <p className="inline-error inline-review-message" role="alert">
            <TriangleAlert size={16} />
            {error}
            <button className="text-button" disabled={!!busy || dirty} onClick={() => void load()}>
              重新读取
            </button>
          </p>
        )}
        {notice && (
          <p className="inline-review-notice" role="status">
            <CheckCircle2 size={16} />
            {notice}
          </p>
        )}
        {leaveTarget && (
          <section className="inline-review-unsaved" role="alert">
            <strong>有尚未保存的修改</strong>
            <p>继续核对可以保留修改；离开前可先保存，或明确放弃本次修改。</p>
            <div>
              <button className="button secondary small" onClick={() => setLeaveTarget(null)}>
                继续修改
              </button>
              <button
                className="button primary small"
                disabled={!canSave}
                onClick={() => {
                  if (leaveTarget) void mutate('save', leaveTarget);
                }}
              >
                保存后{leaveTarget === 'close' ? '返回列表' : '切换凭证'}
              </button>
              <button className="button secondary small" onClick={discardAndNavigate}>
                放弃修改并{leaveTarget === 'close' ? '返回列表' : '切换凭证'}
              </button>
            </div>
          </section>
        )}
        {keyboardHelp && (
          <p className="inline-review-keyboard-help" role="status">
            J / K 下一笔 / 上一笔 · E 编辑摘要 · Ctrl / ⌘ S 保存 · A 审核（须先勾选已核对）· Esc
            返回。输入框内保留原有键盘操作。
          </p>
        )}
        {loading ? (
          <div className="loading-block">
            <LoaderCircle size={20} className="spinner" />
            正在读取最新版本及授权原件…
          </div>
        ) : detail && draft ? (
          <div className="inline-review-content">
            <div className="inline-review-columns">
              <div className="inline-review-evidence-column" ref={evidenceColumn}>
                {evidenceError && (
                  <p className="inline-error" role="alert">
                    原件暂不可读取：{evidenceError}
                  </p>
                )}
                {bankOriginals.length > 0 ? (
                  <section className="inline-bank-originals" aria-label="流水原表与对应行">
                    <h3>
                      <FileText size={17} />
                      流水原表与对应行
                    </h3>
                    {bankOriginals.map((original) => (
                      <article key={original.file_id}>
                        <div>
                          <strong>{original.filename}</strong>
                          <button
                            className="text-button"
                            disabled={!!busy}
                            onClick={() => void downloadOriginal(original)}
                          >
                            <Download size={14} />
                            下载原表
                          </button>
                        </div>
                        <p>
                          原表第 {original.original_position ?? original.row_number} 行 · SHA-256{' '}
                          {original.sha256.slice(0, 12)}…
                        </p>
                        <dl>
                          {original.raw.map((value, i) => (
                            <div key={i}>
                              <dt>{original.headers?.[i] || `原表第 ${i + 1} 列`}</dt>
                              <dd>{value || '—'}</dd>
                            </div>
                          ))}
                        </dl>
                      </article>
                    ))}
                    <p className="field-help">以上为留存原表的文字内容，仍须核对流水与交易依据。</p>
                  </section>
                ) : (
                  <EvidenceViewer
                    session={session}
                    attachments={attachments}
                    reference={source?.evidence_ref ?? ''}
                    historical={!!draft.source_inactive}
                    focus={evidenceFocus}
                  />
                )}
              </div>
              <section
                className="inline-review-facts"
                aria-label="同屏凭证核对"
                ref={facts}
                tabIndex={-1}
              >
                <WorkflowGuide
                  label="会计复核引导"
                  guide={reviewGuidance({
                    status: draft.status,
                    sourceInactive: !!draft.source_inactive,
                    sourceAvailable: !!source,
                    hasEvidenceReference: !!source?.evidence_ref,
                    editable: !readonly,
                    canReview: !readonly && detail.book.permissions.review,
                    canOpenCase: false,
                    canExport: detail.book.permissions.export,
                    periodClosed: detail.book.period_status === 'closed',
                    dirty,
                    balanced,
                    issueCount: draft.issues.length,
                    busy: !!busy,
                    error,
                    evidenceError,
                    requiresAcknowledgement: true,
                    acknowledged,
                  })}
                />
                <div className="inline-review-state">
                  <span className={`badge ${draft.status}`}>
                    {draft.status === 'draft'
                      ? '待会计审核'
                      : draft.status === 'approved'
                        ? '已审核，待导出'
                        : '已导出'}
                  </span>
                  <span>草稿版本 {draft.version}</span>
                </div>
                <dl className="inline-review-source">
                  <div>
                    <dt>来源金额</dt>
                    <dd>¥{formatMoney(source?.amount ?? '0.00')}</dd>
                  </div>
                  <div>
                    <dt>发生日期</dt>
                    <dd>{source?.date}</dd>
                  </div>
                  <div>
                    <dt>交易对方</dt>
                    <dd>{source?.counterparty || '未填写'}</dd>
                  </div>
                  <div>
                    <dt>来源摘要</dt>
                    <dd>{source?.description}</dd>
                  </div>
                </dl>
                <div className="inline-review-rationale">
                  <h3>建议依据</h3>
                  <p>{draft.explanation || '请根据原始资料核对会计处理。'}</p>
                </div>
                {draft.issues.length > 0 && (
                  <section className="issues-box" aria-label="同屏待处理问题">
                    <h3>
                      <TriangleAlert size={15} />
                      先处理 {draft.issues.length} 项问题
                    </h3>
                    <ul>
                      {draft.issues.map((issue, i) => (
                        <li key={i}>{issue}</li>
                      ))}
                    </ul>
                  </section>
                )}
                <label className="inline-review-label">
                  <span>凭证摘要</span>
                  <textarea
                    aria-label="同屏凭证摘要"
                    value={description}
                    maxLength={500}
                    rows={2}
                    disabled={readonly || !!busy}
                    onChange={(event) => {
                      setDescription(event.target.value);
                      setAcknowledged(false);
                    }}
                  />
                </label>
                <JournalEntryGrid
                  mode="inline"
                  lines={lines}
                  accounts={detail.accounts}
                  readonly={readonly}
                  busy={!!busy}
                  onChange={(value) => {
                    setLines(value);
                    setAcknowledged(false);
                  }}
                />
                {!readonly && (
                  <button
                    className="text-button"
                    disabled={!!busy || lines.length >= 20}
                    onClick={() =>
                      setLines((old) => [
                        ...old,
                        { account_code: '', debit: '0.00', credit: '0.00' },
                      ])
                    }
                  >
                    <Plus size={14} />
                    添加分录
                  </button>
                )}
                <div className={`inline-review-totals ${balanced ? '' : 'unbalanced'}`}>
                  <span>
                    借方 <b>{amountError ? '—' : formatCents(debit)}</b>
                  </span>
                  <span>
                    贷方 <b>{amountError ? '—' : formatCents(credit)}</b>
                  </span>
                  <p>
                    {balanced ? (
                      <>
                        <CheckCircle2 size={14} />
                        借贷平衡
                      </>
                    ) : (
                      amountError ||
                      (debit === 0n && credit === 0n
                        ? '借贷金额须大于零，请补充分录金额'
                        : `借贷差额 ¥${formatCents(debit > credit ? debit - credit : credit - debit)}，请核对金额`)
                    )}
                  </p>
                </div>
                {dirty && (
                  <label className="inline-review-label">
                    <span>会计修正依据（可选）</span>
                    <textarea
                      aria-label="同屏会计修正依据"
                      rows={2}
                      maxLength={1000}
                      value={reason}
                      disabled={!!busy}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                )}
                {readonly ? (
                  <p className="permission-note">
                    <ShieldCheck size={16} />
                    {draft.source_inactive
                      ? '此来源已替代或隔离，保留历史凭据供追溯。'
                      : draft.status === 'draft'
                        ? '当前授权或期间只允许查看。'
                        : '凭证已冻结，修改需使用后续调整流程。'}
                  </p>
                ) : (
                  <label className="inline-review-ack">
                    <input
                      type="checkbox"
                      aria-label="我已核对同屏原件与分录"
                      checked={acknowledged}
                      disabled={!!busy || dirty || !!evidenceError || !!error}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    我已核对原件、金额和会计处理
                  </label>
                )}
              </section>
            </div>
            {assistantOpen && (
              <div className="inline-assistant-pane">
                <AccountingContextAssistant
                  session={session}
                  bookId={bookId}
                  draftId={draft.id}
                  draftVersion={draft.version}
                  hasUnsavedChanges={dirty}
                  actionTypes={['edit', 'review']}
                  onClose={closeAssistant}
                  onOpenEvidence={openEvidence}
                  onAction={assistantAction}
                />
              </div>
            )}
          </div>
        ) : null}
        <footer className="inline-review-footer">
          <div className="inline-review-pagination">
            <button
              className="button secondary small"
              disabled={!!busy || loading || position <= 0}
              onClick={() => navigate(queue[position - 1])}
            >
              <ArrowLeft size={15} />
              上一笔
            </button>
            <button
              className="button secondary small"
              disabled={!!busy || loading || position < 0 || position >= queue.length - 1}
              onClick={() => navigate(queue[position + 1])}
            >
              下一笔
              <ArrowRight size={15} />
            </button>
            <button
              type="button"
              className="text-button"
              aria-expanded={keyboardHelp}
              onClick={() => setKeyboardHelp((value) => !value)}
            >
              快捷键 ?
            </button>
          </div>
          <div className="inline-review-actions">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => navigate('close')}
            >
              返回列表
            </button>
            <button
              className="button secondary"
              disabled={readonly || !!busy || !dirty || !description.trim() || !!amountError}
              onClick={() => void mutate('save')}
            >
              {busy === 'save' ? (
                <LoaderCircle size={15} className="spinner" />
              ) : (
                <FileText size={15} />
              )}
              保存草稿
            </button>
            <button
              className={
                position >= 0 && position < queue.length - 1 ? 'button secondary' : 'button primary'
              }
              disabled={!canApprove}
              onClick={() => void mutate('approve')}
            >
              {busy === 'approve' ? (
                <LoaderCircle size={15} className="spinner" />
              ) : (
                <Check size={15} />
              )}
              确认审核
            </button>
            {position >= 0 && position < queue.length - 1 && (
              <button
                className="button primary"
                disabled={!canApprove}
                onClick={() => void mutate('approve', queue[position + 1])}
              >
                审核并下一笔
                <ArrowRight size={15} />
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
