import { useEffect, useRef, useState } from 'react';
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
import { changeIntakeTemplate, intakeCandidates } from './intake-helpers';
import { uploadLimitLabel } from './transport-limits';
import './intake.css';

export function IntakeWizard({
  session,
  context,
  onClose,
  onCompleted,
  onBusyChange,
  initialDraftId = '',
  initialTemplate = 'reimbursement',
}: {
  session: Session;
  context: BusinessContext;
  onClose: () => void;
  onCompleted: (event: BusinessEvent) => void;
  onBusyChange: (value: boolean) => void;
  initialDraftId?: string;
  initialTemplate?: BusinessTemplate;
}) {
  const companies = context.companies.filter((company) => company.can_submit);
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const [companyId, setCompany] = useState(companies[0]?.id ?? '');
  const company = companies.find((item) => item.id === companyId);
  const direct = company?.submission_policy?.mode === 'direct';
  const [departmentId, setDepartment] = useState(company?.department_id ?? '');
  const [template, setTemplate] = useState<BusinessTemplate>(initialTemplate);
  const [fields, setFields] = useState(initialBusinessPayload(initialTemplate));
  const [draft, setDraft] = useState<IntakeDraft | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<IntakeDraft[]>([]);
  const [busy, setBusy] = useState('');
  const [saveState, setSaveState] = useState('内容与原件仅保存在当前账号获授权的企业');
  const [error, setError] = useState('');
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
  const savedSignature = useRef('');
  const pendingCreate = useRef<{ key: string; body: object; signature: string } | null>(null);
  const completeKey = useRef<{ signature: string; key: string } | null>(null);
  const recognizeKeys = useRef(new Map<string, string>());
  const completionRef = useRef(false);
  useEffect(() => {
    dialog.current?.showModal();
    active.current = true;
    const controller = new AbortController();
    void api<{ drafts: IntakeDraft[] }>('/intake/drafts', session, { signal: controller.signal })
      .then((value) => {
        if (active.current) setSavedDrafts(value.drafts);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [session]);
  useEffect(() => {
    if (initialDraftId) void resume(initialDraftId);
  }, [initialDraftId]);
  useEffect(() => {
    onBusyChange(!!busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);

  function adopt(value: IntakeDraft, replaceFields = false) {
    draftRef.current = value;
    setDraft(value);
    setCompany(value.company_id);
    setDepartment(value.department_id ?? '');
    if (replaceFields) {
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
  async function run(name: string, action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(name);
    setError('');
    try {
      await action();
    } catch (failure) {
      if (!active.current) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        setDraft(null);
        draftRef.current = null;
        pendingCreate.current = null;
        completeKey.current = null;
        savedSignature.current = '';
        setFields(initialBusinessPayload('reimbursement'));
        setSavedDrafts([]);
        setConfirmed(false);
        setTouched(false);
      }
      const uncertain =
        failure instanceof TypeError ||
        (failure instanceof ApiError && [409, 503].includes(failure.status));
      setError(
        errorMessage(failure) +
          (uncertain
            ? ' 草稿或提交结果可能已保存，请使用“读取最新草稿”核对；重复提交保持同一保存编号。'
            : ''),
      );
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
          await persist();
        }),
      1200,
    );
    return () => window.clearTimeout(timer);
  }, [fields, template, touched, busy, error, companyId, departmentId, direct]);
  function field<K extends keyof BusinessPayload>(key: K, value: BusinessPayload[K]) {
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
    setCompany(value);
    setDepartment(companies.find((item) => item.id === value)?.department_id ?? '');
  }
  async function resume(id: string) {
    await run('resume', async () => {
      const value = await api<IntakeDraft>(`/intake/drafts/${encodeURIComponent(id)}`, session);
      if (!active.current) return;
      if (value.status === 'completed' && value.event_id) {
        const event = await api<BusinessEvent>(
          `/business/events/${encodeURIComponent(value.event_id)}`,
          session,
        );
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
      let current = await persist();
      for (const file of selected) {
        const validation = validateAttachment(
          file,
          current.attachments.length,
          current.attachments.reduce((sum, item) => sum + item.size_bytes, 0),
        );
        if (validation) throw new Error(`${file.name}：${validation}`);
        const data = new FormData();
        data.append('file', file);
        current = await api<IntakeDraft>(
          `/intake/drafts/${encodeURIComponent(current.id)}/attachments?expected_version=${current.version}`,
          session,
          { method: 'POST', body: data },
        );
        if (active.current) adopt(current);
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
    const validation = validateBusinessPayload(template, fields);
    if (validation) {
      setError(validation);
      return;
    }
    if (!confirmed) {
      setError('请核对原件并勾选字段确认。');
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
        await persist();
        onClose();
      });
    else onClose();
  }
  async function download(id: string, filename: string) {
    await run('download', async () => {
      const data = await apiBlob(`/intake/attachments/${encodeURIComponent(id)}/content`, session);
      if (!active.current) return;
      const url = URL.createObjectURL(data);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    });
  }
  const disabled = !!busy && busy !== 'saving';
  const latestRuns = new Map<string, IntakeRun>();
  draft?.runs.forEach((row) => {
    if (!latestRuns.has(row.attachment_id)) latestRuns.set(row.attachment_id, row);
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
          <h2 id="intake-title">录入业务事项</h2>
          <p>上传票据 → 核对信息 → {direct ? '提交给会计' : '提交审批'}</p>
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
        {!!savedDrafts.length && !draft && (
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
        <div className="business-template-grid" role="group" aria-label="选择业务模板">
          {BUSINESS_TEMPLATES.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-pressed={template === item.id}
              className={template === item.id ? 'selected' : ''}
              disabled={disabled}
              onClick={() => chooseTemplate(item.id)}
            >
              <Receipt size={18} />
              <strong>{item.name}</strong>
              <span>{item.description}</span>
            </button>
          ))}
        </div>
        <div className="intake-company-grid">
          <label>
            <span className="field-label">客户企业</span>
            <select
              aria-label="事项客户企业"
              value={companyId}
              disabled={!!draft || !!busy || !!pendingCreate.current}
              onChange={(event) => chooseCompany(event.target.value)}
            >
              {companies.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="field-label">{direct ? '所属部门（可选）' : '业务所属部门'}</span>
            <select
              aria-label="事项所属部门"
              value={departmentId}
              disabled={
                !!draft || !!busy || !!pendingCreate.current || company?.role === 'employee'
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
          </label>
        </div>
        <div className="intake-two-column">
          <section className="intake-originals">
            <h3>
              <Paperclip size={17} />
              票据与原件<span>支持多文件</span>
            </h3>
            <div className="intake-upload">
              <Upload size={27} />
              <strong>把发票、收据或业务凭据放在这里</strong>
              <p>PDF、JPG、PNG、WebP · 单个 {uploadLimitLabel()}，合计 50 MB</p>
              <p>数电票 XML 支持本地解析，单份解析上限 1 MB。</p>
              <div>
                <button
                  type="button"
                  className="button secondary"
                  disabled={!!busy || (!direct && !departmentId)}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload size={15} />
                  选择附件
                </button>
                <button
                  type="button"
                  className="button secondary intake-camera"
                  disabled={!!busy || (!direct && !departmentId)}
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
              {draft?.ai_enabled && (draft.vision_ready || draft.structured_ready)
                ? '识别后逐字段选择建议；金额和交易对方由你核对。'
                : '票据识别尚未启用或配置，可以直接手工填写并提交。'}
            </p>
            {draft?.attachments.map((attachment) => {
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
                  {draft.ai_enabled &&
                    (attachment.media_type === 'application/xml'
                      ? draft.structured_ready
                      : draft.vision_ready) && (
                      <button
                        className="button secondary small"
                        type="button"
                        disabled={
                          !!busy || run?.status === 'running' || run?.status === 'succeeded'
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
                  {run && run.status === 'failed' && (
                    <p className="form-error">{run.error_detail}</p>
                  )}
                  {run &&
                    intakeCandidates(run, fields.direction).map((candidate) => (
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
                  {!!run?.checks.length && (
                    <details className="intake-checks">
                      <summary>需要核对的票据问题（{run.checks.length}）</summary>
                      {run.checks.map((item) => (
                        <p key={item.code}>{item.payload.message}</p>
                      ))}
                    </details>
                  )}
                </article>
              );
            })}
            {(draft?.attachments.length ?? 0) > 1 && (
              <p className="field-help">
                多张票据分别展示，不自动合计或猜测交易对方。涉及不同业务时请分别录入。
              </p>
            )}
          </section>
          <section className="intake-fields">
            <h3>
              <CheckCircle2 size={17} />
              核对业务信息
            </h3>
            <div className="business-form-grid">
              <label>
                <span className="field-label">发生日期</span>
                <input
                  type="date"
                  aria-label="发生日期"
                  min="2000-01-01"
                  max="2099-12-31"
                  value={fields.occurred_on}
                  disabled={disabled}
                  onChange={(event) => field('occurred_on', event.target.value)}
                />
              </label>
              <label>
                <span className="field-label">金额（人民币元）</span>
                <input
                  inputMode="decimal"
                  aria-label="事项金额"
                  value={fields.amount}
                  placeholder="例如 128.50"
                  disabled={disabled}
                  onChange={(event) => field('amount', event.target.value)}
                />
              </label>
              <label className="wide-field">
                <span className="field-label">事项摘要</span>
                <input
                  aria-label="事项摘要"
                  value={fields.description}
                  maxLength={500}
                  placeholder="例如：客户拜访交通费"
                  disabled={disabled}
                  onChange={(event) => field('description', event.target.value)}
                />
              </label>
              <label className="wide-field">
                <span className="field-label">交易对方</span>
                <input
                  aria-label="交易对方"
                  value={fields.counterparty}
                  maxLength={200}
                  placeholder="供应商、客户或实际收款人"
                  disabled={disabled}
                  onChange={(event) => field('counterparty', event.target.value)}
                />
              </label>
              <label>
                <span className="field-label">事项分类</span>
                <select
                  aria-label="事项分类"
                  value={fields.category}
                  disabled={disabled}
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
              {template === 'daily_cashflow' ? (
                <label>
                  <span className="field-label">收支方向</span>
                  <select
                    aria-label="收支方向"
                    value={fields.direction}
                    disabled={disabled}
                    onChange={(event) =>
                      field('direction', event.target.value as BusinessPayload['direction'])
                    }
                  >
                    <option value="expense">支出</option>
                    <option value="income">收入</option>
                  </select>
                </label>
              ) : (
                <label>
                  <span className="field-label">企业付款状态</span>
                  <select
                    aria-label="企业付款状态"
                    value={fields.payment_status}
                    disabled={disabled || template === 'reimbursement'}
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
              )}
              <label className="wide-field">
                <span className="field-label">业务用途（可选）</span>
                <textarea
                  aria-label="业务用途"
                  value={fields.purpose}
                  maxLength={1000}
                  rows={2}
                  disabled={disabled}
                  onChange={(event) => field('purpose', event.target.value)}
                />
              </label>
            </div>
            <details className="intake-extra">
              <summary>补充所属项目（可选）</summary>
              <label>
                <span className="field-label">所属项目</span>
                <input
                  aria-label="所属项目"
                  value={fields.project}
                  maxLength={200}
                  disabled={disabled}
                  onChange={(event) => field('project', event.target.value)}
                />
              </label>
            </details>
            <label className="intake-confirm">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={disabled}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>我已核对原件，确认日期、金额、交易对方与本次业务一致。</span>
            </label>
            <p className="field-help">
              {direct
                ? '提交后由授权会计接收资料、生成草稿并独立复核。'
                : '提交后由部门审批、财务核验，再交给会计复核。'}
            </p>
          </section>
        </div>
        {error && (
          <div className="form-error" role="alert">
            {error}
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
          </div>
        )}
      </div>
      <footer className="modal-footer intake-footer">
        <span role="status">{busy === 'saving' ? '正在保存草稿…' : saveState}</span>
        <div>
          <button
            className="button secondary intake-save-draft"
            type="button"
            disabled={!!busy || (!direct && !departmentId)}
            onClick={() =>
              void run('saving', async () => {
                await persist();
              })
            }
          >
            <Save size={15} />
            保存录入草稿
          </button>
          <button
            className="button secondary"
            type="button"
            disabled={!!busy || (!direct && !departmentId) || !confirmed}
            onClick={() => void finish(false)}
          >
            <FileText size={15} />
            保存事项
          </button>
          <button
            className="button primary"
            type="button"
            disabled={
              !!busy || (!direct && !departmentId) || !draft?.attachments.length || !confirmed
            }
            onClick={() => void finish(true)}
          >
            {busy === 'submit' ? (
              <LoaderCircle className="spinner" size={15} />
            ) : (
              <Send size={15} />
            )}
            {direct ? '提交给会计' : '提交审批'}
          </button>
        </div>
      </footer>
    </dialog>
  );
}
