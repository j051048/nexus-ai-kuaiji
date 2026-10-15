import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Building2,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  Download,
  FileCheck2,
  FileText,
  LayoutDashboard,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Upload,
  Users,
  LogOut,
  X,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { AuthScreen, NoAuthorization } from './AuthScreen';
import { useAccountingAuth } from './useAccountingAuth';
import { ActivityTimer } from './ActivityTimer';
import { WorkspaceHome } from './WorkspaceHome';
import { AgentNavigation, type AgentNavigationView } from './AgentNavigation';
import { AgentCommandPalette, type AgentCommand } from './AgentCommandPalette';
import type { AgentRoute } from './agent-workspace-types';
import type {
  AssistantInitialContext,
  AssistantEvidence,
  AssistantActionReceipt,
} from './assistant-types';
import { clearReviewSession } from './review-session';
import { roleCopy, taskDestination } from './workspace-types';
import type { WorkspaceProfile, WorkspaceTask } from './workspace-types';
import type { BusinessTemplate } from './business-types';
import type { OperationsCompany } from './operations-types';
import { downloadCsv, formatMoney } from './csv';
import type { Book, BookDetail, Draft, FirmRole, JournalLine, Session, Workbench } from './types';

import { EmptyState, Modal, PageControls, Spinner, StatCard } from './AppPrimitives';
import { AssistantDialog, NewBookModal } from './AppDialogs';
import { AccountingBookList } from './AccountingBookList';
import { AccountingBookToolbar } from './AccountingBookToolbar';
import { useWorkspaceRoute } from './useWorkspaceRoute';
import { RouteLeaveDialog } from './RouteLeaveDialog';
import { readWorkspaceRoute, workspaceUsesBook, type WorkspaceRoute } from './workspace-route';
import type { MonthlyDestination } from './monthly-workbench-types';

type Filter = 'all' | 'pending' | 'exception' | 'approved';
const AccessManagement = lazy(() =>
  import('./AccessManagement').then((module) => ({ default: module.AccessManagement })),
);
const ProposalTasksPanel = lazy(() =>
  import('./ProposalTasksPanel').then((module) => ({ default: module.ProposalTasksPanel })),
);
const Reviewer = lazy(() =>
  import('./VoucherReviewer').then((module) => ({ default: module.Reviewer })),
);
const BusinessWorkspace = lazy(() =>
  import('./BusinessWorkspace').then((module) => ({ default: module.BusinessWorkspace })),
);
const AdminWorkspace = lazy(() => import('./AdminWorkspace'));
const AdminMfaGate = lazy(() => import('./AdminMfaGate'));
const AccountActivation = lazy(() => import('./AccountActivation'));
const AccountRecovery = lazy(() => import('./AccountRecovery'));
const ChineseImportWizard = lazy(() => import('./ChineseImportWizard'));
const InlineReviewDrawer = lazy(() =>
  import('./InlineReviewDrawer').then((module) => ({ default: module.InlineReviewDrawer })),
);
const ProposalTaskDialog = lazy(() =>
  import('./ProposalTaskDialog').then((module) => ({ default: module.ProposalTaskDialog })),
);
const IntelligenceWorkbench = lazy(() =>
  import('./IntelligenceWorkbench').then((module) => ({ default: module.IntelligenceWorkbench })),
);
const QueueWorkbench = lazy(() =>
  import('./QueueWorkbench').then((module) => ({ default: module.QueueWorkbench })),
);
const OperationsWorkbench = lazy(() =>
  import('./OperationsWorkbench').then((module) => ({ default: module.OperationsWorkbench })),
);
const ReviewWorkbench = lazy(() =>
  import('./ReviewWorkbench').then((module) => ({ default: module.ReviewWorkbench })),
);
const QualityWorkbench = lazy(() =>
  import('./QualityWorkbench').then((module) => ({ default: module.QualityWorkbench })),
);
const CasesWorkbench = lazy(() =>
  import('./CasesWorkbench').then((module) => ({ default: module.CasesWorkbench })),
);
const LearningWorkbench = lazy(() =>
  import('./LearningWorkbench').then((module) => ({ default: module.LearningWorkbench })),
);
const ReconciliationWorkbench = lazy(() =>
  import('./ReconciliationWorkbench').then((module) => ({
    default: module.ReconciliationWorkbench,
  })),
);
const DeliveryWorkbench = lazy(() =>
  import('./DeliveryWorkbench').then((module) => ({ default: module.DeliveryWorkbench })),
);
const SettlementWorkbench = lazy(() =>
  import('./SettlementWorkbench').then((module) => ({ default: module.SettlementWorkbench })),
);
const ClosingWorkbench = lazy(() =>
  import('./ClosingWorkbench').then((module) => ({ default: module.ClosingWorkbench })),
);
const RevisionWorkbench = lazy(() =>
  import('./RevisionWorkbench').then((module) => ({ default: module.RevisionWorkbench })),
);
const EfficiencyWorkbench = lazy(() =>
  import('./EfficiencyWorkbench').then((module) => ({ default: module.EfficiencyWorkbench })),
);
const IntegrationWorkbench = lazy(() =>
  import('./IntegrationWorkbench').then((module) => ({ default: module.IntegrationWorkbench })),
);
const LegacyMigrationWorkspace = lazy(() => import('./LegacyMigrationWorkspace'));
const AgentActivityWorkspace = lazy(() =>
  import('./AgentActivityWorkspace').then((module) => ({ default: module.AgentActivityWorkspace })),
);
const GlobalAccountingAssistant = lazy(() =>
  import('./GlobalAccountingAssistant').then((module) => ({
    default: module.GlobalAccountingAssistant,
  })),
);
const AccountingContextAssistant = lazy(() =>
  import('./AccountingContextAssistant').then((module) => ({
    default: module.AccountingContextAssistant,
  })),
);
const LedgerWorkspace = lazy(() =>
  import('./LedgerWorkspace').then((module) => ({ default: module.LedgerWorkspace })),
);
const CatalogWorkbench = lazy(() =>
  import('./CatalogWorkbench').then((module) => ({ default: module.CatalogWorkbench })),
);
const MonthlyClientWorkbench = lazy(() =>
  import('./MonthlyClientWorkbench').then((module) => ({ default: module.MonthlyClientWorkbench })),
);
const MonthlyReconciliationWorkbench = lazy(() =>
  import('./MonthlyReconciliationWorkbench').then((module) => ({
    default: module.MonthlyReconciliationWorkbench,
  })),
);
const RecurringEntries = lazy(() =>
  import('./RecurringEntries').then((module) => ({ default: module.RecurringEntries })),
);
type ModalName = 'book' | 'import' | 'help' | 'access' | null;
type Notice = { kind: 'success' | 'error'; text: string } | null;

const statusLabels: Record<Draft['status'], string> = {
  draft: '待审核',
  approved: '已审核',
  exported: '已导出',
};
const roleLabels: Record<FirmRole, string> = {
  owner: '事务所负责人',
  admin: '事务所管理员',
  operator: '记账经办员',
  reviewer: '会计复核员',
  viewer: '只读成员',
  client_employee: '客户企业成员',
  client_member: '客户企业成员',
};

export default function App() {
  const location = useLocation();
  const routerNavigate = useNavigate();
  const adminMode = /^\/admin(?:\/|$)/.test(location.pathname);
  const activationMode = location.pathname === '/account-activation';
  const recoveryMode = location.pathname === '/account-recovery';
  const [adminBusy, setAdminBusy] = useState(false);
  const [homeView, setHomeView] = useState(
    () => readWorkspaceRoute(location.pathname, location.search).view === 'home',
  );
  const [runsView, setRunsView] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [globalAssistant, setGlobalAssistant] = useState<AssistantInitialContext | null>(null);
  const [assistantTarget, setAssistantTarget] = useState<{
    bookId: string;
    draftId: string;
    version: number;
  } | null>(null);
  const [storedProfile, setProfile] = useState<WorkspaceProfile | null>(null);
  const [profileError, setProfileError] = useState('');
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
  const [workCompany, setWorkCompany] = useState('');
  const [workPeriod, setWorkPeriod] = useState('');
  const [workBook, setWorkBook] = useState('');
  const [monthlyDirty, setMonthlyDirty] = useState(false);
  const changeMonthlyDirty = useCallback((value: boolean) => setMonthlyDirty(value), []);
  const [intakeTarget, setIntakeTarget] = useState('');
  const [intakeTemplate, setIntakeTemplate] = useState<BusinessTemplate | undefined>();
  const [newIntake, setNewIntake] = useState(false);
  const [initialCaseId, setInitialCaseId] = useState('');
  const [deliveryTarget, setDeliveryTarget] = useState('');
  const [proposalTarget, setProposalTarget] = useState('');
  const inlineContext = useRef<{
    scope: string;
    queue: { book_id: string; draft_id: string }[];
    onChanged: (bookId: string, draft: Draft) => void;
    onClose: () => void;
  } | null>(null);
  const [inlineTarget, setInlineTarget] = useState<{ bookId: string; draftId: string } | null>(
    null,
  );
  const [businessView, setBusinessView] = useState(false);
  const [intelligenceView, setIntelligenceView] = useState(false);
  const [queueView, setQueueView] = useState(false);
  const [operationsView, setOperationsView] = useState(false);
  const [catalogView, setCatalogView] = useState(false);
  const [stage8View, setStage8View] = useState<
    | 'review'
    | 'cases'
    | 'learning'
    | 'reconciliation'
    | 'delivery'
    | 'ledger'
    | 'integration'
    | 'revision'
    | 'efficiency'
    | 'closing'
    | 'settlement'
    | 'quality'
    | 'migration'
    | 'monthly'
    | 'balances'
    | 'recurring'
    | null
  >(null);
  const [caseTarget, setCaseTarget] = useState<
    { eventId?: string; bookId?: string; draftId?: string; title?: string } | undefined
  >();
  const [bookOffset, setBookOffset] = useState(0);
  const [draftOffset, setDraftOffset] = useState(0);
  const [sourceOffset, setSourceOffset] = useState(0);
  const [draftLoading, setDraftLoading] = useState(false);
  const [reviewNavigationLoading, setReviewNavigationLoading] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [reviewDirty, setReviewDirty] = useState(false);
  const changeReviewDirty = useCallback((value: boolean) => setReviewDirty(value), []);
  const [operationsCompanies, setOperationsCompanies] = useState<OperationsCompany[]>([]);
  const [operationsTarget, setOperationsTarget] = useState('');
  const [queueTarget, setQueueTarget] = useState('');
  const [queueJobTarget, setQueueJobTarget] = useState('');
  const [businessTarget, setBusinessTarget] = useState('');
  const [migrationTarget, setMigrationTarget] = useState('');
  const [businessBusy, setBusinessBusy] = useState(false);
  const focusBookRef = useRef('');
  const pendingReview = useRef<{ bookId: string; draftId: string } | null>(null);
  const changeBusinessBusy = useCallback((value: boolean) => setBusinessBusy(value), []);
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [bookId, setBookId] = useState('');
  const [storedDetail, setDetail] = useState<BookDetail | null>(null);
  const [draftId, setDraftId] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [listTab, setListTab] = useState<'drafts' | 'sources' | 'exports'>('drafts');
  const [exportHistory, setExportHistory] = useState<
    { id: string; count: number; filename: string; created_at: string }[]
  >([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [modal, setModal] = useState<ModalName>(null);
  const [operationBusy, setBusy] = useState('');
  const [intelligenceBusy, setIntelligenceBusy] = useState(false);
  const busy =
    operationBusy ||
    (reviewNavigationLoading
      ? 'navigation'
      : intelligenceBusy
        ? 'intelligence'
        : businessBusy
          ? 'business'
          : '');
  const changeIntelligenceBusy = useCallback((value: boolean) => setIntelligenceBusy(value), []);
  const [bootLoading, setBootLoading] = useState(true);
  const [bookLoading, setBookLoading] = useState(false);
  const [bootError, setBootError] = useState('');
  const [bookError, setBookError] = useState('');
  const [notice, setNotice] = useState<Notice>(null);
  useEffect(() => {
    setNotice(null);
  }, [stage8View, businessView, bookId]);
  const bookIdRef = useRef(bookId);
  const hadWorkspaceIdentity = useRef(false);
  const loginLocation = useRef(location);
  loginLocation.current = location;
  const actionLock = useRef(false);
  const workbenchReadEpoch = useRef(0),
    bookReadEpoch = useRef(0),
    draftReadEpoch = useRef(0);
  const exportRequest = useRef<{ signature: string; key: string } | null>(null);
  bookIdRef.current = bookId;

  const clearWorkspace = useCallback(() => {
    setRunsView(false);
    setCommandOpen(false);
    setAssistantTarget(null);
    setGlobalAssistant(null);
    inlineContext.current = null;
    clearReviewSession();
    setHomeView(
      hadWorkspaceIdentity.current ||
        readWorkspaceRoute(loginLocation.current.pathname, loginLocation.current.search).view ===
          'home',
    );
    setMigrationTarget('');
    setOperationsTarget('');
    setProfile(null);
    setProfileError('');
    setIntakeTarget('');
    setIntakeTemplate(undefined);
    setNewIntake(false);
    setInitialCaseId('');
    setDeliveryTarget('');
    setProposalTarget('');
    setInlineTarget(null);
    setWorkspaceRevision(0);
    setWorkCompany('');
    setWorkPeriod('');
    setWorkBook('');
    setMonthlyDirty(false);
    workbenchReadEpoch.current++;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    setBusinessView(false);
    setIntelligenceView(false);
    setQueueView(false);
    setQueueTarget('');
    setQueueJobTarget('');
    setStage8View(null);
    setCatalogView(false);
    setOperationsView(false);
    setOperationsCompanies([]);
    setIntelligenceBusy(false);
    setBusinessTarget('');
    setBusinessBusy(false);
    pendingReview.current = null;
    focusBookRef.current = '';
    setCaseTarget(undefined);
    setWorkbench(null);
    setBookId('');
    setDetail(null);
    setDraftId('');
    setSelectedIds([]);
    setExportHistory([]);
    setHistoryError('');
    setHistoryLoading(false);
    setBookError('');
    setBookLoading(false);
    setBootError('');
    setNotice(null);
    setModal(null);
    setFilter('all');
    setSearch('');
    setListTab('drafts');
    setBookOffset(0);
    setDraftOffset(0);
    setSourceOffset(0);
    setDraftLoading(false);
    setDraftError('');
    setBusy('');
    setReviewDirty(false);
    setReviewNavigationLoading(false);
    actionLock.current = false;
    exportRequest.current = null;
  }, []);
  const loginRoute = readWorkspaceRoute(location.pathname, location.search);
  const auth = useAccountingAuth(
    clearWorkspace,
    homeView &&
      loginRoute.view === 'home' &&
      !loginRoute.companyId &&
      !adminMode &&
      !activationMode &&
      !recoveryMode,
  );
  const { me } = auth;
  // Keep one authenticated identity while unmounting all accounting selections
  // and suppressing workspace fetches in the separate administration surface.
  const session = adminMode || activationMode || recoveryMode ? null : auth.session;
  if (session) hadWorkspaceIdentity.current = true;
  const homeBootstrap =
    session && auth.homeBootstrap?.scope_key === session.scope_key ? auth.homeBootstrap : null;
  const profile = storedProfile ?? homeBootstrap?.profile ?? null;
  const bootstrapProfile = useRef<{
    scope_key: string;
    profile: WorkspaceProfile | null;
  } | null>(null);
  // The child consumes task data in its mount effect. Preserve this scope's
  // profile until the parent has applied it, including React's effect replay.
  if (homeBootstrap)
    bootstrapProfile.current = {
      scope_key: homeBootstrap.scope_key,
      profile: homeBootstrap.profile,
    };
  if (bootstrapProfile.current?.scope_key !== session?.scope_key) bootstrapProfile.current = null;
  const restoredRouteScope = useRef('');
  useEffect(() => {
    if (adminMode || activationMode || recoveryMode) {
      clearWorkspace();
      if (auth.session) auth.consumeHomeBootstrap(auth.session.scope_key);
    }
  }, [
    adminMode,
    activationMode,
    recoveryMode,
    clearWorkspace,
    auth.session,
    auth.consumeHomeBootstrap,
  ]);
  useEffect(() => {
    if (!homeView && homeBootstrap) auth.consumeHomeBootstrap(homeBootstrap.scope_key);
  }, [homeView, homeBootstrap, auth.consumeHomeBootstrap]);
  const routeView: WorkspaceRoute['view'] = homeView
    ? 'home'
    : runsView
      ? 'runs'
      : (stage8View ??
        (catalogView
          ? 'catalog'
          : operationsView
            ? 'operations'
            : queueView
              ? 'queue'
              : intelligenceView
                ? 'intelligence'
                : businessView
                  ? 'business'
                  : 'accounting'));
  const routeUsesBook = workspaceUsesBook(routeView);

  const sessionScope = useRef<string | null>(null);
  sessionScope.current = session?.scope_key ?? null;
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k' || !session?.firm_id)
        return;
      if (
        busy ||
        businessBusy ||
        reviewDirty ||
        inlineTarget ||
        proposalTarget ||
        assistantTarget ||
        globalAssistant ||
        modal
      )
        return;
      event.preventDefault();
      setCommandOpen(true);
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [
    session?.scope_key,
    busy,
    businessBusy,
    reviewDirty,
    inlineTarget,
    proposalTarget,
    assistantTarget,
    globalAssistant,
    modal,
  ]);
  const profileEpoch = useRef(0);
  const loadProfile = useCallback(
    async (signal?: AbortSignal, useBootstrap = false) => {
      if (!session?.firm_id) return;
      const epoch = ++profileEpoch.current,
        scope = session.scope_key;
      if (useBootstrap && bootstrapProfile.current?.scope_key === scope) {
        setProfile(bootstrapProfile.current.profile);
        setProfileError('');
        return;
      }
      try {
        const value = await api<WorkspaceProfile>('/workspace/profile', session, { signal });
        if (!signal?.aborted && sessionScope.current === scope && epoch === profileEpoch.current) {
          setProfile(value);
          setProfileError('');
        }
      } catch (error) {
        if (!signal?.aborted && sessionScope.current === scope && epoch === profileEpoch.current) {
          setProfile(null);
          setProfileError(errorMessage(error));
        }
      }
    },
    [session],
  );
  useEffect(() => {
    const controller = new AbortController();
    void loadProfile(controller.signal, true);
    return () => {
      controller.abort();
      profileEpoch.current++;
    };
  }, [loadProfile]);

  useEffect(() => {
    setOperationsCompanies([]);
    // A current capability bit controls navigation. Load the company list only
    // in its own workspace, so it cannot compete with the home inbox for DB slots.
    if (!operationsView || !session?.firm_id || sessionScope.current !== session.scope_key) return;
    const controller = new AbortController();
    void api<{ companies: OperationsCompany[] }>('/operations/companies', session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setOperationsCompanies(value.companies);
      })
      .catch(() => {
        if (!controller.signal.aborted) setOperationsCompanies([]);
      });
    return () => controller.abort();
  }, [session, operationsView]);

  const workbenchPath = useCallback(
    () =>
      '/workbench?' +
      new URLSearchParams({
        limit: '20',
        offset: String(bookOffset),
        filter,
        search,
        ...(focusBookRef.current ? { focus_book_id: focusBookRef.current } : {}),
      }).toString(),
    [bookOffset, filter, search],
  );
  const bookPath = useCallback(
    (id: string) =>
      '/books/' +
      encodeURIComponent(id) +
      '?' +
      new URLSearchParams({
        limit: '20',
        offset: String(draftOffset),
        source_offset: String(sourceOffset),
        summaries: 'true',
        filter,
        ...(pendingReview.current?.bookId === id
          ? { focus_draft_id: pendingReview.current.draftId }
          : {}),
      }).toString(),
    [draftOffset, sourceOffset, filter],
  );
  useEffect(() => {
    setBookOffset(0);
    setDraftOffset(0);
    setSourceOffset(0);
    setSelectedIds([]);
  }, [filter, search]);

  const initialize = useCallback(
    async (signal?: AbortSignal, focusId?: string) => {
      if (!session?.firm_id || sessionScope.current !== session.scope_key) return;
      const scope = session.scope_key;
      const requestEpoch = ++workbenchReadEpoch.current;
      setBootLoading(true);
      setBootError('');
      try {
        const path = focusId
          ? '/workbench?' +
            new URLSearchParams({
              limit: '20',
              offset: '0',
              filter: 'all',
              focus_book_id: focusId,
            }).toString()
          : workbenchPath();
        const data = await api<Workbench>(path, session, { signal });
        if (
          signal?.aborted ||
          sessionScope.current !== scope ||
          workbenchReadEpoch.current !== requestEpoch
        )
          return;
        setWorkbench(data);
        setBookId(
          (current) =>
            pendingReview.current?.bookId ??
            (focusBookRef.current ||
              (data.books.some((book) => book.id === current)
                ? current
                : (data.books[0]?.id ?? ''))),
        );
      } catch (error) {
        if (
          !signal?.aborted &&
          sessionScope.current === scope &&
          workbenchReadEpoch.current === requestEpoch
        )
          setBootError(errorMessage(error));
      } finally {
        if (
          !signal?.aborted &&
          sessionScope.current === scope &&
          workbenchReadEpoch.current === requestEpoch
        )
          setBootLoading(false);
      }
    },
    [session, workbenchPath],
  );

  useEffect(() => {
    if (!routeUsesBook) {
      setBootLoading(false);
      return;
    }
    const controller = new AbortController();
    void initialize(controller.signal);
    return () => {
      controller.abort();
      workbenchReadEpoch.current++;
    };
  }, [initialize, routeUsesBook]);

  useEffect(() => {
    if (!routeUsesBook || !session || !bookId) {
      setDetail(null);
      setBookLoading(false);
      return;
    }
    const controller = new AbortController();
    const requestEpoch = ++bookReadEpoch.current,
      scope = session.scope_key;
    draftReadEpoch.current++;
    setDetail(null);
    setDraftId(pendingReview.current?.bookId === bookId ? pendingReview.current.draftId : '');
    setSelectedIds([]);
    setBookLoading(true);
    setBookError('');
    exportRequest.current = null;
    void api<BookDetail>(bookPath(bookId), session, { signal: controller.signal })
      .then((data) => {
        if (
          controller.signal.aborted ||
          sessionScope.current !== scope ||
          bookReadEpoch.current !== requestEpoch ||
          bookIdRef.current !== bookId
        )
          return;
        setDetail(data);
        const requested =
          pendingReview.current?.bookId === data.book.id
            ? pendingReview.current.draftId
            : undefined;
        setDraftId(
          requested ??
            data.drafts.find((draft) => draft.status === 'draft')?.id ??
            data.drafts[0]?.id ??
            '',
        );
        setReviewNavigationLoading(false);
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          bookReadEpoch.current === requestEpoch
        ) {
          setBookError(errorMessage(error));
          setReviewNavigationLoading(false);
        }
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          bookReadEpoch.current === requestEpoch
        )
          setBookLoading(false);
      });
    return () => controller.abort();
  }, [bookId, session, bookPath, routeUsesBook]);

  useEffect(() => {
    if (!notice || notice.kind === 'error') return;
    const timeout = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    setExportHistory([]);
    setHistoryError('');
    if (routeView !== 'accounting' || !session || !bookId || listTab !== 'exports') return;
    const controller = new AbortController();
    setHistoryLoading(true);
    void api<{ exports: { id: string; count: number; filename: string; created_at: string }[] }>(
      `/books/${encodeURIComponent(bookId)}/exports`,
      session,
      { signal: controller.signal },
    )
      .then((result) => {
        if (!controller.signal.aborted) setExportHistory(result.exports);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setHistoryError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [bookId, session, listTab, storedDetail, routeView]);

  async function refresh(id: string) {
    if (!session || sessionScope.current !== session.scope_key) return;
    const scope = session.scope_key,
      listEpoch = ++workbenchReadEpoch.current,
      detailEpoch = ++bookReadEpoch.current;
    draftReadEpoch.current++;
    let nextWorkbench: Workbench, nextDetail: BookDetail;
    try {
      [nextWorkbench, nextDetail] = await Promise.all([
        api<Workbench>(workbenchPath(), session),
        api<BookDetail>(bookPath(id), session),
      ]);
    } catch (error) {
      if (
        sessionScope.current === scope &&
        bookIdRef.current === id &&
        bookReadEpoch.current === detailEpoch
      ) {
        setDetail(null);
        setBookError(errorMessage(error));
        setBookLoading(false);
        setReviewNavigationLoading(false);
      }
      if (sessionScope.current === scope && workbenchReadEpoch.current === listEpoch)
        setBootLoading(false);
      throw error;
    }
    if (sessionScope.current !== scope) return;
    if (workbenchReadEpoch.current === listEpoch) {
      setWorkbench(nextWorkbench);
      setBootLoading(false);
    }
    if (bookIdRef.current !== id || bookReadEpoch.current !== detailEpoch) return;
    setDetail(nextDetail);
    setBookError('');
    const requested =
      pendingReview.current?.bookId === id ? pendingReview.current.draftId : undefined;
    setDraftId((current) => requested ?? (current || nextDetail.drafts[0]?.id || ''));
    setBookLoading(false);
    setReviewNavigationLoading(false);
    setSelectedIds((current) =>
      current.filter((value) =>
        nextDetail.drafts.some((draft) => draft.id === value && draft.status === 'approved'),
      ),
    );
  }

  async function runAction(name: string, action: () => Promise<void>) {
    if (
      actionLock.current ||
      reviewNavigationLoading ||
      intelligenceBusy ||
      businessBusy ||
      (reviewDirty && name !== 'save')
    )
      return;
    actionLock.current = true;
    // Reads started before a write cannot later restore an older accounting
    // version, even if their network response arrives after the mutation.
    workbenchReadEpoch.current++;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    const scope = sessionScope.current;
    setBusy(name);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      if (
        sessionScope.current === scope &&
        !(error instanceof DOMException && error.name === 'AbortError')
      )
        setNotice({ kind: 'error', text: errorMessage(error) });
    } finally {
      if (sessionScope.current === scope) {
        actionLock.current = false;
        setBusy('');
      }
    }
  }

  function chooseBook(id: string) {
    if (id === bookId || busy || reviewDirty) return;
    focusBookRef.current = '';
    pendingReview.current = null;
    // Clear related data synchronously, before the next request can render.
    setDetail(null);
    setDraftId('');
    setSelectedIds([]);
    setBookError('');
    setBookId(id);
    setListTab('drafts');
    setDraftOffset(0);
    setSourceOffset(0);
  }

  function openDraftInBook(id: string, targetDraftId: string) {
    if (!session || sessionScope.current !== session.scope_key || !targetDraftId) return;
    setHomeView(false);
    workbenchReadEpoch.current++;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    focusBookRef.current = id;
    pendingReview.current = { bookId: id, draftId: targetDraftId };
    setDetail(null);
    setDraftId(targetDraftId);
    setDraftError('');
    setReviewNavigationLoading(true);
    setSelectedIds([]);
    setBookOffset(0);
    setDraftOffset(0);
    setSourceOffset(0);
    setFilter('all');
    setSearch('');
    setBookId(id);
    setStage8View(null);
    setCatalogView(false);
    setOperationsView(false);
    setQueueView(false);
    setIntelligenceView(false);
    setBusinessView(false);
    if (routeUsesBook && bookId === id)
      void refresh(id).catch((error) => {
        if (sessionScope.current === session.scope_key && bookIdRef.current === id) {
          setBookError(errorMessage(error));
          setReviewNavigationLoading(false);
        }
      });
    // A target outside the current twenty-book page must be brought into the
    // authorized workbench page before the membership guard can select it.
    if (routeUsesBook) void initialize(undefined, id);
  }

  function acceptMutatedDraft(id: string, draft: Draft) {
    if (!session || sessionScope.current !== session.scope_key || bookIdRef.current !== id) return;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    setDetail((current) =>
      current?.book.id === id
        ? {
            ...current,
            drafts: current.drafts.map((row) =>
              row.id === draft.id && row.version <= draft.version
                ? { ...draft, detail_loaded: true }
                : row,
            ),
          }
        : current,
    );
  }

  async function generateSuggestions() {
    if (!session || !bookId) return;
    const id = bookId;
    await runAction('suggest', async () => {
      const result = await api<{ created: number }>(
        `/books/${encodeURIComponent(id)}/suggestions`,
        session,
        { method: 'POST', body: {} },
      );
      await refresh(id);
      setListTab('drafts');
      setNotice({
        kind: 'success',
        text: result.created
          ? `已生成 ${result.created} 张凭证草稿，请逐张核对依据后审核。`
          : '没有待生成建议的流水。已有草稿不会重复生成。',
      });
    });
  }

  async function saveDraft(
    draft: Draft,
    description: string,
    lines: JournalLine[],
    correctionReason = '',
  ) {
    if (!session) return;
    const id = bookId;
    await runAction('save', async () => {
      const saved = await api<Draft>(
        `/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}`,
        session,
        {
          method: 'PATCH',
          body: {
            expected_version: draft.version,
            description,
            lines,
            correction_reason: correctionReason,
          },
        },
      );
      acceptMutatedDraft(id, saved);
      await refresh(id);
      setNotice({ kind: 'success', text: '草稿已保存，金额和科目校验结果已更新。' });
    });
  }

  async function approveDraft(draft: Draft) {
    if (!session) return;
    const id = bookId;
    await runAction('approve', async () => {
      const approved = await api<Draft>(
        `/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}/approve`,
        session,
        {
          method: 'POST',
          body: { expected_version: draft.version },
        },
      );
      acceptMutatedDraft(id, approved);
      await refresh(id);
      setNotice({ kind: 'success', text: '凭证已审核，可选中后导出。审核不会写入外部正式账本。' });
    });
  }

  async function exportDrafts() {
    if (!session || !selectedIds.length) return;
    const id = bookId;
    const ids = [...selectedIds].sort();
    const signature = `${id}:${ids.join(',')}`;
    if (exportRequest.current?.signature !== signature)
      exportRequest.current = { signature, key: crypto.randomUUID() };
    const key = exportRequest.current.key;
    await runAction('export', async () => {
      const result = await api<{ id: string; csv: string; count: number; filename: string }>(
        `/books/${encodeURIComponent(id)}/exports`,
        session,
        {
          method: 'POST',
          body: { draft_ids: ids, idempotency_key: key },
        },
      );
      downloadCsv(result.csv, result.filename);
      await refresh(id);
      setSelectedIds([]);
      exportRequest.current = null;
      setNotice({
        kind: 'success',
        text: `已导出 ${result.count} 张凭证。请在目标财务系统导入后核验回执与金额。`,
      });
    });
  }

  const stats = workbench?.stats;
  const filteredBooks = workbench?.books ?? [];
  const detail = filteredBooks.some((book) => book.id === bookId) ? storedDetail : null;
  useEffect(() => {
    if (!routeUsesBook || busy || bootLoading || !workbench) return;
    if (!filteredBooks.some((book) => book.id === bookId)) {
      setDetail(null);
      if (pendingReview.current?.bookId === bookId || focusBookRef.current === bookId) {
        setDraftId(pendingReview.current?.draftId ?? '');
        setSelectedIds([]);
        if (!bookLoading)
          setBookError(
            (current) =>
              current || '所选账套或凭证不存在或当前无权访问，请核对权限或手动选择另一账套。',
          );
        return;
      }
      setDraftId('');
      setSelectedIds([]);
      setBookId(filteredBooks[0]?.id ?? '');
    }
  }, [filter, search, workbench, bookId, busy, bootLoading, bookLoading, routeUsesBook]);
  const drafts = detail?.drafts ?? [];
  const activeDraft = draftId ? drafts.find((draft) => draft.id === draftId) : drafts[0];
  useEffect(() => {
    setDraftError('');
    if (
      routeView !== 'accounting' ||
      !session ||
      !activeDraft ||
      activeDraft.detail_loaded !== false
    ) {
      setDraftLoading(false);
      return;
    }
    const controller = new AbortController();
    const requestEpoch = ++draftReadEpoch.current,
      scope = session.scope_key;
    const id = activeDraft.id;
    const version = activeDraft.version;
    setDraftLoading(true);
    void api<Draft>(
      '/books/' + encodeURIComponent(bookId) + '/drafts/' + encodeURIComponent(id),
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDetail((current) =>
            current?.book.id === bookId
              ? {
                  ...current,
                  drafts: current.drafts.map((row) =>
                    row.id === id &&
                    row.version === version &&
                    value.id === id &&
                    value.version >= row.version
                      ? value
                      : row,
                  ),
                }
              : current,
          );
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDraftError(errorMessage(error));
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDraftLoading(false);
      });
    return () => controller.abort();
  }, [
    session,
    bookId,
    activeDraft?.id,
    activeDraft?.version,
    activeDraft?.detail_loaded,
    storedDetail,
    routeView,
  ]);

  const approvedDraftIds = drafts
    .filter((draft) => draft.status === 'approved')
    .map((draft) => draft.id);
  const navItems: { id: Filter; label: string; icon: typeof LayoutDashboard; count?: number }[] = [
    { id: 'all', label: '会计工作台', icon: LayoutDashboard },
    { id: 'pending', label: '待审核凭证', icon: ClipboardCheck, count: stats?.pending_count },
    { id: 'exception', label: '异常处理', icon: TriangleAlert, count: stats?.exception_count },
    { id: 'approved', label: '审核与导出', icon: FileCheck2, count: stats?.approved_count },
  ];

  const [drawerDirty, setDrawerDirty] = useState(false);
  useEffect(() => {
    const changed = (event: Event) => setDrawerDirty(!!(event as CustomEvent<boolean>).detail);
    window.addEventListener('accounting:review-dirty', changed);
    return () => window.removeEventListener('accounting:review-dirty', changed);
  }, []);
  const workspaceRoute: WorkspaceRoute = {
    view: routeView,
    bookId: routeUsesBook ? bookId : workBook,
    companyId: workCompany,
    period: workPeriod,
    draftId: routeView === 'accounting' ? draftId : '',
    filter,
    listTab,
    search,
    eventId: businessTarget,
    intakeId: intakeTarget,
    caseId: initialCaseId,
    batchId: queueTarget,
    jobId: queueJobTarget,
    migrationId: migrationTarget,
    deliveryId: deliveryTarget,
    proposalId: proposalTarget,
    reviewBookId: inlineTarget?.bookId ?? '',
    reviewDraftId: inlineTarget?.draftId ?? '',
  };
  const routeGuard = useWorkspaceRoute({
    scope: session?.firm_id ? session.scope_key : undefined,
    value: workspaceRoute,
    metadata:
      storedDetail?.book.id === bookId
        ? { companyId: storedDetail.book.company_id, period: storedDetail.book.period }
        : undefined,
    locked:
      adminBusy ||
      !!busy ||
      businessBusy ||
      reviewDirty ||
      drawerDirty ||
      monthlyDirty ||
      modal === 'import' ||
      !!globalAssistant ||
      !!assistantTarget,
    canDiscard:
      (reviewDirty || drawerDirty || monthlyDirty) &&
      !operationBusy &&
      !intelligenceBusy &&
      !reviewNavigationLoading,
    onRestore: (route) => {
      const initialHome =
        restoredRouteScope.current !== session?.scope_key &&
        bootstrapProfile.current?.scope_key === session?.scope_key &&
        !route.companyId;
      restoredRouteScope.current = session?.scope_key ?? '';
      setHomeView(route.view === 'home');
      setRunsView(route.view === 'runs');
      setBusinessView(route.view === 'business');
      setQueueView(route.view === 'queue');
      setOperationsView(route.view === 'operations');
      setIntelligenceView(route.view === 'intelligence');
      setCatalogView(route.view === 'catalog');
      setStage8View(
        [
          'review',
          'cases',
          'learning',
          'reconciliation',
          'delivery',
          'ledger',
          'integration',
          'revision',
          'efficiency',
          'closing',
          'settlement',
          'quality',
          'migration',
          'monthly',
          'balances',
          'recurring',
        ].includes(route.view)
          ? (route.view as typeof stage8View)
          : null,
      );
      if (!route.reviewDraftId || route.view !== 'review') {
        inlineContext.current?.onClose();
        inlineContext.current = null;
      }
      setGlobalAssistant(null);
      setAssistantTarget(null);
      setCommandOpen(false);
      setModal(null);
      setReviewDirty(false);
      setDrawerDirty(false);
      setMonthlyDirty(false);
      setWorkCompany(route.companyId);
      setWorkPeriod(route.period);
      setWorkBook(['review', 'cases', 'business'].includes(route.view) ? route.bookId : '');
      setBusinessBusy(false);
      setSelectedIds([]);
      setBusinessTarget(route.eventId);
      setIntakeTarget(route.intakeId);
      setInitialCaseId(route.caseId);
      setQueueTarget(route.batchId);
      setQueueJobTarget(route.jobId);
      setMigrationTarget(route.migrationId);
      setDeliveryTarget(route.deliveryId);
      setProposalTarget(route.proposalId);
      setInlineTarget(
        route.reviewBookId && route.reviewDraftId
          ? { bookId: route.reviewBookId, draftId: route.reviewDraftId }
          : null,
      );
      setFilter(route.filter);
      setListTab(route.listTab);
      setSearch(route.search);
      setCaseTarget(undefined);
      setIntakeTemplate(undefined);
      setNewIntake(false);
      focusBookRef.current = route.bookId;
      pendingReview.current =
        route.bookId && route.draftId ? { bookId: route.bookId, draftId: route.draftId } : null;
      setDraftId(route.draftId);
      setBookOffset(0);
      setDraftOffset(0);
      setSourceOffset(0);
      if (route.bookId && workspaceUsesBook(route.view)) {
        setDetail(null);
        setBookId(route.bookId);
        if (routeUsesBook) void initialize(undefined, route.bookId);
        if (routeUsesBook && route.bookId === bookId)
          void refresh(route.bookId).catch((failure) => setBookError(errorMessage(failure)));
      }
      if (route.view === 'home' && !initialHome) setWorkspaceRevision((value) => value + 1);
    },
  });

  if (recoveryMode)
    return (
      <Suspense
        fallback={
          <div className="loading-block">
            <Spinner />
            正在打开密码找回…
          </div>
        }
      >
        <AccountRecovery
          key={location.hash || location.key}
          config={auth.config}
          onLogout={auth.logout}
        />
      </Suspense>
    );
  if (activationMode)
    return (
      <Suspense
        fallback={
          <div className="loading-block">
            <Spinner />
            正在打开账号激活…
          </div>
        }
      >
        <AccountActivation
          key={location.hash || location.key}
          config={auth.config}
          session={auth.session}
          loading={auth.loading}
          error={auth.error}
          onLogin={auth.login}
          onRefresh={auth.refreshIdentity}
          onLogout={auth.logout}
        />
      </Suspense>
    );
  if (adminMode && auth.session && me)
    return (
      <Suspense
        fallback={
          <div className="loading-block">
            <Spinner />
            正在打开管理后台…
          </div>
        }
      >
        {routeGuard.blocked && (
          <RouteLeaveDialog
            canDiscard={false}
            onStay={routeGuard.cancel}
            onLeave={routeGuard.proceed}
          />
        )}
        <AdminMfaGate
          key={auth.session.scope_key}
          required={
            auth.config?.admin_requires_mfa === true &&
            (me.permissions.manage_admin === true || me.permissions.manage_firm)
          }
          onVerified={auth.refreshIdentity}
          onLogout={auth.logout}
        >
          <AdminWorkspace
            session={auth.session}
            me={me}
            loading={auth.loading}
            onLogout={auth.logout}
            onSwitchFirm={auth.switchFirm}
            onBusyChange={setAdminBusy}
          />
        </AdminMfaGate>
      </Suspense>
    );
  if (!session)
    return (
      <AuthScreen
        admin={adminMode}
        config={auth.config}
        loading={auth.loading}
        error={auth.error}
        onLogin={auth.login}
        onRetry={auth.initialize}
      />
    );
  if (!session.firm_id)
    return (
      <NoAuthorization
        name={session.user_name}
        loading={auth.loading}
        error={auth.error}
        onCreate={auth.createFirm}
        onLogout={auth.logout}
        allowCreate={auth.config?.allow_firm_signup === true || auth.config?.mode === 'local'}
      />
    );
  const canSeeAccounting =
    profile?.can_accounting ??
    (!!stats?.book_count && !['client_employee', 'client_member'].includes(session.role ?? ''));
  const navigationLocked =
    !!busy ||
    businessBusy ||
    reviewDirty ||
    monthlyDirty ||
    !!inlineTarget ||
    !!proposalTarget ||
    !!assistantTarget ||
    !!globalAssistant ||
    modal === 'import';
  function navigate(view: AgentNavigationView, fromAssistant = false) {
    if (
      navigationLocked &&
      (!fromAssistant ||
        busy ||
        businessBusy ||
        reviewDirty ||
        monthlyDirty ||
        inlineTarget ||
        proposalTarget ||
        assistantTarget ||
        modal)
    )
      return;
    setHomeView(view === 'home');
    setRunsView(view === 'runs');
    setBusinessView(view === 'business');
    setQueueView(view === 'queue');
    setIntelligenceView(view === 'intelligence');
    setOperationsView(view === 'operations');
    setCatalogView(false);
    setStage8View(
      ['review', 'cases', 'revision', 'monthly'].includes(view)
        ? (view as 'review' | 'cases' | 'revision' | 'monthly')
        : null,
    );
    setBusinessTarget('');
    setWorkCompany('');
    setWorkPeriod(view === 'monthly' ? workPeriod || new Date().toISOString().slice(0, 7) : '');
    setWorkBook('');
    setMigrationTarget('');
    setIntakeTarget('');
    setNewIntake(false);
    setIntakeTemplate(undefined);
    setQueueTarget('');
    setQueueJobTarget('');
    setCaseTarget(undefined);
    setInitialCaseId('');
    setDeliveryTarget('');
    if (view === 'accounting') {
      setFilter('all');
      setSearch('');
      focusBookRef.current = '';
      pendingReview.current = null;
    }
    if (view === 'home') setWorkspaceRevision((n) => n + 1);
  }
  function openWorkspaceTask(task: WorkspaceTask) {
    if (navigationLocked) return;
    const target = taskDestination(task);
    if (target.type === 'review' && target.bookId) {
      setInlineTarget({ bookId: target.bookId, draftId: target.id });
      return;
    }
    if (target.type === 'proposal') {
      setProposalTarget(target.id);
      return;
    }
    if (target.type === 'business' || target.type === 'intake') {
      navigate('business');
      if (target.type === 'intake') setIntakeTarget(target.id);
      else setBusinessTarget(target.id);
    } else if (target.type === 'case') {
      navigate('cases');
      setInitialCaseId(target.id);
    } else if (target.type === 'ocr' && target.id) {
      navigate('queue');
      setQueueTarget(target.id);
      setQueueJobTarget(target.jobId);
    } else if (target.type === 'export' && target.bookId) {
      navigate('accounting');
      setBookId(target.bookId);
      focusBookRef.current = target.bookId;
      setFilter('approved');
      setListTab('drafts');
      if (routeUsesBook) void initialize(undefined, target.bookId);
    } else if (target.type === 'delivery' && target.bookId) {
      navigate('accounting');
      setBookId(target.bookId);
      focusBookRef.current = target.bookId;
      setDeliveryTarget(target.id);
      setStage8View('delivery');
      if (routeUsesBook) void initialize(undefined, target.bookId);
    } else if (target.type === 'migration' && target.bookId) {
      navigate('accounting');
      setBookId(target.bookId);
      focusBookRef.current = target.bookId;
      setMigrationTarget(target.id);
      setStage8View('migration');
      if (routeUsesBook) void initialize(undefined, target.bookId);
    } else if (target.type === 'alert') {
      navigate('operations');
      setOperationsTarget(target.id);
    }
  }
  function openInlineReview(
    target: { book_id: string; draft_id: string },
    queue: { book_id: string; draft_id: string }[],
    onChanged: (bookId: string, draft: Draft) => void,
    onClose: () => void,
  ) {
    inlineContext.current = { scope: session!.scope_key, queue, onChanged, onClose };
    setInlineTarget({ bookId: target.book_id, draftId: target.draft_id });
  }
  function openNewIntake(template: BusinessTemplate) {
    navigate('business');
    setIntakeTemplate(template);
    setNewIntake(true);
  }
  function openMonthlyTarget(target: MonthlyDestination) {
    if (navigationLocked) return;
    navigate(
      target.view === 'business' || target.view === 'review' || target.view === 'cases'
        ? target.view
        : 'accounting',
    );
    setWorkCompany(target.companyId);
    setWorkPeriod(target.period);
    setWorkBook(['review', 'cases', 'business'].includes(target.view) ? (target.bookId ?? '') : '');
    if (target.bookId && workspaceUsesBook(target.view)) {
      setBookId(target.bookId);
      focusBookRef.current = target.bookId;
      if (routeUsesBook) void initialize(undefined, target.bookId);
    }
    if (['delivery', 'closing', 'efficiency', 'balances', 'recurring'].includes(target.view))
      setStage8View(target.view as typeof stage8View);
    if (target.filter) setFilter(target.filter);
  }
  const showBusiness =
    !runsView &&
    !stage8View &&
    !catalogView &&
    !operationsView &&
    !queueView &&
    !intelligenceView &&
    (businessView || (!!profile && !canSeeAccounting));
  async function openAgentRun(route: AgentRoute) {
    const currentSession = session;
    if (navigationLocked || !currentSession) return;
    const started = currentSession.scope_key;
    if (route.type === 'proposal') {
      setProposalTarget(route.job_id);
      return;
    }
    if (route.type === 'migration' && route.book_id) {
      navigate('accounting');
      setBookId(route.book_id);
      focusBookRef.current = route.book_id;
      setMigrationTarget(route.id);
      setStage8View('migration');
      if (routeUsesBook) void initialize(undefined, route.book_id);
      return;
    }
    if (route.type === 'ocr') {
      try {
        const value = await api<{ id: string; batch_id: string }>(
          `/intelligence/jobs/${encodeURIComponent(route.job_id)}`,
          currentSession,
        );
        if (sessionScope.current !== started || value.id !== route.job_id) return;
        navigate('queue');
        setQueueTarget(value.batch_id);
        setQueueJobTarget(value.id);
      } catch (failure) {
        if (sessionScope.current === started)
          setNotice({ kind: 'error', text: errorMessage(failure) });
      }
    }
  }
  function openCommandBook(book: Book, fromAssistant = false) {
    if (
      navigationLocked &&
      (!fromAssistant ||
        busy ||
        businessBusy ||
        reviewDirty ||
        inlineTarget ||
        proposalTarget ||
        assistantTarget ||
        modal)
    )
      return;
    navigate('accounting', fromAssistant);
    setBookId(book.id);
    focusBookRef.current = book.id;
    if (routeUsesBook) void initialize(undefined, book.id);
  }
  function openGlobalAssistant() {
    if (navigationLocked || modal || commandOpen) return;
    // Keep global browsing separate from the current voucher's explicit scope.
    setGlobalAssistant(
      routeUsesBook && detail
        ? {
            companyId: detail.book.company_id,
            bookId: detail.book.id,
            draftId: routeView === 'accounting' ? activeDraft?.id : undefined,
          }
        : {},
    );
  }
  async function openAssistantBook(targetBookId: string) {
    if (!session) return;
    const scope = session.scope_key;
    try {
      const value = await api<BookDetail>('/books/' + encodeURIComponent(targetBookId), session);
      if (sessionScope.current !== scope || value.book.id !== targetBookId) return;
      openCommandBook(value.book, true);
    } catch (failure) {
      if (sessionScope.current === scope) setNotice({ kind: 'error', text: errorMessage(failure) });
    }
  }
  function openAssistantEvidence(evidence: AssistantEvidence) {
    setGlobalAssistant(null);
    if (evidence.book_id && evidence.draft_id) {
      setInlineTarget({ bookId: evidence.book_id, draftId: evidence.draft_id });
      return;
    }
    if (evidence.event_id) {
      navigate('business', true);
      setBusinessTarget(evidence.event_id);
      return;
    }
    if (evidence.book_id) {
      void openAssistantBook(evidence.book_id);
    }
  }
  function openAssistantTask(receipt: AssistantActionReceipt) {
    setGlobalAssistant(null);
    setWorkspaceRevision((value) => value + 1);
    if (receipt.batch_id) {
      navigate('queue', true);
      setQueueTarget(receipt.batch_id);
      return;
    }
    if (receipt.kind === 'proposal_batch' && receipt.jobs[0]) {
      setProposalTarget(receipt.jobs[0].id);
      return;
    }
    if (receipt.kind === 'bank_drafts' && receipt.draft_refs?.[0]) {
      const target = receipt.draft_refs[0];
      setInlineTarget({ bookId: target.book_id, draftId: target.id });
    }
  }
  const quickCommands: AgentCommand[] = [
    {
      id: 'assistant',
      label: '会计助手',
      description: '按企业与账期提问、预览后台任务',
      run: () => setGlobalAssistant({}),
    },
    {
      id: 'home',
      label: '我的待办',
      description: '集中处理授权范围内的下一步工作',
      run: () => navigate('home'),
    },
    {
      id: 'business',
      label: '业务事项',
      description: '录入、补件与审批进度',
      run: () => navigate('business'),
    },
    {
      id: 'runs',
      label: '后台运行',
      description: '查看真实识别、分录与迁移任务',
      run: () => navigate('runs'),
    },
    {
      id: 'cases',
      label: '异常与补件',
      description: '核对问题并联系资料提供人',
      run: () => navigate('cases'),
    },
    ...(canSeeAccounting
      ? [
          {
            id: 'monthly',
            label: '客户月度进度',
            description: '按企业与账期定位复核、异常及交付',
            run: () => navigate('monthly'),
          },
          {
            id: 'companies',
            label: '客户企业',
            description: '进入已授权账套与交付工作区',
            run: () => navigate('accounting'),
          },
        ]
      : []),
    ...(profile?.can_review
      ? [
          {
            id: 'review',
            label: '跨企业批量复核',
            description: '准备可比较的凭证复核包',
            run: () => navigate('review'),
          },
        ]
      : []),
  ];

  return (
    <div className="app-shell agent-shell">
      {routeGuard.blocked && (
        <RouteLeaveDialog
          canDiscard={routeGuard.canDiscard}
          onStay={routeGuard.cancel}
          onLeave={routeGuard.proceed}
        />
      )}
      <AgentNavigation
        session={session}
        current={
          stage8View === 'monthly'
            ? 'monthly'
            : runsView
              ? 'runs'
              : homeView
                ? 'home'
                : showBusiness
                  ? 'business'
                  : stage8View === 'review'
                    ? 'review'
                    : stage8View === 'cases'
                      ? 'cases'
                      : queueView
                        ? 'queue'
                        : intelligenceView
                          ? 'intelligence'
                          : operationsView
                            ? 'operations'
                            : 'accounting'
        }
        canAccounting={canSeeAccounting}
        canMonthly={canSeeAccounting || !!profile?.can_manage}
        canReview={!!profile?.can_review}
        canOperations={
          profile?.can_operations ??
          (!!profile?.can_manage || !!profile?.can_finance || !!me?.permissions.manage_firm)
        }
        locked={navigationLocked || !!modal || commandOpen}
        onNavigate={navigate}
        onAccountingFilter={(value) => {
          navigate('accounting');
          setFilter(value);
        }}
        onCommand={() => setCommandOpen(true)}
        onAssistant={openGlobalAssistant}
        firmSelector={
          (me?.firms.length ?? 0) > 1 ? (
            <select
              aria-label="切换事务所"
              value={session.firm_id}
              disabled={navigationLocked || auth.loading || commandOpen}
              onChange={(event) => void auth.switchFirm(event.target.value)}
            >
              {me?.firms.map((firm) => (
                <option key={firm.id} value={firm.id}>
                  {firm.name}
                </option>
              ))}
            </select>
          ) : (
            <strong>{session.firm_name}</strong>
          )
        }
        footer={
          <>
            {(me?.permissions.manage_admin || me?.permissions.manage_firm) && (
              <button
                className="help-link"
                disabled={navigationLocked || commandOpen}
                onClick={() => void routerNavigate('/admin')}
              >
                <ShieldCheck size={16} />
                组织管理后台
                <ArrowUpRight size={13} />
              </button>
            )}
            {session.mode === 'local' && !!auth.config?.personas?.length && (
              <label className="persona-switch">
                <span>切换验证身份</span>
                <select
                  aria-label="切换验证身份"
                  value={auth.persona}
                  disabled={navigationLocked || auth.loading || commandOpen}
                  onChange={(event) => void auth.choosePersona(event.target.value)}
                >
                  {auth.config.personas.map((persona) => (
                    <option key={persona.id} value={persona.id}>
                      {persona.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {me?.permissions.manage_firm && (
              <button
                className="help-link"
                disabled={navigationLocked || commandOpen}
                onClick={() => {
                  setNotice(null);
                  setModal('access');
                }}
              >
                <Users size={16} />
                成员与账套授权
                <ArrowUpRight size={13} />
              </button>
            )}
            <button
              type="button"
              className="help-link"
              disabled={navigationLocked || commandOpen}
              onClick={() => setModal('help')}
            >
              <CircleHelp size={16} />
              使用说明与版本范围
              <ArrowUpRight size={13} />
            </button>
            <div className="user-block">
              <span className="avatar">{session.user_name.slice(0, 1)}</span>
              <div>
                <strong>{session.user_name}</strong>
                <span>
                  {profile
                    ? roleCopy[profile.role].name
                    : session.role
                      ? roleLabels[session.role]
                      : '工作空间成员'}
                </span>
              </div>
              {session.mode === 'supabase' ? (
                <button
                  type="button"
                  className="icon-button logout-button"
                  aria-label="退出登录"
                  disabled={navigationLocked || auth.loading}
                  onClick={() => void auth.logout()}
                >
                  <LogOut size={17} />
                </button>
              ) : (
                <ShieldCheck size={17} />
              )}
            </div>
          </>
        }
      />

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            工作空间
            <ChevronRight size={13} />
            <span>
              {stage8View === 'monthly'
                ? '客户月度进度'
                : stage8View === 'balances'
                  ? '参考余额与月度差异'
                  : stage8View === 'recurring'
                    ? '固定事项草稿'
                    : runsView
                      ? '后台运行'
                      : homeView
                        ? '我的待办'
                        : stage8View === 'migration'
                          ? '旧账迁移'
                          : stage8View === 'quality'
                            ? '试点评测'
                            : stage8View === 'settlement'
                              ? '收付款核销'
                              : stage8View === 'closing'
                                ? '月结预检'
                                : stage8View === 'revision'
                                  ? '事实修订与调整'
                                  : stage8View === 'efficiency'
                                    ? '人工效率'
                                    : stage8View === 'integration'
                                      ? '财务软件接入'
                                      : stage8View === 'ledger'
                                        ? '长期账套与主数据'
                                        : stage8View === 'reconciliation'
                                          ? '流水与业务匹配'
                                          : stage8View === 'delivery'
                                            ? '导入回执核验'
                                            : stage8View === 'review'
                                              ? '跨企业批量复核'
                                              : stage8View === 'cases'
                                                ? '异常与补件'
                                                : stage8View === 'learning'
                                                  ? '审核样例与企业规则'
                                                  : catalogView
                                                    ? '科目与模板'
                                                    : operationsView
                                                      ? '费用与运行监控'
                                                      : queueView
                                                        ? '票据处理任务'
                                                        : intelligenceView
                                                          ? '票据核对异常'
                                                          : showBusiness
                                                            ? '业务事项'
                                                            : navItems.find(
                                                                (item) => item.id === filter,
                                                              )?.label}
            </span>
          </div>
          <div className="agent-topbar-actions">
            <button
              className="agent-topbar-assistant"
              aria-label="问会计助手"
              disabled={navigationLocked || !!modal || commandOpen}
              onClick={openGlobalAssistant}
            >
              <Sparkles size={15} />
              <span>问会计助手</span>
            </button>
            <button
              className="agent-topbar-command"
              aria-label="查找企业与工作入口"
              disabled={navigationLocked || !!modal}
              onClick={() => setCommandOpen(true)}
            >
              <Search size={15} />
              <span>查找</span>
              <kbd>Ctrl K</kbd>
            </button>
            <div className="environment">
              <span className="status-dot" />
              {session.mode === 'local' ? '本地验证环境' : '已验证账户'}
              <span className="topbar-separator" />
              CNY 人民币
            </div>
          </div>
        </header>
        <ActivityTimer
          key={session.scope_key}
          session={session}
          enabled={!homeView && !runsView && stage8View !== 'efficiency'}
          bookId={
            !showBusiness &&
            !operationsView &&
            !queueView &&
            !intelligenceView &&
            !['review', 'cases', 'revision', 'monthly'].includes(stage8View ?? '')
              ? bookId
              : undefined
          }
          onOpen={() => {
            if (busy || businessBusy || reviewDirty || monthlyDirty) return;
            setHomeView(false);
            setStage8View('efficiency');
          }}
        />
        <Suspense
          fallback={
            <main>
              <div className="loading-block">
                <Spinner />
                正在载入工作台…
              </div>
            </main>
          }
        >
          {homeView ? (
            <WorkspaceHome
              key={`${session.scope_key}:${workCompany}`}
              initialCompanyId={workCompany}
              session={session}
              profile={profile}
              profileError={profileError}
              initialTasks={homeBootstrap?.tasks ?? null}
              onInitialTasksConsumed={() => auth.consumeHomeBootstrap(session.scope_key)}
              onRetryProfile={() => void loadProfile()}
              revision={workspaceRevision}
              paused={navigationLocked}
              onOpen={openWorkspaceTask}
              onNew={openNewIntake}
              onAccounting={() => navigate('accounting')}
              onReview={() => navigate('review')}
              onRuns={() => navigate('runs')}
            />
          ) : runsView ? (
            <AgentActivityWorkspace
              key={session.scope_key}
              session={session}
              revision={workspaceRevision}
              onOpen={openAgentRun}
              onBack={() => navigate('home')}
              onBusyChange={changeBusinessBusy}
            />
          ) : stage8View === 'monthly' ? (
            <MonthlyClientWorkbench
              key={session.scope_key}
              session={session}
              period={workPeriod}
              onPeriodChange={setWorkPeriod}
              onOpen={openMonthlyTarget}
              revision={workspaceRevision}
            />
          ) : stage8View === 'balances' && bookId ? (
            <MonthlyReconciliationWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onDirtyChange={changeMonthlyDirty}
              onChanged={() => {
                void refresh(bookId);
                setWorkspaceRevision((n) => n + 1);
              }}
            />
          ) : stage8View === 'recurring' && bookId ? (
            <RecurringEntries
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onDirtyChange={changeMonthlyDirty}
              onOpenDraft={(id) => openDraftInBook(bookId, id)}
              onChanged={() => {
                void refresh(bookId);
                setWorkspaceRevision((n) => n + 1);
              }}
            />
          ) : stage8View === 'migration' && bookId ? (
            <LegacyMigrationWorkspace
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              initialProjectId={migrationTarget || undefined}
              onBack={() => {
                setMigrationTarget('');
                setStage8View(null);
              }}
              onBusyChange={changeBusinessBusy}
              onOpenDraft={openDraftInBook}
              onChanged={() => {
                void refresh(bookId);
                setWorkspaceRevision((n) => n + 1);
              }}
            />
          ) : stage8View === 'quality' && bookId ? (
            <QualityWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onOpenDraft={openDraftInBook}
            />
          ) : stage8View === 'settlement' && bookId ? (
            <SettlementWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => {
                void refresh(bookId);
              }}
              onOpenDraft={(id) => openDraftInBook(bookId, id)}
            />
          ) : stage8View === 'closing' && bookId ? (
            <ClosingWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => {
                void refresh(bookId);
                void initialize();
              }}
              onOpenIssue={(issue) => {
                if (issue.type === 'draft') {
                  openDraftInBook(issue.bookId, issue.id);
                } else if (issue.type === 'event') {
                  setBusinessTarget(issue.id);
                  setStage8View(null);
                  setBusinessView(true);
                } else if (issue.type === 'export') {
                  setDeliveryTarget(issue.id);
                  setStage8View('delivery');
                } else if (issue.type === 'revision') {
                  setBusinessTarget('');
                  setStage8View('revision');
                } else if (issue.type === 'case') {
                  setCaseTarget(undefined);
                  setInitialCaseId(issue.id);
                  setStage8View('cases');
                } else if (issue.type === 'proposal_job') {
                  setProposalTarget(issue.id);
                } else if (issue.type === 'ocr_job') {
                  void api<{ batch_id: string }>(
                    '/intelligence/jobs/' + encodeURIComponent(issue.id),
                    session,
                  )
                    .then((job) => {
                      if (sessionScope.current !== session.scope_key) return;
                      setQueueTarget(job.batch_id);
                      setQueueJobTarget(issue.id);
                      setStage8View(null);
                      setQueueView(true);
                    })
                    .catch((error) => setNotice({ kind: 'error', text: errorMessage(error) }));
                } else {
                  setStage8View(null);
                  setFilter('all');
                  void refresh(issue.bookId);
                }
              }}
            />
          ) : stage8View === 'revision' ? (
            <RevisionWorkbench
              key={session.scope_key + ':' + businessTarget}
              session={session}
              initialEventId={businessTarget}
              onBusyChange={changeBusinessBusy}
              onChanged={() => {
                if (bookId) void refresh(bookId);
              }}
            />
          ) : stage8View === 'efficiency' && bookId ? (
            <EfficiencyWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
            />
          ) : stage8View === 'integration' && bookId ? (
            <IntegrationWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
            />
          ) : stage8View === 'ledger' && bookId ? (
            <LedgerWorkspace
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => {
                void refresh(bookId);
                void initialize();
              }}
            />
          ) : stage8View === 'reconciliation' && bookId ? (
            <ReconciliationWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => {
                void refresh(bookId);
              }}
            />
          ) : stage8View === 'delivery' && bookId ? (
            <DeliveryWorkbench
              key={session.scope_key + ':' + bookId + ':' + deliveryTarget}
              session={session}
              bookId={bookId}
              initialExportId={deliveryTarget}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
            />
          ) : stage8View === 'review' ? (
            <ReviewWorkbench
              key={session.scope_key + ':' + workCompany + ':' + workPeriod + ':' + workBook}
              session={session}
              onBusyChange={changeBusinessBusy}
              onChanged={() => initialize()}
              onOpenDraft={openDraftInBook}
              onOpenInline={openInlineReview}
            />
          ) : stage8View === 'cases' ? (
            <CasesWorkbench
              key={
                session.scope_key +
                ':' +
                JSON.stringify(caseTarget ?? {}) +
                ':' +
                initialCaseId +
                ':' +
                workCompany +
                ':' +
                workBook
              }
              session={session}
              companyId={workCompany}
              bookId={workBook}
              initialTarget={caseTarget}
              initialCaseId={initialCaseId}
              onChanged={() => {
                if (bookId) void refresh(bookId);
              }}
            />
          ) : stage8View === 'learning' && bookId ? (
            <LearningWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setStage8View(null)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => refresh(bookId)}
            />
          ) : catalogView && bookId ? (
            <CatalogWorkbench
              key={session.scope_key + ':' + bookId}
              session={session}
              bookId={bookId}
              onBack={() => setCatalogView(false)}
              onBusyChange={changeBusinessBusy}
              onChanged={() => refresh(bookId)}
            />
          ) : operationsView ? (
            <OperationsWorkbench
              key={session.scope_key + ':' + operationsTarget}
              session={session}
              companies={
                operationsTarget
                  ? operationsCompanies.filter((c) => c.id === operationsTarget)
                  : operationsCompanies
              }
              onBusyChange={changeBusinessBusy}
            />
          ) : queueView ? (
            <QueueWorkbench
              key={`${session.scope_key}:${queueTarget}`}
              session={session}
              initialBatchId={queueTarget}
              initialJobId={queueJobTarget}
              onBusyChange={changeBusinessBusy}
              onOpenEvent={(id) => {
                setQueueView(false);
                setBusinessTarget(id);
                setBusinessView(true);
              }}
            />
          ) : intelligenceView ? (
            <IntelligenceWorkbench
              key={session.scope_key}
              session={session}
              onOpenEvent={(id) => {
                setIntelligenceView(false);
                setBusinessTarget(id);
                setBusinessView(true);
              }}
            />
          ) : showBusiness ? (
            <BusinessWorkspace
              key={
                session.scope_key +
                ':' +
                intakeTarget +
                ':' +
                intakeTemplate +
                ':' +
                newIntake +
                ':' +
                workCompany +
                ':' +
                workPeriod
              }
              session={session}
              initialCompanyId={workCompany}
              initialPeriod={workPeriod}
              initialIntakeId={intakeTarget}
              initialTemplate={intakeTemplate}
              initialNew={newIntake}
              books={workbench?.books ?? []}
              initialEventId={businessTarget}
              onOpenBatch={(id) => {
                setQueueTarget(id);
                setQueueView(true);
                setBusinessView(false);
                setIntelligenceView(false);
              }}
              onBusyChange={changeBusinessBusy}
              onOpenRevision={(eventId) => {
                setBusinessTarget(eventId);
                setBusinessView(false);
                setStage8View('revision');
              }}
              onOpenCase={(target) => {
                setBusinessView(false);
                setCaseTarget(target);
                setStage8View('cases');
              }}
              onOpenDraft={openDraftInBook}
            />
          ) : (
            <main>
              <section className="page-heading">
                <div>
                  <p className="eyebrow">ACCOUNTING, WITH CLARITY</p>
                  <h1>
                    {filter === 'all'
                      ? '每一笔，都有据可循。'
                      : navItems.find((item) => item.id === filter)?.label}
                  </h1>
                  <p className="page-description">
                    多企业协同记账，从原始流水到会计审核，在一个工作台完成。
                  </p>
                </div>
                <button
                  className="button primary"
                  type="button"
                  disabled={!!busy || reviewDirty || !me?.permissions.create_book}
                  title={!me?.permissions.create_book ? '当前账号没有新建账套权限' : undefined}
                  onClick={() => {
                    setNotice(null);
                    setModal('book');
                  }}
                >
                  <Plus size={16} />
                  新建账套
                </button>
              </section>
              {bootLoading ? (
                <div className="loading-block">
                  <Spinner />
                  正在载入会计工作空间…
                </div>
              ) : bootError ? (
                <div className="connection-error" role="alert">
                  <TriangleAlert size={25} />
                  <div>
                    <h2>工作空间暂时无法连接</h2>
                    <p>{bootError}</p>
                    {session.mode === 'local' && <code>后端默认地址：127.0.0.1:8000</code>}
                  </div>
                  <button className="button secondary" onClick={() => void initialize()}>
                    重新连接
                  </button>
                </div>
              ) : (
                <>
                  <section className="stats-grid" aria-label="事务所账务概览">
                    <StatCard
                      label="管理账套"
                      value={stats?.book_count ?? 0}
                      unit="家"
                      icon={<Building2 size={19} />}
                      hint="当前事务所客户企业"
                    />
                    <StatCard
                      label="待审核凭证"
                      value={stats?.pending_count ?? 0}
                      unit="张"
                      icon={<ClipboardCheck size={19} />}
                      hint="等待会计核对与确认"
                    />
                    <StatCard
                      label="待处理异常"
                      value={stats?.exception_count ?? 0}
                      unit="项"
                      icon={<TriangleAlert size={19} />}
                      hint="优先处理缺证及校验问题"
                      warning
                    />
                    <StatCard
                      label="已审核凭证"
                      value={stats?.approved_count ?? 0}
                      unit="张"
                      icon={<CheckCheck size={19} />}
                      hint="含已导出，交付后请核对回执"
                    />
                  </section>
                  <section className="process-strip" aria-label="记账流程">
                    <span>
                      <span className="step-number">01</span>导入原始流水
                    </span>
                    <ArrowRight size={15} />
                    <span>
                      <span className="step-number">02</span>规则与模型建议
                    </span>
                    <ArrowRight size={15} />
                    <span>
                      <span className="step-number">03</span>会计复核与导出
                    </span>
                    <p>
                      <ShieldCheck size={14} />
                      每一步保留来源
                    </p>
                  </section>
                  <div className="workspace-grid">
                    <div className="work-column">
                      <AccountingBookList
                        books={filteredBooks}
                        bookId={bookId}
                        search={search}
                        filtered={filter !== 'all'}
                        busy={!!busy || reviewDirty}
                        loading={bootLoading}
                        pagination={workbench?.pagination}
                        onChoose={chooseBook}
                        onSearch={(value) => {
                          focusBookRef.current = '';
                          pendingReview.current = null;
                          setSearch(value);
                        }}
                        onPage={(value) => {
                          focusBookRef.current = '';
                          pendingReview.current = null;
                          setBookOffset(value);
                        }}
                      />

                      <section className="panel transaction-panel">
                        <AccountingBookToolbar
                          book={detail?.book}
                          fallbackName={workbench?.books.find((book) => book.id === bookId)?.name}
                          busy={!!busy}
                          loading={bookLoading}
                          dirty={reviewDirty}
                          onImport={() => {
                            setNotice(null);
                            setModal('import');
                          }}
                          onWorkspace={(view) => {
                            if (view === 'catalog') setCatalogView(true);
                            else setStage8View(view as typeof stage8View);
                          }}
                        />
                        {detail?.book.permissions.edit && (
                          <Suspense
                            fallback={
                              <div className="loading-block">
                                <Spinner />
                                正在读取后台任务…
                              </div>
                            }
                          >
                            <ProposalTasksPanel
                              key={session.scope_key + ':' + bookId}
                              session={session}
                              bookId={bookId}
                              companyId={detail.book.company_id}
                              paused={reviewDirty || !!busy}
                              onBusyChange={changeBusinessBusy}
                              onChanged={() => refresh(bookId)}
                            />
                          </Suspense>
                        )}
                        <div className="list-tabs">
                          <div role="tablist" aria-label="账套数据">
                            <button
                              type="button"
                              role="tab"
                              aria-selected={listTab === 'drafts'}
                              className={listTab === 'drafts' ? 'active' : ''}
                              onClick={() => setListTab('drafts')}
                            >
                              凭证队列<span>{drafts.length}</span>
                            </button>
                            <button
                              type="button"
                              role="tab"
                              aria-selected={listTab === 'sources'}
                              className={listTab === 'sources' ? 'active' : ''}
                              onClick={() => setListTab('sources')}
                            >
                              原始流水
                              <span>
                                {detail?.source_pagination?.total ?? detail?.sources.length ?? 0}
                              </span>
                            </button>
                            <button
                              type="button"
                              role="tab"
                              aria-selected={listTab === 'exports'}
                              className={listTab === 'exports' ? 'active' : ''}
                              onClick={() => setListTab('exports')}
                            >
                              导出记录
                            </button>
                          </div>
                          <button
                            type="button"
                            className="text-button"
                            disabled={
                              !detail?.book.permissions.edit ||
                              !detail?.sources.length ||
                              !!busy ||
                              bookLoading ||
                              reviewDirty
                            }
                            onClick={() => void generateSuggestions()}
                          >
                            {busy === 'suggest' ? <Spinner /> : <Sparkles size={14} />}生成记账建议
                          </button>
                        </div>
                        {bookLoading ? (
                          <div className="loading-block">
                            <Spinner />
                            正在载入账套…
                          </div>
                        ) : bookError ? (
                          <div className="inline-error" role="alert">
                            {bookError}
                            <button
                              className="text-button"
                              disabled={!!busy}
                              onClick={() => void runAction('refresh', () => refresh(bookId))}
                            >
                              重新加载
                            </button>
                          </div>
                        ) : !detail ? (
                          <EmptyState title="选择一个账套开始处理">
                            每家企业的流水、凭证和审核记录独立保存。
                          </EmptyState>
                        ) : listTab === 'drafts' ? (
                          <>
                            {drafts.length ? (
                              <div className="transaction-table-wrap">
                                <table className="transaction-table">
                                  <thead>
                                    <tr>
                                      <th className="checkbox-cell">
                                        <input
                                          type="checkbox"
                                          aria-label="选择全部可导出凭证"
                                          disabled={
                                            !detail.book.permissions.export ||
                                            !approvedDraftIds.length ||
                                            !!busy
                                          }
                                          checked={
                                            approvedDraftIds.length > 0 &&
                                            approvedDraftIds.every((id) => selectedIds.includes(id))
                                          }
                                          onChange={(event) =>
                                            setSelectedIds(
                                              event.target.checked ? approvedDraftIds : [],
                                            )
                                          }
                                        />
                                      </th>
                                      <th>摘要 / 交易日期</th>
                                      <th className="number-cell">金额（元）</th>
                                      <th>审核状态</th>
                                      <th />
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {drafts.map((draft) => {
                                      const source = detail.sources.find(
                                        (item) => item.id === draft.source_id,
                                      );
                                      return (
                                        <tr
                                          key={draft.id}
                                          className={draft.id === activeDraft?.id ? 'active' : ''}
                                        >
                                          <td className="checkbox-cell">
                                            <input
                                              type="checkbox"
                                              aria-label={`选择凭证 ${draft.description}`}
                                              disabled={
                                                !detail.book.permissions.export ||
                                                draft.status !== 'approved' ||
                                                !!busy
                                              }
                                              checked={selectedIds.includes(draft.id)}
                                              onChange={(event) =>
                                                setSelectedIds((ids) =>
                                                  event.target.checked
                                                    ? [...ids, draft.id]
                                                    : ids.filter((id) => id !== draft.id),
                                                )
                                              }
                                            />
                                          </td>
                                          <td>
                                            <button
                                              type="button"
                                              className="draft-title"
                                              disabled={!!busy || reviewDirty}
                                              onClick={() => {
                                                pendingReview.current = null;
                                                setDraftId(draft.id);
                                              }}
                                            >
                                              <strong>
                                                {draft.description ||
                                                  source?.description ||
                                                  '未命名凭证'}
                                              </strong>
                                              <span>
                                                {source?.date ?? '—'}
                                                <span className="inline-dot">·</span>
                                                {source?.counterparty || '未填写交易对方'}
                                              </span>
                                            </button>
                                          </td>
                                          <td className="number-cell">
                                            <span className="amount">
                                              {source ? formatMoney(source.amount) : '—'}
                                            </span>
                                            <span className="direction-label">
                                              {source?.direction === 'income' ? '收入' : '支出'}
                                            </span>
                                          </td>
                                          <td>
                                            <span
                                              className={`badge ${draft.status === 'draft' && draft.issues.length ? 'exception' : draft.status}`}
                                            >
                                              {draft.source_inactive
                                                ? '已替代 / 隔离'
                                                : draft.status === 'draft' && draft.issues.length
                                                  ? '需处理'
                                                  : statusLabels[draft.status]}
                                            </span>
                                          </td>
                                          <td>
                                            <button
                                              type="button"
                                              className="icon-button review-arrow"
                                              aria-label={`复核 ${draft.description}`}
                                              disabled={!!busy || reviewDirty}
                                              onClick={() => {
                                                pendingReview.current = null;
                                                setDraftId(draft.id);
                                              }}
                                            >
                                              <ChevronRight size={16} />
                                            </button>
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            ) : (
                              <EmptyState
                                title={
                                  filter === 'all' ? '还没有凭证草稿' : '当前账套没有符合条件的凭证'
                                }
                                icon={<FileText size={28} />}
                              >
                                {detail.sources.length
                                  ? '点击“生成记账建议”，将原始流水转换为可审核草稿。'
                                  : '先导入企业流水，再生成记账建议。'}
                              </EmptyState>
                            )}
                            <div className="export-bar">
                              <span>
                                已选 <strong>{selectedIds.length}</strong> 张已审核凭证
                              </span>
                              <button
                                className="button secondary small"
                                disabled={
                                  !detail.book.permissions.export ||
                                  !selectedIds.length ||
                                  !!busy ||
                                  reviewDirty
                                }
                                onClick={() => void exportDrafts()}
                              >
                                {busy === 'export' ? <Spinner /> : <Download size={14} />}导出凭证
                                CSV
                              </button>
                            </div>
                          </>
                        ) : listTab === 'exports' ? (
                          historyLoading ? (
                            <div className="loading-block">
                              <Spinner />
                              正在读取导出记录…
                            </div>
                          ) : historyError ? (
                            <div className="inline-error" role="alert">
                              {historyError}
                              <button
                                type="button"
                                className="text-button"
                                onClick={() => {
                                  setListTab('drafts');
                                }}
                              >
                                返回凭证队列
                              </button>
                            </div>
                          ) : exportHistory.length ? (
                            <div className="source-list">
                              {exportHistory.map((record) => (
                                <div className="source-row export-history-row" key={record.id}>
                                  <span className="source-direction income">
                                    <FileCheck2 size={18} />
                                  </span>
                                  <div>
                                    <strong>{record.filename}</strong>
                                    <span>
                                      {record.count} 张凭证 ·{' '}
                                      {new Date(record.created_at).toLocaleString('zh-CN', {
                                        timeZone: 'Asia/Shanghai',
                                        hour12: false,
                                      })}
                                    </span>
                                  </div>
                                  <button
                                    type="button"
                                    className="button secondary small"
                                    disabled={!detail.book.permissions.export || !!busy}
                                    aria-label={`重新下载 ${record.filename}`}
                                    onClick={() =>
                                      void runAction('download', async () => {
                                        if (!session) return;
                                        const receipt = await api<{
                                          csv: string;
                                          filename: string;
                                        }>(
                                          `/books/${encodeURIComponent(bookId)}/exports/${encodeURIComponent(record.id)}`,
                                          session,
                                        );
                                        downloadCsv(receipt.csv, receipt.filename);
                                      })
                                    }
                                  >
                                    {busy === 'download' ? <Spinner /> : <Download size={14} />}
                                    重新下载
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <EmptyState title="还没有导出记录" icon={<Download size={26} />}>
                              审核并导出凭证后，可在这里重新下载历史文件。
                            </EmptyState>
                          )
                        ) : detail.sources.length ? (
                          <div className="source-list">
                            {detail.sources
                              .filter(
                                (source) =>
                                  !detail.source_page_ids ||
                                  detail.source_page_ids.includes(source.id),
                              )
                              .map((source) => (
                                <div className="source-row" key={source.id}>
                                  <span className={`source-direction ${source.direction}`}>
                                    {source.direction === 'income' ? (
                                      <ArrowDownLeft size={18} />
                                    ) : (
                                      <ArrowUpRight size={18} />
                                    )}
                                  </span>
                                  <div>
                                    <strong>{source.description}</strong>
                                    <span>
                                      {source.date} · {source.external_id}
                                    </span>
                                    <small>
                                      {source.evidence_ref
                                        ? `凭据引用：${source.evidence_ref}`
                                        : '尚未补充凭据引用'}
                                    </small>
                                  </div>
                                  <div className="source-amount">
                                    <strong>{formatMoney(source.amount)}</strong>
                                    <span>{source.direction === 'income' ? '收入' : '支出'}</span>
                                  </div>
                                </div>
                              ))}
                          </div>
                        ) : (
                          <EmptyState title="还没有原始流水">
                            支持带表头的 UTF-8 CSV，点击“导入流水”开始。
                          </EmptyState>
                        )}
                        {detail && listTab !== 'exports' && (
                          <PageControls
                            label={listTab === 'sources' ? '原始流水' : '凭证待办'}
                            page={
                              listTab === 'sources' ? detail.source_pagination : detail.pagination
                            }
                            busy={!!busy || bookLoading || draftLoading || reviewDirty}
                            onPage={(value) => {
                              pendingReview.current = null;
                              setSelectedIds([]);
                              setDraftId('');
                              if (listTab === 'sources') setSourceOffset(value);
                              else setDraftOffset(value);
                            }}
                          />
                        )}
                      </section>
                    </div>

                    <aside className="panel review-panel" aria-label="凭证复核">
                      <div className="review-panel-header">
                        <div className="review-heading">
                          <span className="review-icon">
                            <ClipboardCheck size={18} />
                          </span>
                          <div>
                            <h2>凭证复核</h2>
                            <p>核对来源，再确认入账建议</p>
                          </div>
                        </div>
                        <span className="review-step">REVIEW</span>
                      </div>
                      {draftError ? (
                        <div className="inline-error" role="alert">
                          {draftError}
                          <button className="text-button" onClick={() => void refresh(bookId)}>
                            重新加载
                          </button>
                        </div>
                      ) : reviewNavigationLoading ||
                        draftLoading ||
                        activeDraft?.detail_loaded === false ? (
                        <div className="loading-block">
                          <Spinner />
                          正在核对当前凭证…
                        </div>
                      ) : !bookLoading && detail && activeDraft ? (
                        <Suspense
                          fallback={
                            <div className="loading-block">
                              <Spinner />
                              正在打开凭证复核…
                            </div>
                          }
                        >
                          <Reviewer
                            key={`${session.scope_key}:${bookId}:${activeDraft.id}`}
                            session={session}
                            draft={activeDraft}
                            detail={detail}
                            busy={busy}
                            externalBusy={!!operationBusy}
                            onAssistant={() =>
                              setAssistantTarget({
                                bookId,
                                draftId: activeDraft.id,
                                version: activeDraft.version,
                              })
                            }
                            onDirtyChange={changeReviewDirty}
                            onIntelligenceBusyChange={changeIntelligenceBusy}
                            onIntelligenceChanged={() => refresh(bookId)}
                            onOpenBatch={(id) => {
                              setQueueTarget(id);
                              setQueueView(true);
                              setIntelligenceView(false);
                              setBusinessView(false);
                            }}
                            onOpenCase={(target) => {
                              setBusinessView(false);
                              setCatalogView(false);
                              setCaseTarget(target);
                              setStage8View('cases');
                            }}
                            onSave={saveDraft}
                            onApprove={approveDraft}
                            onBusinessEvidence={() => {
                              if (!reviewDirty)
                                setInlineTarget({ bookId, draftId: activeDraft.id });
                            }}
                            onEvidence={async (evidenceRef) => {
                              if (!session) return;
                              const id = bookId;
                              await runAction('evidence', async () => {
                                await api(
                                  `/books/${encodeURIComponent(id)}/sources/${encodeURIComponent(activeDraft.source_id)}/evidence`,
                                  session,
                                  { method: 'POST', body: { evidence_ref: evidenceRef } },
                                );
                                await refresh(id);
                                setNotice({
                                  kind: 'success',
                                  text: '凭据引用已补充，请人工核对对应文件的真实性与完整性。',
                                });
                              });
                            }}
                          />
                        </Suspense>
                      ) : (
                        <EmptyState
                          title={bookLoading ? '正在载入凭证' : '选择一张凭证开始复核'}
                          icon={<ClipboardCheck size={30} />}
                        >
                          这里将展示原始流水、建议分录和需要处理的问题。
                        </EmptyState>
                      )}
                    </aside>
                  </div>
                  <footer className="workspace-footer">
                    <span>衡账 · AI 辅助记账工作台</span>
                    <span>中国大陆企业 / 人民币 / 会计审核后导出</span>
                  </footer>
                </>
              )}
            </main>
          )}
        </Suspense>
      </div>

      {globalAssistant && (
        <Suspense
          fallback={
            <AssistantDialog onClose={() => setGlobalAssistant(null)}>
              <div className="loading-block">
                <Spinner />
                正在打开会计助手…
              </div>
            </AssistantDialog>
          }
        >
          <GlobalAccountingAssistant
            key={session.scope_key}
            session={session}
            initialContext={globalAssistant}
            onClose={() => setGlobalAssistant(null)}
            onNavigateEvidence={openAssistantEvidence}
            onNavigateContext={(context) => {
              setGlobalAssistant(null);
              if (context.draft && context.books.length === 1)
                setInlineTarget({ bookId: context.books[0].id, draftId: context.draft.id });
              else if (context.books.length === 1) void openAssistantBook(context.books[0].id);
              else if (context.events.length === 1) {
                navigate('business', true);
                setBusinessTarget(context.events[0].id);
              } else navigate('accounting', true);
            }}
            onOpenTask={openAssistantTask}
          />
        </Suspense>
      )}
      {commandOpen && (
        <AgentCommandPalette
          key={session.scope_key}
          session={session}
          commands={quickCommands}
          canSearchBooks={canSeeAccounting}
          onOpenBook={openCommandBook}
          onClose={() => setCommandOpen(false)}
        />
      )}
      {assistantTarget && (
        <AssistantDialog onClose={() => setAssistantTarget(null)}>
          <Suspense
            fallback={
              <div className="loading-block">
                <Spinner />
                正在读取当前凭证依据…
              </div>
            }
          >
            <AccountingContextAssistant
              key={`${session.scope_key}:${assistantTarget.bookId}:${assistantTarget.draftId}:${assistantTarget.version}`}
              session={session}
              bookId={assistantTarget.bookId}
              draftId={assistantTarget.draftId}
              draftVersion={assistantTarget.version}
              actionTypes={['review', 'edit']}
              onClose={() => setAssistantTarget(null)}
              onOpenEvidence={() => {
                setInlineTarget({
                  bookId: assistantTarget.bookId,
                  draftId: assistantTarget.draftId,
                });
                setAssistantTarget(null);
              }}
              onAction={() => {
                setInlineTarget({
                  bookId: assistantTarget.bookId,
                  draftId: assistantTarget.draftId,
                });
                setAssistantTarget(null);
              }}
            />
          </Suspense>
        </AssistantDialog>
      )}
      {notice && (
        <div className={`toast ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
          {notice.kind === 'error' ? <TriangleAlert size={18} /> : <CheckCircle2 size={18} />}
          <span>{notice.text}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            className="icon-button"
            aria-label="关闭消息"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {modal === 'book' && (
        <NewBookModal
          session={session}
          canCreateCompany={!!me?.permissions.create_company}
          serverError={notice?.kind === 'error' ? notice.text : undefined}
          busy={!!busy}
          onClose={() => setModal(null)}
          onSubmit={async (name, period, companyId) => {
            if (!session) return;
            await runAction('book', async () => {
              const book = await api<Book>('/books', session, {
                method: 'POST',
                body: { name, period, company_id: companyId },
              });
              focusBookRef.current = book.id;
              pendingReview.current = null;
              const data = await api<Workbench>(
                '/workbench?limit=20&focus_book_id=' + encodeURIComponent(book.id),
                session,
              );
              setDraftOffset(0);
              setSourceOffset(0);
              setWorkbench(data);
              setBookId(book.id);
              setDetail(null);
              setSelectedIds([]);
              setFilter('all');
              setSearch('');
              setModal(null);
              setNotice({ kind: 'success', text: `账套“${book.name}”已创建，可以导入流水。` });
            });
          }}
        />
      )}
      {modal === 'access' && me?.permissions.manage_firm && (
        <Suspense
          fallback={
            <div className="loading-block" role="status">
              <Spinner />
              正在读取权限管理…
            </div>
          }
        >
          <AccessManagement
            session={session}
            books={workbench?.books ?? []}
            onClose={() => setModal(null)}
            onChanged={async () => {
              await initialize();
            }}
          />
        </Suspense>
      )}
      {modal === 'import' && detail && (
        <Suspense
          fallback={
            <div className="loading-block" role="status">
              <Spinner />
              正在打开详情…
            </div>
          }
        >
          {' '}
          <ChineseImportWizard
            session={session}
            book={detail.book}
            onBusyChange={changeBusinessBusy}
            onClose={() => setModal(null)}
            onImported={async (result) => {
              const id = detail.book.id;
              try {
                await refresh(id);
                setModal(null);
                setListTab('sources');
                setNotice({
                  kind: 'success',
                  text: `成功导入 ${result.imported} 条流水，跳过 ${result.duplicates} 条重复记录。可继续生成记账建议。`,
                });
              } finally {
                setBusinessBusy(false);
              }
            }}
          />
        </Suspense>
      )}
      {inlineTarget && (
        <Suspense
          fallback={
            <div className="loading-block" role="status">
              <Spinner />
              正在打开详情…
            </div>
          }
        >
          {' '}
          <InlineReviewDrawer
            key={`${session.scope_key}:${inlineTarget.bookId}:${inlineTarget.draftId}`}
            session={session}
            bookId={inlineTarget.bookId}
            draftId={inlineTarget.draftId}
            queue={
              inlineContext.current?.scope === session.scope_key
                ? inlineContext.current.queue
                : homeView
                  ? []
                  : drafts.map((d) => ({ book_id: bookId, draft_id: d.id }))
            }
            onBusyChange={changeBusinessBusy}
            onClose={() => {
              inlineContext.current?.onClose();
              inlineContext.current = null;
              setInlineTarget(null);
              setWorkspaceRevision((n) => n + 1);
            }}
            onNavigate={(targetBook, targetDraft) =>
              setInlineTarget({ bookId: targetBook, draftId: targetDraft })
            }
            onChanged={(targetBook, draft) => {
              if (inlineContext.current?.scope === session.scope_key)
                inlineContext.current.onChanged(targetBook, draft);
              acceptMutatedDraft(targetBook, draft);
              setWorkspaceRevision((n) => n + 1);
            }}
          />
        </Suspense>
      )}
      {proposalTarget && (
        <Suspense
          fallback={
            <div className="loading-block" role="status">
              <Spinner />
              正在打开详情…
            </div>
          }
        >
          {' '}
          <ProposalTaskDialog
            key={`${session.scope_key}:${proposalTarget}`}
            session={session}
            jobId={proposalTarget}
            onBusyChange={changeBusinessBusy}
            onClose={() => {
              setProposalTarget('');
              setWorkspaceRevision((n) => n + 1);
            }}
            onChanged={() => setWorkspaceRevision((n) => n + 1)}
            onOpenDraft={(id, targetDraft) => {
              setProposalTarget('');
              setInlineTarget({ bookId: id, draftId: targetDraft });
            }}
          />
        </Suspense>
      )}
      {modal === 'help' && (
        <Modal
          title="从可靠的第一笔账开始"
          subtitle={
            session.mode === 'local' ? '当前版本 · 本地辅助记账验证' : '当前版本 · 企业试点辅助记账'
          }
          onClose={() => setModal(null)}
        >
          <div className="help-content">
            <p>
              适用于中国大陆企业、人民币账套。支持员工报销、采购 /
              费用、日常收支录入与附件上传；经部门审批和企业财务核验后交接凭证草稿。也支持 CSV
              流水导入、规则建议、人工修改与审核、凭证导出。
            </p>
            <ol>
              <li>
                <strong>创建账套并导入流水</strong>
                <span>每条流水须有稳定的外部编号。重复导入相同记录不会重复创建。</span>
              </li>
              <li>
                <strong>检查建议与原始依据</strong>
                <span>
                  建议来自规则或管理员配置的模型。模型结果会与业务事项、票据及关联流水核对；实际会计处理须由会计确认。
                </span>
              </li>
              <li>
                <strong>审核后导出</strong>
                <span>补全借贷分录与凭据引用后审核，再导出到目标财务系统并核对回执。</span>
              </li>
            </ol>
            <div className="info-callout">
              <ShieldCheck size={18} />
              <p>
                {session.mode === 'local'
                  ? '首次启动的三家企业与流水均为虚构示例。'
                  : '仅可访问当前账户获授权的客户企业与账套。'}
                凭据引用是人工填写的索引，不代表附件已经验证。票据字段为模型识别转录，须核对原件。当前版本不自动过账，也不生成正式财务报表。
              </p>
            </div>
          </div>
          <div className="modal-footer">
            <button className="button primary" onClick={() => setModal(null)}>
              开始处理
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
