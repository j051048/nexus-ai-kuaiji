import { lazy, Suspense, type Dispatch, type SetStateAction } from 'react';
import {
  ClipboardCheck,
  FileCheck2,
  LayoutDashboard,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { EmptyState, Spinner } from './AppPrimitives';
import type { AgentRoute } from './agent-workspace-types';
import type { BusinessTemplate } from './business-types';
import type { MonthlyDestination } from './monthly-workbench-types';
import type { OperationsCompany } from './operations-types';
import type { Draft, Me, Session } from './types';
import type { BookFilter, BookNotice, useBookWorkspace } from './useBookWorkspace';
import type { WorkspaceHomeBootstrap, WorkspaceProfile, WorkspaceTask } from './workspace-types';
import type { AgentNavigationView } from './AgentNavigation';
import type { WorkspaceView } from './workspace-route';
import type { useWorkspaceProgress } from './useWorkspaceProgress';
import type { ServiceJourneyTarget } from './service-journey';
import { presentationRole, workSurface } from './workspace-presentation';

const BookWorkspaceShell = lazy(() =>
  import('./BookWorkspaceShell').then((module) => ({ default: module.BookWorkspaceShell })),
);
const WorkspaceHome = lazy(() =>
  import('./WorkspaceHome').then((module) => ({ default: module.WorkspaceHome })),
);

const AgentActivityWorkspace = lazy(() =>
  import('./AgentActivityWorkspace').then((module) => ({ default: module.AgentActivityWorkspace })),
);
const FirmCockpit = lazy(() =>
  import('./FirmCockpit').then((module) => ({ default: module.FirmCockpit })),
);
const FirmOnboarding = lazy(() =>
  import('./FirmOnboarding').then((module) => ({ default: module.FirmOnboarding })),
);
const ClientMessages = lazy(() =>
  import('./ClientMessages').then((module) => ({ default: module.ClientMessages })),
);
const FirmInbox = lazy(() =>
  import('./FirmInbox').then((module) => ({ default: module.FirmInbox })),
);
const FirmObservability = lazy(() =>
  import('./FirmObservability').then((module) => ({ default: module.FirmObservability })),
);
const FirmIngestionCenter = lazy(() =>
  import('./FirmIngestionCenter').then((module) => ({ default: module.FirmIngestionCenter })),
);
const MonthlyClientWorkbench = lazy(() =>
  import('./MonthlyClientWorkbench').then((module) => ({ default: module.MonthlyClientWorkbench })),
);
const ServicePlanPanel = lazy(() =>
  import('./ServicePlanPanel').then((module) => ({ default: module.ServicePlanPanel })),
);
const ServicePlanMaterials = lazy(() =>
  import('./ServicePlanMaterials').then((module) => ({ default: module.ServicePlanMaterials })),
);
const EfficiencyWorkbench = lazy(() =>
  import('./EfficiencyWorkbench').then((module) => ({ default: module.EfficiencyWorkbench })),
);
const QualityWorkbench = lazy(() =>
  import('./QualityWorkbench').then((module) => ({ default: module.QualityWorkbench })),
);
const RevisionWorkbench = lazy(() =>
  import('./RevisionWorkbench').then((module) => ({ default: module.RevisionWorkbench })),
);
const ReviewWorkbench = lazy(() =>
  import('./ReviewWorkbench').then((module) => ({ default: module.ReviewWorkbench })),
);
const CasesWorkbench = lazy(() =>
  import('./CasesWorkbench').then((module) => ({ default: module.CasesWorkbench })),
);
const OperationsWorkbench = lazy(() =>
  import('./OperationsWorkbench').then((module) => ({ default: module.OperationsWorkbench })),
);
const QueueWorkbench = lazy(() =>
  import('./QueueWorkbench').then((module) => ({ default: module.QueueWorkbench })),
);
const IntelligenceWorkbench = lazy(() =>
  import('./IntelligenceWorkbench').then((module) => ({ default: module.IntelligenceWorkbench })),
);
const BusinessWorkspace = lazy(() =>
  import('./BusinessWorkspace').then((module) => ({ default: module.BusinessWorkspace })),
);
const ReconciliationWorkbench = lazy(() =>
  import('./ReconciliationWorkbench').then((module) => ({
    default: module.ReconciliationWorkbench,
  })),
);
const MonthlyReconciliationWorkbench = lazy(() =>
  import('./MonthlyReconciliationWorkbench').then((module) => ({
    default: module.MonthlyReconciliationWorkbench,
  })),
);
const ClosingWorkbench = lazy(() =>
  import('./ClosingWorkbench').then((module) => ({ default: module.ClosingWorkbench })),
);
const DeliveryWorkbench = lazy(() =>
  import('./DeliveryWorkbench').then((module) => ({ default: module.DeliveryWorkbench })),
);
const SettlementWorkbench = lazy(() =>
  import('./SettlementWorkbench').then((module) => ({ default: module.SettlementWorkbench })),
);
const RecurringEntries = lazy(() =>
  import('./RecurringEntries').then((module) => ({ default: module.RecurringEntries })),
);
const MonthlyBriefing = lazy(() =>
  import('./MonthlyBriefing').then((module) => ({ default: module.MonthlyBriefing })),
);
const IntegrationWorkbench = lazy(() =>
  import('./IntegrationWorkbench').then((module) => ({ default: module.IntegrationWorkbench })),
);
const CatalogWorkbench = lazy(() =>
  import('./CatalogWorkbench').then((module) => ({ default: module.CatalogWorkbench })),
);
const LearningWorkbench = lazy(() =>
  import('./LearningWorkbench').then((module) => ({ default: module.LearningWorkbench })),
);
const LedgerWorkspace = lazy(() =>
  import('./LedgerWorkspace').then((module) => ({ default: module.LedgerWorkspace })),
);
const FormalBooksWorkbench = lazy(() =>
  import('./FormalBooksWorkbench').then((module) => ({ default: module.FormalBooksWorkbench })),
);
const LegacyMigrationWorkspace = lazy(() => import('./LegacyMigrationWorkspace'));

type CaseTarget = { eventId?: string; bookId?: string; draftId?: string; title?: string };

interface WorkspaceRouteApp {
  session: Session;
  me: Me | null;
  routeView: WorkspaceView;
  routeUsesBook: boolean;
  profile: WorkspaceProfile | null;
  profileError: string;
  homeBootstrap: WorkspaceHomeBootstrap | null;
  consumeHomeBootstrap: (scope: string) => void;
  loadProfile: () => void;
  workspaceRevision: number;
  setWorkspaceRevision: Dispatch<SetStateAction<number>>;
  navigationLocked: boolean;
  showBusiness: boolean;
  canFirmOperations: boolean;
  businessBusy: boolean;
  monthlyDirty: boolean;
  changeBusinessBusy: (value: boolean) => void;
  changeMonthlyDirty: (value: boolean) => void;
  workCompany: string;
  workPeriod: string;
  workBook: string;
  businessTarget: string;
  intakeTarget: string;
  intakeTemplate?: BusinessTemplate;
  newIntake: boolean;
  materialTarget: string;
  deliveryTarget: string;
  migrationTarget: string;
  operationsTarget: string;
  operationsCompanies: OperationsCompany[];
  queueTarget: string;
  queueJobTarget: string;
  initialCaseId: string;
  caseTarget?: CaseTarget;
  setWorkCompany: (value: string) => void;
  setWorkPeriod: (value: string) => void;
  setWorkBook: (value: string) => void;
  setBusinessTarget: (value: string) => void;
  setQueueTarget: (value: string) => void;
  setQueueJobTarget: (value: string) => void;
  setCaseTarget: (value: CaseTarget | undefined) => void;
  setMigrationTarget: (value: string) => void;
  setRouteView: (view: WorkspaceView) => void;
  setNotice: (value: BookNotice) => void;
  reviewNotice?: BookNotice;
  setInlineTarget: (value: { bookId: string; draftId: string } | null) => void;
  navigate: (view: AgentNavigationView, fromAssistant?: boolean) => void;
  openWorkspaceTask: (task: WorkspaceTask) => void;
  openNewIntake: (template: BusinessTemplate) => void;
  openAgentRun: (route: AgentRoute) => void;
  openMonthlyTarget: (target: MonthlyDestination) => void;
  openServicePlan: (target: { bookId: string; companyId: string; period: string }) => void;
  openServiceStage: (target: ServiceJourneyTarget) => void;
  openRecordRevision: (eventId: string, scope?: { companyId: string; period: string }) => void;
  openRecordDraft: (bookId: string, draftId: string) => void;
  returnToWork: (discarded?: boolean) => void;
  progress: ReturnType<typeof useWorkspaceProgress>;
  openClosingIssue: (issue: { type: string; id: string; bookId: string }) => void;
  openInlineReview: (
    target: { book_id: string; draft_id: string },
    queue: { book_id: string; draft_id: string }[],
    onChanged: (bookId: string, draft: Draft) => void,
    onClose: () => void,
  ) => void;
  openContextAssistant: (
    target: { bookId: string; draftId: string; version: number } | null,
  ) => void;
  onManageTeam?: () => void;
  openBookModal: () => void;
  openImport: () => void;
}

export function WorkspaceRouteContent({
  app,
  book,
}: {
  app: WorkspaceRouteApp;
  book: ReturnType<typeof useBookWorkspace>;
}) {
  const {
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
    listTab,
    openDraftInBook,
    operationBusy,
    pendingReview,
    refresh,
    reviewDirty,
    reviewNavigationLoading,
    runAction,
    saveDraft,
    search,
    selectedIds,
    setBookId,
    setBookOffset,
    setDetail,
    setDraftId,
    setDraftOffset,
    setFilter,
    setListTab,
    setSearch,
    setSelectedIds,
    setSourceOffset,
    stats,
    workbench,
  } = book;
  const {
    session,
    me,
    routeView,
    routeUsesBook,
    profile,
    profileError,
    homeBootstrap,
    consumeHomeBootstrap,
    loadProfile,
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
    reviewNotice,
    setInlineTarget,
    navigate,
    openWorkspaceTask,
    openNewIntake,
    openAgentRun,
    openMonthlyTarget,
    openServicePlan,
    openServiceStage,
    openRecordRevision,
    openRecordDraft,
    returnToWork,
    progress,
    openClosingIssue,
    openInlineReview,
    openContextAssistant,
    onManageTeam,
    openBookModal,
    openImport,
  } = app;
  const navItems: {
    id: BookFilter;
    label: string;
    icon: typeof LayoutDashboard;
    count?: number;
  }[] = [
    { id: 'all', label: '会计工作台', icon: LayoutDashboard },
    { id: 'pending', label: '待审核凭证', icon: ClipboardCheck, count: stats?.pending_count },
    { id: 'exception', label: '异常处理', icon: TriangleAlert, count: stats?.exception_count },
    { id: 'approved', label: '审核与导出', icon: FileCheck2, count: stats?.approved_count },
  ];

  return (
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
      {routeView === 'home' ? (
        <WorkspaceHome
          key={session.scope_key}
          initialCompanyId={workCompany}
          session={session}
          profile={profile}
          profileError={profileError}
          initialTasks={homeBootstrap?.tasks ?? null}
          onInitialTasksConsumed={() => consumeHomeBootstrap(session.scope_key)}
          onRetryProfile={() => void loadProfile()}
          revision={workspaceRevision}
          progress={progress.memory.getHome(workCompany)}
          onProgressChange={progress.saveHome}
          paused={navigationLocked}
          onOpen={openWorkspaceTask}
          onNew={openNewIntake}
          onCompanyChange={(companyId) => {
            setWorkCompany(companyId);
            setWorkBook('');
            setWorkPeriod('');
          }}
          onAccounting={() => navigate('accounting')}
          onMonthly={() => navigate('monthly')}
          onReview={() => navigate('review')}
          onBriefing={(target) => {
            navigate('accounting');
            setWorkCompany(target.company_id);
            setWorkPeriod(target.period);
            setBookId(target.book_id);
            focusBookRef.current = target.book_id;
            setRouteView('briefing');
          }}
          onRuns={() => navigate('runs')}
          onBusiness={() => {
            navigate('business');
            setWorkCompany(workCompany);
            setWorkPeriod(workPeriod);
          }}
          onMaterials={() => {
            navigate('materials');
            setWorkCompany(workCompany);
            setWorkBook(workBook);
            setWorkPeriod(workPeriod);
          }}
        />
      ) : routeView === 'runs' ? (
        <AgentActivityWorkspace
          key={session.scope_key}
          session={session}
          revision={workspaceRevision}
          onOpen={openAgentRun}
          onBack={() => navigate('home')}
          onBusyChange={changeBusinessBusy}
        />
      ) : routeView === 'firm' && canFirmOperations ? (
        <FirmCockpit
          key={`${session.scope_key}:${workPeriod}`}
          session={session}
          initialPeriod={workPeriod}
          onOpenBook={(target) => {
            navigate('accounting');
            setWorkCompany(target.companyId);
            setWorkPeriod(target.period);
            setBookId(target.bookId);
            focusBookRef.current = target.bookId;
            void initialize(undefined, target.bookId);
          }}
          onOpenInbox={() => navigate('inbox')}
          onMonthly={(period) => {
            navigate('monthly');
            setWorkPeriod(period);
          }}
          onTeam={onManageTeam}
          onOnboarding={(period) => {
            navigate('onboarding');
            setWorkPeriod(period);
          }}
        />
      ) : routeView === 'firm' ? (
        <EmptyState title="全所工作台由事务所负责人管理" icon={<ShieldCheck size={28} />}>
          当前账号可以继续处理本企业获授权的资料和事项。
        </EmptyState>
      ) : routeView === 'onboarding' ? (
        <FirmOnboarding
          key={`${session.scope_key}:${workPeriod}`}
          session={session}
          initialPeriod={workPeriod}
          onOpenBook={(target) => {
            navigate('accounting');
            setWorkCompany(target.companyId);
            setWorkPeriod(target.period);
            setBookId(target.bookId);
            focusBookRef.current = target.bookId;
            void initialize(undefined, target.bookId);
          }}
        />
      ) : routeView === 'inbox' ? (
        <FirmInbox key={session.scope_key} session={session} onOpen={openWorkspaceTask} />
      ) : routeView === 'observability' ? (
        <FirmObservability session={session} />
      ) : routeView === 'ingestion' ? (
        <FirmIngestionCenter session={session} />
      ) : routeView === 'messages' ? (
        <ClientMessages session={session} onOpen={openWorkspaceTask} />
      ) : routeView === 'monthly' ? (
        <MonthlyClientWorkbench
          key={session.scope_key}
          session={session}
          period={workPeriod}
          onPeriodChange={setWorkPeriod}
          onOpen={openMonthlyTarget}
          progress={progress.memory.getMonthly(workPeriod)}
          onProgressChange={progress.saveMonthly}
          revision={workspaceRevision}
          onOpenServicePlan={openServicePlan}
          canBatchOpen={!!me?.permissions.manage_firm}
        />
      ) : routeView === 'service' ? (
        <main className="monthly-workbench">
          <button
            className="text-button"
            disabled={businessBusy || monthlyDirty}
            onClick={() => returnToWork()}
          >
            返回客户月度进度
          </button>
          {workBook && workCompany && workPeriod ? (
            <ServicePlanPanel
              key={`${session.scope_key}:${workBook}`}
              session={session}
              bookId={workBook}
              companyId={workCompany}
              companyName={
                profile?.companies.find((company) => company.id === workCompany)?.name ?? ''
              }
              period={workPeriod}
              revision={workspaceRevision}
              onBusyChange={changeBusinessBusy}
              onDirtyChange={changeMonthlyDirty}
              onChanged={() => setWorkspaceRevision((n) => n + 1)}
              onOpenStage={openServiceStage}
            />
          ) : (
            <p className="field-help">请从客户月度进度选择具体账套和月份。</p>
          )}
        </main>
      ) : routeView === 'materials' ? (
        <main className="monthly-workbench">
          <ServicePlanMaterials
            key={`${session.scope_key}:${workBook}:${materialTarget}`}
            session={session}
            companyId={workCompany}
            period={workPeriod}
            bookId={workBook || undefined}
            initialRequestId={materialTarget}
            revision={workspaceRevision}
            onBusyChange={changeBusinessBusy}
            onDirtyChange={changeMonthlyDirty}
            onChanged={() => setWorkspaceRevision((n) => n + 1)}
          />
        </main>
      ) : routeView === 'portfolio' ? (
        <EfficiencyWorkbench
          key={session.scope_key + ':portfolio'}
          session={session}
          onBusyChange={changeBusinessBusy}
          onBack={() => navigate('home')}
          onOpenBook={(id) => {
            navigate('accounting');
            setBookId(id);
            focusBookRef.current = id;
            setRouteView('efficiency');
            if (routeUsesBook) void initialize(undefined, id);
          }}
        />
      ) : routeView === 'quality' && bookId ? (
        <QualityWorkbench
          key={session.scope_key + ':' + bookId}
          session={session}
          bookId={bookId}
          onBack={() => setRouteView('accounting')}
          onBusyChange={changeBusinessBusy}
          onOpenDraft={openDraftInBook}
        />
      ) : routeView === 'revision' ? (
        <RevisionWorkbench
          key={session.scope_key + ':' + businessTarget}
          session={session}
          initialEventId={businessTarget}
          onBusyChange={changeBusinessBusy}
          onDirtyChange={changeMonthlyDirty}
          onBack={() => returnToWork(true)}
          backLabel={businessTarget ? '返回原业务事项' : '返回工作台'}
          onChanged={() => {
            if (bookId) void refresh(bookId);
          }}
        />
      ) : routeView === 'efficiency' && bookId ? (
        <EfficiencyWorkbench
          key={session.scope_key + ':' + bookId}
          session={session}
          bookId={bookId}
          onBack={() => setRouteView('accounting')}
          onBusyChange={changeBusinessBusy}
        />
      ) : routeView === 'review' ? (
        <ReviewWorkbench
          key={session.scope_key + ':' + workCompany + ':' + workPeriod + ':' + workBook}
          session={session}
          onBusyChange={changeBusinessBusy}
          onChanged={() => initialize()}
          onOpenDraft={openDraftInBook}
          onOpenInline={openInlineReview}
        />
      ) : routeView === 'cases' ? (
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
      ) : routeView === 'operations' && canFirmOperations ? (
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
      ) : routeView === 'operations' ? (
        <main>
          <EmptyState title="费用与运行监控由事务所管理" icon={<ShieldCheck size={26} />}>
            该工作区只向事务所管理层开放。企业侧可在“组织管理后台”的智能识别页签中，查看或暂停本企业的资料外发。
          </EmptyState>
        </main>
      ) : routeView === 'queue' ? (
        <QueueWorkbench
          key={`${session.scope_key}:${queueTarget}`}
          session={session}
          initialBatchId={queueTarget}
          initialJobId={queueJobTarget}
          onBusyChange={changeBusinessBusy}
          onOpenEvent={(id) => {
            setBusinessTarget(id);
            setRouteView('business');
          }}
        />
      ) : routeView === 'intelligence' ? (
        <IntelligenceWorkbench
          key={session.scope_key}
          session={session}
          onOpenEvent={(id) => {
            setBusinessTarget(id);
            setRouteView('business');
          }}
        />
      ) : routeView === 'briefing' &&
        bookId &&
        profile &&
        workSurface(presentationRole(profile)) === 'client' ? (
        <MonthlyBriefing
          key={session.scope_key + ':' + bookId}
          session={session}
          bookId={bookId}
          onBack={() => navigate('home')}
          onBusyChange={changeBusinessBusy}
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
            setRouteView('queue');
          }}
          onBusyChange={changeBusinessBusy}
          onOpenRevision={openRecordRevision}
          onOpenCase={(target) => {
            setCaseTarget(target);
            setRouteView('cases');
          }}
          onOpenDraft={openRecordDraft}
        />
      ) : (
        <BookWorkspaceShell
          session={session}
          me={me}
          routeView={routeView}
          bookView={
            routeView === 'reconciliation' && bookId ? (
              <ReconciliationWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => {
                  void refresh(bookId);
                }}
              />
            ) : routeView === 'balances' && bookId ? (
              <MonthlyReconciliationWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onDirtyChange={changeMonthlyDirty}
                onChanged={() => {
                  void refresh(bookId);
                  setWorkspaceRevision((n) => n + 1);
                }}
              />
            ) : routeView === 'closing' && bookId ? (
              <ClosingWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => {
                  void refresh(bookId);
                  setWorkspaceRevision((n) => n + 1);
                }}
                onOpenIssue={openClosingIssue}
              />
            ) : routeView === 'delivery' && bookId ? (
              <DeliveryWorkbench
                key={session.scope_key + ':' + bookId + ':' + deliveryTarget}
                session={session}
                bookId={bookId}
                companyId={detail?.book.id === bookId ? detail.book.company_id : undefined}
                period={detail?.book.id === bookId ? detail.book.period : undefined}
                initialExportId={deliveryTarget}
                embedded
                onBack={() => (progress.origin ? returnToWork() : setRouteView('accounting'))}
                onBusyChange={changeBusinessBusy}
              />
            ) : routeView === 'recurring' && bookId ? (
              <RecurringEntries
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onDirtyChange={changeMonthlyDirty}
                onOpenDraft={(id) => openDraftInBook(bookId, id)}
                onChanged={() => {
                  void refresh(bookId);
                  setWorkspaceRevision((n) => n + 1);
                }}
              />
            ) : routeView === 'settlement' && bookId ? (
              <SettlementWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => {
                  void refresh(bookId);
                }}
                onOpenDraft={(id) => openDraftInBook(bookId, id)}
              />
            ) : routeView === 'briefing' && bookId ? (
              <MonthlyBriefing
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
              />
            ) : routeView === 'integration' && bookId ? (
              <IntegrationWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
              />
            ) : routeView === 'catalog' && bookId ? (
              <CatalogWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => refresh(bookId)}
              />
            ) : routeView === 'learning' && bookId ? (
              <LearningWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => refresh(bookId)}
              />
            ) : routeView === 'formal-books' && bookId ? (
              <FormalBooksWorkbench
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                bookName={detail?.book.name}
                companyId={detail?.book.company_id}
                period={detail?.book.period}
                canDownload={detail?.book.permissions.export ?? false}
                canReview={detail?.book.permissions.review ?? false}
                onBack={() => setRouteView('accounting')}
              />
            ) : routeView === 'ledger' && bookId ? (
              <LedgerWorkspace
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                embedded
                onBack={() => setRouteView('accounting')}
                onBusyChange={changeBusinessBusy}
                onChanged={() => {
                  void refresh(bookId);
                  setWorkspaceRevision((n) => n + 1);
                }}
              />
            ) : routeView === 'migration' && bookId ? (
              <LegacyMigrationWorkspace
                key={session.scope_key + ':' + bookId}
                session={session}
                bookId={bookId}
                initialProjectId={migrationTarget || undefined}
                embedded
                onBack={() => {
                  setMigrationTarget('');
                  setRouteView('accounting');
                }}
                onBusyChange={changeBusinessBusy}
                onOpenDraft={openDraftInBook}
                onChanged={() => {
                  void refresh(bookId);
                  setWorkspaceRevision((n) => n + 1);
                }}
              />
            ) : null
          }
          bookViewBusy={businessBusy}
          bookViewDirty={monthlyDirty}
          filter={filter}
          navItems={navItems}
          stats={stats}
          workbench={workbench}
          filteredBooks={filteredBooks}
          detail={detail}
          bookId={bookId}
          drafts={drafts}
          activeDraft={activeDraft}
          approvedDraftIds={approvedDraftIds}
          selectedIds={selectedIds}
          search={search}
          listTab={listTab}
          busy={busy}
          operationBusy={operationBusy}
          bootLoading={bootLoading}
          bootError={bootError}
          bookLoading={bookLoading}
          bookError={bookError}
          draftLoading={draftLoading}
          draftError={draftError}
          reviewNavigationLoading={reviewNavigationLoading}
          reviewDirty={reviewDirty}
          historyLoading={historyLoading}
          historyError={historyError}
          exportHistory={exportHistory}
          focusBookRef={focusBookRef}
          pendingReview={pendingReview}
          initialize={initialize}
          refresh={refresh}
          runAction={runAction}
          chooseBook={chooseBook}
          generateSuggestions={generateSuggestions}
          saveDraft={saveDraft}
          approveDraft={approveDraft}
          exportDrafts={exportDrafts}
          setSearch={setSearch}
          setBookOffset={setBookOffset}
          setDraftOffset={setDraftOffset}
          setSourceOffset={setSourceOffset}
          setListTab={setListTab}
          setDraftId={setDraftId}
          setSelectedIds={setSelectedIds}
          setRouteView={setRouteView}
          setQueueTarget={setQueueTarget}
          setInlineTarget={setInlineTarget}
          setAssistantTarget={openContextAssistant}
          setCaseTarget={setCaseTarget}
          setNotice={setNotice}
          reviewNotice={reviewNotice}
          changeBusinessBusy={changeBusinessBusy}
          changeReviewDirty={changeReviewDirty}
          changeIntelligenceBusy={changeIntelligenceBusy}
          onOpenBookModal={openBookModal}
          onOpenImport={openImport}
        />
      )}
    </Suspense>
  );
}
