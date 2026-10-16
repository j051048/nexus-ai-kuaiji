import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileCheck2, Paperclip, RefreshCw, Upload, X } from 'lucide-react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import { prepareDirectUpload } from './upload-client';
import { PageControls, Spinner } from './AppPrimitives';
import { IntakeWizard } from './IntakeWizard';
import type { BusinessContext, BusinessEvent } from './business-types';
import type { Session } from './types';
import { useServicePlanData } from './service-plan-data';
import {
  eligibleMaterialEvent,
  materialModeLabels,
  materialStateLabels,
  materialSubmissionIssue,
  materialAttachmentIssue,
} from './service-plan-types';
import type {
  MaterialAttachment,
  MaterialMode,
  MaterialRequest,
  MaterialRequests,
} from './service-plan-types';
import { uploadLimitLabel } from './transport-limits';
import './service-plan.css';

export interface ServicePlanMaterialsProps {
  session: Session;
  companyId?: string;
  period?: string;
  bookId?: string;
  initialRequestId?: string;
  revision?: number;
  onChanged?: () => void;
  onBusyChange?: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

/** Employee entry reads assigned requests only. Book entry is separately authorized server-side. */
export function ServicePlanMaterials({
  session,
  companyId = '',
  period = '',
  bookId = '',
  initialRequestId = '',
  revision = 0,
  onChanged,
  onBusyChange,
  onDirtyChange,
}: ServicePlanMaterialsProps) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState('');
  const [dirty, setDirty] = useState(false);
  const [editingBusy, setEditingBusy] = useState(false);
  const filters = new URLSearchParams({ limit: '20', offset: String(offset) });
  if (!bookId && companyId) filters.set('company_id', companyId);
  if (!bookId && period) filters.set('period', period);
  if (initialRequestId) filters.set('request_id', initialRequestId);
  const path = bookId
    ? `/books/${encodeURIComponent(bookId)}/material-requests?${filters}`
    : `/material-requests?${filters}`;
  const resource = useServicePlanData<MaterialRequests>(session, path, revision);
  const scope = `${session.scope_key}:${bookId}:${companyId}:${period}:${initialRequestId}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const [selectionScope, setSelectionScope] = useState(scope);
  const current =
    selectionScope === scope ? resource.data?.rows.find((item) => item.id === selected) : undefined;
  useEffect(() => {
    setOffset(0);
    setSelected(initialRequestId);
    setSelectionScope(scope);
    setDirty(false);
  }, [scope, initialRequestId]);
  useEffect(() => {
    if (!resource.data) {
      setSelected('');
      setDirty(false);
      setEditingBusy(false);
    } else if (initialRequestId) {
      setSelected(initialRequestId);
    }
  }, [resource.data, initialRequestId]);
  useEffect(() => {
    onBusyChange?.(resource.busy || editingBusy);
    return () => onBusyChange?.(false);
  }, [resource.busy, editingBusy, onBusyChange]);
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  const canLeave = () =>
    !dirty || window.confirm('资料说明尚未提交，离开会放弃本次编辑。是否继续？');
  function choose(id: string) {
    if (resource.busy || editingBusy || !canLeave()) return;
    setDirty(false);
    setSelected(id);
    setSelectionScope(scope);
  }
  return (
    <section
      className="service-materials"
      aria-label={bookId ? '本月资料清单' : '指派给我的资料清单'}
    >
      <header className="service-section-heading">
        <div>
          <p className="eyebrow">MONTHLY MATERIALS</p>
          <h2>{bookId ? '本月资料清单' : '需要我提供的资料'}</h2>
          <p>上传、无发生及不适用声明均交由独立人员验收。</p>
        </div>
        <button
          type="button"
          className="button secondary small"
          disabled={resource.busy || editingBusy || resource.loading}
          onClick={() => {
            if (canLeave()) void resource.load();
          }}
        >
          <RefreshCw size={14} />
          刷新资料清单
        </button>
      </header>
      {resource.error && (
        <p className="inline-error" role="alert">
          {resource.error}
        </p>
      )}
      {resource.notice && (
        <p className="service-notice" role="status">
          {resource.notice}
        </p>
      )}
      {resource.loading && (
        <p className="loading-block">
          <Spinner />
          正在读取当前授权的资料…
        </p>
      )}
      {resource.data && (
        <>
          <div className="service-material-list">
            {resource.data.rows.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`service-material-row ${current?.id === item.id ? 'selected' : ''}`}
                disabled={resource.busy || editingBusy}
                onClick={() => choose(item.id)}
                aria-expanded={current?.id === item.id}
              >
                <FileCheck2 size={19} aria-hidden="true" />
                <span className="service-material-title">
                  <strong>{item.title}</strong>
                  <small>
                    {item.company_name} · {item.period} ·{' '}
                    {item.assignee_name ??
                      (item.assignee_user_id === session.user_id
                        ? session.user_name
                        : item.assignee_user_id
                          ? '已指派，姓名待核对'
                          : '尚未指派')}
                  </small>
                </span>
                <span className="service-material-date">
                  {item.due_on}
                  <small>截止日期</small>
                </span>
                <span className={`service-state ${item.state}`}>
                  {materialStateLabels[item.state]}
                </span>
              </button>
            ))}
            {!resource.data.rows.length && (
              <div className="service-empty">
                <CheckCircle2 size={23} />
                <strong>
                  {initialRequestId
                    ? '该资料请求已不可访问或当前指派已变更'
                    : bookId
                      ? '本月尚未生成资料清单'
                      : '当前没有指派给你的资料请求'}
                </strong>
                <p>
                  {bookId
                    ? '请先选择已核对的模板，明确生成本月清单。'
                    : '这里只显示本人当前获授权企业内的指派资料。'}
                </p>
              </div>
            )}
          </div>
          <PageControls
            label="资料清单"
            page={resource.data.pagination}
            busy={resource.loading || resource.busy || editingBusy}
            onPage={(next) => {
              if (canLeave()) {
                setSelected('');
                setDirty(false);
                setOffset(next);
              }
            }}
          />
        </>
      )}
      {current && (
        <MaterialRequestEditor
          key={`${scope}:${current.id}:${current.version}:${resource.reset}`}
          session={session}
          request={current}
          busy={resource.busy}
          onDirtyChange={setDirty}
          onBusyChange={setEditingBusy}
          onAuthorizationLost={resource.invalidate}
          onClose={() => choose('')}
          onSubmit={async (body) => {
            const started = scope;
            const saved = await resource.mutate(
              `/material-requests/${encodeURIComponent(current.id)}/submit`,
              { expected_version: current.version, ...body },
              '资料已提交，等待独立验收；尚未视为资料齐全。',
            );
            if (saved && currentScope.current === started) onChanged?.();
          }}
          onReview={async (action, reason) => {
            const started = scope;
            const saved = await resource.mutate(
              `/material-requests/${encodeURIComponent(current.id)}/review`,
              { expected_version: current.version, action, reason },
              action === 'accept'
                ? '资料验收已记录；凭证仍须经过会计复核后导出。'
                : '资料已退回，等待责任人补充并重新提交。',
            );
            if (saved && currentScope.current === started) onChanged?.();
          }}
        />
      )}
    </section>
  );
}

function MaterialRequestEditor({
  session,
  request,
  busy,
  onSubmit,
  onReview,
  onClose,
  onDirtyChange,
  onBusyChange,
  onAuthorizationLost,
}: {
  session: Session;
  request: MaterialRequest;
  busy: boolean;
  onSubmit: (body: {
    mode: MaterialMode;
    note: string;
    event_ids: string[];
    attachment_ids: string[];
  }) => Promise<void>;
  onReview: (action: 'accept' | 'return', reason: string) => Promise<void>;
  onClose: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onAuthorizationLost: (message: string) => void;
}) {
  const [mode, setMode] = useState<MaterialMode>('provided');
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<MaterialAttachment[]>(request.attachments ?? []);
  const [selectedAttachments, setSelectedAttachments] = useState<string[]>([]);
  const [showEvents, setShowEvents] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const attachmentLock = useRef(false);
  const [events, setEvents] = useState<BusinessEvent[]>([]);
  const [eventOffset, setEventOffset] = useState(0);
  const [eventTotal, setEventTotal] = useState(0);
  const [context, setContext] = useState<BusinessContext | null>(null);
  const [uploading, setUploading] = useState(false);
  const [intakeBusy, setIntakeBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const target = `${session.scope_key}:${request.id}:${request.version}`;
  const current = useRef(target);
  current.current = target;
  const submission = request.latest_submission;
  useEffect(() => {
    onBusyChange(uploading || loading || intakeBusy);
    return () => onBusyChange(false);
  }, [uploading, loading, intakeBusy, onBusyChange]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    onDirtyChange(!!note || !!reason || !!selected.length || !!selectedAttachments.length);
  }, [note, reason, selected, selectedAttachments, onDirtyChange]);
  useEffect(() => {
    if (!request.permissions.submit || !showEvents) return;
    const controller = new AbortController();
    const started = target;
    setLoading(true);
    const query = new URLSearchParams({
      scope: 'own',
      company_id: request.company_id,
      period: request.period,
      limit: '20',
      offset: String(eventOffset),
    });
    void api<{ events: BusinessEvent[]; total: number }>(`/business/events?${query}`, session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted && active.current && current.current === started) {
          setEvents(
            value.events.filter((event) => eligibleMaterialEvent(event, request, session.user_id)),
          );
          setEventTotal(value.total);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted && active.current && current.current === started) {
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
            onAuthorizationLost(errorMessage(failure));
          else setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && active.current && current.current === started)
          setLoading(false);
      });
    return () => controller.abort();
  }, [eventOffset, request, session, target, showEvents]);
  async function uploadFiles(files: FileList | null) {
    if (!files?.length || attachmentLock.current || busy || loading) return;
    const chosen = Array.from(files);
    const started = target;
    attachmentLock.current = true;
    setLoading(true);
    setError('');
    let currentFiles = attachments;
    try {
      for (const file of chosen) {
        const issue = materialAttachmentIssue(file, currentFiles);
        if (issue) throw new Error(`${file.name}：${issue}`);
        const direct = await prepareDirectUpload(session, file, 'material_attachment');
        const form = new FormData();
        if (direct) form.append('upload_id', direct);
        else form.append('file', file);
        const result = await api<{ attachment: MaterialAttachment; version: number }>(
          `/material-requests/${encodeURIComponent(request.id)}/attachments?expected_version=${request.version}`,
          session,
          { method: 'POST', body: form },
        );
        if (!active.current || current.current !== started) return;
        if (result.version !== request.version)
          throw new Error('资料版本已变更，请刷新后重新核对原件。');
        currentFiles = [
          ...currentFiles.filter((item) => item.id !== result.attachment.id),
          result.attachment,
        ];
        setAttachments(currentFiles);
        setSelectedAttachments((ids) =>
          ids.includes(result.attachment.id) ? ids : [...ids, result.attachment.id],
        );
      }
    } catch (failure) {
      if (!active.current || current.current !== started) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
        onAuthorizationLost(errorMessage(failure));
      else
        setError(
          errorMessage(failure) +
            (failure instanceof TypeError || (failure instanceof ApiError && failure.status === 409)
              ? ' 请刷新资料清单核对已保存原件和当前状态，未自动重传。'
              : ''),
        );
    } finally {
      attachmentLock.current = false;
      if (active.current && current.current === started) setLoading(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }
  async function upload() {
    const started = target;
    setError('');
    setLoading(true);
    try {
      const result = await api<BusinessContext>('/business/context', session);
      if (!active.current || current.current !== started) return;
      const companies = result.companies.filter(
        (item) => item.id === request.company_id && item.can_submit,
      );
      if (!companies.length) throw new Error('当前企业提交权限已变更，请重新读取资料清单。');
      setContext({ ...result, companies });
      setUploading(true);
    } catch (failure) {
      if (active.current && current.current === started) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          onAuthorizationLost(errorMessage(failure));
        else setError(errorMessage(failure));
      }
    } finally {
      if (active.current && current.current === started) setLoading(false);
    }
  }
  async function download(
    file: { id: string; filename: string; sha256: string },
    standalone = false,
  ) {
    const started = target;
    setLoading(true);
    setError('');
    try {
      const blob = await apiBlob(
        standalone
          ? `/material-requests/${encodeURIComponent(request.id)}/attachments/${encodeURIComponent(file.id)}/content`
          : `/business/attachments/${encodeURIComponent(file.id)}/content`,
        session,
      );
      const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
      const sha256 = Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, '0'))
        .join('');
      if (!active.current || current.current !== started) return;
      if (sha256 !== file.sha256)
        throw new Error('原件摘要与提交快照不一致，请退回后重新核对资料。');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = file.filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      if (active.current && current.current === started) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          onAuthorizationLost(errorMessage(failure));
        else setError(errorMessage(failure));
      }
    } finally {
      if (active.current && current.current === started) setLoading(false);
    }
  }
  return (
    <article className="service-material-editor" aria-label={`资料详情：${request.title}`}>
      <header className="service-section-heading">
        <div>
          <h3>{request.title}</h3>
          <p>{request.description || '请提供本月真实资料，供独立验收人核对。'}</p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="关闭资料详情"
          onClick={onClose}
          disabled={busy || uploading}
        >
          <X size={18} />
        </button>
      </header>
      <p className="service-context">
        {request.company_name} / {request.period} ·{' '}
        {request.required ? '必需资料' : '按业务情况提供'} · 截止 {request.due_on}
      </p>
      {request.last_review && (
        <p className="service-notice">
          {request.last_review.action === 'return' ? '退回原因：' : '验收说明：'}
          {request.last_review.reason}
        </p>
      )}
      {submission && (
        <div className="service-submission">
          <strong>最近提交 · {materialModeLabels[submission.mode]}</strong>
          <p>{submission.note}</p>
          {submission.attachments?.map((file) => (
            <div key={file.id} className="service-original">
              <Paperclip size={14} />
              <span>
                {file.filename}
                <small>
                  原件摘要 {file.sha256.slice(0, 12)} · {(file.size_bytes / 1024).toFixed(0)} KB
                </small>
              </span>
              <button
                type="button"
                className="text-button"
                disabled={busy || loading}
                onClick={() => void download(file, true)}
              >
                下载原件
              </button>
            </div>
          ))}
          {submission.evidence.map((item) => (
            <div key={item.event_id}>
              <span>
                {item.title} · 提交事实版本 {item.event_version}
              </span>
              {item.attachments.map((file) => (
                <small key={file.id}>
                  <Paperclip size={12} />
                  {file.filename} · 原件摘要 {file.sha256.slice(0, 12)}{' '}
                  <button
                    className="text-button"
                    type="button"
                    disabled={busy || loading}
                    onClick={() => void download(file)}
                  >
                    下载原件
                  </button>
                </small>
              ))}
            </div>
          ))}
        </div>
      )}
      {request.evidence_current === false && (
        <p role="alert" className="inline-error">
          引用资料已发生变化，请退回后重新提交当前版本，不能直接验收。
        </p>
      )}
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {request.permissions.submit && (
        <form
          className="service-form"
          onSubmit={(event) => {
            event.preventDefault();
            const issue = materialSubmissionIssue(mode, note, selected, selectedAttachments);
            if (issue) setError(issue);
            else
              void onSubmit({
                mode,
                note: note.trim(),
                event_ids: mode === 'provided' ? selected : [],
                attachment_ids: mode === 'provided' ? selectedAttachments : [],
              });
          }}
        >
          <label>
            本次提供方式
            <select
              aria-label="资料提供方式"
              value={mode}
              disabled={busy || uploading}
              onChange={(event) => setMode(event.target.value as MaterialMode)}
            >
              {Object.entries(materialModeLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {mode === 'provided' && (
            <section className="service-original-upload" aria-label="直接上传资料原件">
              <h4>
                <Paperclip size={15} />
                资料原件
              </h4>
              <p>上传银行对账单、工资表及其他月度资料。原件直接交验收人核对。</p>
              <p>PDF、图片、XML、CSV、XLSX · 单个 {uploadLimitLabel()}，最多 10 份，合计 50 MB。</p>
              <button
                type="button"
                className="button secondary small"
                disabled={busy || uploading || loading}
                onClick={() => fileInput.current?.click()}
              >
                <Upload size={14} />
                {loading ? '正在保存原件…' : '上传资料原件'}
              </button>
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                aria-label="上传月度资料原件"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.xml,.csv,.xlsx"
                onChange={(event) => void uploadFiles(event.target.files)}
              />
              {attachments.map((file) => (
                <label className="service-original" key={file.id}>
                  <input
                    type="checkbox"
                    aria-label={`随本次提交：${file.filename}`}
                    disabled={busy || loading}
                    checked={selectedAttachments.includes(file.id)}
                    onChange={(event) =>
                      setSelectedAttachments((ids) =>
                        event.target.checked
                          ? [...ids, file.id]
                          : ids.filter((id) => id !== file.id),
                      )
                    }
                  />
                  <span>
                    {file.filename}
                    <small>原件已保存 · {(file.size_bytes / 1024).toFixed(0)} KB</small>
                  </span>
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy || loading}
                    onClick={() => void download(file, true)}
                  >
                    下载核对
                  </button>
                </label>
              ))}
              {!!attachments.length && (
                <small>勾选随本次提交的原件；保存原件后仍需点击下方“提交给独立验收人”。</small>
              )}
            </section>
          )}
          {mode === 'provided' && (
            <details
              className="service-business-reference"
              onToggle={(event) => {
                if (event.currentTarget.open) setShowEvents(true);
              }}
            >
              <summary>关联我已提交的业务事项（可选）</summary>
              <fieldset className="service-event-picker" disabled={busy || uploading}>
                <legend>选择本企业、本月带附件的业务资料</legend>
                {loading ? (
                  <p>
                    <Spinner />
                    读取我的资料…
                  </p>
                ) : (
                  events.map((item) => (
                    <label key={item.id}>
                      <input
                        type="checkbox"
                        checked={selected.includes(item.id)}
                        onChange={(event) =>
                          setSelected((ids) =>
                            event.target.checked
                              ? [...ids, item.id]
                              : ids.filter((id) => id !== item.id),
                          )
                        }
                      />
                      <span>
                        {item.payload.description}
                        <small>
                          {item.payload.occurred_on} · {item.attachments.length} 个附件
                        </small>
                      </span>
                    </label>
                  ))
                )}
                {!loading && !events.length && (
                  <p>这一页暂无可关联资料。可以上传附件，或翻页查找已提交事项。</p>
                )}
                <div className="service-actions">
                  <button
                    type="button"
                    className="button secondary small"
                    disabled={loading}
                    onClick={() => void upload()}
                  >
                    <Upload size={14} />
                    上传附件并录入事项
                  </button>
                  {eventTotal > 20 && (
                    <>
                      <button
                        type="button"
                        className="text-button"
                        disabled={loading || eventOffset === 0}
                        onClick={() => setEventOffset(Math.max(0, eventOffset - 20))}
                      >
                        上一页资料
                      </button>
                      <button
                        type="button"
                        className="text-button"
                        disabled={loading || eventOffset + 20 >= eventTotal}
                        onClick={() => setEventOffset(eventOffset + 20)}
                      >
                        下一页资料
                      </button>
                    </>
                  )}
                </div>
              </fieldset>
            </details>
          )}
          <label>
            {mode === 'provided' ? '资料说明' : '无发生或不适用的事实依据'}
            <textarea
              aria-label="资料提交说明"
              maxLength={1000}
              minLength={3}
              required
              value={note}
              disabled={busy || uploading}
              onChange={(event) => setNote(event.target.value)}
              placeholder={
                mode === 'provided'
                  ? '说明资料覆盖的范围，以及需要会计注意的事项'
                  : '说明核对过的业务范围及确认依据，提交后仍需独立验收'
              }
            />
          </label>
          <button
            type="submit"
            className="button primary"
            disabled={
              busy ||
              uploading ||
              loading ||
              !!materialSubmissionIssue(mode, note, selected, selectedAttachments)
            }
          >
            {busy ? <Spinner /> : <FileCheck2 size={15} />}提交给独立验收人
          </button>
        </form>
      )}
      {request.permissions.review && (
        <form
          className="service-form service-review"
          onSubmit={(event) => {
            event.preventDefault();
            if (
              request.state === 'submitted' &&
              request.evidence_current !== false &&
              reason.trim().length >= 3
            )
              void onReview('accept', reason.trim());
          }}
        >
          <label>
            验收或退回说明
            <textarea
              aria-label="资料验收说明"
              minLength={3}
              maxLength={1000}
              required
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
              placeholder="记录核对范围、验收依据，或明确需要补充的内容"
            />
          </label>
          <div className="service-actions">
            <button
              type="submit"
              className="button primary"
              disabled={
                busy ||
                reason.trim().length < 3 ||
                request.evidence_current === false ||
                request.state !== 'submitted'
              }
            >
              确认资料验收
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={busy || reason.trim().length < 3}
              onClick={() => void onReview('return', reason.trim())}
            >
              {request.state === 'accepted' ? '退回补正' : '退回补充'}
            </button>
          </div>
          <small>资料验收与会计凭证复核分别记录；申请人不能验收自己的提交。</small>
        </form>
      )}
      {uploading && context && (
        <IntakeWizard
          session={session}
          context={context}
          lockedCompanyId={request.company_id}
          onBusyChange={setIntakeBusy}
          onClose={() => {
            setUploading(false);
            setContext(null);
          }}
          onCompleted={(event) => {
            setUploading(false);
            setContext(null);
            if (!active.current || !eligibleMaterialEvent(event, request, session.user_id)) {
              setError('新事项未满足本企业、本月及附件条件，请核对日期和资料。');
              return;
            }
            setEvents((items) => [event, ...items.filter((item) => item.id !== event.id)]);
            setSelected((ids) => (ids.includes(event.id) ? ids : [...ids, event.id]));
            setError('');
          }}
        />
      )}
    </article>
  );
}
