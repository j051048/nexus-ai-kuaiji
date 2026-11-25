import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowUp,
  BookOpen,
  Building2,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileSearch,
  FileText,
  History,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { ExecutionRecordPanel } from './ExecutionRecordPanel';
import { AssistantDeliveryDetails } from './AssistantDeliveryDetails';
import { assistantDeliveryTarget } from './assistant-delivery-target';
import { useAssistantTurnEvents } from './useAssistantTurnEvents';
import {
  assistantAllowedProposals,
  assistantBusinessStatus,
  assistantCitations,
  assistantDetailMatches,
  assistantErrorCodeLabel,
  assistantPlanConfirmable,
  assistantPlanMatches,
  assistantQuestion,
  assistantReceiptProgress,
  assistantTaskLabels,
  assistantTurnActive,
  assistantTurnLabels,
} from './assistant-helpers';
import type {
  AssistantActionPage,
  AssistantActionPlan,
  AssistantActionReceipt,
  AssistantContext,
  AssistantConversation,
  AssistantConversationDetail,
  AssistantConversationPage,
  AssistantConversationProgress,
  AssistantConversationWorkspace,
  AssistantEvidence,
  AssistantInitialContext,
  AssistantMessages,
  AssistantScopeOptions,
  AssistantTaskKind,
  AssistantTurn,
} from './assistant-types';
import type { BookDetail, Company, Pagination, Session } from './types';
import type { PresentationRole } from './workspace-types';
import { fscRoleProfile } from './fsc-shell';
import { isCompositionKey } from './keyboard';
import './global-assistant.css';

export interface GlobalAccountingAssistantProps {
  session: Session;
  initialContext?: AssistantInitialContext;
  /** 当前账号岗位视角，用于展示权限边界与角色专属快捷提问 */
  role?: PresentationRole;
  /** 是否处于纯手工/离线保底模式 */
  manualMode?: boolean;
  /** 顶栏全局选中的子公司，变化时自动同步聊天范围 */
  activeCompanyId?: string;
  onClose: () => void;
  onNavigateEvidence?: (evidence: AssistantEvidence) => void;
  onNavigateContext?: (context: AssistantContext) => void;
  onOpenTask?: (receipt: AssistantActionReceipt) => void;
  embedded?: boolean;
}
type Loaded<T> = { key: string; value: T };
type DetailRead = { key: string; controller: AbortController; promise: Promise<void> };
function encode(value: string) {
  return encodeURIComponent(value);
}
function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';
}
function PlanItems({ items }: { items: AssistantActionPlan['items'] }) {
  return (
    <>
      <ul>
        {items.slice(0, 5).map((item, index) => (
          <li key={`${index}:${item.title}`}>{item.title}</li>
        ))}
      </ul>
      {items.length > 5 && (
        <details className="ga-plan-full-list">
          <summary>查看其余 {items.length - 5} 项资料</summary>
          <ul>
            {items.slice(5).map((item, index) => (
              <li key={`${index}:${item.title}`}>{item.title}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/** History lives in the database. The drawer retains no enterprise data in browser storage. */
export function GlobalAccountingAssistant({
  session,
  initialContext,
  role = 'accountant',
  manualMode = false,
  activeCompanyId,
  onClose,
  onNavigateEvidence,
  onNavigateContext,
  onOpenTask,
  embedded = false,
}: GlobalAccountingAssistantProps) {
  const [companies, setCompanies] = useState<Company[]>([]),
    [companyId, setCompanyId] = useState(initialContext?.companyId ?? activeCompanyId ?? '');
  const [conversationId, setConversationId] = useState<string | null>(null),
    [showHistory, setShowHistory] = useState(false),
    [creating, setCreating] = useState(false);
  const [scopeOptions, setScopeOptions] = useState<Loaded<AssistantScopeOptions> | null>(null),
    [conversations, setConversations] = useState<Loaded<AssistantConversationPage> | null>(null);
  const [detail, setDetail] = useState<Loaded<AssistantConversationDetail> | null>(null),
    [messages, setMessages] = useState<Loaded<AssistantMessages> | null>(null);
  const [bookIds, setBookIds] = useState<string[]>(
      initialContext?.bookId ? [initialContext.bookId] : [],
    ),
    [eventIds, setEventIds] = useState<string[]>(
      initialContext?.eventId ? [initialContext.eventId] : [],
    );
  const [eventQuery, setEventQuery] = useState(''),
    [eventOffset, setEventOffset] = useState(0),
    [historyOffset, setHistoryOffset] = useState(0),
    [messageOffset, setMessageOffset] = useState(0),
    [planOffset, setPlanOffset] = useState(0),
    [actionPagination, setActionPagination] = useState<Pagination | null>(null);
  const [title, setTitle] = useState(''),
    [question, setQuestion] = useState(''),
    [pendingQuestion, setPendingQuestion] = useState<{ text: string; at: string } | null>(null),
    [toolsOpen, setToolsOpen] = useState(false),
    [loading, setLoading] = useState(false),
    [scopeLoading, setScopeLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const optimisticConversationId = useRef<string | null>(null);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [revoked, setRevoked] = useState(false),
    [plans, setPlans] = useState<AssistantActionPlan[]>([]);
  const dialog = useRef<HTMLDivElement>(null),
    closeButton = useRef<HTMLButtonElement>(null),
    transcript = useRef<HTMLDivElement>(null),
    questionInput = useRef<HTMLTextAreaElement>(null);
  const scopeKey = `${session.scope_key}:${session.user_id}:${companyId}`,
    detailKey = `${scopeKey}:${conversationId ?? ''}`;
  const liveScope = useRef(scopeKey),
    liveDetail = useRef(detailKey);
  liveScope.current = scopeKey;
  liveDetail.current = detailKey;
  const epoch = useRef(0),
    bootEpoch = useRef(0),
    detailEpoch = useRef(0),
    detailRead = useRef<DetailRead | null>(null),
    scopeController = useRef(new AbortController()),
    mutationBusy = useRef(false),
    closeRef = useRef(onClose);
  closeRef.current = onClose;
  const requestKeys = useRef(new Map<string, { signature: string; id: string }>());
  const progressRevision = useRef<string | null>(null);
  const invalidateDetailRead = useCallback(() => {
    detailEpoch.current++;
    detailRead.current?.controller.abort();
    detailRead.current = null;
  }, []);
  useEffect(() => invalidateDetailRead, [invalidateDetailRead]);
  const closing = useRef(false);
  const closeAssistant = useCallback(() => {
    if (!closing.current) {
      closing.current = true;
      invalidateDetailRead();
      closeRef.current();
    }
  }, [invalidateDetailRead]);
  const current = detail?.key === detailKey ? detail.value : null;
  const currentMessages = messages?.key === detailKey ? messages.value : null;
  const options = scopeOptions?.key === scopeKey ? scopeOptions.value : null;
  const history = conversations?.key === scopeKey ? conversations.value : null;
  const activeTurn = current?.turns.find((turn) => assistantTurnActive(turn.status));
  const progressTurn = messageOffset === 0 ? (activeTurn ?? current?.turns[0]) : undefined;
  const eventTurnId = progressTurn?.events_available === true ? progressTurn.id : undefined;
  const activeTask = plans.some((plan) =>
    plan.receipt?.jobs.some((job) =>
      ['queued', 'running', 'retry_wait', 'waiting'].includes(job.status),
    ),
  );
  const modelAvailable = manualMode
    ? false
    : current
      ? (current.model_available ??
        current.context.model_available ??
        options?.model_available ??
        false)
      : (options?.model_available ?? false);
  const companyAIEnabled =
    current?.company_enabled ?? current?.context.company_enabled ?? options?.company_enabled;
  const supplierConfigured =
    current?.supplier_configured ??
    current?.context.supplier_configured ??
    options?.supplier_configured;
  const modelUnavailableNotice = manualMode
    ? '当前已切换为纯手工办理模式，智能问答已暂停。左侧所有填单、制单、复核与结账功能全部纯手工正常可用。'
    : companyAIEnabled === false
      ? '该子公司智能问答已由管理员暂停，历史会话与左侧纯手工账务流程全部继续可用。'
      : current?.notice ||
        current?.context.notice ||
        options?.notice ||
        '云端智能问答暂未配置或当前离线，左侧填单、做凭证、审单与月结均可纯手工正常办理。';
  const modelUnavailablePlaceholder = manualMode
    ? '当前为纯手工模式，可点击顶栏恢复智能在线'
    : companyAIEnabled === false
      ? '该子公司智能问答已暂停，左侧手工流程照常可用'
      : supplierConfigured === false
        ? '云端问答暂离线，左侧手工账务照常可用'
        : modelUnavailableNotice;
  const companyName =
    current?.context.company_name ||
    options?.company_name ||
    companies.find((company) => company.id === companyId)?.name ||
    '';
  const roleMeta = fscRoleProfile(role, {
    companyName: companyName || undefined,
    companyCount: companies.length,
  });

  useLayoutEffect(() => {
    if (embedded) return;
    const before = document.activeElement as HTMLElement | null,
      overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeButton.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      if (before?.isConnected) before.focus();
    };
  }, [embedded]);
  useEffect(() => {
    if (embedded) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        if (!event.repeat) closeAssistant();
        return;
      }
      if (event.key !== 'Tab' || !dialog.current) return;
      const elements = Array.from(
        dialog.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]',
        ),
      ).filter((item) => item.getClientRects().length);
      const first = elements[0],
        last = elements[elements.length - 1],
        inside = dialog.current.contains(document.activeElement);
      if (!inside) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
    };
  }, [closeAssistant, embedded]);
  // A history row or the create button can disappear during a view transition.
  // Retain the keyboard inside the drawer without moving focus on polling.
  useLayoutEffect(() => {
    if (embedded) return;
    if (dialog.current && !dialog.current.contains(document.activeElement))
      closeButton.current?.focus();
  }, [embedded, showHistory, creating, conversationId, loading]);
  const clearSensitive = useCallback(() => {
    epoch.current++;
    bootEpoch.current++;
    invalidateDetailRead();
    scopeController.current.abort();
    scopeController.current = new AbortController();
    mutationBusy.current = false;
    setCompanies([]);
    setScopeOptions(null);
    setConversations(null);
    setDetail(null);
    setMessages(null);
    setPlans([]);
    setActionPagination(null);
    setQuestion('');
    setPendingQuestion(null);
    setToolsOpen(false);
    optimisticConversationId.current = null;
    setTitle('');
    setBookIds([]);
    setEventIds([]);
    setConversationId(null);
    setLoading(false);
    setScopeLoading(false);
    setBusy(false);
    setRevoked(true);
    requestKeys.current.clear();
    progressRevision.current = null;
  }, [invalidateDetailRead]);
  useEffect(() => {
    const revoke = () => {
      clearSensitive();
      setError('当前授权已更新，会话与资料已清空。请关闭助手，返回工作区重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [clearSensitive]);
  useEffect(() => {
    clearSensitive();
    setRevoked(false);
    setError('');
    setCreating(false);
    setShowHistory(false);
    const preferredCompany = initialContext?.companyId || activeCompanyId || '';
    setCompanyId(preferredCompany);
    setBookIds(initialContext?.bookId ? [initialContext.bookId] : []);
    setEventIds(initialContext?.eventId ? [initialContext.eventId] : []);
    const controller = new AbortController(),
      generation = ++bootEpoch.current;
    void (async () => {
      try {
        const value = await api<{ companies: Company[] }>('/companies', session, {
          signal: controller.signal,
        });
        if (controller.signal.aborted || generation !== bootEpoch.current) return;
        setCompanies(value.companies);
        if (!preferredCompany && initialContext?.bookId) {
          const book = await api<BookDetail>(`/books/${encode(initialContext.bookId)}`, session, {
            signal: controller.signal,
          });
          if (
            !controller.signal.aborted &&
            generation === bootEpoch.current &&
            book.book.company_id &&
            value.companies.some((company) => company.id === book.book.company_id)
          ) {
            setCompanyId(book.book.company_id);
            return;
          }
        }
        if (preferredCompany && value.companies.some((c) => c.id === preferredCompany)) {
          setCompanyId(preferredCompany);
        } else if (value.companies.length > 0) {
          setCompanyId(value.companies[0].id);
        }
      } catch (failure) {
        if (!controller.signal.aborted && generation === bootEpoch.current) {
          if (failure instanceof ApiError && [401, 403].includes(failure.status)) clearSensitive();
          setError(errorMessage(failure));
        }
      }
    })();
    return () => {
      controller.abort();
      bootEpoch.current++;
      scopeController.current.abort();
    };
  }, [
    session.scope_key,
    session.user_id,
    initialContext?.companyId,
    initialContext?.bookId,
    initialContext?.draftId,
    initialContext?.eventId,
    clearSensitive,
  ]);
  useEffect(() => {
    if (!activeCompanyId || activeCompanyId === companyId || revoked) return;
    if (companies.some((company) => company.id === activeCompanyId)) {
      setCompanyId(activeCompanyId);
      setCreating(false);
      setShowHistory(false);
    }
  }, [activeCompanyId, companies, companyId, revoked]);
  useEffect(() => {
    scopeController.current.abort();
    scopeController.current = new AbortController();
    epoch.current++;
    invalidateDetailRead();
    setScopeOptions(null);
    setConversations(null);
    setDetail(null);
    setMessages(null);
    setPlans([]);
    setActionPagination(null);
    setConversationId(null);
    setQuestion('');
    setPendingQuestion(null);
    setToolsOpen(false);
    optimisticConversationId.current = null;
    setNotice('');
    setHistoryOffset(0);
    setMessageOffset(0);
    setPlanOffset(0);
    mutationBusy.current = false;
    setBusy(false);
    requestKeys.current.clear();
    return () => {
      scopeController.current.abort();
      invalidateDetailRead();
    };
  }, [scopeKey, invalidateDetailRead]);
  useEffect(() => {
    if (!companyId || revoked) return;
    const controller = new AbortController(),
      started = scopeKey;
    setScopeLoading(true);
    const timer = window.setTimeout(
      () => {
        void api<AssistantScopeOptions>(
          '/assistant/scope?' +
            new URLSearchParams({
              company_id: companyId,
              event_q: eventQuery.trim(),
              limit: '20',
              offset: String(eventOffset),
            }),
          session,
          { signal: controller.signal },
        )
          .then((value) => {
            if (!controller.signal.aborted && started === liveScope.current) {
              if (value.company_id !== companyId)
                throw new Error('返回资料不属于当前企业，请重新打开助手。');
              setScopeOptions({ key: started, value });
            }
          })
          .catch((failure) => {
            if (!controller.signal.aborted && started === liveScope.current) {
              if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
                clearSensitive();
              setError(errorMessage(failure));
            }
          })
          .finally(() => {
            if (!controller.signal.aborted && started === liveScope.current) setScopeLoading(false);
          });
      },
      eventQuery ? 180 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [companyId, scopeKey, eventQuery, eventOffset, revoked, session, clearSensitive]);
  const loadHistory = useCallback(
    async (requestSignal?: AbortSignal) => {
      if (!companyId || revoked) return;
      const started = scopeKey,
        signal = requestSignal ?? scopeController.current.signal;
      try {
        const value = await api<AssistantConversationPage>(
          '/assistant/conversations?' +
            new URLSearchParams({
              company_id: companyId,
              limit: '20',
              offset: String(historyOffset),
            }),
          session,
          { signal },
        );
        if (!signal.aborted && started === liveScope.current)
          setConversations({
            key: started,
            value: {
              ...value,
              conversations: value.conversations.filter((row) => row.company_id === companyId),
            },
          });
      } catch (failure) {
        if (!signal.aborted && started === liveScope.current) {
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
            clearSensitive();
          setError(errorMessage(failure));
        }
      }
    },
    [companyId, scopeKey, historyOffset, revoked, session, clearSensitive],
  );
  useEffect(() => {
    if (!showHistory) return;
    const controller = new AbortController();
    setConversations(null);
    void loadHistory(AbortSignal.any([controller.signal, scopeController.current.signal]));
    return () => controller.abort();
  }, [loadHistory, showHistory]);
  const loadConversation = useCallback(
    async (initial = false, force = false) => {
      if (!conversationId || revoked) return;
      const key = `${detailKey}:${messageOffset}:${planOffset}`;
      const pending = detailRead.current;
      // Polling must not supersede a slow read of the same authorized page.
      // Writes and explicit refreshes bypass this join to discard pre-write snapshots.
      if (!force && pending?.key === key && !pending.controller.signal.aborted)
        return pending.promise;
      pending?.controller.abort();
      const controller = new AbortController();
      const started = detailKey,
        generation = ++detailEpoch.current,
        signal = AbortSignal.any([scopeController.current.signal, controller.signal]);
      const reading: DetailRead = { key, controller, promise: Promise.resolve() };
      detailRead.current = reading;
      if (initial) {
        setLoading(true);
        setError('');
      }
      reading.promise = (async () => {
        try {
          let workspace: AssistantConversationWorkspace;
          try {
            workspace = await api<AssistantConversationWorkspace>(
              `/assistant/conversations/${encode(conversationId)}/workspace?` +
                new URLSearchParams({
                  message_limit: '100',
                  message_offset: String(messageOffset),
                  action_limit: '20',
                  action_offset: String(planOffset),
                }),
              session,
              { signal },
            );
          } catch (failure) {
            // During a rolling release an older API may not have this route.
            // A missing resource or revoked scope must not retry legacy reads.
            if (
              !(failure instanceof ApiError) ||
              failure.status !== 404 ||
              failure.message !== 'Not Found'
            )
              throw failure;
            const values = await Promise.all([
              api<AssistantConversationDetail>(
                `/assistant/conversations/${encode(conversationId)}`,
                session,
                { signal },
              ),
              api<AssistantMessages>(
                `/assistant/conversations/${encode(conversationId)}/messages?limit=100&offset=${messageOffset}`,
                session,
                { signal },
              ),
              api<AssistantActionPage>(
                `/assistant/conversations/${encode(conversationId)}/actions?limit=20&offset=${planOffset}`,
                session,
                { signal },
              ),
            ]);
            workspace = { detail: values[0], messages: values[1], actions: values[2] };
          }
          const values = [workspace.detail, workspace.messages, workspace.actions] as const;
          if (
            signal.aborted ||
            started !== liveDetail.current ||
            generation !== detailEpoch.current
          )
            return;
          if (
            !assistantDetailMatches(values[0], companyId, conversationId) ||
            values[2].plans.some((plan) => !assistantPlanMatches(plan, values[0].conversation))
          ) {
            setDetail(null);
            setMessages(null);
            setPlans([]);
            setQuestion('');
            throw new Error('会话或任务资料范围已变化，请关闭后重新读取。');
          }
          setDetail({ key: started, value: values[0] });
          setMessages({ key: started, value: values[1] });
          setPlans(values[2].plans);
          setActionPagination(values[2].pagination);
          if (values[0].turns.length > 0 || values[1].messages.length > 0) {
            setPendingQuestion(null);
          }
        } catch (failure) {
          if (
            signal.aborted ||
            started !== liveDetail.current ||
            generation !== detailEpoch.current
          )
            return;
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
            clearSensitive();
          setError(errorMessage(failure));
        } finally {
          if (
            !signal.aborted &&
            started === liveDetail.current &&
            generation === detailEpoch.current
          )
            setLoading(false);
          if (detailRead.current === reading) detailRead.current = null;
          controller.abort();
        }
      })();
      return reading.promise;
    },
    [
      conversationId,
      detailKey,
      companyId,
      messageOffset,
      planOffset,
      revoked,
      session,
      clearSensitive,
    ],
  );
  const loadConversationRef = useRef(loadConversation);
  loadConversationRef.current = loadConversation;
  const turnEvents = useAssistantTurnEvents({
    session,
    companyId,
    conversationId,
    turnId: revoked ? undefined : eventTurnId,
    active: !!activeTurn,
    scopeKey: detailKey,
    onRevoked: clearSensitive,
    onFinished: () => loadConversationRef.current(false, true),
  });
  const readTurnEventsRef = useRef(turnEvents.read);
  readTurnEventsRef.current = turnEvents.read;
  useEffect(() => {
    if (!conversationId) {
      setDetail(null);
      setMessages(null);
      setPlans([]);
      progressRevision.current = null;
      return;
    }
    const isOptimistic = optimisticConversationId.current === conversationId;
    if (isOptimistic) {
      optimisticConversationId.current = null;
      progressRevision.current = null;
      void loadConversationRef.current(false, true);
      return;
    }
    setDetail(null);
    setMessages(null);
    setPlans([]);
    setNotice('');
    setError('');
    progressRevision.current = null;
    void loadConversationRef.current(true, true);
  }, [conversationId, messageOffset]);
  useEffect(() => {
    if (!conversationId || revoked) return;
    const captured = detailKey,
      controller = new AbortController(),
      signal = AbortSignal.any([controller.signal, scopeController.current.signal]);
    let reading = false,
      legacy = false,
      lastFull = Date.now();
    const poll = async () => {
      if (reading || document.visibilityState !== 'visible') return;
      reading = true;
      try {
        // A cheap, freshly authorized cursor makes completed responses visible
        // sooner. Full facts are still refreshed periodically and on focus.
        if (legacy || Date.now() - lastFull >= 30_000) {
          await loadConversationRef.current(false, false);
          lastFull = Date.now();
          return;
        }
        // Active supported turns use a bounded milestone cursor, not repeated
        // whole-workspace reads. Action receipts retain their own revision path.
        if (await readTurnEventsRef.current()) {
          if (!activeTask) return;
        }
        const progress = await api<AssistantConversationProgress>(
          `/assistant/conversations/${encode(conversationId)}/progress?action_offset=${planOffset}`,
          session,
          { signal },
        );
        if (signal.aborted || captured !== liveDetail.current) return;
        if (
          progress.company_id !== companyId ||
          progress.conversation_id !== conversationId ||
          progress.scope_version !== 1
        ) {
          clearSensitive();
          setError('会话资料范围已变化，请关闭后重新读取。');
          return;
        }
        if (progressRevision.current !== progress.revision) {
          await loadConversationRef.current(false, true);
          if (!signal.aborted && captured === liveDetail.current) {
            progressRevision.current = progress.revision;
            lastFull = Date.now();
          }
        }
      } catch (failure) {
        if (signal.aborted || captured !== liveDetail.current) return;
        if (
          failure instanceof ApiError &&
          failure.status === 404 &&
          failure.message === 'Not Found'
        ) {
          legacy = true;
          await loadConversationRef.current(false, false);
        } else {
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
            clearSensitive();
          setError(errorMessage(failure));
        }
      } finally {
        reading = false;
      }
    };
    const timer = window.setInterval(() => void poll(), activeTurn || activeTask ? 1200 : 15000);
    const foreground = () => {
      if (document.visibilityState === 'visible') void loadConversationRef.current(false, false);
    };
    window.addEventListener('focus', foreground);
    document.addEventListener('visibilitychange', foreground);
    return () => {
      window.clearInterval(timer);
      controller.abort();
      window.removeEventListener('focus', foreground);
      document.removeEventListener('visibilitychange', foreground);
    };
  }, [
    conversationId,
    companyId,
    detailKey,
    planOffset,
    session.scope_key,
    activeTurn?.id,
    activeTask,
    revoked,
    clearSensitive,
  ]);
  useEffect(() => {
    if (messageOffset === 0)
      transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: 'auto' });
  }, [currentMessages?.messages.length, activeTurn?.status, pendingQuestion?.text, messageOffset]);

  function keyFor(operation: string, signature: string) {
    const prior = requestKeys.current.get(operation);
    if (prior?.signature === signature) return prior.id;
    const id = crypto.randomUUID();
    requestKeys.current.set(operation, { signature, id });
    return id;
  }
  async function mutate<T>(
    operation: () => Promise<T>,
    receive: (value: T) => void,
    target = detailKey,
  ) {
    if (mutationBusy.current || revoked) return;
    mutationBusy.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const generation = epoch.current;
    try {
      const value = await operation();
      if (generation === epoch.current && target === liveDetail.current && !revoked) receive(value);
    } catch (failure) {
      if (generation === epoch.current && target === liveDetail.current) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          clearSensitive();
        setPendingQuestion(null);
        setError(errorMessage(failure));
      }
    } finally {
      if (generation === epoch.current && target === liveDetail.current) {
        mutationBusy.current = false;
        setBusy(false);
      }
    }
  }
  function changeCompany(id: string) {
    setCompanyId(id);
    setCreating(false);
    setShowHistory(false);
    setBookIds([]);
    setEventIds([]);
    setTitle('');
    setEventQuery('');
    setEventOffset(0);
    setError('');
  }
  function startNew() {
    invalidateDetailRead();
    optimisticConversationId.current = null;
    setConversationId(null);
    setDetail(null);
    setMessages(null);
    setPlans([]);
    setActionPagination(null);
    setCreating(false);
    setShowHistory(false);
    setToolsOpen(false);
    setQuestion('');
    setPendingQuestion(null);
    setError('');
    setNotice('');
    requestKeys.current.clear();
  }
  function openScopeBuilder() {
    setShowHistory(false);
    setCreating((value) => !value);
    setError('');
  }
  function openConversation(row: AssistantConversation) {
    if (row.company_id !== companyId) return;
    invalidateDetailRead();
    setDetail(null);
    setMessages(null);
    setPlans([]);
    setActionPagination(null);
    setQuestion('');
    setNotice('');
    setError('');
    setMessageOffset(0);
    setPlanOffset(0);
    setConversationId(row.id);
    setCreating(false);
    setShowHistory(false);
    if (row.id === conversationId && messageOffset === 0 && planOffset === 0)
      void loadConversation(true, true);
  }
  function createConversation() {
    if (
      !options ||
      scopeLoading ||
      bookIds.some((id) => !options.books.some((book) => book.id === id))
    )
      return;
    const body = {
      company_id: companyId,
      book_ids: bookIds,
      event_ids: eventIds,
      ...(initialContext?.draftId && bookIds.length === 1 && bookIds[0] === initialContext.bookId
        ? { draft_id: initialContext.draftId }
        : {}),
      ...(title.trim() ? { title: title.trim() } : {}),
    };
    void mutate(
      () =>
        api<{ conversation: AssistantConversation } | AssistantConversation>(
          '/assistant/conversations',
          session,
          {
            method: 'POST',
            body: { ...body, idempotency_key: keyFor('create', JSON.stringify(body)) },
            signal: scopeController.current.signal,
          },
        ),
      (value) => {
        const row = 'conversation' in value ? value.conversation : value;
        requestKeys.current.delete('create');
        openConversation(row);
      },
    );
  }
  function sendQuestion(value: string) {
    const submitted = assistantQuestion(value);
    if (!submitted || activeTurn || !modelAvailable || !companyId) return;
    setPendingQuestion({ text: submitted, at: new Date().toISOString() });
    setQuestion('');
    if (current) {
      const conversation = current.conversation;
      void mutate(
        () =>
          api<{ turn: AssistantTurn }>(
            `/assistant/conversations/${encode(conversation.id)}/turns`,
            session,
            {
              method: 'POST',
              body: {
                question: submitted,
                expected_scope_version: conversation.scope_version,
                idempotency_key: keyFor('turn', `${conversation.id}:${submitted}`),
              },
              signal: scopeController.current.signal,
            },
          ),
        (result) => {
          requestKeys.current.delete('turn');
          setPendingQuestion(null);
          setDetail((previous) =>
            previous?.key === detailKey
              ? {
                  ...previous,
                  value: {
                    ...previous.value,
                    turns: [
                      result.turn,
                      ...previous.value.turns.filter((turn) => turn.id !== result.turn.id),
                    ],
                  },
                }
              : previous,
          );
          void loadConversationRef.current(false, true);
        },
      );
      return;
    }
    // 零门槛原地直聊：静默建会话并入队提问，绝不清空界面跳白屏。
    const autoBooks = bookIds.length
      ? bookIds.filter((id) => !options || options.books.some((book) => book.id === id))
      : (options?.books.slice(0, 5).map((book) => book.id) ?? []);
    const createBody = {
      company_id: companyId,
      book_ids: autoBooks,
      event_ids: eventIds,
      ...(initialContext?.draftId &&
      autoBooks.length === 1 &&
      autoBooks[0] === initialContext.bookId
        ? { draft_id: initialContext.draftId }
        : {}),
      title: submitted.slice(0, 24),
    };
    void mutate(
      async () => {
        const created = await api<{ conversation: AssistantConversation } | AssistantConversation>(
          '/assistant/conversations',
          session,
          {
            method: 'POST',
            body: {
              ...createBody,
              idempotency_key: keyFor('quick-create', `${companyId}:${submitted}`),
            },
            signal: scopeController.current.signal,
          },
        );
        const row = 'conversation' in created ? created.conversation : created;
        const turnResult = await api<{ turn: AssistantTurn }>(
          `/assistant/conversations/${encode(row.id)}/turns`,
          session,
          {
            method: 'POST',
            body: {
              question: submitted,
              expected_scope_version: row.scope_version,
              idempotency_key: keyFor('turn', `${row.id}:${submitted}`),
            },
            signal: scopeController.current.signal,
          },
        );
        return { row, turn: turnResult.turn };
      },
      ({ row }) => {
        requestKeys.current.delete('quick-create');
        requestKeys.current.delete('turn');
        optimisticConversationId.current = row.id;
        setCreating(false);
        setShowHistory(false);
        setConversationId(row.id);
      },
    );
  }
  function prepareTask(kind: AssistantTaskKind, reason: string) {
    if (!current) return;
    const conversation = current.conversation;
    void mutate(
      () =>
        api<AssistantActionPlan>(
          `/assistant/conversations/${encode(conversation.id)}/actions`,
          session,
          {
            method: 'POST',
            body: {
              kind,
              reason,
              idempotency_key: keyFor(`preview:${kind}`, `${conversation.id}:${kind}:${reason}`),
            },
            signal: scopeController.current.signal,
          },
        ),
      (plan) => {
        if (!assistantPlanMatches(plan, conversation)) {
          setError('任务预览超出当前会话范围，请重新读取资料。');
          return;
        }
        requestKeys.current.delete(`preview:${kind}`);
        setPlans((previous) => [...previous.filter((row) => row.id !== plan.id), plan]);
        invalidateDetailRead();
        setPlanOffset(0);
        // New previews are on the first page; its effect uses the new offset.
        if (planOffset === 0) void loadConversation(false, true);
      },
    );
  }
  function confirmTask(plan: AssistantActionPlan) {
    if (!current || !assistantPlanConfirmable(plan, current.conversation)) return;
    void mutate(
      () =>
        api<AssistantActionReceipt>(
          `/assistant/conversations/${encode(current.conversation.id)}/actions/${encode(plan.id)}/confirm`,
          session,
          {
            method: 'POST',
            body: {
              fingerprint: plan.fingerprint,
              confirmed: true,
              idempotency_key: keyFor(`confirm:${plan.id}`, plan.fingerprint),
            },
            signal: scopeController.current.signal,
          },
        ),
      (receipt) => {
        if (receipt.plan_id !== plan.id || receipt.kind !== plan.kind) {
          setError('任务回执与预览不匹配，请重新读取。');
          return;
        }
        requestKeys.current.delete(`confirm:${plan.id}`);
        setPlans((previous) =>
          previous.map((row) =>
            row.id === plan.id ? { ...row, state: 'executed', receipt } : row,
          ),
        );
        setNotice('任务已登记。会计审核与导出继续在原工作流完成。');
        void loadConversation(false, true);
      },
    );
  }
  function cancelTurn(turn: AssistantTurn) {
    if (!current) return;
    void mutate(
      () =>
        api(
          `/assistant/conversations/${encode(current.conversation.id)}/turns/${encode(turn.id)}/cancel`,
          session,
          {
            method: 'POST',
            body: { reason: '用户在会计助手中取消本次请求' },
            signal: scopeController.current.signal,
          },
        ),
      () => {
        setNotice('取消请求已登记，已发送的模型请求仍需核对执行结果与费用。');
        void loadConversation(false, true);
      },
    );
  }
  function toggleId(id: string, selected: string[], set: (ids: string[]) => void, limit: number) {
    if (selected.includes(id)) set(selected.filter((value) => value !== id));
    else if (selected.length < limit) set([...selected, id]);
  }
  const visibleTurns = current?.turns ?? [];
  const referencedTurns = new Set(
    currentMessages?.messages.map((message) => message.turn_id) ?? [],
  );

  const drawer = (
    <div
      className={'global-assistant-drawer' + (embedded ? ' embedded' : '')}
      ref={dialog}
      role={embedded ? 'region' : 'dialog'}
      aria-modal={embedded ? undefined : true}
      aria-labelledby={embedded ? undefined : 'global-assistant-title'}
      aria-label={embedded ? '会计助手' : undefined}
      data-assistant-company={companyId || undefined}
    >
      {!embedded && (
        <header className="ga-header">
          <div className="ga-mark">
            <Sparkles size={17} />
          </div>
          <div>
            <h2 id="global-assistant-title">智能财税副驾</h2>
          </div>
          <div className="ga-header-actions">
            <button
              ref={closeButton}
              className="icon-button"
              aria-label="关闭全局会计助手"
              onClick={closeAssistant}
            >
              <X size={18} />
            </button>
          </div>
        </header>
      )}
      <div className="ga-toolbar">
        <div className="ga-company-select-wrap">
          <Building2 size={14} aria-hidden="true" />
          <label className="sr-only" htmlFor="ga-company-select">
            当前查询子公司
          </label>
          <select
            id="ga-company-select"
            value={companyId}
            disabled={busy || revoked}
            onChange={(event) => changeCompany(event.target.value)}
          >
            <option value="">选择子公司</option>
            {companies.map((company) => (
              <option key={company.id} value={company.id}>
                {company.name}
              </option>
            ))}
          </select>
        </div>
        <div className="ga-toolbar-actions">
          <button
            type="button"
            className={'icon-button' + (showHistory ? ' active' : '')}
            aria-label="查看历史对话"
            title="历史对话"
            disabled={!companyId || revoked || busy}
            onClick={() => {
              setCreating(false);
              setShowHistory((value) => !value);
            }}
          >
            <History size={16} />
          </button>
          <button
            type="button"
            className={'icon-button' + (creating ? ' active' : '')}
            aria-label="限定专项核对资料"
            title={creating ? '返回对话' : '限定专项资料范围'}
            disabled={!companyId || revoked || busy}
            onClick={openScopeBuilder}
          >
            <FileSearch size={16} />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="开启新对话"
            title="新对话"
            disabled={!companyId || revoked || busy}
            onClick={startNew}
          >
            <Plus size={17} />
          </button>
        </div>
      </div>
      {error && (
        <div className="ga-error" role="alert">
          <TriangleAlert size={16} />
          <p>{error}</p>
          {!revoked && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => {
                setError('');
                if (conversationId) void loadConversation(true, true);
                else void loadHistory();
              }}
            >
              <RefreshCw size={13} />
              重新读取
            </button>
          )}
        </div>
      )}
      {notice && (
        <div className="ga-notice" role="status">
          <CheckCircle2 size={15} />
          <p>{notice}</p>
        </div>
      )}
      {!companyId && !revoked && (
        <div className="ga-empty">
          <div className="ga-empty-symbol">
            <BookOpen size={31} />
          </div>
          <p className="ga-eyebrow">从明确的资料范围开始</p>
          <h3>把资料、问题与任务放在一起</h3>
          <p>选择企业，再加入账套或业务事项。助手只读取你明确选择且有权查看的资料。</p>
        </div>
      )}
      {companyId && !revoked && showHistory ? (
        <section className="ga-history ga-scroll">
          <div className="ga-section-heading">
            <h3>我的会话</h3>
            <button className="text-button" onClick={() => setShowHistory(false)}>
              <ArrowLeft size={13} />
              返回
            </button>
          </div>
          <p className="ga-help">{companyName} · 历史会话每次打开都会重新检查资料授权。</p>
          {!history && !error && (
            <div className="ga-loading" role="status">
              <LoaderCircle className="spin" size={20} />
              <p>正在读取我的会话…</p>
            </div>
          )}
          {history?.conversations.map((row) => (
            <button className="ga-history-row" key={row.id} onClick={() => openConversation(row)}>
              <MessageSquareText size={17} />
              <span>
                <strong>{row.title || '会计资料核对'}</strong>
                <small>
                  {row.book_ids.length} 个账套 · {row.event_ids.length} 项业务 ·{' '}
                  {dateLabel(row.updated_at)}
                </small>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
          {history && !history.conversations.length && (
            <div className="ga-subtle-empty">
              <MessageSquareText size={23} />
              <p>这家企业还没有你的会话。</p>
              <button className="ga-primary" onClick={startNew}>
                开始新会话
              </button>
            </div>
          )}
          {history && history.pagination.total > 20 && (
            <div className="ga-pagination">
              <button
                disabled={!historyOffset}
                onClick={() => setHistoryOffset(Math.max(0, historyOffset - 20))}
              >
                <ChevronLeft size={15} />
                上一页
              </button>
              <span>
                {historyOffset + 1}–{Math.min(historyOffset + 20, history.pagination.total)} /{' '}
                {history.pagination.total}
              </span>
              <button
                disabled={!history.pagination.has_more}
                onClick={() => setHistoryOffset(historyOffset + 20)}
              >
                下一页
                <ChevronRight size={15} />
              </button>
            </div>
          )}
        </section>
      ) : companyId && !revoked && creating ? (
        <section className="ga-scope-builder ga-scroll">
          <p className="ga-eyebrow">新会话 · 01 / 选择资料</p>
          <h3>这次核对什么？</h3>
          <p className="ga-help">
            选中的资料会成为本次会话的固定范围。后续换企业或加入其他资料，请新建会话。
          </p>
          <label className="ga-field">
            <span>
              会话名称 <small>可选</small>
            </span>
            <input
              value={title}
              maxLength={120}
              placeholder="例如：10 月报销与票据核对"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <fieldset className="ga-selection">
            <legend>
              <BookOpen size={14} />
              账套与期间 <small>{bookIds.length} / 12</small>
            </legend>
            {options?.books.map((book) => (
              <label key={book.id} className={bookIds.includes(book.id) ? 'selected' : ''}>
                <input
                  type="checkbox"
                  checked={bookIds.includes(book.id)}
                  disabled={busy || (!bookIds.includes(book.id) && bookIds.length >= 12)}
                  onChange={() => toggleId(book.id, bookIds, setBookIds, 12)}
                />
                <span>
                  <strong>{book.name}</strong>
                  <small>
                    {book.period} · {book.period_status === 'closed' ? '已关期' : '开放期间'}
                  </small>
                </span>
                {bookIds.includes(book.id) && <Check size={15} />}
              </label>
            ))}
            {options && !options.books.length && (
              <p className="ga-help">当前账号没有可加入的账套，可选择有权查看的业务事项。</p>
            )}
          </fieldset>
          <fieldset className="ga-selection">
            <legend>
              <FileText size={14} />
              业务事项 <small>{eventIds.length} / 20</small>
            </legend>
            <label className="ga-search">
              <Search size={14} />
              <input
                value={eventQuery}
                maxLength={120}
                placeholder="搜索事项说明"
                aria-label="搜索助手业务事项"
                onChange={(event) => {
                  setEventQuery(event.target.value);
                  setEventOffset(0);
                }}
              />
            </label>
            {scopeLoading && (
              <p className="ga-status" role="status">
                <LoaderCircle className="spinner" size={14} />
                读取授权资料…
              </p>
            )}
            {options?.events.map((item) => (
              <label key={item.id} className={eventIds.includes(item.id) ? 'selected' : ''}>
                <input
                  type="checkbox"
                  checked={eventIds.includes(item.id)}
                  disabled={busy || (!eventIds.includes(item.id) && eventIds.length >= 20)}
                  onChange={() => toggleId(item.id, eventIds, setEventIds, 20)}
                />
                <span>
                  <strong>{item.title || item.description || '业务事项'}</strong>
                  <small>
                    {item.amount ? `¥${item.amount} · ` : ''}
                    {assistantBusinessStatus(item.status)}
                  </small>
                </span>
                {eventIds.includes(item.id) && <Check size={15} />}
              </label>
            ))}
            {options && !options.events.length && !scopeLoading && (
              <p className="ga-help">当前筛选下没有可选事项。</p>
            )}
            {eventIds.some((id) => !options?.events.some((item) => item.id === id)) && (
              <p className="ga-help">
                另有{' '}
                {eventIds.filter((id) => !options?.events.some((item) => item.id === id)).length}{' '}
                项已选资料位于其他页或当前事项上下文中。
              </p>
            )}
            {options?.pagination && options.pagination.total > 20 && (
              <div className="ga-pagination">
                <button
                  disabled={!eventOffset || scopeLoading}
                  onClick={() => setEventOffset(Math.max(0, eventOffset - 20))}
                >
                  <ChevronLeft size={14} />
                  上一页
                </button>
                <span>
                  {eventOffset + 1}–{Math.min(eventOffset + 20, options.pagination.total)} /{' '}
                  {options.pagination.total}
                </span>
                <button
                  disabled={!options.pagination.has_more || scopeLoading}
                  onClick={() => setEventOffset(eventOffset + 20)}
                >
                  下一页
                  <ChevronRight size={14} />
                </button>
              </div>
            )}
          </fieldset>
          {!bookIds.length && !eventIds.length && (
            <p className="ga-help ga-scope-note">
              <ShieldCheck size={14} />
              不选择资料也可发起一般咨询；助手不会默认读取整家企业。
            </p>
          )}
          {options && !modelAvailable && (
            <p className="ga-config-note">
              <TriangleAlert size={15} />
              {modelUnavailableNotice}
            </p>
          )}
          <button
            className="ga-primary ga-create"
            disabled={
              busy ||
              !options ||
              scopeLoading ||
              bookIds.some((id) => !options.books.some((book) => book.id === id))
            }
            onClick={createConversation}
          >
            {busy ? (
              <LoaderCircle className="spinner" size={16} />
            ) : (
              <MessageSquareText size={16} />
            )}
            创建会话 · {bookIds.length + eventIds.length} 项资料
          </button>
        </section>
      ) : companyId && !revoked ? (
        <>
          {current &&
            (current.context.books.length > 0 ||
              current.context.events.length > 0 ||
              current.context.draft) && (
              <div className="ga-scope-line">
                <div className="ga-scope-line-tags">
                  {current.context.books.slice(0, 2).map((book) => (
                    <span key={book.id}>
                      {book.period} · {book.name}
                    </span>
                  ))}
                  {current.context.books.length > 2 && (
                    <span>等 {current.context.books.length} 套账</span>
                  )}
                  {current.context.events.length > 0 && (
                    <span>{current.context.events.length} 项事项</span>
                  )}
                  {current.context.draft && <span>当前凭证</span>}
                </div>
                {onNavigateContext && (
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => onNavigateContext(current.context)}
                  >
                    查看资料
                    <ChevronRight size={12} />
                  </button>
                )}
              </div>
            )}

          <div className="ga-transcript ga-scroll" ref={transcript} aria-label="智能财税副驾对话">
            {loading && !current && !pendingQuestion ? (
              <div className="ga-inline-loading" role="status">
                <LoaderCircle className="spinner" size={18} />
                <span>正在加载对话…</span>
              </div>
            ) : !current && !pendingQuestion ? (
              <div className="ga-welcome-clean">
                <p className="ga-welcome-hello">有什么财税或账务问题，直接问我即可。</p>
                <div className="ga-prompt-list">
                  {roleMeta.quickPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="ga-prompt-chip"
                      disabled={busy || !modelAvailable || scopeLoading}
                      onClick={() => {
                        if (modelAvailable) sendQuestion(prompt);
                        else setQuestion(prompt);
                      }}
                    >
                      <span>{prompt}</span>
                      <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {currentMessages?.pagination.has_more && (
                  <button
                    type="button"
                    className="ga-older"
                    disabled={busy}
                    onClick={() => setMessageOffset(messageOffset + 100)}
                  >
                    查看更早记录
                  </button>
                )}
                {messageOffset > 0 && (
                  <button
                    type="button"
                    className="ga-older"
                    disabled={busy}
                    onClick={() => setMessageOffset(Math.max(0, messageOffset - 100))}
                  >
                    返回最新记录
                  </button>
                )}
                {current &&
                  !currentMessages?.messages.length &&
                  !visibleTurns.length &&
                  !pendingQuestion && (
                    <div className="ga-welcome-clean">
                      <p className="ga-welcome-hello">已就绪，请直接输入您的问题。</p>
                      <div className="ga-prompt-list">
                        {[
                          '帮我梳理所选资料中需要人工处理的问题',
                          '核对所选票据与业务事项的金额差异',
                          '解释当前分录草稿的依据与缺少的证据',
                        ].map((value) => (
                          <button
                            key={value}
                            type="button"
                            className="ga-prompt-chip"
                            disabled={!modelAvailable || busy}
                            onClick={() => sendQuestion(value)}
                          >
                            <span>{value}</span>
                            <ChevronRight size={14} aria-hidden="true" />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                {currentMessages?.messages.map((message) => {
                  const reply =
                    message.role === 'assistant' && 'answer' in message.content
                      ? message.content
                      : null;
                  const citedEvidence = message.citations ?? current?.context.evidence ?? [];
                  const detailTargets =
                    reply && message.turn_id
                      ? assistantCitations(reply, citedEvidence)
                          .map(assistantDeliveryTarget)
                          .filter((target) => target !== null)
                      : [];
                  const historical =
                    !!current &&
                    !!message.input_fingerprint &&
                    message.input_fingerprint !== current.context.input_fingerprint;
                  return (
                    <article key={message.id} className={`ga-message ${message.role}`}>
                      <p className="ga-message-text">
                        {reply?.answer ||
                          ('question' in message.content ? message.content.question : '')}
                      </p>
                      {reply && (
                        <>
                          {(historical ||
                            new Set(reply.citation_ids).size >
                              assistantCitations(reply, citedEvidence).length) && (
                            <p className="ga-historical-citation">
                              该回复基于历史资料，当前数据已有更新。
                            </p>
                          )}
                          {assistantCitations(reply, citedEvidence).length > 0 && (
                            <div className="ga-citations">
                              {assistantCitations(reply, citedEvidence).map((item) => (
                                <button
                                  key={item.id}
                                  type="button"
                                  disabled={!onNavigateEvidence}
                                  title={item.detail}
                                  onClick={() => onNavigateEvidence?.(item)}
                                >
                                  <FileText size={12} />
                                  {item.label}
                                </button>
                              ))}
                            </div>
                          )}
                          {current &&
                            detailTargets.length === 1 &&
                            detailTargets[0].company_id === current.conversation.company_id && (
                              <AssistantDeliveryDetails
                                key={JSON.stringify([
                                  session.scope_key,
                                  current.conversation.id,
                                  message.turn_id,
                                  detailTargets[0],
                                ])}
                                session={session}
                                conversationId={current.conversation.id}
                                turnId={message.turn_id}
                                target={detailTargets[0]}
                              />
                            )}
                          {assistantAllowedProposals(reply).map((proposal, index) => (
                            <button
                              type="button"
                              className="ga-task-proposal"
                              key={`${proposal.kind}:${index}`}
                              disabled={busy}
                              onClick={() => prepareTask(proposal.kind, proposal.reason)}
                            >
                              <ListChecks size={15} />
                              <span>
                                <strong>{assistantTaskLabels[proposal.kind]}</strong>
                                <small>{proposal.reason}</small>
                              </span>
                              <span className="ga-proposal-label">
                                预览
                                <ChevronRight size={13} />
                              </span>
                            </button>
                          ))}
                        </>
                      )}
                    </article>
                  );
                })}

                {visibleTurns
                  .filter((turn) => !referencedTurns.has(turn.id) && messageOffset === 0)
                  .slice()
                  .reverse()
                  .map((turn) => (
                    <article className="ga-message user" key={`turn:${turn.id}`}>
                      <p className="ga-message-text">{turn.question}</p>
                    </article>
                  ))}

                {pendingQuestion && (
                  <article className="ga-message user" key="pending-user">
                    <p className="ga-message-text">{pendingQuestion.text}</p>
                  </article>
                )}

                {(pendingQuestion || activeTurn) && messageOffset === 0 && (
                  <article className="ga-message assistant ga-thinking-bubble" role="status">
                    <div className="ga-thinking-row">
                      <LoaderCircle className="spinner" size={14} aria-hidden="true" />
                      <span>
                        {turnEvents.feed?.events.at(-1)?.message ||
                          (activeTurn ? assistantTurnLabels[activeTurn.status] : '正在思考…')}
                      </span>
                      {activeTurn && (
                        <button
                          type="button"
                          className="text-button ga-cancel-inline"
                          disabled={busy}
                          onClick={() => cancelTurn(activeTurn)}
                        >
                          <Square size={10} />
                          停止
                        </button>
                      )}
                    </div>
                  </article>
                )}

                {current &&
                  visibleTurns
                    .filter(
                      (turn) =>
                        turn.status !== 'succeeded' &&
                        !assistantTurnActive(turn.status) &&
                        messageOffset === 0,
                    )
                    .slice(0, 1)
                    .map((turn) => (
                      <article
                        className="ga-message assistant ga-error-bubble"
                        role="status"
                        key={`status:${turn.id}`}
                      >
                        <div className="ga-error-bubble-head">
                          <TriangleAlert size={14} aria-hidden="true" />
                          <strong>{assistantTurnLabels[turn.status]}</strong>
                          <button
                            type="button"
                            className="text-button ga-retry-inline"
                            disabled={busy || !modelAvailable}
                            onClick={() => sendQuestion(turn.question)}
                          >
                            <RefreshCw size={12} />
                            重试
                          </button>
                        </div>
                        <p className="ga-message-text">
                          {turn.error_detail ||
                            assistantErrorCodeLabel(turn.error_code) ||
                            '本次未能完成回复，请稍后重试。'}
                        </p>
                        <ExecutionRecordPanel
                          session={session}
                          companyId={companyId}
                          kind="chat"
                          runId={turn.id}
                          contextKey={`${current.conversation.id}:${current.conversation.scope_version}:${current.context.input_fingerprint}`}
                        />
                      </article>
                    ))}

                {current &&
                  plans.map((plan) => (
                    <section className="ga-action-plan" key={plan.id}>
                      <div className="ga-plan-title">
                        <ListChecks size={15} />
                        <h4>{assistantTaskLabels[plan.kind]}</h4>
                        <span>
                          {plan.state === 'executed'
                            ? '已登记'
                            : plan.state === 'empty'
                              ? '无可执行项'
                              : plan.state === 'expired'
                                ? '已过期'
                                : '待确认'}
                        </span>
                      </div>
                      <p>{plan.reason}</p>
                      <div className="ga-plan-summary">
                        <strong>{plan.count}</strong>
                        <span>项资料</span>
                      </div>
                      <PlanItems items={plan.items} />
                      {plan.blocked.map((blocked, index) => (
                        <p className="ga-plan-blocked" key={`${blocked.code}:${index}`}>
                          <TriangleAlert size={13} />
                          {blocked.message}
                        </p>
                      ))}
                      {!plan.receipt && (
                        <button
                          type="button"
                          className="ga-plan-rebuild"
                          disabled={busy}
                          onClick={() => prepareTask(plan.kind, plan.reason)}
                        >
                          <RefreshCw size={12} />
                          重新预览
                        </button>
                      )}
                      {plan.receipt ? (
                        <div className="ga-receipt">
                          <CheckCircle2 size={15} />
                          <div>
                            <strong>
                              {plan.receipt.jobs.length
                                ? `${plan.receipt.jobs.length} 个后台任务已登记`
                                : `${plan.receipt.created} 份草稿已生成`}
                            </strong>
                            <p>{assistantReceiptProgress(plan.receipt)}</p>
                          </div>
                          {onOpenTask && (
                            <button
                              type="button"
                              className="text-button"
                              onClick={() => onOpenTask(plan.receipt!)}
                            >
                              查看
                              <ChevronRight size={13} />
                            </button>
                          )}
                        </div>
                      ) : plan.state === 'ready' ? (
                        <button
                          type="button"
                          className="ga-primary"
                          disabled={busy || !assistantPlanConfirmable(plan, current.conversation)}
                          onClick={() => confirmTask(plan)}
                        >
                          {busy ? (
                            <LoaderCircle className="spinner" size={14} />
                          ) : (
                            <Check size={14} />
                          )}
                          确认执行这 {plan.count} 项
                        </button>
                      ) : null}
                    </section>
                  ))}

                {actionPagination && actionPagination.total > 20 && (
                  <div className="ga-pagination">
                    <button
                      type="button"
                      disabled={!planOffset || busy}
                      onClick={() => setPlanOffset(Math.max(0, planOffset - 20))}
                    >
                      <ChevronLeft size={14} />
                      上一页
                    </button>
                    <span>
                      {planOffset + 1}–{Math.min(planOffset + 20, actionPagination.total)} /{' '}
                      {actionPagination.total}
                    </span>
                    <button
                      type="button"
                      disabled={!actionPagination.has_more || busy}
                      onClick={() => setPlanOffset(planOffset + 20)}
                    >
                      下一页
                      <ChevronRight size={14} />
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          <footer className="ga-composer ga-composer-clean">
            {!modelAvailable && (
              <p className="ga-config-note">
                <TriangleAlert size={14} />
                {modelUnavailableNotice}
              </p>
            )}
            {current && toolsOpen && (
              <div className="ga-tools-popover" role="menu" aria-label="快捷后台任务">
                {Object.entries(assistantTaskLabels).map(([kind, label]) => (
                  <button
                    key={kind}
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setToolsOpen(false);
                      prepareTask(
                        kind as AssistantTaskKind,
                        `用户申请${label}，范围限定为当前会话所选资料`,
                      );
                    }}
                  >
                    {label}
                    <ChevronRight size={13} />
                  </button>
                ))}
              </div>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                sendQuestion(question);
              }}
            >
              <label className="sr-only" htmlFor="ga-question">
                向智能财税副驾提问
              </label>
              <textarea
                id="ga-question"
                ref={questionInput}
                rows={2}
                value={question}
                maxLength={4000}
                disabled={busy || !!activeTurn || !modelAvailable}
                placeholder={
                  modelAvailable ? '输入财税或账务问题，回车发送…' : modelUnavailablePlaceholder
                }
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey && !isCompositionKey(event)) {
                    event.preventDefault();
                    sendQuestion(question);
                  }
                }}
              />
              <div className="ga-composer-bar">
                {current ? (
                  <button
                    type="button"
                    className={'ga-tools-trigger' + (toolsOpen ? ' active' : '')}
                    disabled={busy}
                    onClick={() => setToolsOpen((prev) => !prev)}
                  >
                    <ListChecks size={13} />
                    后台任务
                  </button>
                ) : (
                  <span className="ga-composer-hint">Shift + 回车换行</span>
                )}
                <button
                  aria-label="发送问题"
                  type="submit"
                  className="ga-send-btn"
                  disabled={
                    busy || !!activeTurn || !modelAvailable || !assistantQuestion(question)
                  }
                >
                  {busy || !!activeTurn ? (
                    <LoaderCircle className="spinner" size={16} />
                  ) : (
                    <ArrowUp size={16} />
                  )}
                </button>
              </div>
            </form>
          </footer>
        </>
      ) : null}
    </div>
  );
  return embedded ? (
    drawer
  ) : (
    <div
      className="global-assistant-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeAssistant();
      }}
    >
      {drawer}
    </div>
  );
}
