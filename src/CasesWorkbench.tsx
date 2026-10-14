import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ClipboardCheck,
  Filter,
  LoaderCircle,
  Plus,
  RefreshCw,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, errorMessage } from './api';
import type { Book, BookDetail, Session } from './types';
import type { BusinessContext, BusinessEvent } from './business-types';
import { CasePanel } from './CasePanel';
import { caseReasonError, caseStateLabels } from './case-types';
import type { CaseState, CaseTask } from './case-types';
import './cases.css';

export interface CaseTarget {
  eventId?: string;
  bookId?: string;
  draftId?: string;
  title?: string;
}

export function CaseCreatePanel({
  session,
  target,
  onCreated,
  onClose,
}: {
  session: Session;
  target?: CaseTarget;
  onCreated?: (task: CaseTask) => void;
  onClose?: () => void;
}) {
  const [events, setEvents] = useState<BusinessEvent[]>([]);
  const [books, setBooks] = useState<Book[]>([]);
  const [drafts, setDrafts] = useState<BookDetail['drafts']>([]);
  const [kind, setKind] = useState<'exception' | 'supplement'>('exception');
  const [source, setSource] = useState<'event' | 'draft'>(
    target?.eventId ? 'event' : target?.bookId ? 'draft' : 'event',
  );
  const [eventId, setEventId] = useState(target?.eventId ?? '');
  const [bookId, setBookId] = useState(target?.bookId ?? '');
  const [draftId, setDraftId] = useState(target?.draftId ?? '');
  const [title, setTitle] = useState(target?.title ?? '');
  const [reason, setReason] = useState('');
  const [fields, setFields] = useState('');
  const [attachmentRequired, setAttachmentRequired] = useState(false);
  const [due, setDue] = useState('');
  const [priority, setPriority] = useState<'normal' | 'high' | 'urgent'>('normal');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const requestKeys = useRef(new Map<string, string>());
  const lock = useRef(false);
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    setLoading(true);
    void (async () => {
      try {
        const context = await api<BusinessContext>('/business/context', session, {
          signal: controller.signal,
        });
        const scope = context.companies.some((company) => company.can_finance)
          ? 'company'
          : 'accounting';
        const [eventResult, bookResult] = await Promise.all([
          api<{ events: BusinessEvent[]; total: number }>(
            `/business/events?scope=${scope}&limit=100`,
            session,
            { signal: controller.signal },
          ).catch(() => ({ events: [], total: 0 })),
          api<{ books: Book[] }>('/workbench?limit=100', session, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        setEvents(eventResult.events);
        setBooks(bookResult.books.filter((book) => book.permissions.review));
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [session]);
  useEffect(() => {
    if (!bookId || source !== 'draft') {
      setDrafts([]);
      return;
    }
    const controller = new AbortController();
    setDrafts([]);
    void api<BookDetail>(
      `/books/${encodeURIComponent(bookId)}?filter=pending&summaries=true&limit=100${target?.draftId ? `&focus_draft_id=${encodeURIComponent(target.draftId)}` : ''}`,
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setDrafts(value.drafts);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [session, bookId, source, target?.draftId]);
  async function create() {
    if (lock.current) return;
    if (!title.trim() || caseReasonError(reason)) {
      setError('请填写任务标题和具体处理依据');
      return;
    }
    if ((source === 'event' && !eventId) || (source === 'draft' && (!bookId || !draftId))) {
      setError('请选择当前可访问的业务事项或凭证');
      return;
    }
    const data = {
      ...(source === 'event' ? { event_id: eventId } : { book_id: bookId, draft_id: draftId }),
      kind,
      title: title.trim(),
      reason: reason.trim(),
      due_on: due || null,
      priority,
      required_fields:
        kind === 'supplement'
          ? fields
              .split(/[\n，,]/)
              .map((field) => field.trim())
              .filter(Boolean)
          : [],
      attachment_required: kind === 'supplement' && attachmentRequired,
    };
    const signature = JSON.stringify(data);
    let key = requestKeys.current.get(signature);
    if (!key) {
      key = crypto.randomUUID();
      requestKeys.current.set(signature, key);
    }
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await api<CaseTask>('/cases', session, {
        method: 'POST',
        body: { ...data, idempotency_key: key },
      });
      if (active.current) {
        requestKeys.current.delete(signature);
        onCreated?.(result);
      }
    } catch (failure) {
      if (active.current) setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <section className="panel case-create" aria-label="创建异常或补件任务">
      <div className="panel-heading">
        <div>
          <p className="case-kicker">NEW CASE / 创建处理任务</p>
          <h2>明确问题、责任和补件要求</h2>
        </div>
        {onClose && (
          <button
            className="icon-button"
            aria-label="关闭新建异常任务"
            disabled={busy}
            onClick={onClose}
          >
            <X size={16} />
          </button>
        )}
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="case-create-grid">
        <label>
          任务类型
          <select
            aria-label="任务类型"
            value={kind}
            disabled={busy}
            onChange={(event) => {
              const value = event.target.value as typeof kind;
              setKind(value);
              if (value === 'supplement') setSource('event');
            }}
          >
            <option value="exception">会计内部异常</option>
            <option value="supplement">向原申请人请求补件</option>
          </select>
        </label>
        <label>
          绑定来源
          <select
            aria-label="异常任务来源"
            value={source}
            disabled={busy || kind === 'supplement' || !!target}
            onChange={(event) => setSource(event.target.value as typeof source)}
          >
            <option value="event">业务事项</option>
            <option value="draft">账套凭证</option>
          </select>
        </label>
        {source === 'event' ? (
          <label className="case-wide">
            业务事项
            <select
              aria-label="补件业务事项"
              value={eventId}
              disabled={busy || loading || !!target?.eventId}
              onChange={(event) => setEventId(event.target.value)}
            >
              <option value="">选择已授权的业务事项</option>
              {target?.eventId && !events.some((event) => event.id === target.eventId) && (
                <option value={target.eventId}>{target.title || '当前业务事项'}</option>
              )}
              {events.map((event) => (
                <option value={event.id} key={event.id}>
                  {event.company_name} · {event.payload.description} · {event.payload.amount} 元
                </option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label>
              账套
              <select
                aria-label="异常任务账套"
                value={bookId}
                disabled={busy || loading || !!target?.bookId}
                onChange={(event) => {
                  setBookId(event.target.value);
                  setDraftId('');
                }}
              >
                <option value="">选择已授权的账套</option>
                {target?.bookId && !books.some((book) => book.id === target.bookId) && (
                  <option value={target.bookId}>当前授权账套</option>
                )}
                {books.map((book) => (
                  <option value={book.id} key={book.id}>
                    {book.company_name || book.name} · {book.period}
                  </option>
                ))}
              </select>
            </label>
            <label>
              待复核凭证
              <select
                aria-label="异常任务凭证"
                value={draftId}
                disabled={busy || !bookId || !!target?.draftId}
                onChange={(event) => setDraftId(event.target.value)}
              >
                <option value="">选择待复核凭证</option>
                {target?.draftId && !drafts.some((draft) => draft.id === target.draftId) && (
                  <option value={target.draftId}>{target.title || '当前待复核凭证'}</option>
                )}
                {drafts.map((draft) => (
                  <option value={draft.id} key={draft.id}>
                    {draft.description}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <label className="case-wide">
          任务标题
          <input
            aria-label="异常任务标题"
            maxLength={200}
            value={title}
            disabled={busy}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：补充采购合同与付款用途"
          />
        </label>
        <label className="case-wide">
          {kind === 'supplement' ? '发给申请人的补件要求' : '内部异常与处理依据'}
          <textarea
            aria-label="创建任务依据"
            rows={3}
            maxLength={2000}
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            placeholder="说明缺失内容、需要核对的事实及处理要求"
          />
        </label>
        {kind === 'supplement' && (
          <>
            <label className="case-wide">
              要求补充的字段
              <textarea
                aria-label="补件要求字段"
                rows={2}
                maxLength={2000}
                value={fields}
                disabled={busy}
                onChange={(event) => setFields(event.target.value)}
                placeholder="每行一个字段，例如：业务用途、合同编号"
              />
            </label>
            <label className="case-checkbox">
              <input
                type="checkbox"
                checked={attachmentRequired}
                disabled={busy}
                onChange={(event) => setAttachmentRequired(event.target.checked)}
              />
              须上传票据或业务原件
            </label>
          </>
        )}
        <label>
          完成期限
          <input
            type="date"
            aria-label="创建任务期限"
            value={due}
            disabled={busy}
            onChange={(event) => setDue(event.target.value)}
          />
        </label>
        <label>
          优先级
          <select
            aria-label="任务优先级"
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
      <p className="field-help">
        未关闭任务会阻止凭证复核和导出。内部异常仅授权财务与会计可见；补件要求会显示在原申请人的站内待办中。
      </p>
      <button
        className="button secondary"
        disabled={busy || loading || !title.trim() || !!caseReasonError(reason)}
        onClick={() => void create()}
      >
        {busy ? <LoaderCircle size={14} className="spinner" /> : <Plus size={14} />}创建处理任务
      </button>
    </section>
  );
}

export function CasesWorkbench({
  session,
  companyId = '',
  bookId = '',
  initialTarget,
  initialCaseId,
  onChanged,
}: {
  session: Session;
  companyId?: string;
  bookId?: string;
  initialTarget?: CaseTarget;
  initialCaseId?: string;
  onChanged?: () => void;
}) {
  const [tasks, setTasks] = useState<CaseTask[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<CaseState | ''>('');
  const [overdue, setOverdue] = useState(false);
  const [mine, setMine] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string | null>(initialCaseId ?? null);
  const [creating, setCreating] = useState(!initialCaseId && !!initialTarget);
  const [canCreate, setCanCreate] = useState(false);
  const epoch = useRef(0);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const request = ++epoch.current;
      setLoading(true);
      const query = new URLSearchParams({
        limit: '20',
        offset: String(offset),
        overdue: String(overdue),
        mine: String(mine),
      });
      if (state) query.set('state', state);
      if (companyId) query.set('company_id', companyId);
      if (bookId) query.set('book_id', bookId);
      try {
        const value = await api<{ cases: CaseTask[]; total: number }>(`/cases?${query}`, session, {
          signal,
        });
        if (!signal?.aborted && request === epoch.current) {
          setTasks(value.cases);
          setTotal(value.total);
          setError('');
        }
      } catch (failure) {
        if (!signal?.aborted && request === epoch.current) {
          setError(errorMessage(failure));
          setTasks([]);
          setTotal(0);
          if (!initialCaseId) setSelected(null);
        }
      } finally {
        if (!signal?.aborted && request === epoch.current) setLoading(false);
      }
    },
    [session, companyId, bookId, offset, state, overdue, mine, initialCaseId],
  );
  useEffect(() => {
    setTasks([]);
    setSelected(initialCaseId ?? null);
    setCreating(!initialCaseId && !!initialTarget);
    setOffset(0);
    setCanCreate(false);
    const controller = new AbortController();
    void Promise.all([
      api<BusinessContext>('/business/context', session, { signal: controller.signal }),
      api<{ books: Book[] }>('/workbench?limit=100', session, { signal: controller.signal }),
    ])
      .then(([context, workbench]) => {
        if (!controller.signal.aborted)
          setCanCreate(
            context.companies.some((company) => company.can_finance) ||
              workbench.books.some((book) => book.permissions.review),
          );
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [session.scope_key, initialTarget, initialCaseId]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current += 1;
    };
  }, [load]);
  function changed() {
    void load();
    onChanged?.();
  }
  return (
    <div className="cases-workbench">
      <div className="case-page-heading">
        <div>
          <p className="case-kicker">ACCOUNTING OPERATIONS / EXCEPTIONS</p>
          <h1>异常与补件</h1>
          <p>把需要人工介入的事项交给合适的人，跟踪资料、核验和处理结果。</p>
        </div>
        <div className="case-actions">
          <button
            className="text-button"
            disabled={loading || creating || !!selected}
            onClick={() => void load()}
          >
            <RefreshCw size={14} />
            刷新待办
          </button>
          {canCreate && (
            <button
              className="button secondary"
              disabled={creating || !!selected}
              onClick={() => setCreating(true)}
            >
              <Plus size={14} />
              创建处理任务
            </button>
          )}
        </div>
      </div>
      {(companyId || bookId) && (
        <p className="field-help">
          当前已按选定客户{bookId ? '及账套' : ''}限定异常范围，可从客户月度进度重新选择。
        </p>
      )}
      {creating ? (
        <CaseCreatePanel
          session={session}
          target={initialTarget}
          onClose={() => setCreating(false)}
          onCreated={(task) => {
            setCreating(false);
            setSelected(task.id);
            changed();
          }}
        />
      ) : selected ? (
        <CasePanel
          key={`${session.scope_key}:${selected}`}
          session={session}
          caseId={selected}
          onClose={() => setSelected(null)}
          onChanged={changed}
        />
      ) : (
        <section className="panel">
          <div className="case-filters">
            <Filter size={15} />
            <label>
              任务状态
              <select
                aria-label="筛选异常状态"
                value={state}
                onChange={(event) => {
                  setOffset(0);
                  setState(event.target.value as typeof state);
                }}
              >
                <option value="">全部状态</option>
                {Object.entries(caseStateLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="case-checkbox">
              <input
                type="checkbox"
                checked={overdue}
                onChange={(event) => {
                  setOffset(0);
                  setOverdue(event.target.checked);
                }}
              />
              仅逾期
            </label>
            <label className="case-checkbox">
              <input
                type="checkbox"
                checked={mine}
                onChange={(event) => {
                  setOffset(0);
                  setMine(event.target.checked);
                }}
              />
              与我有关
            </label>
            <span>{total} 项当前授权待办</span>
          </div>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {loading ? (
            <div className="loading-block">
              <LoaderCircle size={16} className="spinner" />
              正在读取异常与补件…
            </div>
          ) : tasks.length ? (
            <div className="case-list">
              {tasks.map((task) => (
                <button
                  key={task.id}
                  className="case-list-item"
                  onClick={() => setSelected(task.id)}
                >
                  <span className={`case-marker ${task.priority}`}>
                    {task.overdue ? <TriangleAlert size={16} /> : <ClipboardCheck size={16} />}
                  </span>
                  <span className="case-list-main">
                    <strong>{task.title}</strong>
                    <small>
                      {task.company_name} ·{' '}
                      {task.collection_request
                        ? '已指派资料收集'
                        : task.kind === 'supplement'
                          ? '申请人补件'
                          : '会计内部异常'}{' '}
                      · {task.due_on ? `期限 ${task.due_on}` : '未设定期限'}
                    </small>
                  </span>
                  <span className={`badge ${task.state === 'closed' ? 'approved' : 'exception'}`}>
                    {caseStateLabels[task.state]}
                  </span>
                  {task.overdue && <span className="case-overdue">逾期</span>}
                </button>
              ))}
            </div>
          ) : (
            !error && (
              <div className="case-empty">
                <ClipboardCheck size={24} />
                <strong>当前范围没有待办</strong>
                <p>补件请求发布后，原申请人可以在这里提交说明和原件。</p>
              </div>
            )
          )}
          <div className="case-pagination">
            <span>每页 20 项</span>
            <div>
              <button
                className="text-button"
                disabled={loading || offset === 0}
                onClick={() => setOffset((value) => Math.max(0, value - 20))}
              >
                上一页异常待办
              </button>
              <button
                className="text-button"
                disabled={loading || offset + 20 >= total}
                onClick={() => setOffset((value) => value + 20)}
              >
                下一页异常待办
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
