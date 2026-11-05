import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Building2,
  CalendarDays,
  CalendarPlus,
  LoaderCircle,
  RefreshCw,
  Search,
  ClipboardList,
  FileText,
} from 'lucide-react';
import type { Session } from './types';
import { durationLabel } from './efficiency-helpers';
import { validMonthlyPeriod } from './monthly-helpers';
import {
  monthlyClosingLabel,
  monthlyCount,
  monthlyRowDestination,
  monthlyFocusLabels,
  type MonthlyFocus,
} from './monthly-workbench-types';
import type {
  MonthlyClients,
  MonthlyClientRow,
  MonthlyDestination,
} from './monthly-workbench-types';
import { MonthlyBatchDialog } from './MonthlyBatchDialog';
import { BatchOperationsDialog } from './BatchOperationsDialog';
import { HandoffDialog } from './HandoffDialog';
import { HandoffSourcePanel } from './HandoffSourcePanel';
import { handoffOriginalHref } from './batch-operations';
import { useLocation, useNavigate } from 'react-router-dom';
import { useMonthlyData } from './useMonthlyData';
import type { MonthlyProgress } from './workspace-progress';
import './monthly.css';
import './service-plan.css';
import './workbench-focus.css';

function ProgressCell({
  label,
  primary,
  secondary,
  attention = false,
  onOpen,
}: {
  label: string;
  primary: string;
  secondary: string;
  attention?: boolean;
  onOpen?: () => void;
}) {
  const content = (
    <>
      <span className="monthly-mobile-label">{label}</span>
      <strong>{primary}</strong>
      <small>{secondary}</small>
      {onOpen && <ArrowUpRight size={13} aria-hidden="true" />}
    </>
  );
  return onOpen ? (
    <button
      type="button"
      aria-label={`${label}：${primary}`}
      className={'monthly-progress-cell ' + (attention ? 'attention' : '')}
      onClick={onOpen}
    >
      {content}
    </button>
  ) : (
    <div className="monthly-progress-cell unavailable">{content}</div>
  );
}

export function MonthlyClientWorkbench({
  session,
  period,
  onPeriodChange,
  onOpen,
  revision = 0,
  onOpenServicePlan,
  canBatchOpen = false,
  progress,
  onProgressChange,
}: {
  session: Session;
  period: string;
  onPeriodChange: (period: string) => void;
  onOpen: (target: MonthlyDestination) => void;
  revision?: number;
  onOpenServicePlan?: (target: {
    bookId: string;
    companyId: string;
    companyName: string;
    period: string;
  }) => void;
  canBatchOpen?: boolean;
  progress?: MonthlyProgress;
  onProgressChange?: (value: MonthlyProgress) => void;
}) {
  const initial = useRef(progress?.period === period ? progress : undefined);
  const [query, setQuery] = useState(initial.current?.query ?? ''),
    [search, setSearch] = useState(initial.current?.search ?? ''),
    [offset, setOffset] = useState(initial.current?.offset ?? 0);
  const restoredScroll = useRef(false);
  const [focus, setFocus] = useState<MonthlyFocus>(initial.current?.focus ?? 'all');
  const [expandedRows, setExpandedRows] = useState<string[]>(initial.current?.expandedRows ?? []);
  const [batchOpen, setBatchOpen] = useState(false);
  const [operationsOpen, setOperationsOpen] = useState(false);
  const [initialBatchId, setInitialBatchId] = useState<string | undefined>();
  const [handoffOpen, setHandoffOpen] = useState(false);
  const location = useLocation(),
    navigate = useNavigate();
  const hasHandoffTarget = ['kind', 'id', 'version', 'fingerprint'].some((key) =>
    new URLSearchParams(location.search).has(`handoff_${key}`),
  );
  const selectedPeriod = validMonthlyPeriod(period) ? period : new Date().toISOString().slice(0, 7);
  const path =
    '/monthly/workbench?' +
    new URLSearchParams({
      period: selectedPeriod,
      search,
      focus,
      limit: '20',
      offset: String(offset),
    });
  const { data, loading, error, revoked, load } = useMonthlyData<MonthlyClients>(
    session,
    path,
    revision,
  );
  useEffect(() => {
    onProgressChange?.({ period: selectedPeriod, query, search, offset, focus, expandedRows });
  }, [selectedPeriod, query, search, offset, focus, expandedRows, onProgressChange]);
  useEffect(() => {
    if (!data || loading || revoked || restoredScroll.current) return;
    const frame = requestAnimationFrame(() => {
      window.scrollTo({ top: initial.current?.scrollY ?? 0, behavior: 'instant' });
      restoredScroll.current = true;
    });
    return () => cancelAnimationFrame(frame);
  }, [data, loading, revoked]);
  function open(
    row: MonthlyClientRow,
    view: MonthlyDestination['view'],
    filter?: MonthlyDestination['filter'],
  ) {
    const target = monthlyRowDestination(row, view, filter);
    return !revoked && target ? () => onOpen(target) : undefined;
  }
  function nextAction(row: MonthlyClientRow) {
    if (revoked || !row.book_id || !row.work?.target) return undefined;
    if (row.work.target === 'service') {
      if (
        !onOpenServicePlan ||
        !(row.permissions.edit || row.permissions.review || row.permissions.manage)
      )
        return undefined;
      return () =>
        onOpenServicePlan({
          bookId: row.book_id!,
          companyId: row.company_id,
          companyName: row.company_name,
          period: row.period,
        });
    }
    return open(row, row.work.target, row.work.target === 'review' ? 'pending' : undefined);
  }
  return (
    <main className="monthly-workbench">
      <section className="page-heading">
        <div>
          <h1>客户月度工作</h1>
          <p className="page-description">选出要办理的客户，处理后回到当前清单继续。</p>
        </div>
        <div className="monthly-heading-actions">
          {canBatchOpen && (
            <>
              <button
                className="button secondary"
                disabled={loading || revoked}
                onClick={() => {
                  setInitialBatchId(undefined);
                  setOperationsOpen(true);
                }}
              >
                批量办理
              </button>
            </>
          )}
          {['owner', 'admin', 'operator', 'reviewer', 'viewer'].includes(session.role ?? '') && (
            <button
              className="button secondary"
              disabled={loading || revoked}
              onClick={() => setHandoffOpen(true)}
            >
              人工接手
            </button>
          )}
          {canBatchOpen && (
            <button
              className="button secondary"
              disabled={loading || revoked}
              onClick={() => setBatchOpen(true)}
            >
              <CalendarPlus size={15} />
              批量开期
            </button>
          )}
          <button
            className="button secondary"
            disabled={loading || revoked}
            onClick={() => void load()}
          >
            <RefreshCw size={15} />
            刷新进度
          </button>
        </div>
      </section>
      <form
        className="monthly-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(query.trim());
          setOffset(0);
          setExpandedRows([]);
        }}
      >
        <label className="monthly-period">
          <CalendarDays size={16} />
          <span className="sr-only">客户进度月份</span>
          <input
            aria-label="客户进度月份"
            type="month"
            min="2000-01"
            max="2099-12"
            value={selectedPeriod}
            disabled={revoked}
            onChange={(event) => {
              if (validMonthlyPeriod(event.target.value)) {
                onPeriodChange(event.target.value);
                setOffset(0);
                setFocus('all');
                setExpandedRows([]);
              }
            }}
          />
        </label>
        <label className="monthly-search">
          <Search size={16} />
          <input
            aria-label="搜索月度客户"
            placeholder="搜索企业或账套"
            maxLength={120}
            value={query}
            disabled={revoked}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button className="button secondary small" disabled={revoked}>
          查找
        </button>
        <span>{data ? `共 ${data.pagination.total} 个授权账套 / 企业` : '正在核对授权范围'}</span>
      </form>
      <nav className="monthly-focus" aria-label="月度工作筛选">
        {Object.entries(monthlyFocusLabels).map(([key, label]) => (
          <button
            type="button"
            key={key}
            aria-pressed={focus === key}
            disabled={revoked}
            onClick={() => {
              setFocus(key as MonthlyFocus);
              setOffset(0);
              setExpandedRows([]);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p className="loading-block" role="status">
          <LoaderCircle size={16} className="spinner" />
          正在读取本月进度…
        </p>
      ) : data ? (
        <>
          <section className="monthly-matrix" aria-label="企业月度进度矩阵">
            {data.rows.map((row) => (
              <article
                className="monthly-matrix-row"
                key={row.row_key}
                data-company={row.company_id}
                data-book={row.book_id ?? ''}
              >
                <div className="monthly-client">
                  <Building2 size={18} />
                  <div>
                    <h2>{row.company_name}</h2>
                    <small>
                      {row.period} ·{' '}
                      {row.status === 'closed'
                        ? '期间已锁定'
                        : row.status === 'no_book'
                          ? '等待建账'
                          : '开放期间'}
                    </small>
                  </div>
                </div>
                <div className="monthly-work-next">
                  <div>
                    <span className="monthly-current-stage">
                      当前：{row.work?.label ?? '进度待核实'}
                    </span>
                    <p>
                      {row.work?.reason ?? '当前办理进度待核实，请展开明细核对；不会视为已经完成。'}
                    </p>
                    <span>
                      负责人：{row.work?.owner ?? '待确认'} ·{' '}
                      {row.work?.due_on ? `期限 ${row.work.due_on}` : '期限待安排'}
                    </span>
                  </div>
                  {row.work?.target && (
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={!nextAction(row)}
                      onClick={nextAction(row)}
                    >
                      {row.work.label}
                      <ArrowUpRight size={14} />
                    </button>
                  )}
                </div>
                <details
                  className="monthly-row-details"
                  open={expandedRows.includes(row.row_key)}
                  onToggle={(event) => {
                    const expanded = event.currentTarget.open;
                    setExpandedRows((old) =>
                      expanded
                        ? old.includes(row.row_key)
                          ? old
                          : [...old, row.row_key].slice(-20)
                        : old.filter((key) => key !== row.row_key),
                    );
                  }}
                >
                  <summary>查看各环节明细</summary>
                  <div className="monthly-detail-context">
                    <p>{row.book_name ?? '尚未建立本月账套'}</p>
                    {row.warnings?.length ? (
                      <details className="monthly-warning">
                        <summary>查看资料范围</summary>
                        <p>{row.warnings.join('；')}</p>
                      </details>
                    ) : null}
                    {onOpenServicePlan && row.book_id && (
                      <>
                        <button
                          type="button"
                          className="text-button monthly-service-entry"
                          disabled={
                            revoked ||
                            !(
                              row.permissions.edit ||
                              row.permissions.review ||
                              row.permissions.manage
                            )
                          }
                          onClick={() =>
                            onOpenServicePlan({
                              bookId: row.book_id!,
                              companyId: row.company_id,
                              companyName: row.company_name,
                              period: row.period,
                            })
                          }
                        >
                          <ClipboardList size={12} /> 本月服务单与资料
                        </button>
                      </>
                    )}

                    {row.service_plan ? (
                      <p className="monthly-service-summary">
                        主办{' '}
                        {row.service_plan.lead_name ??
                          (row.service_plan.lead_user_id ? '已指派' : '尚未指派')}{' '}
                        · 截止 {row.service_plan.due_on}
                      </p>
                    ) : row.permissions.edit || row.permissions.review || row.permissions.manage ? (
                      <p className="monthly-service-summary">本月分工待建立</p>
                    ) : null}
                    {row.material_requests && (
                      <p className="monthly-service-summary">
                        必需资料待完成 {monthlyCount(row.material_requests.required_pending)} ·
                        待验收 {monthlyCount(row.material_requests.submitted)}
                      </p>
                    )}
                  </div>
                  <div className="monthly-detail-grid">
                    <ProgressCell
                      label="资料与草稿"
                      primary={`${monthlyCount(row.materials.sources)} 份来源`}
                      secondary={`待移交 ${monthlyCount(row.materials.business_pending)} · 待补资料 ${monthlyCount(row.materials.missing)}`}
                      attention={(row.materials.missing ?? 0) > 0}
                      onOpen={open(row, 'business')}
                    />
                    <ProgressCell
                      label="会计复核"
                      primary={`${monthlyCount(row.drafts.pending)} 待审`}
                      secondary={`已审待导出 ${monthlyCount(row.drafts.approved)} · 已导出 ${monthlyCount(row.drafts.exported)}`}
                      attention={(row.drafts.pending ?? 0) > 0}
                      onOpen={open(row, 'review', 'pending')}
                    />
                    <ProgressCell
                      label="异常与补件"
                      primary={`${monthlyCount(row.exceptions.open)} 未结`}
                      secondary={`待补件 ${monthlyCount(row.exceptions.awaiting_info)} · 逾期 ${monthlyCount(row.exceptions.overdue)}`}
                      attention={(row.exceptions.open ?? 0) > 0}
                      onOpen={open(row, 'cases')}
                    />
                    <ProgressCell
                      label="交付核验"
                      primary={`${monthlyCount(row.delivery.unverified)} 待核验`}
                      secondary={`未知 ${monthlyCount(row.delivery.unknown)} · 差异 ${monthlyCount(row.delivery.differences)}`}
                      attention={
                        (row.delivery.unknown ?? 0) > 0 || (row.delivery.differences ?? 0) > 0
                      }
                      onOpen={open(row, 'delivery')}
                    />
                    <div className="monthly-close-cell">
                      <ProgressCell
                        label="月结"
                        primary={monthlyClosingLabel(row.closing.state)}
                        secondary={
                          row.closing.state === 'closed'
                            ? '依据已保存的锁期回执'
                            : row.closing.last_check
                              ? `历史预检 ${row.closing.last_check.issue_count} 项问题 · 需重新核对`
                              : '尚无当前预检证据'
                        }
                        attention={row.closing.state === 'recheck'}
                        onOpen={open(row, 'closing')}
                      />
                      <button
                        className="text-button"
                        disabled={!open(row, 'balances')}
                        onClick={open(row, 'balances')}
                      >
                        参考余额与差异
                        <ArrowUpRight size={12} />
                      </button>
                      {row.closing.state === 'closed' && row.book_id && (
                        <button
                          className="text-button monthly-briefing-entry"
                          disabled={!open(row, 'briefing')}
                          onClick={open(row, 'briefing')}
                        >
                          <FileText size={12} />
                          查看本月财税简报
                        </button>
                      )}
                    </div>
                    <ProgressCell
                      label="人工投入"
                      primary={
                        row.efficiency.handling_seconds == null
                          ? '未采集 / 未授权'
                          : durationLabel(row.efficiency.handling_seconds)
                      }
                      secondary={`${monthlyCount(row.efficiency.reviews)} 审核 · ${monthlyCount(row.efficiency.manual_actions)} 次记录操作`}
                      onOpen={open(row, 'efficiency')}
                    />
                  </div>
                </details>
              </article>
            ))}
            {!data.rows.length && (
              <div className="monthly-empty">
                本月在当前授权范围内没有匹配客户。请调整月份或搜索条件。
              </div>
            )}
          </section>
          <div className="pagination-controls">
            <span>
              第 {data.rows.length ? data.pagination.offset + 1 : 0}–
              {data.pagination.offset + data.rows.length} 项 · 每页 {data.pagination.limit} 项
            </span>
            <button
              className="button secondary small"
              disabled={loading || offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - 20));
                setExpandedRows([]);
              }}
            >
              上一页客户
            </button>
            <button
              className="button secondary small"
              disabled={loading || !data.pagination.has_more}
              onClick={() => {
                setOffset(offset + 20);
                setExpandedRows([]);
              }}
            >
              下一页客户
            </button>
          </div>
          <p className="monthly-boundary">
            同企业的多个账套分别展示；无权限的数据保持未知。月结以当前事实的预检与人工锁期记录为准，交付完成须有回执核验。
          </p>
        </>
      ) : null}
      {batchOpen && (
        <MonthlyBatchDialog
          session={session}
          period={selectedPeriod}
          initialSearch={search}
          onClose={() => setBatchOpen(false)}
          onOpened={() => void load()}
          onManageBatch={(id) => {
            setBatchOpen(false);
            setInitialBatchId(id);
            setOperationsOpen(true);
          }}
        />
      )}
      {operationsOpen && canBatchOpen && !revoked && (
        <BatchOperationsDialog
          key={`${session.scope_key}:${selectedPeriod}`}
          session={session}
          period={selectedPeriod}
          rows={data?.rows ?? []}
          initialBatchId={initialBatchId}
          onClose={() => {
            setOperationsOpen(false);
            void load();
          }}
        />
      )}
      {hasHandoffTarget && !revoked && (
        <HandoffSourcePanel
          key={`${session.scope_key}:${location.search}`}
          session={session}
          search={location.search}
          onClose={() => {
            const query = new URLSearchParams(location.search);
            for (const key of ['kind', 'id', 'version', 'fingerprint'])
              query.delete(`handoff_${key}`);
            void navigate({ pathname: location.pathname, search: query.toString() });
            setHandoffOpen(true);
          }}
        />
      )}
      {handoffOpen && !hasHandoffTarget && !revoked && (
        <HandoffDialog
          key={`${session.scope_key}:${selectedPeriod}`}
          session={session}
          period={selectedPeriod}
          canAssign={canBatchOpen}
          onOpenOriginal={(item) => {
            setHandoffOpen(false);
            void navigate(handoffOriginalHref(item));
          }}
          onClose={() => setHandoffOpen(false)}
        />
      )}
    </main>
  );
}
