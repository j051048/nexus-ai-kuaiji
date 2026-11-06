import { useEffect, useId, useRef, useState } from 'react';
import {
  Camera,
  CheckCircle2,
  ChevronDown,
  FileText,
  LoaderCircle,
  Paperclip,
  Receipt,
  Save,
  Send,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import { presentApiError, type ErrorPresentation } from './api-error-presentation';
import { attachmentForm, prepareDirectUpload } from './upload-client';
import {
  BUSINESS_CATEGORIES,
  BUSINESS_TEMPLATES,
  initialBusinessPayload,
  normalizeBusinessPayload,
  validateAttachment,
  validateBusinessPayload,
} from './business-forms';
import type {
  BusinessContext,
  BusinessEvent,
  BusinessPayload,
  BusinessTemplate,
} from './business-types';
import type { Session } from './types';
import type { IntakeDraft, IntakeRun } from './intake-types';
import { changeIntakeTemplate, intakeCandidates, prefillIntake } from './intake-helpers';
import { directUploadLimitLabel } from './transport-limits';
import { WorkflowGuide } from './WorkflowGuide';
import { intakeGuidance } from './intake-guidance';
import { TermHelp } from './TermHelp';
import { BusinessPreparation } from './BusinessPreparation';
import { ReturnedBusinessNotice } from './ReturnedBusinessNotice';
import './intake.css';

export function IntakeWizard({
  session,
  context,
  onClose,
  onCompleted,
  onBusyChange,
  onAccessLost,
  initialDraftId = '',
  initialTemplate = 'reimbursement',
  initialEvent,
  lockedCompanyId = '',
}: {
  session: Session;
  context: BusinessContext;
  onClose: () => void;
  onCompleted: (event: BusinessEvent) => void;
  onBusyChange: (value: boolean) => void;
  onAccessLost?: () => void;
  initialDraftId?: string;
  initialTemplate?: BusinessTemplate;
  initialEvent?: BusinessEvent;
  /** A material request may only resume or submit drafts of its own company. */
  lockedCompanyId?: string;
}) {
  const fieldHelpId = useId();
  const companies = context.companies.filter(
    (company) => company.can_submit && (!lockedCompanyId || company.id === lockedCompanyId),
  );
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const amountInput = useRef<HTMLInputElement>(null);
  const descriptionInput = useRef<HTMLInputElement>(null);
  const uploadButton = useRef<HTMLButtonElement>(null);
  const extraFields = useRef<HTMLDetailsElement>(null);
  const [editing, setEditing] = useState<BusinessEvent | null>(initialEvent ?? null);
  const initialCompanyId =
    initialEvent?.company_id ?? (companies.length === 1 ? companies[0].id : '');
  const [companyId, setCompany] = useState(initialCompanyId);
  const company = companies.find((item) => item.id === companyId);
  const direct =
    initialEvent?.submission_mode === 'direct' || company?.submission_policy?.mode === 'direct';
  const [departmentId, setDepartment] = useState(
    initialEvent?.department_id ?? company?.department_id ?? '',
  );
  const [template, setTemplate] = useState<BusinessTemplate>(
    initialEvent?.template_id ?? initialTemplate,
  );
  const [fields, setFields] = useState(
    initialEvent?.payload ?? initialBusinessPayload(initialTemplate),
  );
  const [draft, setDraft] = useState<IntakeDraft | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<IntakeDraft[]>([]);
  const [busy, setBusy] = useState('');
  const [saveState, setSaveState] = useState('内容与原件仅保存在当前账号获授权的企业');
  const [error, setError] = useState('');
  const failureBox = useRef<HTMLDivElement>(null);
  const [failureFocus, setFailureFocus] = useState(0);
  useEffect(() => {
    if (!failureFocus) return;
    const frame = requestAnimationFrame(() => {
      failureBox.current?.focus({ preventScroll: true });
      failureBox.current?.scrollIntoView({ block: 'center' });
    });
    return () => cancelAnimationFrame(frame);
  }, [failureFocus]);
  const [accessLost, setAccessLost] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [failureInfo, setFailureInfo] = useState<ErrorPresentation | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [touched, setTouched] = useState(false);
  const active = useRef(true);
  const lock = useRef(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const fieldRef = useRef(fields);
  fieldRef.current = fields;
  const templateRef = useRef(template);
  templateRef.current = template;
  const savedSignature = useRef(
    initialEvent
      ? JSON.stringify({ template_id: initialEvent.template_id, fields: initialEvent.payload })
      : '',
  );
  const pendingCreate = useRef<{ key: string; body: object; signature: string } | null>(null);
  const completeKey = useRef<{ signature: string; key: string } | null>(null);
  const recognizeKeys = useRef(new Map<string, string>());
  const editedFields = useRef(
    new Set<string>(initialEvent ? Object.keys(initialEvent.payload) : []),
  );
  const pendingPrefill = useRef<{ draft: IntakeDraft; runId: string } | null>(null);
  const [prefillNotice, setPrefillNotice] = useState('');
  const completionRef = useRef(false);
  useEffect(() => {
    dialog.current?.showModal();
    active.current = true;
    const controller = new AbortController();
    if (initialEvent)
      return () => {
        active.current = false;
        controller.abort();
      };
    void api<{ drafts: IntakeDraft[] }>('/intake/drafts', session, { signal: controller.signal })
      .then((value) => {
        if (active.current)
          setSavedDrafts(
            value.drafts.filter((item) => !lockedCompanyId || item.company_id === lockedCompanyId),
          );
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [session, lockedCompanyId, initialEvent]);
  useEffect(() => {
    if (initialDraftId) void resume(initialDraftId);
  }, [initialDraftId]);
  const hasRunningRun = !!draft?.runs.some((row) => row.status === 'running');
  useEffect(() => {
    if (!draft?.id || !hasRunningRun) return;
    let disposed = false;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void api<IntakeDraft>(`/intake/drafts/${encodeURIComponent(draft.id)}`, session, {
        signal: controller.signal,
      })
        .then((value) => {
          if (
            !disposed &&
            active.current &&
            draftRef.current?.id === draft.id &&
            value.id === draft.id &&
            value.company_id === draft.company_id
          ) {
            applyPrefill(value);
            adopt(value);
          }
        })
        .catch((failure) => {
          if (!disposed && !controller.signal.aborted) setError(errorMessage(failure));
        });
    }, 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      controller.abort();
    };
  }, [draft?.id, hasRunningRun, session]);
  useEffect(() => {
    onBusyChange(!!busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);

  function applyPrefill(value: IntakeDraft) {
    const pending = pendingPrefill.current;
    if (
      !pending ||
      !value.runs.some((item) => item.id === pending.runId && item.status !== 'running')
    )
      return;
    pendingPrefill.current = null;
    if (
      draftRef.current?.id !== pending.draft.id ||
      templateRef.current !== pending.draft.template_id
    )
      return;
    const before = fieldRef.current;
    const next = prefillIntake(pending.draft, value, pending.runId, before, editedFields.current);
    if (JSON.stringify(before) !== JSON.stringify(next)) {
      fieldRef.current = next;
      setFields(next);
      setTouched(true);
      setConfirmed(false);
      setPrefillNotice('已预填未修改的日期、金额或交易对方，请对照原件确认；你填写的内容已保留。');
    }
  }

  function adopt(value: IntakeDraft, replaceFields = false) {
    if (lockedCompanyId && value.company_id !== lockedCompanyId)
      throw new Error('当前资料请求只接受所属企业的事项，请重新选择草稿。');
    draftRef.current = value;
    setDraft(value);
    setCompany(value.company_id);
    setDepartment(value.department_id ?? '');
    if (replaceFields) {
      pendingPrefill.current = null;
      editedFields.current = new Set(Object.keys(value.fields));
      setPrefillNotice('');
      setCompany(value.company_id);
      setDepartment(value.department_id ?? '');
      setTemplate(value.template_id);
      setFields(value.fields);
      templateRef.current = value.template_id;
      fieldRef.current = value.fields;
      setConfirmed(false);
    }
    savedSignature.current = JSON.stringify({
      template_id: value.template_id,
      fields: value.fields,
    });
  }
  async function persist() {
    if (!companyId || (!direct && !departmentId)) throw new Error('请先选择企业和所属部门。');
    if (lockedCompanyId && companyId !== lockedCompanyId)
      throw new Error('当前资料请求的企业范围已变更，请重新进入。');
    const signature = JSON.stringify({
      template_id: templateRef.current,
      fields: fieldRef.current,
    });
    let current = draftRef.current;
    if (!current) {
      if (!pendingCreate.current)
        pendingCreate.current = {
          key: crypto.randomUUID(),
          signature,
          body: {
            company_id: companyId,
            department_id: departmentId || null,
            template_id: templateRef.current,
            fields: fieldRef.current,
          },
        };
      current = await api<IntakeDraft>('/intake/drafts', session, {
        method: 'POST',
        body: { ...pendingCreate.current.body, idempotency_key: pendingCreate.current.key },
      });
      if (!active.current) return current;
      pendingCreate.current = null;
      adopt(current);
    }
    if (savedSignature.current !== signature) {
      current = await api<IntakeDraft>(
        `/intake/drafts/${encodeURIComponent(current.id)}`,
        session,
        {
          method: 'PATCH',
          body: {
            expected_version: current.version,
            template_id: templateRef.current,
            fields: fieldRef.current,
          },
        },
      );
      if (active.current) {
        adopt(current);
        savedSignature.current = signature;
      }
    }
    if (active.current) setSaveState('草稿已保存 · 关闭或刷新后可继续填写');
    return current;
  }
  async function persistEditing() {
    const current = editing;
    if (!current) throw new Error('当前修改事项已失效，请关闭后重新打开。');
    if (!companyId) throw new Error('请先选择客户企业。');
    const signature = JSON.stringify({
      template_id: current.template_id,
      fields: fieldRef.current,
    });
    if (savedSignature.current === signature) return current;
    const value = await api<BusinessEvent>(
      `/business/events/${encodeURIComponent(current.id)}`,
      session,
      {
        method: 'PATCH',
        body: {
          expected_version: current.version,
          payload: normalizeBusinessPayload(fieldRef.current),
        },
      },
    );
    if (active.current) {
      setEditing(value);
      setFields(value.payload);
      fieldRef.current = value.payload;
      savedSignature.current = JSON.stringify({
        template_id: value.template_id,
        fields: value.payload,
      });
      setSaveState('修改已保存');
    }
    return value;
  }
  async function run(name: string, action: () => Promise<void>) {
    if (lock.current || accessLost) return;
    lock.current = true;
    setBusy(name);
    setError('');
    setFailureInfo(null);
    try {
      await action();
    } catch (failure) {
      if (!active.current) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        setAccessLost(true);
        setEditing(null);
        onAccessLost?.();
        setDraft(null);
        draftRef.current = null;
        pendingCreate.current = null;
        completeKey.current = null;
        savedSignature.current = '';
        setFields(initialBusinessPayload('reimbursement'));
        setSavedDrafts([]);
        setConfirmed(false);
        setTouched(false);
        setPrefillNotice('');
        setSaveState('当前资料已隐藏，请关闭后重新确认权限。');
      }
      const uncertain =
        failure instanceof TypeError ||
        (failure instanceof ApiError && [409, 503].includes(failure.status));
      // Existing intake creation/completion keeps its idempotent retry key.
      // Only edits to an existing business record require a fresh version first.
      if (uncertain && editing) setNeedsRefresh(true);
      setFailureInfo(presentApiError(failure));
      setError(
        errorMessage(failure) +
          (uncertain
            ? editing
              ? ' 请先读取最新事项核对，当前未自动重试。尚未保存的修改将以服务端记录为准。'
              : ' 草稿或提交结果可能已保存，请使用“读取最新草稿”核对；重复提交保持同一保存编号。'
            : ''),
      );
      // A deliberate action needs a visible recovery path. Background autosave
      // must not steal focus while the user is still editing a field.
      if (name !== 'saving') setFailureFocus((value) => value + 1);
    } finally {
      lock.current = false;
      if (active.current) setBusy('');
    }
  }
  useEffect(() => {
    if (
      !touched ||
      busy ||
      error ||
      completionRef.current ||
      !companyId ||
      (!direct && !departmentId)
    )
      return;
    if (
      draftRef.current &&
      savedSignature.current === JSON.stringify({ template_id: template, fields })
    )
      return;
    const timer = window.setTimeout(
      () =>
        void run('saving', async () => {
          if (editing) await persistEditing();
          else await persist();
        }),
      1200,
    );
    return () => window.clearTimeout(timer);
  }, [
    fields,
    template,
    touched,
    busy,
    error,
    companyId,
    departmentId,
    direct,
    editing?.id,
    editing?.version,
  ]);
  function field<K extends keyof BusinessPayload>(key: K, value: BusinessPayload[K]) {
    editedFields.current.add(key);
    fieldRef.current = { ...fieldRef.current, [key]: value };
    setFields((current) => ({ ...current, [key]: value }));
    setConfirmed(false);
    setTouched(true);
    setSaveState('有修改待保存…');
  }
  function chooseTemplate(value: BusinessTemplate) {
    setTemplate(value);
    setFields((current) => changeIntakeTemplate(current, value));
    setConfirmed(false);
    setTouched((current) => current || !!draftRef.current);
    if (draftRef.current) setSaveState('模板已切换，通用信息保留，等待保存…');
  }
  function chooseCompany(value: string) {
    if (lockedCompanyId && value !== lockedCompanyId) return;
    setCompany(value);
    setDepartment(companies.find((item) => item.id === value)?.department_id ?? '');
  }
  async function resume(id: string) {
    await run('resume', async () => {
      const value = await api<IntakeDraft>(`/intake/drafts/${encodeURIComponent(id)}`, session);
      if (!active.current) return;
      setNeedsRefresh(false);
      if (lockedCompanyId && value.company_id !== lockedCompanyId)
        throw new Error('该草稿不属于当前资料请求的企业。');
      if (value.status === 'completed' && value.event_id) {
        const event = await api<BusinessEvent>(
          `/business/events/${encodeURIComponent(value.event_id)}`,
          session,
        );
        if (lockedCompanyId && event.company_id !== lockedCompanyId)
          throw new Error('该事项不属于当前资料请求的企业。');
        if (active.current) {
          completionRef.current = true;
          onCompleted(event);
        }
        return;
      }
      pendingCreate.current = null;
      adopt(value, true);
      setTouched(true);
      setSaveState('已恢复服务端草稿，请核对后继续填写');
    });
  }
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    const selected = Array.from(files);
    await run('upload', async () => {
      let current = editing ? (touched ? await persistEditing() : editing) : await persist();
      for (const file of selected) {
        const validation = validateAttachment(
          file,
          current.attachments.length,
          current.attachments.reduce((sum, item) => sum + item.size_bytes, 0),
        );
        if (validation) throw new Error(`${file.name}：${validation}`);
        const direct = await prepareDirectUpload(
          session,
          file,
          editing ? 'event_attachment' : 'intake_attachment',
        );
        const data = attachmentForm(file, direct);
        if (editing) {
          current = await api<BusinessEvent>(
            `/business/events/${encodeURIComponent(current.id)}/attachments?expected_version=${current.version}`,
            session,
            { method: 'POST', body: data },
          );
          if (active.current) setEditing(current);
        } else {
          current = await api<IntakeDraft>(
            `/intake/drafts/${encodeURIComponent(current.id)}/attachments?expected_version=${current.version}`,
            session,
            { method: 'POST', body: data },
          );
          if (active.current) adopt(current);
        }
      }
      if (active.current) {
        setTouched(true);
        setConfirmed(false);
        setSaveState(`${current.attachments.length} 个原件已保存，继续核对事项信息`);
      }
    });
    if (fileInput.current) fileInput.current.value = '';
    if (photoInput.current) photoInput.current.value = '';
  }
  async function recognize(id: string) {
    await run('recognize-' + id, async () => {
      const current = await persist();
      if (!recognizeKeys.current.has(id)) recognizeKeys.current.set(id, crypto.randomUUID());
      const result = await api<IntakeRun>(
        `/intake/drafts/${encodeURIComponent(current.id)}/attachments/${encodeURIComponent(id)}/recognize`,
        session,
        {
          method: 'POST',
          body: {
            expected_version: current.version,
            idempotency_key: recognizeKeys.current.get(id),
          },
        },
      );
      if (active.current) {
        const value = await api<IntakeDraft>(
          `/intake/drafts/${encodeURIComponent(current.id)}`,
          session,
        );
        if (
          value.id !== current.id ||
          value.company_id !== current.company_id ||
          draftRef.current?.id !== current.id
        )
          throw new Error('识别返回范围已变更，请重新读取草稿。');
        pendingPrefill.current = { draft: current, runId: result.id };
        applyPrefill(value);
        adopt(value);
        setConfirmed(false);
        if (result.status === 'failed') {
          recognizeKeys.current.delete(id);
          setError(result.error_detail);
        }
      }
    });
  }
  async function finish(submit: boolean) {
    if (accessLost || needsRefresh) return;
    const validation = validateBusinessPayload(template, fields);
    if (validation) {
      setError(validation);
      return;
    }
    if (!confirmed) {
      setError('请核对原件并勾选字段确认。');
      return;
    }
    if (editing) {
      await run(submit ? 'submit' : 'complete', async () => {
        let current = touched ? await persistEditing() : editing;
        if (submit)
          current = await api<BusinessEvent>(
            `/business/events/${encodeURIComponent(current.id)}/submit`,
            session,
            {
              method: 'POST',
              body: { expected_version: current.version },
            },
          );
        if (active.current) {
          completionRef.current = true;
          onCompleted(current);
        }
      });
      return;
    }
    await run(submit ? 'submit' : 'complete', async () => {
      const current = await persist();
      const payload = normalizeBusinessPayload(fieldRef.current);
      const signature = JSON.stringify({ payload, submit });
      if (completeKey.current?.signature !== signature)
        completeKey.current = { signature, key: crypto.randomUUID() };
      const value = await api<{ event: BusinessEvent }>(
        `/intake/drafts/${encodeURIComponent(current.id)}/complete`,
        session,
        {
          method: 'POST',
          body: {
            expected_version: current.version,
            payload,
            fields_confirmed: true,
            submit,
            idempotency_key: completeKey.current.key,
          },
        },
      );
      if (active.current) {
        completionRef.current = true;
        onCompleted(value.event);
      }
    });
  }
  async function close() {
    if (busy) return;
    if (touched && !error && !completionRef.current)
      await run('saving', async () => {
        if (editing) await persistEditing();
        else await persist();
        onClose();
      });
    else onClose();
  }
  async function download(id: string, filename: string) {
    await run('download', async () => {
      const data = await apiBlob(
        `${editing ? '/business' : '/intake'}/attachments/${encodeURIComponent(id)}/content`,
        session,
      );
      if (!active.current) return;
      const url = URL.createObjectURL(data);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    });
  }
  const disabled = accessLost || (!!busy && busy !== 'saving');
  const fieldsDisabled = accessLost || (disabled && !busy.startsWith('recognize-'));
  const latestRuns = new Map<string, IntakeRun>();
  draft?.runs.forEach((row) => {
    if (!latestRuns.has(row.attachment_id)) latestRuns.set(row.attachment_id, row);
  });
  const attachments = editing?.attachments ?? draft?.attachments ?? [];
  const aiEnabled = !editing && !!draft?.ai_enabled;
  const visionReady = !editing && !!draft?.vision_ready;
  const structuredReady = !editing && !!draft?.structured_ready;
  const guidance = intakeGuidance({
    companyReady: !!company,
    companiesAvailable: companies.length > 0,
    departmentReady: !!departmentId,
    templateLabel: BUSINESS_TEMPLATES.find((item) => item.id === template)?.name ?? '业务事项',
    fieldsValidation: validateBusinessPayload(template, fields),
    attachmentCount: attachments.length,
    confirmed,
    busy,
    error,
    saved:
      (!!draft || !!editing) &&
      savedSignature.current === JSON.stringify({ template_id: template, fields }),
    direct,
    runs: attachments.flatMap((attachment) => {
      const run = latestRuns.get(attachment.id);
      return run ? [run] : [];
    }),
  });
  return (
    <dialog
      ref={dialog}
      className="modal intake-modal"
      aria-labelledby="intake-title"
      onCancel={(event) => {
        event.preventDefault();
        void close();
      }}
    >
      <header className="modal-header">
        <div>
          <p className="eyebrow">把业务说清楚，票据一起交</p>
          <h2 id="intake-title">{initialEvent ? '修改业务事项' : '录入业务事项'}</h2>
          <p>
            {editing?.status === 'returned'
              ? '按退回说明补充 → 核对信息 → 重新提交'
              : `上传票据 → 核对信息 → ${direct ? '提交财务处理' : '提交审批'}`}
          </p>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="关闭业务事项表单"
          disabled={!!busy}
          onClick={() => void close()}
        >
          <X size={20} />
        </button>
      </header>
      <div className="intake-scroll">
        {editing && (
          <ReturnedBusinessNotice event={editing}>
            <button
              className="button secondary small"
              type="button"
              disabled={disabled}
              onClick={() => uploadButton.current?.focus()}
            >
              补充附件
            </button>
            <button
              className="button secondary small"
              type="button"
              disabled={disabled}
              onClick={() => {
                if (extraFields.current) extraFields.current.open = true;
                descriptionInput.current?.focus();
              }}
            >
              修改信息
            </button>
          </ReturnedBusinessNotice>
        )}
        <section className="intake-current-step" aria-label="员工录入引导">
          <p>
            <strong>{guidance.status}</strong>
            <span className="intake-status-next"> · {guidance.next}</span>
          </p>
          <details className="intake-help">
            <summary>查看录入步骤与说明</summary>
            <WorkflowGuide label="详细录入步骤" guide={guidance} />
          </details>
        </section>
        {!!savedDrafts.length && !draft && !editing && (
          <details className="intake-resume">
            <summary>
              <Save size={15} />
              继续未完成的录入（{savedDrafts.length}）<ChevronDown size={15} />
            </summary>
            {savedDrafts.map((row) => (
              <button
                type="button"
                key={row.id}
                disabled={disabled}
                onClick={() => void resume(row.id)}
              >
                <strong>{row.fields.description || '尚未填写摘要'}</strong>
                <span>
                  {row.company_name} · {row.attachments.length} 个附件
                </span>
              </button>
            ))}
          </details>
        )}
        <details className="intake-template-picker">
          <summary>
            事项类型：{BUSINESS_TEMPLATES.find((item) => item.id === template)?.name} ·{' '}
            {editing ? '当前记录' : '更换类型'}
          </summary>
          <div className="business-template-grid" role="group" aria-label="选择业务模板">
            {BUSINESS_TEMPLATES.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-pressed={template === item.id}
                className={template === item.id ? 'selected' : ''}
                disabled={disabled || !!editing}
                onClick={() => chooseTemplate(item.id)}
              >
                <Receipt size={18} />
                <strong>{item.name}</strong>
                <span className="intake-template-description">{item.description}</span>
                {template === item.id && (
                  <span className="intake-template-selected" aria-hidden="true">
                    <CheckCircle2 size={12} />
                    当前模板
                  </span>
                )}
              </button>
            ))}
          </div>
        </details>
        <div className="intake-company-grid">
          <label>
            <span className="field-label">客户企业</span>
            {companies.length === 1 && (lockedCompanyId || company?.role === 'employee') ? (
              <span aria-label="事项客户企业" className="intake-identity-summary">
                {company?.name}
              </span>
            ) : (
              <select
                aria-label="事项客户企业"
                value={companyId}
                disabled={!!draft || !!busy || !!pendingCreate.current || !!editing}
                onChange={(event) => chooseCompany(event.target.value)}
              >
                <option value="">请选择客户企业</option>
                {companies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
          </label>
          <label>
            <span className="field-label">{direct ? '所属部门（可选）' : '业务所属部门'}</span>
            {company?.role === 'employee' && company.department_id ? (
              <span aria-label="事项所属部门" className="intake-identity-summary">
                {company.departments.find((item) => item.id === departmentId)?.name ??
                  '当前所属部门'}
              </span>
            ) : (
              <select
                aria-label="事项所属部门"
                value={departmentId}
                disabled={
                  !!draft ||
                  !!busy ||
                  !!pendingCreate.current ||
                  !!editing ||
                  company?.role === 'employee'
                }
                onChange={(event) => setDepartment(event.target.value)}
              >
                <option value="">选择部门</option>
                {company?.departments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
          </label>
        </div>
        {!accessLost && (
          <BusinessPreparation
            session={session}
            companyId={companyId}
            occurredOn={fields.occurred_on}
          />
        )}
        <div className="intake-two-column">
          <section className="intake-originals" aria-labelledby="intake-originals-title">
            <h3 id="intake-originals-title">
              <span className="intake-step-number" aria-hidden="true">
                2
              </span>
              <Paperclip size={17} aria-hidden="true" />
              票据与原件<span className="intake-section-note">支持多文件</span>
            </h3>
            <div className="intake-upload">
              <Upload size={27} />
              <strong>把发票、收据或业务凭据放在这里</strong>
              <p>PDF、JPG、PNG、WebP · 单个 {directUploadLimitLabel()}，合计 50 MB</p>
              <p>数电票 XML 支持本地解析，单份解析上限 1 MB。</p>
              <div>
                <button
                  ref={uploadButton}
                  type="button"
                  className="button secondary"
                  disabled={accessLost || !!busy || (!direct && !departmentId)}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload size={15} />
                  选择附件
                </button>
                <button
                  type="button"
                  className="button secondary intake-camera"
                  disabled={accessLost || !!busy || (!direct && !departmentId)}
                  onClick={() => photoInput.current?.click()}
                >
                  <Camera size={16} />
                  拍照上传
                </button>
              </div>
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                accept=".xml,application/xml,text/xml,application/pdf,image/png,image/jpeg,image/webp"
                aria-label="一体化录入上传附件"
                onChange={(event) => void upload(event.target.files)}
              />
              <input
                ref={photoInput}
                type="file"
                hidden
                accept="image/jpeg,image/png,image/webp"
                capture="environment"
                aria-label="拍照上传业务附件"
                onChange={(event) => void upload(event.target.files)}
              />
            </div>
            <p className="intake-ai-help">
              <Sparkles size={15} />
              {aiEnabled && (visionReady || structuredReady)
                ? '单张票据识别后预填未修改的信息，请核对后提交；多张票据逐项选择。'
                : '票据识别尚未启用或配置，可以直接手工填写并提交。'}
            </p>
            {attachments.map((attachment) => {
              const run = latestRuns.get(attachment.id);
              return (
                <article className="intake-file" key={attachment.id}>
                  <div className="intake-file-header">
                    <FileText size={17} />
                    <div>
                      <strong>{attachment.filename}</strong>
                      <span>{(attachment.size_bytes / 1024).toFixed(0)} KB · 原件已保存</span>
                    </div>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!!busy}
                      onClick={() => void download(attachment.id, attachment.filename)}
                    >
                      下载
                    </button>
                  </div>
                  {aiEnabled &&
                    (attachment.media_type === 'application/xml'
                      ? structuredReady
                      : visionReady) && (
                      <button
                        className="button secondary small"
                        type="button"
                        disabled={
                          !!busy ||
                          run?.status === 'running' ||
                          run?.status === 'succeeded' ||
                          run?.status === 'stale'
                        }
                        onClick={() => void recognize(attachment.id)}
                      >
                        {busy === 'recognize-' + attachment.id || run?.status === 'running' ? (
                          <LoaderCircle size={14} className="spinner" />
                        ) : (
                          <Sparkles size={14} />
                        )}
                        {run?.status === 'running'
                          ? '识别处理中，读取最新草稿查看'
                          : run?.status === 'succeeded'
                            ? '识别建议已就绪'
                            : attachment.media_type === 'application/xml'
                              ? '解析 XML 并预填'
                              : 'AI 识别并预填'}
                      </button>
                    )}
                  {run && (run.status === 'failed' || run.status === 'stale') && (
                    <p className="form-error" role="alert">
                      {run.status === 'stale'
                        ? '识别已中断，未自动重发；请刷新等待管理员核定费用后再恢复。'
                        : run.error_detail}
                    </p>
                  )}
                  {run && intakeCandidates(run, fields.direction).length > 0 && (
                    <details className="intake-evidence">
                      <summary>
                        查看识别依据与其他候选（{intakeCandidates(run, fields.direction).length}{' '}
                        张单据）
                      </summary>
                      {intakeCandidates(run, fields.direction).map((candidate) => (
                        <div className="intake-candidate" key={candidate.index}>
                          <strong>
                            单据 {candidate.index + 1}
                            {candidate.document.invoice_number
                              ? ` · ${candidate.document.invoice_number}`
                              : ''}
                          </strong>
                          {candidate.document.tax_amount !== null && (
                            <span>票面税额 {candidate.document.tax_amount} 元（仅供核票）</span>
                          )}
                          {candidate.fields.map((item) => (
                            <div className="intake-suggested-field" key={item.target}>
                              <div>
                                <span>{item.label}</span>
                                <strong>{item.value}</strong>
                                {attachment.media_type === 'application/xml' ? (
                                  <details>
                                    <summary>查看 XML 原文字段依据</summary>
                                    <small>{item.quote}</small>
                                  </details>
                                ) : (
                                  <small>
                                    第 {item.page} 页 · {item.quote}
                                  </small>
                                )}
                              </div>
                              <button
                                type="button"
                                className="text-button"
                                disabled={disabled}
                                onClick={() => field(item.target, item.value)}
                              >
                                采用此字段
                              </button>
                            </div>
                          ))}
                        </div>
                      ))}
                    </details>
                  )}
                  {run?.status === 'succeeded' && (
                    <button
                      type="button"
                      className="text-button"
                      disabled={fieldsDisabled}
                      onClick={() => amountInput.current?.focus()}
                    >
                      核对已填写信息
                    </button>
                  )}
                  {!!run?.checks.length && (
                    <div className="intake-checks" role="note" aria-label="需要核对的票据问题">
                      <strong>需要核对的票据问题（{run.checks.length}）</strong>
                      {run.checks.map((item) => (
                        <p key={item.code}>{item.payload.message}</p>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
            {attachments.length > 1 && (
              <p className="field-help">
                多张票据分别展示，不自动合计或猜测交易对方。涉及不同业务时请分别录入。
              </p>
            )}
          </section>
          <section className="intake-fields" aria-labelledby="intake-fields-title">
            <h3 id="intake-fields-title">
              <span className="intake-step-number" aria-hidden="true">
                3
              </span>
              <CheckCircle2 size={17} aria-hidden="true" />
              核对业务信息
            </h3>
            {prefillNotice && (
              <p role="status" className="field-help">
                {prefillNotice}
              </p>
            )}
            <div className="business-form-grid">
              <div className="term-field">
                <label>
                  <span className="field-label">发生日期</span>
                  <input
                    type="date"
                    aria-label="发生日期"
                    aria-describedby={`${fieldHelpId}-occurred-on`}
                    min="2000-01-01"
                    max="2099-12-31"
                    value={fields.occurred_on}
                    disabled={fieldsDisabled}
                    onChange={(event) => field('occurred_on', event.target.value)}
                  />
                </label>
                <TermHelp term="occurred_on" descriptionId={`${fieldHelpId}-occurred-on`} />
              </div>
              <label>
                <span className="field-label">金额（人民币元）</span>
                <input
                  ref={amountInput}
                  inputMode="decimal"
                  className="intake-amount"
                  aria-label="事项金额"
                  value={fields.amount}
                  placeholder="例如 128.50"
                  disabled={fieldsDisabled}
                  onChange={(event) => field('amount', event.target.value)}
                />
              </label>
              <label className="wide-field">
                <span className="field-label">事项摘要</span>
                <input
                  ref={descriptionInput}
                  aria-label="事项摘要"
                  value={fields.description}
                  maxLength={500}
                  placeholder="例如：客户拜访交通费"
                  disabled={fieldsDisabled}
                  onChange={(event) => field('description', event.target.value)}
                />
              </label>
              <div className="wide-field term-field">
                <label>
                  <span className="field-label">交易对方</span>
                  <input
                    aria-label="交易对方"
                    aria-describedby={`${fieldHelpId}-counterparty`}
                    value={fields.counterparty}
                    maxLength={200}
                    placeholder="供应商、客户或实际收款人"
                    disabled={fieldsDisabled}
                    onChange={(event) => field('counterparty', event.target.value)}
                  />
                </label>
                <TermHelp term="counterparty" descriptionId={`${fieldHelpId}-counterparty`} />
              </div>
              {template === 'daily_cashflow' ? (
                <label>
                  <span className="field-label">收支方向</span>
                  <select
                    aria-label="收支方向"
                    value={fields.direction}
                    disabled={fieldsDisabled}
                    onChange={(event) =>
                      field('direction', event.target.value as BusinessPayload['direction'])
                    }
                  >
                    <option value="expense">支出</option>
                    <option value="income">收入</option>
                  </select>
                </label>
              ) : (
                <div className="term-field">
                  <label>
                    <span className="field-label">企业付款状态</span>
                    <select
                      aria-label="企业付款状态"
                      aria-describedby={`${fieldHelpId}-payment-status`}
                      value={fields.payment_status}
                      disabled={fieldsDisabled || template === 'reimbursement'}
                      onChange={(event) =>
                        field(
                          'payment_status',
                          event.target.value as BusinessPayload['payment_status'],
                        )
                      }
                    >
                      <option value="unpaid">待企业付款</option>
                      {template === 'procurement_expense' && <option value="paid">已付款</option>}
                    </select>
                  </label>
                  <TermHelp term="payment_status" descriptionId={`${fieldHelpId}-payment-status`} />
                </div>
              )}
            </div>
            <details className="intake-extra" ref={extraFields}>
              <summary>更多信息（分类、用途、项目）</summary>
              <label>
                <span className="field-label">事项分类</span>
                <select
                  aria-label="事项分类"
                  value={fields.category}
                  disabled={fieldsDisabled}
                  onChange={(event) =>
                    field('category', event.target.value as BusinessPayload['category'])
                  }
                >
                  {Object.entries(BUSINESS_CATEGORIES).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wide-field">
                <span className="field-label">业务用途（可选）</span>
                <textarea
                  aria-label="业务用途"
                  value={fields.purpose}
                  maxLength={1000}
                  rows={2}
                  disabled={fieldsDisabled}
                  onChange={(event) => field('purpose', event.target.value)}
                />
              </label>

              <label>
                <span className="field-label">所属项目</span>
                <input
                  aria-label="所属项目"
                  value={fields.project}
                  maxLength={200}
                  disabled={fieldsDisabled}
                  onChange={(event) => field('project', event.target.value)}
                />
              </label>
            </details>
            <label className="intake-confirm">
              <input
                type="checkbox"
                aria-describedby="intake-submit-help"
                checked={confirmed}
                disabled={disabled}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>我已核对原件，确认日期、金额、交易对方与本次业务一致。</span>
            </label>
            <p id="intake-submit-help" className="field-help">
              {direct
                ? '提交后由授权会计接收资料、生成草稿并按账套政策复核。'
                : '提交后由部门审批、财务核验，再交给会计复核。'}
            </p>
          </section>
        </div>
        {error && (
          <div className="form-error" role="alert" ref={failureBox} tabIndex={-1}>
            {failureInfo && <strong className="form-error-title">{failureInfo.title}</strong>}
            {error}
            {failureInfo && (
              <>
                <p className="field-help">{failureInfo.nextStep}</p>
                <details>
                  <summary>技术信息（复制给支持）</summary>
                  <code>
                    {failureInfo.requestId ? `请求编号 ${failureInfo.requestId} · ` : ''}
                    {failureInfo.technical}
                  </code>
                </details>
              </>
            )}
            {draft && (
              <button
                className="text-button"
                type="button"
                disabled={!!busy}
                onClick={() => void resume(draft.id)}
              >
                读取最新草稿
              </button>
            )}
            {editing && !accessLost && (
              <button
                className="text-button"
                type="button"
                disabled={!!busy}
                onClick={() =>
                  void run('reload-event', async () => {
                    const value = await api<BusinessEvent>(
                      `/business/events/${encodeURIComponent(editing.id)}`,
                      session,
                    );
                    if (!active.current) return;
                    if (
                      value.id !== editing.id ||
                      value.company_id !== editing.company_id ||
                      value.firm_id !== editing.firm_id
                    )
                      throw new Error('事项返回范围不一致，请关闭后重新打开原事项。');
                    setConfirmed(false);
                    setTouched(false);
                    setNeedsRefresh(false);
                    if (!value.permissions.edit) {
                      completionRef.current = true;
                      onCompleted(value);
                      return;
                    }
                    setEditing(value);
                    setFields(value.payload);
                    fieldRef.current = value.payload;
                    savedSignature.current = JSON.stringify({
                      template_id: value.template_id,
                      fields: value.payload,
                    });
                    setSaveState('已读取最新事项，请重新核对后提交。');
                  })
                }
              >
                读取最新事项
              </button>
            )}
          </div>
        )}
      </div>
      <footer className="modal-footer intake-footer">
        <span className="intake-save-state" role="status" aria-live="polite" aria-atomic="true">
          <Save size={15} aria-hidden="true" />
          <span>{busy === 'saving' ? '正在保存草稿…' : saveState}</span>
        </span>
        <div>
          <button
            className="button secondary intake-save-draft"
            type="button"
            disabled={
              accessLost || needsRefresh || !!busy || (!direct && !departmentId) || !confirmed
            }
            onClick={() => void finish(false)}
          >
            <Save size={15} />
            {editing ? '保存修改' : '暂存草稿'}
          </button>
          <button
            className="button primary"
            type="button"
            disabled={
              accessLost ||
              needsRefresh ||
              !!busy ||
              (!direct && !departmentId) ||
              !attachments.length ||
              !confirmed
            }
            onClick={() => void finish(true)}
          >
            {busy === 'submit' ? (
              <LoaderCircle className="spinner" size={15} />
            ) : (
              <Send size={15} />
            )}
            {editing?.status === 'returned'
              ? '修改后重新提交'
              : direct
                ? '提交财务处理'
                : '提交审批'}
          </button>
        </div>
      </footer>
    </dialog>
  );
}
