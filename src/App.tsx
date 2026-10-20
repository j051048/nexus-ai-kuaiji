import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  FileCheck2,
  LayoutDashboard,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Users,
  LogOut,
  X,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { AuthScreen, NoAuthorization } from './AuthScreen';
import { useAccountingAuth } from './useAccountingAuth';
import { ActivityTimer } from './ActivityTimer';
import { AgentNavigation, type AgentNavigationView } from './AgentNavigation';
import { AgentCommandPalette, type AgentCommand } from './AgentCommandPalette';
import { buildQuickCommands } from './quick-commands';
import type { AgentRoute } from './agent-workspace-types';
import type {
  AssistantInitialContext,
  AssistantEvidence,
  AssistantActionReceipt,
} from './assistant-types';
import { clearReviewSession } from './review-session';
import { roleCopy, taskDestination } from './workspace-types';
import { workspaceExperience } from './workspace-experience';
import {
  presentationRole,
  primaryNav,
  roleHome,
  selectBriefing,
} from './workspace-presentation';
import type { WorkspaceProfile, WorkspaceTask } from './workspace-types';
import type { BusinessTemplate } from './business-types';
import type { OperationsCompany } from './operations-types';
import type { Book, BookDetail, Draft, Session, Workbench } from './types';

import { EmptyState, Modal, Spinner } from './AppPrimitives';
import { NewBookModal } from './AppDialogs';
import { AssistantDrawer, type AssistantDrawerMode } from './AssistantDrawer';
import { ClientPortalShell, type ClientPortalTab } from './ClientPortalShell';
import { useBookWorkspace, type BookFilter, type BookNotice } from './useBookWorkspace';
import { WorkspaceRouteContent } from './WorkspaceRouteContent';
import { WorkspaceMfaPrompt } from './WorkspaceMfaPrompt';
import { useWorkspaceRoute } from './useWorkspaceRoute';
import { RouteLeaveDialog } from './RouteLeaveDialog';
import {
  readWorkspaceRoute,
  workspaceUsesBook,
  type WorkspaceRoute,
  type WorkspaceView,
} from './workspace-route';
import type { MonthlyDestination } from './monthly-workbench-types';
import { roleLabels, workspaceViewLabels } from './workspace-labels';
import {
  AccessManagement,
  AccountActivation,
  AccountRecovery,
  AccountingContextAssistant,
  AdminMfaGate,
  AdminWorkspace,
  ChineseImportWizard,
  GlobalAccountingAssistant,
  InlineReviewDrawer,
  ProposalTaskDialog,
} from './workspace-modules';

type Filter = BookFilter;
type ModalName = 'book' | 'import' | 'help' | 'access' | null;
type Notice = BookNotice;

export default function App() {
  const location = useLocation();
  const routerNavigate = useNavigate();
  const adminMode = /^\/admin(?:\/|$)/.test(location.pathname);
  const activationMode = location.pathname === '/account-activation';
  const recoveryMode = location.pathname === '/account-recovery';
  const [adminBusy, setAdminBusy] = useState(false);
  const [routeView, setRouteView] = useState<WorkspaceView>(() =>
    readWorkspaceRoute(location.pathname, location.search).view === 'home' ? 'home' : 'accounting',
  );
  const [commandOpen, setCommandOpen] = useState(false);
  const [globalAssistant, setGlobalAssistant] = useState<AssistantInitialContext | null>(null);
  const [assistantMode, setAssistantMode] = useState<AssistantDrawerMode>('global');
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
  const [materialTarget, setMaterialTarget] = useState('');
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
  const [caseTarget, setCaseTarget] = useState<
    { eventId?: string; bookId?: string; draftId?: string; title?: string } | undefined
  >();
  const [operationsCompanies, setOperationsCompanies] = useState<OperationsCompany[]>([]);
  const [operationsTarget, setOperationsTarget] = useState('');
  const [queueTarget, setQueueTarget] = useState('');
  const [queueJobTarget, setQueueJobTarget] = useState('');
  const [businessTarget, setBusinessTarget] = useState('');
  const [migrationTarget, setMigrationTarget] = useState('');
  const [businessBusy, setBusinessBusy] = useState(false);
  const changeBusinessBusy = useCallback((value: boolean) => setBusinessBusy(value), []);
  const [modal, setModal] = useState<ModalName>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const bookResetRef = useRef<() => void>(() => undefined);
  const hadWorkspaceIdentity = useRef(false);
  const loginLocation = useRef(location);
  loginLocation.current = location;

  const clearWorkspace = useCallback(() => {
    setCommandOpen(false);
    setAssistantTarget(null);
    setGlobalAssistant(null);
    inlineContext.current = null;
    clearReviewSession();
    setRouteView(
      hadWorkspaceIdentity.current ||
        readWorkspaceRoute(loginLocation.current.pathname, loginLocation.current.search).view ===
          'home'
        ? 'home'
        : 'accounting',
    );
    setMigrationTarget('');
    setOperationsTarget('');
    setProfile(null);
    setProfileError('');
    setIntakeTarget('');
    setIntakeTemplate(undefined);
    setNewIntake(false);
    setInitialCaseId('');
    setMaterialTarget('');
    setDeliveryTarget('');
    setProposalTarget('');
    setInlineTarget(null);
    setWorkspaceRevision(0);
    setWorkCompany('');
    setWorkPeriod('');
    setWorkBook('');
    setMonthlyDirty(false);
    bookResetRef.current();
    setQueueTarget('');
    setQueueJobTarget('');
    setOperationsCompanies([]);
    setBusinessTarget('');
    setBusinessBusy(false);
    setCaseTarget(undefined);
    setNotice(null);
    setModal(null);
  }, []);
  const loginRoute = readWorkspaceRoute(location.pathname, location.search);
  const auth = useAccountingAuth(
    clearWorkspace,
    routeView === 'home' &&
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
  const defaultLandingFor = useRef('');
  const entryPath = useRef(location.pathname);
  // Role default landing applies only to the bare root login. An explicit
  // /workspace/home (including refresh) is a user target and must be preserved.
  useEffect(() => {
    if (!session || !profile) return;
    if (entryPath.current !== '/') return;
    const key = session.scope_key;
    if (defaultLandingFor.current === key) return;
    const explicit = readWorkspaceRoute(location.pathname, location.search);
    const hasTarget = Boolean(
      explicit.companyId ||
        explicit.bookId ||
        explicit.draftId ||
        explicit.caseId ||
        explicit.eventId ||
        explicit.reviewBookId ||
        explicit.intakeId ||
        explicit.batchId ||
        explicit.jobId ||
        explicit.migrationId ||
        explicit.deliveryId ||
        explicit.proposalId ||
        explicit.materialId ||
        explicit.search,
    );
    if (explicit.view !== 'home' || hasTarget || adminMode) {
      defaultLandingFor.current = key;
      return;
    }
    const role = presentationRole(profile);
    const target = roleHome(role).defaultView;
    defaultLandingFor.current = key;
    if (target === 'admin') {
      if (me?.permissions.manage_admin || me?.permissions.manage_firm) void routerNavigate('/admin');
      return;
    }
    if (target !== 'home') {
      // Bare login without a target: route once to the role default. Explicit
      // query/deep links were returned above and are never rewritten.
      void routerNavigate(`/workspace/${target}`, { replace: true });
    }
  }, [session, profile, me, location.pathname, location.search, adminMode, routerNavigate]);
  // 代账包干制：模型费用与运行监控是事务所内部的生产资料，客户企业侧不展示；
  // can_operations 仅保留给接口兼容，导航与工作区渲染都以事务所管理层能力位为准。
  const canFirmOperations = profile?.can_firm_operations ?? !!me?.permissions.manage_firm;
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
    if (routeView !== 'home' && homeBootstrap) auth.consumeHomeBootstrap(homeBootstrap.scope_key);
  }, [routeView, homeBootstrap, auth.consumeHomeBootstrap]);
  const routeUsesBook = workspaceUsesBook(routeView);

  const sessionScope = useRef<string | null>(null);
  sessionScope.current = session?.scope_key ?? null;
  const bookWorkspace = useBookWorkspace({
    session,
    sessionScope,
    routeView,
    routeUsesBook,
    businessBusy,
    setRouteView,
    setNotice,
  });
  const clearProtectedBookData = bookWorkspace.clearProtectedData;
  useEffect(() => {
    const onForbidden = (event: Event) => {
      if (!session || (event as CustomEvent<string>).detail !== session.scope_key) return;
      // Resource denial is not logout. Clear cached evidence while keeping the page/selection.
      clearProtectedBookData();
      bootstrapProfile.current = null;
      auth.consumeHomeBootstrap(session.scope_key);
      setProfile(null);
      setProfileError('权限不足或授权已变更，请确认权限后刷新。');
      setOperationsCompanies([]);
      setCommandOpen(false);
      setAssistantTarget(null);
      setGlobalAssistant(null);
      inlineContext.current = null;
      clearReviewSession();
      setInlineTarget(null);
      setProposalTarget('');
      setModal(null);
    };
    window.addEventListener('accounting:forbidden', onForbidden);
    return () => window.removeEventListener('accounting:forbidden', onForbidden);
  }, [session, clearProtectedBookData, auth.consumeHomeBootstrap]);
  const {
    acceptMutatedDraft,
    activeDraft,
    approveDraft,
    approvedDraftIds,
    bookError,
    bookId,
    bookLoading,
    bookOffset,
    bootError,
    bootLoading,
    busy,
    changeIntelligenceBusy,
    changeReviewDirty,
    chooseBook,
    detail,
    draftError,
    draftId,
    draftLoading,
    draftOffset,
    drafts,
    exportDrafts,
    exportHistory,
    filter,
    filteredBooks,
    focusBookRef,
    generateSuggestions,
    historyError,
    historyLoading,
    initialize,
    intelligenceBusy,
    listTab,
    openDraftInBook,
    operationBusy,
    pendingReview,
    refresh,
    reset: resetBookWorkspace,
    reviewDirty,
    reviewNavigationLoading,
    runAction,
    saveDraft,
    search,
    selectedIds,
    setBookId,
    setBookError,
    setBookOffset,
    setDetail,
    setDraftId,
    setDraftOffset,
    setFilter,
    setListTab,
    setSearch,
    setSelectedIds,
    setSourceOffset,
    setReviewDirty,
    setWorkbench,
    sourceOffset,
    stats,
    workbench,
  } = bookWorkspace;
  const storedDetail = detail;
  bookResetRef.current = resetBookWorkspace;
  useEffect(() => {
    setNotice(null);
  }, [routeView, bookId]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k' || !session?.firm_id)
        return;
      if (busy || businessBusy || reviewDirty || inlineTarget || proposalTarget || modal) return;
      event.preventDefault();
      setCommandOpen(true);
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [session?.scope_key, busy, businessBusy, reviewDirty, inlineTarget, proposalTarget, modal]);
  useEffect(() => {
    if (!modal && !commandOpen) return;
    setGlobalAssistant(null);
    setAssistantTarget(null);
  }, [modal, commandOpen]);
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
    if (
      !canFirmOperations ||
      routeView !== 'operations' ||
      !session?.firm_id ||
      sessionScope.current !== session.scope_key
    )
      return;
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
  }, [session, routeView, canFirmOperations]);

  useEffect(() => {
    if (!notice || notice.kind === 'error') return;
    const timeout = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

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
    materialId: routeView === 'materials' ? materialTarget : '',
  };
  const routeGuard = useWorkspaceRoute({
    scope: session?.firm_id ? session.scope_key : undefined,
    value: workspaceRoute,
    metadata:
      routeUsesBook && storedDetail?.book.id === bookId
        ? { companyId: storedDetail.book.company_id, period: storedDetail.book.period }
        : undefined,
    locked:
      adminBusy ||
      !!busy ||
      businessBusy ||
      reviewDirty ||
      drawerDirty ||
      monthlyDirty ||
      modal === 'import',
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
      setRouteView(route.view);
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
      setWorkBook(
        ['review', 'cases', 'business', 'service', 'materials'].includes(route.view)
          ? route.bookId
          : '',
      );
      setBusinessBusy(false);
      setSelectedIds([]);
      setBusinessTarget(route.eventId);
      setIntakeTarget(route.intakeId);
      setInitialCaseId(route.caseId);
      setMaterialTarget(route.materialId);
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
          scopeKey={auth.session.scope_key}
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
  const clientPortal = !!profile && !canSeeAccounting;
  const experience = workspaceExperience(profile);
  const navigationLocked =
    !!busy ||
    businessBusy ||
    reviewDirty ||
    monthlyDirty ||
    !!inlineTarget ||
    !!proposalTarget ||
    modal === 'import';
  const assistantOpen = !!globalAssistant || !!assistantTarget;
  const effectiveAssistantMode: AssistantDrawerMode =
    assistantMode === 'context' && assistantTarget ? 'context' : 'global';
  const assistantContextLabel = assistantTarget
    ? `${detail?.book.id === assistantTarget.bookId ? detail.book.name : '当前凭证'} · v${assistantTarget.version}`
    : undefined;
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
        modal)
    )
      return;
    if (view === 'quality' && !bookId) return;
    setRouteView(
      [
        'home',
        'firm',
        'onboarding',
        'inbox',
        'observability',
        'ingestion',
        'runs',
        'business',
        'queue',
        'intelligence',
        'operations',
        'efficiency',
        'review',
        'cases',
        'revision',
        'monthly',
        'materials',
        'messages',
        'portfolio',
        'briefing',
        'quality',
      ].includes(view)
        ? (view as WorkspaceView)
        : 'accounting',
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
    setMaterialTarget('');
    setDeliveryTarget('');
    if (view === 'accounting') {
      setFilter('all');
      setSearch('');
      focusBookRef.current = '';
      pendingReview.current = null;
    }
    if (view === 'home') setWorkspaceRevision((n) => n + 1);
    // Keep the address bar/deep link in sync with the authorized navigation.
    void routerNavigate(`/workspace/${view}`, { replace: false });
  }
  function openWorkspaceTask(task: WorkspaceTask) {
    if (navigationLocked) return;
    const target = taskDestination(task);
    if (target.type === 'material') {
      navigate('materials');
      setMaterialTarget(target.id);
      setWorkCompany(task.company_id);
      setWorkBook(profile?.can_accounting ? (task.book_id ?? '') : '');
      return;
    }
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
      setRouteView('delivery');
      if (routeUsesBook) void initialize(undefined, target.bookId);
    } else if (target.type === 'migration' && target.bookId) {
      navigate('accounting');
      setBookId(target.bookId);
      focusBookRef.current = target.bookId;
      setMigrationTarget(target.id);
      setRouteView('migration');
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
    if (
      ['delivery', 'closing', 'efficiency', 'balances', 'recurring', 'briefing'].includes(
        target.view,
      )
    )
      setRouteView(target.view as WorkspaceView);
    if (target.filter) setFilter(target.filter);
  }
  function openClosingIssue(issue: { type: string; id: string; bookId: string }) {
    if (issue.type === 'draft') {
      openDraftInBook(issue.bookId, issue.id);
    } else if (issue.type === 'event') {
      setBusinessTarget(issue.id);
      setRouteView('business');
    } else if (issue.type === 'export') {
      setDeliveryTarget(issue.id);
      setRouteView('delivery');
    } else if (issue.type === 'revision') {
      setBusinessTarget('');
      setRouteView('revision');
    } else if (issue.type === 'case') {
      setCaseTarget(undefined);
      setInitialCaseId(issue.id);
      setRouteView('cases');
    } else if (issue.type === 'proposal_job') {
      setProposalTarget(issue.id);
    } else if (issue.type === 'ocr_job') {
      void api<{ batch_id: string }>('/intelligence/jobs/' + encodeURIComponent(issue.id), session!)
        .then((job) => {
          if (sessionScope.current !== session?.scope_key) return;
          setQueueTarget(job.batch_id);
          setQueueJobTarget(issue.id);
          setRouteView('queue');
        })
        .catch((error) => setNotice({ kind: 'error', text: errorMessage(error) }));
    } else {
      setRouteView('accounting');
      setFilter('all');
      void refresh(issue.bookId);
    }
  }
  const showBusiness =
    routeView === 'business' || (routeView === 'accounting' && !!profile && !canSeeAccounting);
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
      setRouteView('migration');
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
        modal)
    )
      return;
    navigate('accounting', fromAssistant);
    setBookId(book.id);
    focusBookRef.current = book.id;
    if (routeUsesBook) void initialize(undefined, book.id);
  }
  function openGlobalAssistant() {
    if (navigationLocked || modal || commandOpen || assistantOpen) return;
    setAssistantMode('global');
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
  function closeAssistantDrawer() {
    setGlobalAssistant(null);
    setAssistantTarget(null);
  }
  function switchAssistantMode(mode: AssistantDrawerMode) {
    if (mode === 'context' && !assistantTarget) return;
    if (mode === 'global') setGlobalAssistant((current) => current ?? {});
    setAssistantMode(mode);
  }
  function openContextAssistant(
    target: { bookId: string; draftId: string; version: number } | null,
  ) {
    if (!target) {
      setAssistantTarget(null);
      return;
    }
    setAssistantTarget(target);
    setAssistantMode('context');
    setGlobalAssistant(null);
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
    closeAssistantDrawer();
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
    closeAssistantDrawer();
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
  const quickCommands: AgentCommand[] = buildQuickCommands({
    navigate,
    openAssistant: () => {
      setAssistantMode('global');
      setGlobalAssistant({});
    },
    canSeeAccounting,
    canReview: !!profile?.can_review,
    processing: experience.processing,
    businessLabel: experience.businessLabel,
  });

  const routeApp = {
    session,
    me,
    routeView,
    routeUsesBook,
    profile,
    profileError,
    homeBootstrap,
    consumeHomeBootstrap: auth.consumeHomeBootstrap,
    loadProfile: () => void loadProfile(),
    workspaceRevision,
    setWorkspaceRevision,
    navigationLocked,
    showBusiness,
    canFirmOperations,
    businessBusy,
    monthlyDirty,
    changeBusinessBusy,
    changeMonthlyDirty,
    workCompany,
    workPeriod,
    workBook,
    businessTarget,
    intakeTarget,
    intakeTemplate,
    newIntake,
    materialTarget,
    deliveryTarget,
    migrationTarget,
    operationsTarget,
    operationsCompanies,
    queueTarget,
    queueJobTarget,
    initialCaseId,
    caseTarget,
    setWorkCompany,
    setWorkPeriod,
    setWorkBook,
    setBusinessTarget,
    setQueueTarget,
    setQueueJobTarget,
    setCaseTarget,
    setMigrationTarget,
    setRouteView,
    setNotice,
    setInlineTarget,
    navigate,
    openWorkspaceTask,
    openNewIntake,
    openAgentRun,
    openMonthlyTarget,
    openClosingIssue,
    openInlineReview,
    openContextAssistant,
    openBookModal: () => {
      setNotice(null);
      setModal('book');
    },
    openImport: () => {
      setNotice(null);
      setModal('import');
    },
  };
  const portalRouteView: WorkspaceView =
    clientPortal &&
    !['home', 'accounting', 'business', 'materials', 'messages', 'briefing'].includes(routeView)
      ? 'home'
      : routeView;
  const routeContent = (
    <WorkspaceRouteContent
      app={
        portalRouteView === routeView
          ? routeApp
          : {
              ...routeApp,
              routeView: portalRouteView,
              showBusiness:
                portalRouteView === 'business' ||
                (portalRouteView === 'accounting' && clientPortal),
            }
      }
      book={bookWorkspace}
    />
  );
  const clientPortalTab: ClientPortalTab =
    portalRouteView === 'materials'
      ? 'materials'
      : portalRouteView === 'messages'
        ? 'messages'
        : portalRouteView === 'briefing'
          ? 'briefing'
          : portalRouteView === 'business' || portalRouteView === 'accounting'
            ? 'submit'
            : 'home';

  if (clientPortal && profile) {
    return (
      <>
        {routeGuard.blocked && (
          <RouteLeaveDialog
            canDiscard={routeGuard.canDiscard}
            onStay={routeGuard.cancel}
            onLeave={routeGuard.proceed}
          />
        )}
        <ClientPortalShell
          session={session}
          profile={profile}
          current={clientPortalTab}
          onNavigate={(tab) => {
            if (tab === 'home') navigate('home');
            else if (tab === 'materials') navigate('materials');
            else if (tab === 'messages') navigate('messages');
            else navigate('business');
          }}
          onBriefing={() => {
            const scoped = (profile.briefings ?? []).filter(
              (item) =>
                (!workCompany || item.company_id === workCompany) &&
                (!workPeriod || item.period === workPeriod) &&
                (!bookId || item.book_id === bookId),
            );
            const target = selectBriefing(profile.briefings, {
              companyId: workCompany,
              bookId,
              period: workPeriod,
            });
            if (!target) {
              setNotice({
                kind: 'error',
                text:
                  !workCompany && !workPeriod && !bookId
                    ? '请先选择企业、账套和期间，再打开报告。'
                    : scoped.length > 1
                      ? '当前范围有多份报告，请先明确账套后再打开。'
                      : '当前范围没有可打开的报告，请确认企业、账套和期间。',
              });
              navigate('home');
              return;
            }
            navigate('accounting');
            setWorkCompany(target.company_id);
            setWorkPeriod(target.period);
            setBookId(target.book_id);
            focusBookRef.current = target.book_id;
            setRouteView('briefing');
            void initialize(undefined, target.book_id);
          }}
          onLogout={() => void auth.logout()}
          persona={auth.persona}
          personas={auth.config?.personas}
          onPersonaChange={(value) => void auth.choosePersona(value)}
        >
          {auth.error && (
            <p className="form-error" role="alert">
              {auth.error}
            </p>
          )}
          {notice && (
            <p className={notice.kind === 'error' ? 'form-error' : 'form-success'} role="alert">
              {notice.text}
            </p>
          )}
          <WorkspaceMfaPrompt
            key={`mfa:${session.scope_key}`}
            session={session}
            onVerified={auth.refreshIdentity}
            onLogout={auth.logout}
          />
          {routeContent}
        </ClientPortalShell>
      </>
    );
  }

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
          routeView === 'service'
            ? 'monthly'
            : routeView === 'accounting' && showBusiness
              ? 'business'
              : [
                    'home',
                    'firm',
                    'onboarding',
                    'inbox',
                    'observability',
                    'ingestion',
                    'runs',
                    'review',
                    'cases',
                    'queue',
                    'intelligence',
                    'operations',
                    'monthly',
                    'materials',
                    'messages',
                    'portfolio',
                    'quality',
                  ].includes(routeView)
                ? routeView
                : 'accounting'
        }
        presentationNav={profile ? primaryNav(presentationRole(profile), profile) : undefined}
        presentationLabel={profile ? roleHome(presentationRole(profile)).name : undefined}
        onAdmin={() => {
          if (!navigationLocked) void routerNavigate('/admin');
        }}
        canAccounting={canSeeAccounting}
        canProcessing={experience.processing}
        businessLabel={experience.businessLabel}
        canMonthly={canSeeAccounting || !!profile?.can_manage}
        canReview={!!profile?.can_review}
        canQuality={!!profile?.can_review && !!bookId}
        canOperations={canFirmOperations}
        locked={navigationLocked || !!modal || commandOpen}
        onNavigate={navigate}
        onAccountingFilter={(value) => {
          navigate('accounting');
          setFilter(value);
        }}
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
        {auth.error && (
          <p className="form-error" role="alert">
            {auth.error}
          </p>
        )}
        <WorkspaceMfaPrompt
          key={`mfa:${session.scope_key}`}
          session={session}
          onVerified={auth.refreshIdentity}
          onLogout={auth.logout}
        />
        <header className="topbar">
          <div className="breadcrumbs">
            工作空间
            <ChevronRight size={13} />
            <span>
              {routeView === 'accounting' && showBusiness
                ? '业务事项'
                : (workspaceViewLabels[routeView] ??
                  navItems.find((item) => item.id === filter)?.label)}
            </span>
          </div>
          <div className="agent-topbar-actions">
            <button
              className="agent-topbar-assistant"
              aria-label="问会计助手"
              disabled={navigationLocked || !!modal || commandOpen || assistantOpen}
              onClick={openGlobalAssistant}
            >
              <Sparkles size={15} />
              <span>问会计助手</span>
              <small>
                {[
                  profile?.companies.find((company) => company.id === workCompany)?.name,
                  workPeriod,
                ]
                  .filter(Boolean)
                  .join(' · ') || '当前授权范围'}
              </small>
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
          enabled={routeView !== 'home' && routeView !== 'runs' && routeView !== 'efficiency'}
          bookId={workspaceUsesBook(routeView) ? bookId : undefined}
          onOpen={() => {
            if (busy || businessBusy || reviewDirty || monthlyDirty) return;
            setRouteView('efficiency');
          }}
        />
        {routeContent}
      </div>

      {assistantOpen && (
        <AssistantDrawer
          mode={effectiveAssistantMode}
          contextAvailable={!!assistantTarget}
          contextLabel={assistantContextLabel}
          onModeChange={switchAssistantMode}
          onClose={closeAssistantDrawer}
        >
          {globalAssistant && (
            <div className="assistant-mode-panel" hidden={effectiveAssistantMode !== 'global'}>
              <Suspense
                fallback={
                  <div className="loading-block">
                    <Spinner />
                    正在打开全账套助手…
                  </div>
                }
              >
                <GlobalAccountingAssistant
                  key={session.scope_key}
                  session={session}
                  initialContext={globalAssistant}
                  embedded
                  onClose={closeAssistantDrawer}
                  onNavigateEvidence={openAssistantEvidence}
                  onNavigateContext={(context) => {
                    closeAssistantDrawer();
                    if (context.draft && context.books.length === 1)
                      setInlineTarget({
                        bookId: context.books[0].id,
                        draftId: context.draft.id,
                      });
                    else if (context.books.length === 1)
                      void openAssistantBook(context.books[0].id);
                    else if (context.events.length === 1) {
                      navigate('business', true);
                      setBusinessTarget(context.events[0].id);
                    } else navigate('accounting', true);
                  }}
                  onOpenTask={openAssistantTask}
                />
              </Suspense>
            </div>
          )}
          {assistantTarget && (
            <div className="assistant-mode-panel" hidden={effectiveAssistantMode !== 'context'}>
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
                  embedded
                  onClose={closeAssistantDrawer}
                  onOpenEvidence={() => {
                    setInlineTarget({
                      bookId: assistantTarget.bookId,
                      draftId: assistantTarget.draftId,
                    });
                    closeAssistantDrawer();
                  }}
                  onAction={() => {
                    setInlineTarget({
                      bookId: assistantTarget.bookId,
                      draftId: assistantTarget.draftId,
                    });
                    closeAssistantDrawer();
                  }}
                />
              </Suspense>
            </div>
          )}
        </AssistantDrawer>
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
                : routeView === 'home'
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
