import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowRightLeft,
  CheckCircle2,
  CircleDot,
  Clock3,
  FilePlus2,
  FileText,
  List,
  LoaderCircle,
  Play,
  RefreshCw,
  Receipt,
  Save,
  Search,
  ShoppingBag,
  Table2,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { AgentCompanyPicker } from './AgentCompanyPicker';
import {
  formatTaskAmount,
  savedWorkspaceViews,
  taskLabels,
  viewStorageKey,
  workspaceSortLabels,
} from './workspace-types';
import type {
  SavedWorkspaceView,
  WorkspaceDensity,
  WorkspaceProfile,
  WorkspaceSort,
  WorkspaceTask,
  WorkspaceTasks,
  TaskKind,
} from './workspace-types';
import type { Session } from './types';
import type { BusinessTemplate } from './business-types';
import { workspaceExperience } from './workspace-experience';
import { roleHomeGuide } from './workspace-role-home';
import { presentationRole, roleHome } from './workspace-presentation';
import { currentHomeTask } from './workspace-home-focus';
import { taskPresentation } from './task-presentation';
import { homeProgress, type HomeProgress } from './workspace-progress';
import './workspace.css';

export function WorkspaceHome({
  session,
  profile,
  profileError,
  onRetryProfile,
  onOpen,
  onNew,
  onCompanyChange,
  onAccounting,
  onMaterials,
  onBusiness,
  onReview,
  onBriefing,
  revision,
  paused = false,
  initialCompanyId = '',
  initialTasks = null,
  onInitialTasksConsumed,
  progress,
  onProgressChange,
}: {
  session: Session;
  profile: WorkspaceProfile | null;
  profileError: string;
  onRetryProfile: () => void;
  onOpen: (task: WorkspaceTask) => void;
  onNew: (template: BusinessTemplate) => void;
  onCompanyChange?: (companyId: string) => void;
  onAccounting: () => void;
  onMaterials?: () => void;
  onBusiness?: () => void;
  onReview: () => void;
  onBriefing?: (target: {
    book_id: string;
    book_name: string;
    period: string;
    company_id: string;
    company_name: string;
  }) => void;
  onRuns?: () => void;
  revision: number;
  paused?: boolean;
  initialCompanyId?: string;
  initialTasks?: WorkspaceTasks | null;
  onInitialTasksConsumed?: () => void;
  progress?: HomeProgress;
  onProgressChange?: (value: HomeProgress) => void;
}) {
  const initialProgress = useRef(homeProgress(progress, initialCompanyId));
  const [bucket, setBucket] = useState(initialProgress.current.bucket);
  const experience = workspaceExperience(profile);
  const [listOptionsOpen, setListOptionsOpen] = useState(initialProgress.current.optionsOpen);
  const showListOptions = !experience.simple || listOptionsOpen;
  const [kind, setKind] = useState<TaskKind | ''>(initialProgress.current.kind);
  const [company, setCompany] = useState(initialCompanyId);
  const initialCompany = useRef(initialCompanyId);
  initialCompany.current = initialCompanyId;
  const [offset, setOffset] = useState(initialProgress.current.offset);
  const [sort, setSort] = useState<WorkspaceSort>(initialProgress.current.sort);
  const [density, setDensity] = useState<WorkspaceDensity>(initialProgress.current.density);
  const [query, setQuery] = useState(initialProgress.current.query);
  const [search, setSearch] = useState(initialProgress.current.query.trim());
  const scrollRestore = useRef(initialProgress.current.scrollY ?? 0);
  const restoredScroll = useRef(false);
  const appliedCompany = useRef(initialCompanyId);
  const [views, setViews] = useState<SavedWorkspaceView[]>([]);
  const [viewNotice, setViewNotice] = useState('');
  const firstTasks = useRef(initialCompanyId ? null : initialTasks);
  const initialTasksConsumed = useRef(false);
  const consumeInitialTasks = useRef(onInitialTasksConsumed);
  consumeInitialTasks.current = onInitialTasksConsumed;
  const [value, setValue] = useState<WorkspaceTasks | null>(firstTasks.current);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState('');
  const [openError, setOpenError] = useState('');
  const [privacyRevision, setPrivacyRevision] = useState(0);
  const epoch = useRef(0);
  const alive = useRef(true);
  const scope = useRef(session.scope_key);
  scope.current = session.scope_key;
  const dataScope = useRef(firstTasks.current ? session.scope_key : '');
  const dataFilter = useRef(
    firstTasks.current
      ? JSON.stringify([session.scope_key, 'action', '', '', 0, 'priority', ''])
      : '',
  );
  const openingLock = useRef(false);
  const openingController = useRef<AbortController | null>(null);
  const filterKey = JSON.stringify([
    session.scope_key,
    bucket,
    kind,
    company,
    offset,
    sort,
    search,
  ]);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      firstTasks.current = null;
      const request = ++epoch.current;
      const started = session.scope_key;
      setLoading(true);
      setError('');
      try {
        const params = new URLSearchParams({
          bucket,
          sort,
          limit: '20',
          offset: String(offset),
          ...(kind ? { kind } : {}),
          ...(company ? { company_id: company } : {}),
          ...(search ? { q: search } : {}),
        });
        const next = await api<WorkspaceTasks>(`/workspace/tasks?${params}`, session, { signal });
        if (
          alive.current &&
          scope.current === started &&
          !signal?.aborted &&
          request === epoch.current
        ) {
          dataScope.current = started;
          dataFilter.current = JSON.stringify([
            started,
            bucket,
            kind,
            company,
            offset,
            sort,
            search,
          ]);
          setValue(next);
        }
      } catch (caught) {
        if (
          alive.current &&
          scope.current === started &&
          !signal?.aborted &&
          request === epoch.current
        ) {
          setValue(null);
          if (caught instanceof ApiError && [401, 403, 404].includes(caught.status))
            setPrivacyRevision((value) => value + 1);
          setError(errorMessage(caught));
        }
      } finally {
        if (
          alive.current &&
          scope.current === started &&
          !signal?.aborted &&
          request === epoch.current
        )
          setLoading(false);
      }
    },
    [session, bucket, kind, company, offset, sort, search],
  );

  useEffect(() => {
    alive.current = true;
    setValue(firstTasks.current);
    setOpenError('');
    setViews([]);
    setBucket(initialProgress.current.bucket);
    setKind(initialProgress.current.kind);
    setCompany(initialCompany.current);
    setOffset(initialProgress.current.offset);
    setSort(initialProgress.current.sort);
    setDensity(initialProgress.current.density);
    setSearch(initialProgress.current.query.trim());
    setQuery(initialProgress.current.query);
    setViewNotice('');
    try {
      setViews(savedWorkspaceViews(localStorage.getItem(viewStorageKey(session))));
    } catch {
      /* Privacy mode may disable local storage. */
    }
    return () => {
      alive.current = false;
      epoch.current++;
      openingController.current?.abort();
    };
  }, [session.scope_key]);
  useEffect(() => {
    if (appliedCompany.current === initialCompanyId) return;
    appliedCompany.current = initialCompanyId;
    setCompany(initialCompanyId);
    setOffset(0);
  }, [initialCompanyId]);
  useEffect(() => {
    onProgressChange?.({
      bucket,
      kind,
      companyId: company,
      offset,
      sort,
      density,
      query,
      optionsOpen: listOptionsOpen,
    });
  }, [bucket, kind, company, offset, sort, density, query, listOptionsOpen, onProgressChange]);
  useEffect(() => {
    if (query.trim() === search) return;
    const timeout = setTimeout(() => {
      setSearch(query.trim());
      setOffset(0);
    }, 220);
    return () => clearTimeout(timeout);
  }, [query, search]);
  useEffect(() => {
    if (!initialTasksConsumed.current) {
      initialTasksConsumed.current = true;
      consumeInitialTasks.current?.();
    }
    // Keep the initial response only inside this mounted view. React's development
    // effect replay must not turn one bootstrap into duplicate authorized reads.
    if (firstTasks.current && revision === 0 && filterKey === dataFilter.current) return;
    const controller = new AbortController();
    setValue(null);
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current++;
    };
  }, [load, revision, filterKey]);

  async function open(task: WorkspaceTask) {
    if (paused || openingLock.current) return;
    openingLock.current = true;
    setOpening(task.key);
    setOpenError('');
    const started = session.scope_key;
    const controller = new AbortController();
    openingController.current?.abort();
    openingController.current = controller;
    try {
      const current = await api<WorkspaceTask>(
        `/workspace/tasks/${encodeURIComponent(task.kind)}/${encodeURIComponent(task.ref)}`,
        session,
        { signal: controller.signal },
      );
      if (alive.current && scope.current === started && !controller.signal.aborted) onOpen(current);
    } catch (caught) {
      if (alive.current && scope.current === started && !controller.signal.aborted) {
        setValue(null);
        if (caught instanceof ApiError && [401, 403, 404].includes(caught.status))
          setPrivacyRevision((value) => value + 1);
        setOpenError(errorMessage(caught));
        void load();
      }
    } finally {
      if (alive.current && scope.current === started) setOpening('');
      openingLock.current = false;
    }
  }
  function saveView() {
    const next = [...views.slice(-2), { bucket, kind, company_id: company, sort, density }];
    setViews(next);
    try {
      localStorage.setItem(viewStorageKey(session), JSON.stringify(next));
      setViewNotice('已保存筛选。企业权限在每次读取时核验。');
    } catch {
      setViewNotice('当前浏览器无法保存筛选，仍可继续工作。');
    }
  }
  function restoreView(view: SavedWorkspaceView) {
    setBucket(view.bucket);
    setKind(view.kind);
    setCompany(view.company_id);
    onCompanyChange?.(view.company_id);
    setSort(view.sort);
    setDensity(view.density);
    setQuery('');
    setSearch('');
    setOffset(0);
    setViewNotice(
      view.company_id ? '已恢复指定企业筛选；不会切换到其他企业。' : '已恢复常用筛选。',
    );
  }
  function removeView(index: number) {
    const next = views.filter((_, position) => position !== index);
    setViews(next);
    try {
      localStorage.setItem(viewStorageKey(session), JSON.stringify(next));
    } catch {
      /* No private records are stored. */
    }
  }
  const role = presentationRole(profile);
  const employee = role === 'employee';
  const finance = role === 'finance';
  const copy = roleHome(role);
  const guide = profile ? roleHomeGuide(profile) : null;
  const briefings =
    profile &&
    ['manager', 'finance', 'accountant', 'reviewer', 'firm_owner', 'admin'].includes(role)
      ? (profile.briefings ?? [])
      : [];
  const visibleValue =
    dataScope.current === session.scope_key && dataFilter.current === filterKey ? value : null;
  const rows = visibleValue?.tasks ?? [];
  useEffect(() => {
    if (loading || !visibleValue || restoredScroll.current) return;
    const frame = requestAnimationFrame(() => {
      window.scrollTo({ top: scrollRestore.current, behavior: 'instant' });
      const key = initialProgress.current.taskKey;
      if (key)
        document
          .querySelector<HTMLElement>(`[data-task-key="${CSS.escape(key)}"] button`)
          ?.focus({ preventScroll: true });
      restoredScroll.current = true;
    });
    return () => cancelAnimationFrame(frame);
  }, [loading, visibleValue]);
  const locked = paused || !!opening;
  const first = currentHomeTask({
    tasks: visibleValue,
    bucket,
    offset,
    company,
    loading,
    error: !!(error || openError || profileError),
    scopeMatches: !onCompanyChange || company === initialCompanyId,
    searchPending: query.trim() !== search,
  });
  const financeNext = profile?.can_finance
    ? currentHomeTask(
        {
          tasks: visibleValue,
          bucket,
          offset,
          company,
          loading,
          error: !!(error || openError || profileError),
          scopeMatches: !onCompanyChange || company === initialCompanyId,
          searchPending: query.trim() !== search,
        },
        true,
      )
    : undefined;
  return (
    <main
      className={
        'workspace-home' +
        (experience.simple ? ' workspace-simple' : '') +
        (employee ? ' workspace-employee' : finance ? ' workspace-finance' : '')
      }
    >
      <header className="workspace-heading">
        <div>
          <p className="workspace-kicker" aria-hidden="true">
            <CircleDot size={13} /> HENGZHANG · ACCOUNTING AGENT
          </p>
          <h1>{copy.name}</h1>
          <p>{copy.subtitle}</p>
        </div>
        <div className="workspace-header-actions">
          <button
            className="button secondary workspace-refresh"
            aria-label="刷新工作台"
            title="刷新工作台"
            disabled={loading || locked}
            onClick={() => {
              onRetryProfile();
              void load();
            }}
          >
            <RefreshCw size={15} />
            <span>刷新工作台</span>
          </button>
          {profile?.can_review && !employee && !finance && (
            <button className="button primary" disabled={locked} onClick={onReview}>
              开始批量复核
              <ArrowRight size={15} />
            </button>
          )}
        </div>
      </header>
      {guide && (
        <section className="workspace-role-guide" aria-label="当前职责与授权范围">
          <details>
            <summary>
              <span>当前身份</span>
              <strong>{guide.label}</strong>
              <span>查看岗位与操作说明</span>
            </summary>
            <div className="workspace-role-guide-content">
              <div className="workspace-role-guide-main">
                <p>{guide.focus}</p>
              </div>
              <div className="workspace-role-guide-actions">
                <span>当前可操作</span>
                <ul>
                  {guide.actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ul>
              </div>
              <p className="workspace-role-guide-boundary">{guide.boundary}</p>
            </div>
          </details>
        </section>
      )}
      {profileError && (
        <p className="inline-error" role="alert">
          {profileError}
          <button className="text-button" onClick={onRetryProfile}>
            重新读取权限
          </button>
        </p>
      )}
      {!profile && !profileError && (
        <p className="loading-block">
          <LoaderCircle className="spinner" size={16} />
          正在读取我的工作范围…
        </p>
      )}
      {employee && profile?.can_submit && (
        <section
          className="workspace-intake-actions workspace-primary-intake"
          aria-label="提交与进度"
        >
          <div>
            <FilePlus2 size={21} />
            <h2>今天要提交什么？</h2>
            <p>上传票据，确认金额和用途后提交；需要补充的资料会在下方提醒。</p>
          </div>
          <div className="workspace-template-grid">
            {(
              [
                ['reimbursement', '提交报销', '员工垫付款项，附上票据和用途', Receipt],
                ['procurement_expense', '采购 / 费用', '记录公司采购或日常开销', ShoppingBag],
                ['daily_cashflow', '日常收支', '说明收到或付出的每一笔款项', ArrowRightLeft],
              ] as const
            ).map(([template, label, hint, Icon]) => (
              <button
                key={template}
                className="workspace-template-button"
                disabled={locked}
                onClick={() => onNew(template)}
              >
                <span className="workspace-template-icon" aria-hidden="true">
                  <Icon size={18} />
                </span>
                <span className="workspace-template-copy">
                  <strong>{label}</strong>
                  <small>{hint}</small>
                </span>
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      )}
      {finance && (
        <section className="workspace-primary-finance" aria-label="资料核验与移交">
          <div>
            <p className="workspace-section-eyebrow">先核对资料，再交给会计</p>
            <h2>核验资料并移交会计</h2>
            <p>核对金额、附件和业务用途；有差异就退回补充，资料完整再移交。</p>
            {financeNext ? (
              <div className={'workspace-next-task' + (financeNext.overdue ? ' overdue' : '')}>
                <span>当前清单下一项</span>
                <strong>{financeNext.title}</strong>
                <small>
                  {financeNext.company_name} · {taskPresentation(financeNext, profile).detail}
                  {financeNext.overdue ? ' · 已逾期' : ''}
                </small>
              </div>
            ) : (
              <p className="workspace-primary-status">
                {!profile?.can_finance
                  ? '财务权限已更新，当前无法核验或移交。请重新读取权限，或联系企业管理员。'
                  : loading
                    ? '正在读取当前清单…'
                    : error || profileError || openError
                      ? '资料状态暂时无法确认，请处理下方提示。'
                      : '按下方当前清单继续办理，也可查看全部业务事项。'}
              </p>
            )}
          </div>
          <div className="workspace-primary-actions">
            {financeNext && (
              <button
                className="button primary"
                disabled={locked || loading}
                onClick={() => void open(financeNext)}
              >
                核验下一项
                <ArrowRight size={15} />
              </button>
            )}
            {onBusiness && (
              <button
                className={financeNext ? 'button secondary' : 'button primary'}
                disabled={locked || !profile?.can_finance || !!profileError}
                onClick={() => {
                  if (profile?.can_finance && !profileError) onBusiness();
                }}
              >
                查看待核验事项
                <ArrowRight size={15} />
              </button>
            )}
            {onMaterials && (
              <button className="button secondary" disabled={locked} onClick={onMaterials}>
                查看资料清单
              </button>
            )}
          </div>
        </section>
      )}
      <section className="workspace-summary" aria-label="我的工作概览">
        <button
          className={bucket === 'action' ? 'active' : ''}
          aria-pressed={bucket === 'action'}
          disabled={locked}
          onClick={() => {
            setBucket('action');
            setOffset(0);
          }}
        >
          <span>
            <TriangleAlert size={16} />
            需要我处理
          </span>
          <strong>{visibleValue?.counts.action ?? '—'}</strong>
          <small>
            {visibleValue?.counts.action == null
              ? '数量暂未汇总，以当前清单为准'
              : experience.actionHint}
          </small>
        </button>
        <button
          className={bucket === 'waiting' ? 'active' : ''}
          aria-pressed={bucket === 'waiting'}
          disabled={locked}
          onClick={() => {
            setBucket('waiting');
            setOffset(0);
          }}
        >
          <span>
            <Clock3 size={16} />
            {experience.waitingLabel}
          </span>
          <strong>{visibleValue?.counts.waiting ?? '—'}</strong>
          <small>保留当前办理进度</small>
        </button>
      </section>
      <section className="panel workspace-inbox">
        <div className="workspace-inbox-heading">
          <div>
            <p className="workspace-section-eyebrow">
              {employee ? '我的提交与补件' : finance ? '当前资料办理清单' : '当前工作清单'}
            </p>
            <h2>
              {bucket === 'action' ? '需要我处理' : '办理进度'}
              <span>{visibleValue?.pagination.total ?? '—'}</span>
            </h2>
          </div>
          <div className="workspace-view-tools">
            {experience.simple && (
              <button
                type="button"
                className="button secondary small"
                aria-expanded={listOptionsOpen}
                disabled={locked}
                onClick={() => setListOptionsOpen((value) => !value)}
              >
                {listOptionsOpen ? '收起列表选项' : '显示列表选项'}
              </button>
            )}
            {showListOptions && (
              <>
                <div className="workspace-density" role="group" aria-label="待办显示密度">
                  <button
                    className={density === 'list' ? 'active' : ''}
                    aria-label="清单视图"
                    aria-pressed={density === 'list'}
                    disabled={locked}
                    onClick={() => setDensity('list')}
                  >
                    <List size={16} />
                  </button>
                  <button
                    className={density === 'table' ? 'active' : ''}
                    aria-label="紧凑表格视图"
                    aria-pressed={density === 'table'}
                    disabled={locked}
                    onClick={() => setDensity('table')}
                  >
                    <Table2 size={16} />
                  </button>
                </div>
                <button className="button secondary small" disabled={locked} onClick={saveView}>
                  <Save size={14} />
                  保存视图
                </button>
              </>
            )}
            {first && (
              <button
                className={
                  financeNext?.key === first.key ? 'button secondary small' : 'button primary small'
                }
                disabled={locked || loading}
                onClick={() => void open(first)}
              >
                <Play size={14} />
                处理下一项
              </button>
            )}
          </div>
        </div>
        <div className="workspace-inbox-filters">
          <label className="agent-search workspace-task-search">
            <Search size={16} />
            <input
              aria-label="搜索工作事项"
              placeholder="搜索事项或企业"
              maxLength={200}
              value={query}
              disabled={locked}
              onChange={(event) => setQuery(event.target.value)}
            />
            {query && (
              <button type="button" aria-label="清空事项搜索" onClick={() => setQuery('')}>
                <X size={14} />
              </button>
            )}
          </label>
          <label className="workspace-select">
            <span className="sr-only">事项类型</span>
            <select
              aria-label="筛选待办类型"
              value={kind}
              disabled={locked}
              onChange={(event) => {
                setKind(event.target.value as TaskKind | '');
                setOffset(0);
              }}
            >
              <option value="">全部事项类型</option>
              {Object.entries(taskLabels)
                .filter(([key]) => !experience.kinds || experience.kinds.includes(key as TaskKind))
                .map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          {profile?.companies.length === 1 && experience.simple && !company ? (
            <span className="workspace-company-identity">{profile.companies[0].name}</span>
          ) : (
            <AgentCompanyPicker
              session={session}
              value={company}
              disabled={locked}
              revision={revision + privacyRevision}
              onChange={(id) => {
                setCompany(id);
                setOffset(0);
                onCompanyChange?.(id);
              }}
            />
          )}
          {showListOptions && (
            <label className="workspace-select">
              <span className="sr-only">排序</span>
              <select
                aria-label="待办排序"
                value={sort}
                disabled={locked}
                onChange={(event) => {
                  setSort(event.target.value as WorkspaceSort);
                  setOffset(0);
                }}
              >
                {Object.entries(workspaceSortLabels).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {showListOptions && !!views.length && (
          <div className="workspace-saved-views" aria-label="常用工作视图">
            <span>常用视图</span>
            {views.map((view, index) => (
              <div key={index}>
                <button disabled={locked} onClick={() => restoreView(view)}>
                  {index + 1} ·{' '}
                  {view.kind
                    ? taskLabels[view.kind]
                    : view.bucket === 'action'
                      ? '待处理'
                      : '办理进度'}
                  {view.company_id ? ' · 指定企业' : ''}
                </button>
                <button
                  aria-label={`删除常用视图 ${index + 1}`}
                  disabled={locked}
                  onClick={() => removeView(index)}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        {viewNotice && (
          <p className="workspace-view-notice" role="status">
            {viewNotice}
          </p>
        )}
        {(openError || error) && (
          <p className="inline-error workspace-load-error" role="alert">
            {openError || error}
          </p>
        )}
        {loading ? (
          <p className="loading-block">
            <LoaderCircle className="spinner" size={16} />
            正在读取待办…
          </p>
        ) : rows.length ? (
          <div
            className={'workspace-task-list ' + (density === 'table' ? 'workspace-table-view' : '')}
          >
            {density === 'table' && (
              <div className="workspace-table-labels" aria-hidden="true">
                <span>事项 / 企业</span>
                <span>状态 / 期限</span>
                <span>金额</span>
                <span>下一步</span>
              </div>
            )}
            {rows.map((task) => {
              const summary = taskPresentation(task, profileError ? null : profile);
              return (
                <article
                  className={'workspace-task ' + (task.overdue ? 'overdue' : '')}
                  key={task.key}
                  data-task-key={task.key}
                  data-summary={summary.key}
                >
                  <div className="workspace-task-main">
                    <div className="workspace-task-meta">
                      <span className="workspace-task-kind">{taskLabels[task.kind]}</span>
                      <span>{task.company_name}</span>
                      {task.overdue && <span className="case-overdue">已逾期</span>}
                      <span className="workspace-task-mobile-amount">
                        {formatTaskAmount(task.amount)}
                      </span>
                    </div>
                    <h3>{task.title}</h3>
                    <small>负责人：{summary.responsible}</small>
                    <p className="workspace-task-guidance">
                      <span>下一步</span>
                      {summary.next}
                    </p>
                  </div>
                  <div className="workspace-task-state">
                    <span data-summary={summary.key} className="workspace-state-pill">
                      {summary.label}
                    </span>
                    <span className="workspace-task-detail" data-state={task.state}>
                      {summary.detail}
                    </span>
                    <small>{task.due_on ? `期限 ${task.due_on}` : '未设置期限'}</small>
                  </div>
                  <span className="workspace-task-amount">{formatTaskAmount(task.amount)}</span>
                  <button
                    className="button secondary"
                    disabled={locked}
                    onClick={() => void open(task)}
                  >
                    {opening === task.key ? <LoaderCircle className="spinner" size={15} /> : null}
                    {summary.actionLabel}
                    <ArrowRight size={15} />
                  </button>
                </article>
              );
            })}
          </div>
        ) : (
          !error && (
            <div className="workspace-clear">
              <CheckCircle2 size={27} />
              <strong>
                {bucket === 'action' ? '当前筛选下没有待处理事项' : '当前筛选下暂无等待事项'}
              </strong>
              <p>
                {company
                  ? '保持指定企业范围，可调整事项类型或搜索条件。'
                  : '新审批、补件、会计异常和迁移事项会集中显示在这里。'}
              </p>
            </div>
          )
        )}
        {visibleValue && (
          <div className="pagination-controls">
            <span>共 {visibleValue.pagination.total} 项 · 每页 20 项</span>
            <button
              className="button secondary small"
              disabled={loading || locked || offset === 0}
              onClick={() => setOffset((n) => Math.max(0, n - 20))}
            >
              上一页待办
            </button>
            <button
              className="button secondary small"
              disabled={loading || locked || !visibleValue.pagination.has_more}
              onClick={() => setOffset((n) => n + 20)}
            >
              下一页待办
            </button>
          </div>
        )}
      </section>
      {!!briefings.length && (
        <section aria-label="本月财税简报入口">
          <details className="workspace-supporting">
            <summary>
              <FileText size={16} />
              <strong>查看已完成期间的财税简报</strong>
            </summary>
            <div className="workspace-briefing-links">
              {briefings
                .filter((item) => !company || item.company_id === company)
                .map((item) => (
                  <button
                    key={item.book_id}
                    type="button"
                    disabled={locked || !onBriefing}
                    onClick={() => onBriefing?.(item)}
                  >
                    <span>
                      <strong>{item.company_name}</strong>
                      <small>
                        {item.book_name} · {item.period}
                      </small>
                    </span>
                    <span>
                      查看简报
                      <ArrowRight size={14} />
                    </span>
                  </button>
                ))}
            </div>
          </details>
        </section>
      )}
      {profile?.can_submit && !employee && (
        <details className="workspace-supporting">
          <summary>
            <FilePlus2 size={16} />
            <strong>记录本人业务</strong>
            <span>报销、采购费用与日常收支</span>
          </summary>
          <div className="workspace-supporting-actions">
            {(
              [
                ['reimbursement', '提交报销'],
                ['procurement_expense', '采购 / 费用'],
                ['daily_cashflow', '日常收支'],
              ] as const
            ).map(([template, label]) => (
              <button
                className="button secondary"
                key={template}
                disabled={locked}
                onClick={() => onNew(template)}
              >
                {label}
                <ArrowRight size={15} />
              </button>
            ))}
          </div>
        </details>
      )}
      {(profile?.can_accounting || (profile?.can_review && (employee || finance))) && (
        <details className="workspace-supporting">
          <summary>
            <FileText size={16} />
            <strong>其他已授权工作</strong>
          </summary>
          <div className="workspace-supporting-actions">
            {profile.can_accounting && (
              <button className="button secondary" disabled={locked} onClick={onAccounting}>
                进入会计账套
                <ArrowRight size={15} />
              </button>
            )}
            {profile.can_review && (employee || finance) && (
              <button className="button secondary" disabled={locked} onClick={onReview}>
                开始批量复核
                <ArrowRight size={15} />
              </button>
            )}
          </div>
        </details>
      )}
      <p className="workspace-boundary">
        <CircleDot size={12} />
        AI 在后台识别、核对与生成草稿；会计复核后导出。
      </p>
    </main>
  );
}
