import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownToLine,
  CheckCircle2,
  LoaderCircle,
  Paperclip,
  RefreshCw,
  Send,
  X,
} from 'lucide-react';
import { api, apiBlob, errorMessage } from './api';
import type { Session } from './types';
import { uploadLimitLabel, uploadSizeError } from './transport-limits';
import { caseCanClose, caseMissingFields, caseReasonError, caseStateLabels } from './case-types';
import type { CaseTask } from './case-types';
import './cases.css';

const labelDate = (value: string) =>
  new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
const actionLabels: Record<string, string> = {
  create: '创建请求',
  assign: '指派责任人',
  comment: '处理记录',
  respond: '提交补件',
  'review-response': '核验补件',
  close: '关闭任务',
  upload: '上传补件原件',
};

export function CasePanel({
  session,
  caseId,
  onChanged,
  onClose,
}: {
  session: Session;
  caseId: string;
  onChanged?: () => void;
  onClose?: () => void;
}) {
  const [task, setTask] = useState<CaseTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reason, setReason] = useState('');
  const [operation, setOperation] = useState<
    'comment' | 'assign' | 'respond' | 'accept' | 'return' | 'close'
  >('comment');
  const [audience, setAudience] = useState<'internal' | 'requester'>('internal');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [assignees, setAssignees] = useState<{ user_id: string; name: string }[]>([]);
  const [assignee, setAssignee] = useState('');
  const [due, setDue] = useState('');
  const [priority, setPriority] = useState<'normal' | 'high' | 'urgent'>('normal');
  const [file, setFile] = useState<File | null>(null);
  const [supersedes, setSupersedes] = useState<string[]>([]);
  const epoch = useRef(0);
  const lock = useRef(false);
  const target = `${session.scope_key}:${caseId}`;
  const currentTarget = useRef(target);
  currentTarget.current = target;
  const keys = useRef(new Map<string, string>());
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const request = ++epoch.current;
      setLoading(true);
      try {
        const value = await api<CaseTask>(`/cases/${encodeURIComponent(caseId)}`, session, {
          signal,
        });
        if (signal?.aborted || request !== epoch.current) return;
        setTask(value);
        setError('');
        setDue(value.due_on ?? '');
        setPriority(value.priority);
        setAssignee(value.assignee_id ?? '');
        if (value.permissions.manage) {
          const choices = await api<{ assignees: { user_id: string; name: string }[] }>(
            `/cases/${encodeURIComponent(caseId)}/assignees`,
            session,
            { signal },
          );
          if (!signal?.aborted && request === epoch.current) setAssignees(choices.assignees);
        }
      } catch (failure) {
        if (!signal?.aborted && request === epoch.current) {
          setTask(null);
          setAssignees([]);
          setError(errorMessage(failure));
        }
      } finally {
        if (!signal?.aborted && request === epoch.current) setLoading(false);
      }
    },
    [session, caseId],
  );
  useEffect(() => {
    const controller = new AbortController();
    setTask(null);
    setFields({});
    setFile(null);
    setSupersedes([]);
    setReason('');
    setNotice('');
    setAssignees([]);
    setOperation('comment');
    keys.current.clear();
    void load(controller.signal);
    return () => {
      epoch.current += 1;
      controller.abort();
    };
  }, [target, load]);
  function commandKey(signature: string) {
    let key = keys.current.get(signature);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(signature, key);
    }
    return key;
  }
  async function execute() {
    if (!task || lock.current) return;
    const problem = caseReasonError(reason);
    if (problem) {
      setError(problem);
      return;
    }
    if (operation === 'respond' && caseMissingFields(task, fields).length) {
      setError(`请补齐：${caseMissingFields(task, fields).join('、')}`);
      return;
    }
    if (operation === 'assign' && !assignee) {
      setError('请选择当前有权限的责任人');
      return;
    }
    const started = target;
    const signature = JSON.stringify({
      target,
      operation,
      version: task.version,
      reason: reason.trim(),
      assignee,
      due,
      priority,
      audience,
      fields,
      supersedes,
    });
    const body = {
      expected_version: task.version,
      idempotency_key: commandKey(signature),
      reason: reason.trim(),
      ...(operation === 'assign' ? { assignee_id: assignee, due_on: due || null, priority } : {}),
      ...(operation === 'comment' ? { audience } : {}),
      ...(operation === 'respond' ? { fields } : {}),
      ...(['accept', 'return'].includes(operation)
        ? {
            decision: operation === 'accept' ? 'accept' : 'return',
            supersedes_attachment_ids: operation === 'accept' ? supersedes : [],
          }
        : {}),
    };
    const route =
      operation === 'comment'
        ? 'comments'
        : ['accept', 'return'].includes(operation)
          ? 'review-response'
          : operation;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await api<CaseTask>(
        `/cases/${encodeURIComponent(task.id)}/${route}`,
        session,
        { method: 'POST', body },
      );
      if (started !== currentTarget.current) return;
      keys.current.delete(signature);
      setTask(result);
      setReason('');
      setFields({});
      setFile(null);
      setSupersedes([]);
      setOperation('comment');
      setNotice(
        operation === 'accept'
          ? '补件已接受，原始审批记录保持不变。请重新识别追加票据并核对异常，处理完毕后关闭任务。'
          : operation === 'close'
            ? '任务已关闭，凭证仍需通过财务校验和会计复核。'
            : '处理记录已保存。',
      );
      onChanged?.();
    } catch (failure) {
      if (started === currentTarget.current) setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (started === currentTarget.current) setBusy(false);
    }
  }
  async function upload() {
    if (!task || !file || lock.current) return;
    const invalidSize = uploadSizeError(file.size);
    if (invalidSize) {
      setError(invalidSize);
      return;
    }
    const started = target;
    const signature = `${target}:upload:${task.version}:${file.name}:${file.size}:${file.lastModified}`;
    const query = new URLSearchParams({
      expected_version: String(task.version),
      idempotency_key: commandKey(signature),
    });
    const body = new FormData();
    body.append('file', file);
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await api<CaseTask>(
        `/cases/${encodeURIComponent(task.id)}/attachments?${query}`,
        session,
        { method: 'POST', body },
      );
      if (started !== currentTarget.current) return;
      setTask(result);
      setFile(null);
      keys.current.delete(signature);
      setNotice('原件已上传，请填写补件字段与说明后提交核验。');
      onChanged?.();
    } catch (failure) {
      if (started === currentTarget.current) setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (started === currentTarget.current) setBusy(false);
    }
  }
  async function download(id: string, filename: string) {
    const started = target;
    setError('');
    try {
      const blob = await apiBlob(`/cases/attachments/${encodeURIComponent(id)}/content`, session);
      if (started !== currentTarget.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      if (started === currentTarget.current) setError(errorMessage(failure));
    }
  }
  return (
    <section className="panel case-detail" aria-label="异常与补件详情">
      <div className="panel-heading">
        <div>
          <p className="case-kicker">EXCEPTION DESK / 处理记录</p>
          <h2>{task?.title ?? '读取任务详情'}</h2>
        </div>
        <div className="case-actions">
          <button
            className="text-button"
            disabled={busy || loading || !!reason || !!file}
            onClick={() => void load()}
          >
            <RefreshCw size={14} />
            刷新详情
          </button>
          {onClose && (
            <button
              className="icon-button"
              aria-label="关闭异常详情"
              disabled={busy}
              onClick={onClose}
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <LoaderCircle size={16} className="spinner" />
          正在读取当前授权的处理记录…
        </div>
      ) : (
        task && (
          <>
            <div className="case-summary">
              <span className={`badge ${task.state === 'closed' ? 'approved' : 'exception'}`}>
                {caseStateLabels[task.state]}
              </span>
              <strong>{task.company_name}</strong>
              <span>
                期限 {task.due_on ?? '未设定'}
                {task.overdue ? ' · 已逾期' : ''}
              </span>
              <span>版本 {task.version}</span>
            </div>
            <blockquote className="case-request">{task.request_reason}</blockquote>
            {task.source_summary && (
              <p className="field-help">
                {task.source_summary.date} · {task.source_summary.description} ·{' '}
                {task.source_summary.counterparty} ·{' '}
                {task.source_summary.direction === 'expense' ? '支出' : '收入'}{' '}
                {task.source_summary.amount} 元
              </p>
            )}
            {task.kind === 'supplement' && (
              <p className="field-help">
                补件作为新的证据保留，不替换已批准事项。金额、日期等业务事实如有错误，请由财务确认更正路径。
              </p>
            )}
            {!!task.required_fields.length && (
              <div className="case-requirements">
                <strong>需要补充的字段</strong>
                {task.required_fields.map((field) => (
                  <span key={field}>{field}</span>
                ))}
                {!!task.attachment_required && (
                  <span>
                    <Paperclip size={12} />
                    须上传原件
                  </span>
                )}
              </div>
            )}
            {!!task.responses?.length && (
              <div className="case-responses">
                <h3>补件响应</h3>
                {task.responses.map((response) => (
                  <article key={response.id}>
                    <div>
                      <strong>
                        {
                          {
                            draft: '待提交补件',
                            submitted: '已提交补件',
                            accepted: '已接受补件',
                            returned: '已退回补件',
                          }[response.state]
                        }
                      </strong>
                      <small>{labelDate(response.created_at)}</small>
                    </div>
                    {response.message && <p>{response.message}</p>}
                    {Object.entries(response.fields).map(([field, value]) => (
                      <dl key={field}>
                        <dt>{field}</dt>
                        <dd>{value}</dd>
                      </dl>
                    ))}
                    {response.attachments.map((attachment) => (
                      <button
                        key={attachment.id}
                        className="text-button"
                        onClick={() => void download(attachment.id, attachment.filename)}
                      >
                        <ArrowDownToLine size={13} />
                        {attachment.filename} · {(attachment.size_bytes / 1024).toFixed(0)} KB
                      </button>
                    ))}
                    {response.review_reason && (
                      <p className="field-help">核验依据：{response.review_reason}</p>
                    )}
                  </article>
                ))}
              </div>
            )}
            {task.permissions.upload && (
              <div className="case-upload">
                <label>
                  补件原件
                  <input
                    aria-label="补件原件"
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,.webp"
                    disabled={busy}
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  />
                </label>
                <button
                  className="button secondary small"
                  disabled={busy || !file}
                  onClick={() => void upload()}
                >
                  <Paperclip size={14} />
                  上传补件原件
                </button>
                <small>单个附件最多 {uploadLimitLabel()}；本次补件最多 10 件。</small>
              </div>
            )}
            {(task.permissions.manage || task.permissions.respond) && (
              <div className="case-command">
                <label>
                  处理操作
                  <select
                    aria-label="异常处理操作"
                    value={
                      task.permissions.respond && !task.permissions.manage ? 'respond' : operation
                    }
                    disabled={busy}
                    onChange={(event) => setOperation(event.target.value as typeof operation)}
                  >
                    {task.permissions.manage && (
                      <>
                        <option value="comment">记录处理依据</option>
                        {task.state !== 'closed' && (
                          <option value="assign">指派责任人及期限</option>
                        )}
                        {task.state === 'response_submitted' && (
                          <>
                            <option value="accept">接受补件 · 继续核对</option>
                            <option value="return">退回补件</option>
                          </>
                        )}
                        {caseCanClose(task) && <option value="close">关闭已处理任务</option>}
                      </>
                    )}
                    {task.permissions.respond && <option value="respond">提交补件核验</option>}
                  </select>
                </label>
                {operation === 'assign' && task.permissions.manage && (
                  <div className="case-assignment">
                    <label>
                      当前有权限的责任人
                      <select
                        aria-label="异常责任人"
                        value={assignee}
                        disabled={busy}
                        onChange={(event) => setAssignee(event.target.value)}
                      >
                        <option value="">请选择责任人</option>
                        {assignees.map((user) => (
                          <option key={user.user_id} value={user.user_id}>
                            {user.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      完成期限
                      <input
                        aria-label="异常完成期限"
                        type="date"
                        value={due}
                        disabled={busy}
                        onChange={(event) => setDue(event.target.value)}
                      />
                    </label>
                    <label>
                      优先级
                      <select
                        value={priority}
                        disabled={busy}
                        onChange={(event) => setPriority(event.target.value as typeof priority)}
                      >
                        <option value="normal">普通</option>
                        <option value="high">优先</option>
                        <option value="urgent">紧急</option>
                      </select>
                    </label>
                  </div>
                )}
                {(operation === 'respond' ||
                  (task.permissions.respond && !task.permissions.manage)) &&
                  task.required_fields.map((field) => (
                    <label key={field}>
                      {field}
                      <input
                        aria-label={`补件字段 ${field}`}
                        maxLength={2000}
                        value={fields[field] ?? ''}
                        disabled={busy}
                        onChange={(event) =>
                          setFields((current) => ({ ...current, [field]: event.target.value }))
                        }
                      />
                    </label>
                  ))}
                {operation === 'comment' && task.permissions.manage && (
                  <label>
                    记录可见范围
                    <select
                      aria-label="处理记录可见范围"
                      value={audience}
                      disabled={busy}
                      onChange={(event) => setAudience(event.target.value as typeof audience)}
                    >
                      <option value="internal">仅财务与授权会计</option>
                      {(task.kind === 'supplement' || !task.event_id) && (
                        <option value="requester">同时对补件申请人或资料收集人可见</option>
                      )}
                    </select>
                  </label>
                )}
                {operation === 'accept' && !!task.replacement_choices?.length && (
                  <div className="case-requirements">
                    <strong>新文件是否替代旧原件</strong>
                    <p className="field-help">
                      仅在同一票据补清晰扫描件时勾选。新增合同或不同票据应保留原件；旧文件始终保留追溯记录。
                    </p>
                    {task.replacement_choices.map((attachment) => (
                      <label className="case-checkbox" key={attachment.id}>
                        <input
                          type="checkbox"
                          disabled={busy}
                          checked={supersedes.includes(attachment.id)}
                          onChange={(event) =>
                            setSupersedes((current) =>
                              event.target.checked
                                ? [...current, attachment.id]
                                : current.filter((id) => id !== attachment.id),
                            )
                          }
                        />
                        替代 {attachment.filename}
                      </label>
                    ))}
                  </div>
                )}
                <label>
                  {task.permissions.respond && !task.permissions.manage ? '补件说明' : '处理依据'}
                  <textarea
                    aria-label="异常处理依据"
                    value={reason}
                    rows={3}
                    maxLength={2000}
                    disabled={busy}
                    onChange={(event) => {
                      setReason(event.target.value);
                      if (task.permissions.respond && !task.permissions.manage)
                        setOperation('respond');
                    }}
                    placeholder="记录已核对的事实、补件说明或处理结果"
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={busy || !!caseReasonError(reason)}
                  onClick={() => void execute()}
                >
                  {busy ? (
                    <LoaderCircle size={14} className="spinner" />
                  ) : operation === 'close' ? (
                    <CheckCircle2 size={14} />
                  ) : (
                    <Send size={14} />
                  )}
                  {operation === 'accept'
                    ? '接受补件并更新证据版本'
                    : operation === 'respond' ||
                        (task.permissions.respond && !task.permissions.manage)
                      ? '提交补件核验'
                      : '保存处理记录'}
                </button>
              </div>
            )}
            <div className="case-history">
              <h3>处理时间线</h3>
              {task.history?.map((item, index) => (
                <article key={`${item.case_version}:${index}`}>
                  <div>
                    <strong>{actionLabels[item.action] ?? '处理记录'}</strong>
                    <small>
                      {labelDate(item.created_at)} · v{item.case_version}
                      {item.audience === 'internal' ? ' · 内部' : ''}
                    </small>
                  </div>
                  <p>{item.reason}</p>
                </article>
              ))}
            </div>
          </>
        )
      )}
    </section>
  );
}
