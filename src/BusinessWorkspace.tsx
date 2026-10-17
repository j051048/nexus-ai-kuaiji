import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Building2,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Download,
  FileText,
  FolderOpen,
  LoaderCircle,
  Paperclip,
  Plus,
  Receipt,
  RotateCcw,
  Send,
  ShieldCheck,
  ShoppingBag,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import { attachmentForm, prepareDirectUpload } from './upload-client';
import { formatMoney } from './csv';
import { directUploadLimitLabel } from './transport-limits';
import {
  BUSINESS_CATEGORIES,
  BUSINESS_TEMPLATES,
  initialBusinessPayload,
  initialBusinessScope,
  normalizeBusinessPayload,
  validateAttachment,
  validateBusinessPayload,
} from './business-forms';
import type {
  BusinessContext,
  BusinessEvent,
  BusinessPayload,
  BusinessScope,
  BusinessStatus,
  BusinessTemplate,
} from './business-types';
import type { Book, Pagination, Session } from './types';
import { BusinessOrganization } from './BusinessOrganization';
import { IntelligencePanel } from './IntelligencePanel';
import { QueueBatchDialog } from './QueueBatchDialog';
import { IntakeWizard } from './IntakeWizard';
import './intake.css';

const statusLabels: Record<BusinessStatus, string> = {
  draft: '草稿',
  submitted: '待部门审批',
  department_approved: '待财务核验',
  finance_approved: '待交接会计',
  handed_off: '已交接会计',
  returned: '已退回',
};
const scopeLabels: Record<BusinessScope, string> = {
  own: '本人事项',
  department: '部门审批',
  company: '企业财务',
  accounting: '会计接收',
};
const actionLabels: Record<string, string> = {
  department: '部门审批',
  finance: '财务核验',
  handoff: '交接会计',
  return: '退回修改',
  submitted: '提交审批',
  submit: '提交审批',
};
const encoded = encodeURIComponent;
function Spinner() {
  return <LoaderCircle className="spinner" size={16} />;
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <FolderOpen size={26} />
      </div>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}
type CompanyBook = Pick<
  Book,
  'id' | 'name' | 'period' | 'period_status' | 'company_id' | 'permissions'
>;
type CompanyBooksPage = { books: CompanyBook[]; pagination: Pagination };

export function BusinessWorkspace({
  session,
  onBusyChange,
  onOpenDraft,
  onOpenBatch,
  onOpenCase,
  onOpenRevision,
  initialEventId = '',
  initialIntakeId = '',
  initialTemplate,
  initialNew = false,
  initialCompanyId = '',
  initialPeriod = '',
}: {
  session: Session;
  books: Book[];
  onBusyChange: (busy: boolean) => void;
  onOpenDraft: (bookId: string, draftId: string) => void;
  onOpenBatch?: (batchId: string) => void;
  onOpenCase?: (target: { eventId: string; title: string }) => void;
  initialEventId?: string;
  onOpenRevision?: (eventId: string) => void;
  initialIntakeId?: string;
  initialTemplate?: BusinessTemplate;
  initialNew?: boolean;
  initialCompanyId?: string;
  initialPeriod?: string;
}) {
  const [context, setContext] = useState<BusinessContext | null>(null);
  const [scope, setScope] = useState<BusinessScope>('own');
  const [companyId, setCompanyId] = useState(initialCompanyId);
  const [status, setStatus] = useState('');
  const [events, setEvents] = useState<BusinessEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<BusinessEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [operationBusy, setBusy] = useState('');
  const [intelligenceBusy, setIntelligenceBusy] = useState(false);
  const busy = operationBusy || (intelligenceBusy ? 'intelligence' : '');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [formEvent, setFormEvent] = useState<BusinessEvent | 'new' | null>(null);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const defaultScopeSet = useRef(false);
  const [organizationOpen, setOrganizationOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [returnReason, setReturnReason] = useState('');
  const [handoffBookId, setHandoffBookId] = useState('');
  const [companyBooks, setCompanyBooks] = useState<{
    target: string;
    books: CompanyBook[];
    pagination: Pagination | null;
  }>({ target: '', books: [], pagination: null });
  const [booksLoading, setBooksLoading] = useState(false);
  const [booksError, setBooksError] = useState('');
  const lock = useRef(false);
  const active = useRef(true);
  const selectionEpoch = useRef(0);
  const listEpoch = useRef(0);
  const booksEpoch = useRef(0);
  const attachmentInput = useRef<HTMLInputElement>(null);
  const handoffKey = useRef<{ eventId: string; bookId: string; key: string } | null>(null);
  const createKey = useRef(0);
  const creationRequest = useRef<{ signature: string; key: string } | null>(null);
  const changeOrganizationBusy = useCallback(
    (value: boolean) => setBusy(value ? 'organization' : ''),
    [],
  );
  const changeIntelligenceBusy = useCallback((value: boolean) => setIntelligenceBusy(value), []);
  // The accounting workbench is paged; its current page is never an authority
  // for a selected business item's available books or existing draft mapping.
  const bookCompanyId = selected?.company_id ?? '';
  const bookPeriod = selected?.payload.occurred_on.slice(0, 7) ?? '';
  const booksTarget =
    selected && (selected.permissions.handoff || selected.mapping)
      ? `${session.scope_key}:${selected.id}:${selected.version}:${bookCompanyId}:${bookPeriod}`
      : '';
  const booksTargetRef = useRef(booksTarget);
  booksTargetRef.current = booksTarget;

  useEffect(() => {
    onBusyChange(!!busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      selectionEpoch.current += 1;
    };
  }, [session.scope_key]);
  const loadCompanyBooks = useCallback(
    async (append = false, signal?: AbortSignal) => {
      if (!booksTarget) return;
      const epoch = ++booksEpoch.current;
      const nextOffset =
        append && companyBooks.target === booksTarget && companyBooks.pagination
          ? companyBooks.pagination.offset + companyBooks.pagination.limit
          : 0;
      const current = () =>
        !signal?.aborted &&
        active.current &&
        booksEpoch.current === epoch &&
        booksTargetRef.current === booksTarget;
      setBooksLoading(true);
      setBooksError('');
      try {
        const query = new URLSearchParams({
          period: bookPeriod,
          limit: '100',
          offset: String(nextOffset),
        });
        const result = await api<CompanyBooksPage>(
          `/companies/${encoded(bookCompanyId)}/books?${query}`,
          session,
          { signal },
        );
        if (!current()) return;
        setCompanyBooks((previous) => {
          const rows =
            append && previous.target === booksTarget
              ? [...previous.books, ...result.books]
              : result.books;
          return {
            target: booksTarget,
            books: [...new Map(rows.map((book) => [book.id, book])).values()],
            pagination: result.pagination,
          };
        });
      } catch (failure) {
        if (!current()) return;
        setBooksError(errorMessage(failure));
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          setCompanyBooks({ target: booksTarget, books: [], pagination: null });
          setHandoffBookId('');
        }
      } finally {
        if (current()) setBooksLoading(false);
      }
    },
    [session, booksTarget, bookCompanyId, bookPeriod, companyBooks.target, companyBooks.pagination],
  );
  // Only selection/scope changes start this first page. A later page is an
  // explicit request, so a newly stored pagination cursor does not reset it.
  const loadCompanyBooksRef = useRef(loadCompanyBooks);
  loadCompanyBooksRef.current = loadCompanyBooks;
  useEffect(() => {
    const controller = new AbortController();
    booksEpoch.current += 1;
    setCompanyBooks({ target: booksTarget, books: [], pagination: null });
    setHandoffBookId('');
    setBooksError('');
    setBooksLoading(false);
    if (booksTarget) void loadCompanyBooksRef.current(false, controller.signal);
    return () => {
      controller.abort();
      booksEpoch.current += 1;
    };
  }, [booksTarget]);
  useEffect(() => {
    const controller = new AbortController();
    void api<BusinessContext>('/business/context', session, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setContext(data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(failure));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [session]);
  useEffect(() => {
    if (!context || defaultScopeSet.current) return;
    defaultScopeSet.current = true;
    if (initialEventId) return;
    const next = initialBusinessScope(context, initialCompanyId);
    setScope(next);
    setStatus(
      initialCompanyId
        ? ''
        : next === 'company'
          ? 'department_approved'
          : next === 'department'
            ? 'submitted'
            : '',
    );
  }, [context, initialEventId, initialCompanyId]);
  useEffect(() => {
    if (context && (initialIntakeId || initialNew)) {
      setScope('own');
      setStatus('');
      setIntakeOpen(true);
    }
  }, [context, initialIntakeId, initialNew]);

  const loadList = useCallback(
    async (signal?: AbortSignal) => {
      if (!context) return;
      const epoch = ++listEpoch.current;
      setLoading(true);
      setError('');
      const query = new URLSearchParams({ scope, limit: '50', offset: String(offset) });
      if (companyId) query.set('company_id', companyId);
      if (status) query.set('status', status);
      if (initialPeriod) query.set('period', initialPeriod);
      try {
        const result = await api<{ events: BusinessEvent[]; total: number }>(
          `/business/events?${query}`,
          session,
          { signal },
        );
        if (signal?.aborted || !active.current || epoch !== listEpoch.current) return;
        setEvents(result.events);
        setTotal(result.total);
      } catch (failure) {
        if (!signal?.aborted && active.current && epoch === listEpoch.current)
          setError(errorMessage(failure));
      } finally {
        if (!signal?.aborted && active.current && epoch === listEpoch.current) setLoading(false);
      }
    },
    [context, session, scope, companyId, initialPeriod, status, offset],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadList(controller.signal);
    return () => controller.abort();
  }, [loadList]);
  useEffect(() => {
    if (!initialEventId || !context) return;
    if (context.scopes.accounting) setScope('accounting');
    void openEvent(initialEventId);
  }, [initialEventId, context]);

  function changeScope(next: BusinessScope) {
    if (busy || next === scope) return;
    listEpoch.current += 1;
    selectionEpoch.current += 1;
    setSelected(null);
    setDetailLoading(false);
    setEvents([]);
    setScope(next);
    setStatus(
      next === 'company' ? 'department_approved' : next === 'department' ? 'submitted' : '',
    );
    setCompanyId('');
    setOffset(0);
    setError('');
    setNotice('');
  }
  function changeFilter(kind: 'company' | 'status', value: string) {
    if (busy || (kind === 'company' ? companyId : status) === value) return;
    listEpoch.current += 1;
    selectionEpoch.current += 1;
    setSelected(null);
    setDetailLoading(false);
    setEvents([]);
    setOffset(0);
    setError('');
    setNotice('');
    if (kind === 'company') setCompanyId(value);
    else setStatus(value);
  }
  async function openEvent(id: string) {
    if (busy) return;
    const epoch = ++selectionEpoch.current;
    setSelected(null);
    setDetailLoading(true);
    setError('');
    setNotice('');
    setReturnReason('');
    setHandoffBookId('');
    try {
      const result = await api<BusinessEvent>(`/business/events/${encoded(id)}`, session);
      if (active.current && epoch === selectionEpoch.current) setSelected(result);
    } catch (failure) {
      if (active.current && epoch === selectionEpoch.current) setError(errorMessage(failure));
    } finally {
      if (active.current && epoch === selectionEpoch.current) setDetailLoading(false);
    }
  }
  async function run(name: string, action: () => Promise<BusinessEvent | void>, success: string) {
    if (lock.current) return;
    lock.current = true;
    setBusy(name);
    setError('');
    setNotice('');
    const epoch = selectionEpoch.current;
    try {
      const result = await action();
      if (!active.current || epoch !== selectionEpoch.current) return;
      if (result) setSelected(result);
      setNotice(success);
      await loadList();
    } catch (failure) {
      if (
        !active.current ||
        epoch !== selectionEpoch.current ||
        (failure instanceof DOMException && failure.name === 'AbortError')
      )
        return;
      let message = errorMessage(failure);
      const uncertain =
        failure instanceof TypeError ||
        (failure instanceof ApiError && [409, 503].includes(failure.status));
      if (uncertain && name !== 'download') {
        if (selected && !(name === 'save' && formEvent === 'new')) {
          try {
            const latest = await api<BusinessEvent>(
              `/business/events/${encoded(selected.id)}`,
              session,
            );
            if (!active.current || epoch !== selectionEpoch.current) return;
            setSelected(latest);
            await loadList();
            message +=
              name === 'save'
                ? ' 已重新读取最新事项；请关闭表单，重新打开后核对再修改。'
                : ' 已重新读取最新事项，请核对当前状态与附件。';
          } catch {
            if (!active.current || epoch !== selectionEpoch.current) return;
            message += ' 最新状态暂未读取成功，请刷新列表并重新点选事项。';
          }
        } else {
          await loadList();
          message +=
            ' 保存结果未返回时，可关闭表单查看本人事项。同样内容再次保存会使用同一请求编号。';
        }
      }
      if (active.current && epoch === selectionEpoch.current) setError(message);
    } finally {
      if (active.current) {
        lock.current = false;
        setBusy('');
      }
    }
  }
  async function transition(
    action: 'submit' | 'department-approve' | 'finance-approve' | 'return',
  ) {
    if (!selected) return;
    await run(
      action,
      () =>
        api<BusinessEvent>(`/business/events/${encoded(selected.id)}/${action}`, session, {
          method: 'POST',
          body: {
            expected_version: selected.version,
            ...(action === 'return' ? { reason: returnReason.trim() } : {}),
          },
        }),
      action === 'submit'
        ? selected?.submission_mode === 'direct'
          ? '资料已提交，等待授权会计接收。'
          : '事项已提交，等待部门审批。'
        : action === 'department-approve'
          ? '部门审批已通过，交由企业财务核验。'
          : action === 'finance-approve'
            ? '财务核验已通过，可交接到账套。'
            : '事项已退回，申请人可修改后重新提交。',
    );
  }
  async function uploadAttachment(file?: File) {
    if (!file || !selected) return;
    const validation = validateAttachment(
      file,
      selected.attachments.length,
      selected.attachments.reduce((sum, attachment) => sum + attachment.size_bytes, 0),
    );
    if (validation) {
      setError(validation);
      return;
    }
    await run(
      'upload',
      async () => {
        const direct = await prepareDirectUpload(session, file, 'event_attachment');
        const data = attachmentForm(file, direct);
        return await api<BusinessEvent>(
          `/business/events/${encoded(selected.id)}/attachments?expected_version=${selected.version}`,
          session,
          { method: 'POST', body: data },
        );
      },
      '附件已上传并保存原始文件，请核对后提交。',
    );
    if (attachmentInput.current) attachmentInput.current.value = '';
  }
  async function downloadAttachment(id: string, filename: string) {
    await run(
      'download',
      async () => {
        const blob = await apiBlob(`/business/attachments/${encoded(id)}/content`, session);
        if (!active.current) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
      },
      '原始附件已下载。',
    );
  }
  const currentBooks = companyBooks.target === booksTarget ? companyBooks.books : [];
  const booksReady = companyBooks.target === booksTarget && companyBooks.pagination !== null;
  const currentBooksError = companyBooks.target === booksTarget ? booksError : '';
  const booksHaveMore = companyBooks.target === booksTarget && !!companyBooks.pagination?.has_more;
  const eligibleBooks = currentBooks.filter(
    (book) =>
      book.company_id === bookCompanyId &&
      book.permissions.edit &&
      book.period_status === 'open' &&
      book.period === bookPeriod,
  );
  const mappedBook = currentBooks.find((book) => book.id === selected?.mapping?.book_id);
  const visibleCompanies = context?.companies.filter((company) =>
    scope === 'own'
      ? company.can_submit
      : scope === 'department'
        ? company.approver_department_ids.length > 0
        : scope === 'company'
          ? company.can_finance
          : !!company.ledger_available,
  );
  async function handoff() {
    if (!selected || booksLoading || !eligibleBooks.some((book) => book.id === handoffBookId))
      return;
    if (handoffKey.current?.eventId !== selected.id || handoffKey.current.bookId !== handoffBookId)
      handoffKey.current = {
        eventId: selected.id,
        bookId: handoffBookId,
        key: crypto.randomUUID(),
      };
    const key = handoffKey.current.key;
    await run(
      'handoff',
      async () => {
        const result = await api<{ event: BusinessEvent }>(
          `/business/events/${encoded(selected.id)}/handoff`,
          session,
          {
            method: 'POST',
            body: {
              expected_version: selected.version,
              book_id: handoffBookId,
              idempotency_key: key,
            },
          },
        );
        return result.event;
      },
      '已生成凭证草稿，仍需会计独立复核后导出。',
    );
  }
  async function reloadContext() {
    const result = await api<BusinessContext>('/business/context', session);
    if (active.current) setContext(result);
  }
  async function refreshSelected() {
    if (!selected) return;
    const epoch = selectionEpoch.current;
    const result = await api<BusinessEvent>(`/business/events/${encoded(selected.id)}`, session);
    if (active.current && epoch === selectionEpoch.current) {
      setSelected(result);
      await loadList();
    }
  }

  return (
    <main className="business-workspace">
      <section className="page-heading">
        <div>
          <p className="eyebrow">BUSINESS, WITH EVIDENCE</p>
          <h1>从业务发生，到有据可记。</h1>
          <p className="page-description">员工录入，部门审批，财务核验，再交给会计复核。</p>
        </div>
        <div className="business-heading-actions">
          {context?.companies.some((company) => company.can_manage_approvers) && (
            <button
              className="button secondary"
              type="button"
              disabled={!!busy}
              onClick={() => setOrganizationOpen(true)}
            >
              <Building2 size={16} />
              部门与企业设置
            </button>
          )}
          <button
            className="button primary"
            type="button"
            disabled={!!busy || !context?.companies.some((company) => company.can_submit)}
            onClick={() => {
              createKey.current += 1;
              creationRequest.current = null;
              setIntakeOpen(true);
              setError('');
            }}
          >
            <Plus size={16} />
            录入业务事项
          </button>
        </div>
      </section>
      <section className="business-intro">
        <div>
          <Receipt size={22} />
          <p>
            每件事项，保留业务全貌。<span>3 个通用模板 · 原始附件 · 分级审批 · 可追溯凭证</span>
          </p>
        </div>
        <p>
          <ShieldCheck size={15} />
          仅显示当前账号获授权的范围
        </p>
      </section>
      {initialPeriod && (
        <p className="field-help" role="status">
          当前按 {initialPeriod} 的业务发生月份筛选。可从客户月度进度切换月份。
        </p>
      )}
      <div className={`business-grid ${selected || detailLoading ? 'mobile-detail' : ''}`}>
        <section className="panel business-list-panel">
          <div className="list-tabs">
            <div role="tablist" aria-label="业务事项范围">
              {(['own', 'department', 'company', 'accounting'] as BusinessScope[])
                .filter((value) => value === 'own' || context?.scopes[value])
                .map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="tab"
                    disabled={!!busy}
                    aria-selected={scope === value}
                    className={scope === value ? 'active' : ''}
                    onClick={() => changeScope(value)}
                  >
                    {scopeLabels[value]}
                  </button>
                ))}
            </div>
          </div>
          {events.some((event) => event.permissions.process_intelligence) && (
            <div className="business-batch-toolbar">
              <span>本页已上传附件可按企业批量识别</span>
              <button
                type="button"
                className="button secondary small"
                disabled={
                  !!busy ||
                  loading ||
                  !events.some(
                    (event) => event.permissions.process_intelligence && event.attachments.length,
                  )
                }
                onClick={() => setBatchOpen(true)}
              >
                <Receipt size={14} />
                批量识别本页票据
              </button>
            </div>
          )}
          <div className="business-filters">
            <label>
              <span>客户企业</span>
              <select
                aria-label="筛选业务事项企业"
                value={companyId}
                disabled={!!busy}
                onChange={(event) => changeFilter('company', event.target.value)}
              >
                <option value="">全部获授权企业</option>
                {visibleCompanies?.map((company) => (
                  <option value={company.id} key={company.id}>
                    {company.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>处理状态</span>
              <select
                aria-label="筛选业务事项状态"
                value={status}
                disabled={!!busy}
                onChange={(event) => changeFilter('status', event.target.value)}
              >
                <option value="">全部状态</option>
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {error && (
            <div className="inline-error" role="alert">
              <TriangleAlert size={16} />
              {error}
              <button
                className="text-button"
                type="button"
                disabled={!!busy}
                onClick={() => void loadList()}
              >
                刷新列表
              </button>
            </div>
          )}
          {notice && (
            <p className="business-notice" role="status">
              <CheckCircle2 size={16} />
              {notice}
            </p>
          )}
          {loading ? (
            <div className="loading-block">
              <Spinner />
              正在载入业务事项…
            </div>
          ) : events.length ? (
            <div className="business-event-list">
              {events.map((event) => (
                <button
                  key={event.id}
                  type="button"
                  className={`business-event-row ${selected?.id === event.id ? 'selected' : ''}`}
                  disabled={!!busy}
                  onClick={() => void openEvent(event.id)}
                >
                  <span className={`source-direction ${event.payload.direction}`}>
                    {event.payload.direction === 'income' ? (
                      <ArrowDownLeft size={19} />
                    ) : (
                      <ArrowUpRight size={19} />
                    )}
                  </span>
                  <span className="business-event-title">
                    <strong>{event.payload.description}</strong>
                    <span>
                      {event.company_name ??
                        context?.companies.find((company) => company.id === event.company_id)
                          ?.name}{' '}
                      · {event.payload.occurred_on}
                    </span>
                    <small>
                      {
                        BUSINESS_TEMPLATES.find((template) => template.id === event.template_id)
                          ?.name
                      }{' '}
                      · {event.applicant_name || '申请人'}
                    </small>
                  </span>
                  <span className="business-event-summary">
                    <strong>{formatMoney(event.payload.amount)}</strong>
                    <span className={`badge business-status ${event.status}`}>
                      {event.submission_mode === 'direct' && event.status === 'submitted'
                        ? '待会计接收'
                        : statusLabels[event.status]}
                    </span>
                  </span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="当前范围还没有业务事项"
              text={
                scope === 'own'
                  ? '从报销、采购 / 费用、日常收支中选择模板，上传原件、核对业务信息后即可提交。'
                  : '获授权范围内的事项进入对应审批阶段后，可在这里处理。'
              }
            />
          )}
          <div className="business-pagination">
            <span>
              {total} 件事项{total > 0 && ` · 第 ${Math.floor(offset / 50) + 1} 页`}
            </span>
            <div>
              <button
                className="text-button"
                type="button"
                disabled={!!busy || offset === 0 || loading}
                onClick={() => {
                  listEpoch.current += 1;
                  selectionEpoch.current += 1;
                  setSelected(null);
                  setDetailLoading(false);
                  setEvents([]);
                  setOffset((value) => Math.max(0, value - 50));
                }}
              >
                上一页
              </button>
              <button
                className="text-button"
                type="button"
                disabled={!!busy || offset + 50 >= total || loading}
                onClick={() => {
                  listEpoch.current += 1;
                  selectionEpoch.current += 1;
                  setSelected(null);
                  setDetailLoading(false);
                  setEvents([]);
                  setOffset((value) => value + 50);
                }}
              >
                下一页
              </button>
            </div>
          </div>
        </section>
        <aside className="panel business-detail-panel" aria-label="业务事项详情">
          <button
            type="button"
            className="business-mobile-back"
            disabled={!!busy}
            onClick={() => {
              selectionEpoch.current += 1;
              setSelected(null);
              setDetailLoading(false);
            }}
          >
            ← 返回事项列表
          </button>
          <div className="review-panel-header">
            <div className="review-heading">
              <span className="review-icon">
                <ClipboardCheck size={18} />
              </span>
              <div>
                <h2>事项与审批</h2>
                <p>查看完整业务依据和处理记录</p>
              </div>
            </div>
            <span className="review-step">RECORD</span>
          </div>
          {detailLoading ? (
            <div className="loading-block">
              <Spinner />
              正在读取事项…
            </div>
          ) : !selected ? (
            <Empty title="选择一件业务事项" text="申请信息、附件与审批依据将在这里展示。" />
          ) : (
            <div className="business-detail-content">
              <div className="business-detail-title">
                <span className={`badge business-status ${selected.status}`}>
                  {selected.submission_mode === 'direct' && selected.status === 'submitted'
                    ? '待会计接收'
                    : statusLabels[selected.status]}
                </span>
                <span>业务事项</span>
                <h3>{selected.payload.description}</h3>
                <p>
                  {selected.company_name ??
                    context?.companies.find((company) => company.id === selected.company_id)
                      ?.name}{' '}
                  · {selected.department_name || '未分配部门'} ·{' '}
                  {selected.applicant_name || '申请人'}
                </p>
              </div>
              <dl className="business-facts">
                <div>
                  <dt>发生日期</dt>
                  <dd>{selected.payload.occurred_on}</dd>
                </div>
                <div>
                  <dt>交易对方</dt>
                  <dd>{selected.payload.counterparty}</dd>
                </div>
                <div>
                  <dt>{selected.payload.direction === 'income' ? '收入金额' : '支出金额'}</dt>
                  <dd className="business-money">¥ {formatMoney(selected.payload.amount)}</dd>
                </div>
                <div>
                  <dt>收付状态</dt>
                  <dd>{selected.payload.payment_status === 'paid' ? '已收付' : '企业待付款'}</dd>
                </div>
                <div>
                  <dt>事项分类</dt>
                  <dd>{BUSINESS_CATEGORIES[selected.payload.category]}</dd>
                </div>
                {selected.payload.purpose && (
                  <div>
                    <dt>业务用途</dt>
                    <dd>{selected.payload.purpose}</dd>
                  </div>
                )}
                {selected.payload.project && (
                  <div>
                    <dt>所属项目</dt>
                    <dd>{selected.payload.project}</dd>
                  </div>
                )}
              </dl>
              <div className="business-next-step">
                {selected.status === 'draft' || selected.status === 'returned'
                  ? selected.submission_mode === 'direct'
                    ? '下一步：核对资料后提交给会计。'
                    : '下一步：申请人核对事项与附件后提交部门审批。'
                  : selected.status === 'submitted'
                    ? '下一步：由获授权的部门审批人核对业务并审批。'
                    : selected.status === 'department_approved'
                      ? '下一步：企业财务核对金额、票据和付款状态。'
                      : selected.status === 'finance_approved'
                        ? '下一步：企业财务将事项交接到同企业、同期间账套。'
                        : '下一步：会计核对凭据和科目后审核，再导出交付。'}
              </div>
              <div className="business-evidence">
                <h4>
                  <Paperclip size={15} />
                  原始附件<span>{selected.attachments.length} / 10</span>
                </h4>
                {selected.attachments.map((attachment) => (
                  <div className="business-attachment" key={attachment.id}>
                    <FileText size={16} />
                    <div>
                      <strong>{attachment.filename}</strong>
                      <span>
                        {(attachment.size_bytes / 1024).toFixed(0)} KB · 原件留存
                        {attachment.superseded ? ' · 已被补件替代，原件留存' : ''}
                      </span>
                    </div>
                    <button
                      className="icon-button"
                      type="button"
                      disabled={!!busy}
                      aria-label={`下载附件 ${attachment.filename}`}
                      onClick={() => void downloadAttachment(attachment.id, attachment.filename)}
                    >
                      <Download size={16} />
                    </button>
                  </div>
                ))}
                {!selected.attachments.length && (
                  <p className="field-help">至少上传 1 个原始附件后才能提交审批。</p>
                )}
                {selected.permissions.edit && (
                  <>
                    <input
                      ref={attachmentInput}
                      type="file"
                      accept=".xml,application/xml,text/xml,application/pdf,image/png,image/jpeg,image/webp"
                      aria-label="上传业务事项附件"
                      hidden
                      onChange={(event) => void uploadAttachment(event.target.files?.[0])}
                      disabled={!!busy}
                    />
                    <button
                      className="button secondary small"
                      type="button"
                      disabled={!!busy || selected.attachments.length >= 10}
                      onClick={() => attachmentInput.current?.click()}
                    >
                      {busy === 'upload' ? <Spinner /> : <Upload size={14} />}上传原始附件
                    </button>
                    <p className="field-help">
                      数电票 XML / PDF / PNG / JPEG / WebP，每个不超过 {directUploadLimitLabel()}
                      ，总计不超过 50 MB。支持票据识别与交叉核对，审批人仍须核对原件。
                    </p>
                  </>
                )}
              </div>
              <IntelligencePanel
                onOpenBatch={onOpenBatch}
                key={selected.id}
                session={session}
                eventId={selected.id}
                event={selected}
                externalBusy={!!operationBusy}
                onBusyChange={changeIntelligenceBusy}
                onChanged={refreshSelected}
                onDismiss={() => {
                  selectionEpoch.current += 1;
                  setSelected(null);
                }}
              />
              {!!selected.actions.length && (
                <div className="business-history">
                  <h4>审批记录</h4>
                  {selected.actions.map((action, index) => (
                    <div key={index}>
                      <span className="small-dot" />
                      <p>
                        <strong>
                          {actionLabels[action.stage] ?? action.stage}
                          {action.decision === 'return'
                            ? ' · 退回'
                            : action.decision === 'approve'
                              ? ' · 通过'
                              : ''}
                        </strong>
                        {action.reason && <span>{action.reason}</span>}
                        <small>
                          {action.created_at &&
                            new Date(action.created_at).toLocaleString('zh-CN', {
                              timeZone: 'Asia/Shanghai',
                              hour12: false,
                            })}
                          {action.submitted_version
                            ? ` · 审核提交版本 ${action.submitted_version}`
                            : ''}
                        </small>
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {!!selected.versions?.length && (
                <details className="business-versions">
                  <summary>查看提交历史与原件校验</summary>
                  {selected.versions.map((version) => (
                    <details key={version.event_version}>
                      <summary>
                        提交版本 {version.event_version}
                        <span>
                          {version.payload.occurred_on} · ¥ {formatMoney(version.payload.amount)}
                        </span>
                      </summary>
                      <p>
                        {version.payload.description}
                        <br />
                        {version.payload.counterparty}
                        <br />
                        {version.payload.purpose || '未补充用途'}
                      </p>
                      <ul>
                        {version.attachment_snapshot.map((attachment) => (
                          <li key={attachment.id}>
                            {attachment.filename}
                            <span>SHA-256 {attachment.sha256}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </details>
              )}
              <div className="business-actions">
                {onOpenCase &&
                  (selected.permissions.finance_approve ||
                    selected.permissions.handoff ||
                    (selected.mapping && mappedBook?.permissions.review)) && (
                    <button
                      type="button"
                      className="button secondary"
                      disabled={!!busy}
                      onClick={() =>
                        onOpenCase({ eventId: selected.id, title: selected.payload.description })
                      }
                    >
                      <TriangleAlert size={14} />
                      指派异常 / 请求补件
                    </button>
                  )}
                {booksTarget && (
                  <div className="business-handoff">
                    {(booksLoading || (!booksReady && !currentBooksError)) && (
                      <p className="field-help">
                        <Spinner />
                        正在读取本企业、本期间的可访问账套…
                      </p>
                    )}
                    {currentBooksError && (
                      <p className="inline-error" role="alert">
                        {currentBooksError}
                        <button
                          type="button"
                          className="text-button"
                          disabled={!!busy || booksLoading}
                          onClick={() => void loadCompanyBooks()}
                        >
                          重新读取事项账套
                        </button>
                      </p>
                    )}
                    {booksHaveMore && (
                      <button
                        type="button"
                        className="button secondary small"
                        disabled={!!busy || booksLoading}
                        onClick={() => void loadCompanyBooks(true)}
                      >
                        读取更多事项账套
                      </button>
                    )}
                  </div>
                )}
                {selected.permissions.edit && (
                  <button
                    type="button"
                    className="button secondary"
                    disabled={!!busy}
                    onClick={() => setFormEvent(selected)}
                  >
                    修改事项
                  </button>
                )}
                {selected.permissions.submit && (
                  <button
                    type="button"
                    className="button primary"
                    disabled={!!busy || !selected.attachments.length}
                    onClick={() => void transition('submit')}
                  >
                    {busy === 'submit' ? <Spinner /> : <Send size={14} />}提交审批
                  </button>
                )}
                {selected.permissions.department_approve && (
                  <button
                    type="button"
                    className="button primary"
                    disabled={!!busy}
                    onClick={() => void transition('department-approve')}
                  >
                    {busy === 'department-approve' ? <Spinner /> : <CheckCircle2 size={14} />}
                    部门审批通过
                  </button>
                )}
                {selected.permissions.finance_approve && (
                  <button
                    type="button"
                    className="button primary"
                    disabled={!!busy}
                    onClick={() => void transition('finance-approve')}
                  >
                    {busy === 'finance-approve' ? <Spinner /> : <CheckCircle2 size={14} />}
                    财务核验通过
                  </button>
                )}
                {selected.permissions.return && (
                  <div className="business-return">
                    <label className="field-label" htmlFor="return-reason">
                      退回原因
                    </label>
                    <textarea
                      id="return-reason"
                      value={returnReason}
                      onChange={(event) => setReturnReason(event.target.value)}
                      rows={2}
                      maxLength={1000}
                      disabled={!!busy}
                      placeholder="说明需要补充或修正的业务信息"
                    />
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={!!busy || !returnReason.trim()}
                      onClick={() => void transition('return')}
                    >
                      <RotateCcw size={14} />
                      退回修改
                    </button>
                  </div>
                )}
                {selected.permissions.handoff && (
                  <div className="business-handoff">
                    <label className="field-label" htmlFor="handoff-book">
                      交接到账套
                    </label>
                    <select
                      id="handoff-book"
                      value={handoffBookId}
                      disabled={!!busy || booksLoading || !booksReady}
                      onChange={(event) => setHandoffBookId(event.target.value)}
                    >
                      <option value="">选择同企业、同期间的开放账套</option>
                      {eligibleBooks.map((book) => (
                        <option key={book.id} value={book.id}>
                          {book.name} · {book.period}
                        </option>
                      ))}
                    </select>
                    {booksReady && !booksLoading && !currentBooksError && !eligibleBooks.length && (
                      <p className="field-help">
                        {booksHaveMore
                          ? '本页没有可交接账套，可继续读取本企业该期间的账套。'
                          : '当前没有该企业在事项发生期间的开放且可编辑账套，请联系管理员授权。'}
                      </p>
                    )}
                    <button
                      className="button primary"
                      type="button"
                      disabled={
                        !!busy ||
                        booksLoading ||
                        !eligibleBooks.some((book) => book.id === handoffBookId)
                      }
                      onClick={() => void handoff()}
                    >
                      {busy === 'handoff' ? <Spinner /> : <ArrowRight size={15} />}生成凭证草稿
                    </button>
                    <p className="field-help">交接后仍需会计核对科目、金额及凭据，再审核导出。</p>
                  </div>
                )}
                {selected.mapping && mappedBook && onOpenRevision && (
                  <button
                    className="button secondary"
                    type="button"
                    disabled={!!busy}
                    onClick={() => onOpenRevision(selected.id)}
                  >
                    申请事实修订
                  </button>
                )}
                {selected.mapping && mappedBook && (
                  <button
                    className="button secondary"
                    type="button"
                    disabled={!!busy}
                    onClick={() =>
                      onOpenDraft(selected.mapping!.book_id, selected.mapping!.draft_id)
                    }
                  >
                    <ClipboardCheck size={15} />
                    打开会计复核
                  </button>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
      <footer className="workspace-footer">
        <span>衡账 · 企业业务协同</span>
        <span>本人录入 / 授权部门审批 / 企业财务核验 / 会计独立复核</span>
      </footer>
      {batchOpen && (
        <QueueBatchDialog
          session={session}
          events={events}
          initialCompanyId={companyId}
          onClose={() => setBatchOpen(false)}
          onBusyChange={changeOrganizationBusy}
          onCreated={(batch) => {
            setBatchOpen(false);
            setNotice('识别任务已保存，可在票据处理任务查看进度。');
            onOpenBatch?.(batch.id);
          }}
        />
      )}
      {organizationOpen && context && (
        <OrganizationDialog busy={!!busy} onClose={() => setOrganizationOpen(false)}>
          <BusinessOrganization
            session={session}
            companies={context.companies.filter((company) => company.can_manage_approvers)}
            onChanged={reloadContext}
            onBusyChange={changeOrganizationBusy}
          />
        </OrganizationDialog>
      )}
      {intakeOpen && context && (
        <IntakeWizard
          session={session}
          context={context}
          initialDraftId={initialIntakeId}
          initialTemplate={initialTemplate}
          onBusyChange={changeOrganizationBusy}
          onClose={() => setIntakeOpen(false)}
          onCompleted={(event) => {
            setIntakeOpen(false);
            setSelected(event);
            setScope('own');
            setStatus('');
            setNotice(
              event.status === 'submitted'
                ? '事项与附件已提交，等待部门审批。'
                : '事项与附件已保存，可核对后提交。',
            );
            void loadList();
          }}
        />
      )}
      {formEvent && context && (
        <BusinessForm
          key={typeof formEvent === 'string' ? `new-${createKey.current}` : formEvent.id}
          context={context}
          event={formEvent === 'new' ? undefined : formEvent}
          busy={!!busy}
          error={error}
          onClose={() => setFormEvent(null)}
          onSave={async (company, department, template, payload) => {
            await run(
              'save',
              async () => {
                const body =
                  typeof formEvent === 'string'
                    ? {
                        company_id: company,
                        department_id: department || null,
                        template_id: template,
                        payload,
                      }
                    : { expected_version: formEvent.version, payload };
                if (typeof formEvent === 'string') {
                  const signature = JSON.stringify(body);
                  if (creationRequest.current?.signature !== signature)
                    creationRequest.current = { signature, key: crypto.randomUUID() };
                }
                const result = await api<BusinessEvent>(
                  typeof formEvent === 'string'
                    ? '/business/events'
                    : `/business/events/${encoded(formEvent.id)}`,
                  session,
                  {
                    method: typeof formEvent === 'string' ? 'POST' : 'PATCH',
                    body: {
                      ...body,
                      ...(typeof formEvent === 'string'
                        ? { idempotency_key: creationRequest.current!.key }
                        : {}),
                    },
                  },
                );
                creationRequest.current = null;
                setFormEvent(null);
                return result;
              },
              '事项已保存，请上传附件后提交审批。',
            );
          }}
        />
      )}
    </main>
  );
}

function BusinessForm({
  context,
  event,
  busy,
  error,
  onClose,
  onSave,
}: {
  context: BusinessContext;
  event?: BusinessEvent;
  busy: boolean;
  error: string;
  onClose: () => void;
  onSave: (
    companyId: string,
    departmentId: string,
    template: BusinessTemplate,
    payload: BusinessPayload,
  ) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [template, setTemplate] = useState<BusinessTemplate>(event?.template_id ?? 'reimbursement');
  const [payload, setPayload] = useState<BusinessPayload>(
    event?.payload ?? initialBusinessPayload('reimbursement'),
  );
  const companies = context.companies.filter((company) => company.can_submit);
  const [companyId, setCompanyId] = useState(event?.company_id ?? companies[0]?.id ?? '');
  const company = context.companies.find((value) => value.id === companyId);
  const [departmentId, setDepartmentId] = useState(
    event?.department_id ?? company?.department_id ?? '',
  );
  const [validation, setValidation] = useState('');
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  function field<K extends keyof BusinessPayload>(key: K, value: BusinessPayload[K]) {
    setPayload((current) => ({ ...current, [key]: value }));
    setValidation('');
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const message = validateBusinessPayload(template, payload);
    if (message) {
      setValidation(message);
      return;
    }
    if (!companyId || (!departmentId && company?.submission_policy?.mode !== 'direct')) {
      setValidation('请选择客户企业和业务所属部门。');
      return;
    }
    await onSave(companyId, departmentId, template, normalizeBusinessPayload(payload));
  }
  return (
    <dialog
      ref={dialog}
      className="modal business-form-modal"
      aria-labelledby="business-form-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="business-form-title">{event ? '修改业务事项' : '录入业务事项'}</h2>
          <p>保存后上传原始附件，再提交审批。</p>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭业务事项表单"
        >
          <X size={20} />
        </button>
      </div>
      <form onSubmit={(e) => void save(e)}>
        <div className="business-form-content">
          <div className="business-template-grid" role="group" aria-label="选择业务模板">
            {BUSINESS_TEMPLATES.map((item) => (
              <button
                key={item.id}
                type="button"
                className={template === item.id ? 'selected' : ''}
                disabled={busy || !!event}
                aria-pressed={template === item.id}
                onClick={() => {
                  setTemplate(item.id);
                  setPayload((current) => ({
                    ...current,
                    direction: item.id === 'daily_cashflow' ? current.direction : 'expense',
                    payment_status:
                      item.id === 'daily_cashflow'
                        ? 'paid'
                        : item.id === 'reimbursement'
                          ? 'unpaid'
                          : current.payment_status,
                  }));
                  setValidation('');
                }}
              >
                {item.id === 'reimbursement' ? (
                  <Receipt size={19} />
                ) : item.id === 'procurement_expense' ? (
                  <ShoppingBag size={19} />
                ) : (
                  <Building2 size={19} />
                )}
                <strong>{item.name}</strong>
                <span>{item.description}</span>
              </button>
            ))}
          </div>
          <div className="business-form-grid">
            <label>
              <span className="field-label">客户企业</span>
              <select
                aria-label="事项客户企业"
                value={companyId}
                required
                disabled={busy || !!event}
                onChange={(e) => {
                  setCompanyId(e.target.value);
                  setDepartmentId(
                    context.companies.find((value) => value.id === e.target.value)?.department_id ??
                      '',
                  );
                }}
              >
                {companies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="field-label">业务所属部门</span>
              <select
                aria-label="事项所属部门"
                value={departmentId}
                required={company?.submission_policy?.mode !== 'direct'}
                disabled={
                  busy || !!event || (company?.role === 'employee' && !!company.department_id)
                }
                onChange={(e) => setDepartmentId(e.target.value)}
              >
                <option value="">选择部门</option>
                {company?.departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="field-label">发生日期</span>
              <input
                type="date"
                min="2000-01-01"
                max="2099-12-31"
                value={payload.occurred_on}
                required
                disabled={busy}
                onChange={(e) => field('occurred_on', e.target.value)}
              />
            </label>
            <label>
              <span className="field-label">金额（人民币元）</span>
              <input
                inputMode="decimal"
                aria-label="事项金额"
                value={payload.amount}
                required
                disabled={busy}
                placeholder="例如 128.50"
                onChange={(e) => field('amount', e.target.value)}
              />
            </label>
            <label className="wide-field">
              <span className="field-label">事项摘要</span>
              <input
                aria-label="事项摘要"
                value={payload.description}
                required
                maxLength={500}
                disabled={busy}
                placeholder="例如：10 月上海客户拜访交通费"
                onChange={(e) => field('description', e.target.value)}
              />
            </label>
            <label className="wide-field">
              <span className="field-label">交易对方</span>
              <input
                aria-label="交易对方"
                value={payload.counterparty}
                required
                maxLength={200}
                disabled={busy}
                placeholder="供应商、客户或实际收款人"
                onChange={(e) => field('counterparty', e.target.value)}
              />
            </label>
            <label>
              <span className="field-label">事项分类</span>
              <select
                aria-label="事项分类"
                value={payload.category}
                disabled={busy}
                onChange={(e) => field('category', e.target.value as BusinessPayload['category'])}
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
                  value={payload.direction}
                  disabled={busy}
                  onChange={(e) =>
                    field('direction', e.target.value as BusinessPayload['direction'])
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
                  value={payload.payment_status}
                  disabled={busy || template === 'reimbursement'}
                  onChange={(e) =>
                    field('payment_status', e.target.value as BusinessPayload['payment_status'])
                  }
                >
                  <option value="unpaid">待企业付款</option>
                  {template === 'procurement_expense' && <option value="paid">已付款</option>}
                </select>
              </label>
            )}
            <label className="wide-field">
              <span className="field-label">业务用途</span>
              <textarea
                aria-label="业务用途"
                value={payload.purpose}
                maxLength={1000}
                rows={2}
                disabled={busy}
                placeholder="补充与业务相关的用途，便于审批人核对"
                onChange={(e) => field('purpose', e.target.value)}
              />
            </label>
            <label className="wide-field">
              <span className="field-label">所属项目（可选）</span>
              <input
                aria-label="所属项目"
                value={payload.project}
                maxLength={200}
                disabled={busy}
                onChange={(e) => field('project', e.target.value)}
              />
            </label>
          </div>
          {(validation || error) && (
            <p className="form-error" role="alert">
              <TriangleAlert size={16} />
              {validation || error}
            </p>
          )}
          <p className="field-help">
            {template === 'reimbursement'
              ? '员工垫付报销记录为待企业付款的支出，财务核验后生成应付款草稿。'
              : template === 'daily_cashflow'
                ? '日常收支仅记录已经实际收付的事项。'
                : '采购 / 费用可记录已支付费用或待付款事项。'}{' '}
            所有草稿仍由会计复核。
          </p>
        </div>
        <div className="modal-footer">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="submit" className="button primary" disabled={busy || !companyId}>
            {busy ? <Spinner /> : <FileText size={15} />}保存事项
          </button>
        </div>
      </form>
    </dialog>
  );
}

function OrganizationDialog({
  busy,
  onClose,
  children,
}: {
  busy: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal access-modal"
      aria-labelledby="business-organization-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="business-organization-title">部门与企业设置</h2>
          <p>管理获授权企业的部门、审批职责与票据处理设置。</p>
        </div>
        <button
          className="icon-button"
          type="button"
          disabled={busy}
          onClick={onClose}
          aria-label="关闭部门审批配置"
        >
          <X size={20} />
        </button>
      </div>
      <div className="access-content">{children}</div>
      <div className="modal-footer">
        <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
          完成
        </button>
      </div>
    </dialog>
  );
}
