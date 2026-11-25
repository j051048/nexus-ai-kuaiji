import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import { api, clearApiReadCache, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import { CompanyDrilldownDrawer } from './CompanyDrilldownDrawer';
import {
  CHART_PALETTE,
  DonutPieChart,
  GroupedBarChart,
  ProgressRing,
  TrendAreaChart,
  WALL_CHART_PALETTE,
  formatCompactCny,
} from './ExecutiveCharts';
import type { Session } from './types';
import { cny } from './value-baseline';
import ConsolidationWorksheetPanel, {
  worksheetCompanyIds,
  worksheetCompanyNames,
} from './firm-insights-consolidation';
import { CompanyComparison, CompanyProfile, DeliveryTimeline } from './FirmInsightsViews';
import {
  CoverageCounts,
  CoverageHead,
  MissingCompanies,
  StandardBreakdown,
} from './firm-insights-coverage';
import './firm-insights.css';
import './firm-insights-executive.css';

type Stage = { code: string; label: string; count: number };
type Exception = { code: string; label: string; count: number };
type Trend = {
  period: string;
  books: number;
  closed: number;
  reviewed_reports: number;
  managed_companies?: number;
  opened_companies?: number;
  reported_companies?: number;
  mixed_standards?: boolean;
  comparable?: boolean;
  status?: 'missing' | 'mixed_standards' | 'partial' | 'complete' | string;
  status_label?: string;
  revenue_cents: number | null;
  profit_cents: number | null;
  ai_cost_micro: number;
};
type Risk = {
  book_id: string;
  company_name: string;
  period: string;
  stage_label: string;
  owner_name: string;
  due_on: string | null;
  overdue: boolean;
  report_status: string;
  blockers: { code: string; label: string; count: number }[];
};
type Staff = {
  user_id: string | null;
  name: string;
  books: number;
  blocked: number;
  overdue: number;
};
type ComparisonRow = {
  book_id: string;
  company_name: string;
  known: boolean;
  report_status: string;
  assets_cents: number;
  liabilities_cents: number;
  equity_cents: number;
  revenue_cents: number;
  expenses_cents: number;
  profit_cents: number;
  previous_known: boolean;
  previous_revenue_cents: number | null;
  previous_profit_cents: number | null;
  revenue_delta_cents: number | null;
  profit_delta_cents: number | null;
  cash_closing_cents: number;
  receivables_cents: number;
  payables_cents: number;
};
type Insights = {
  period: string;
  generated_on: string;
  stage_projection: true;
  note: string;
  kpis: {
    companies: number;
    managed_companies?: number;
    opened_companies?: number;
    reported_companies?: number;
    books: number;
    blocked: number;
    overdue: number;
    closed: number;
    review_pending: number;
    materials_pending: number;
    exceptions: number;
    notification_unknown: number;
    known_reports: number;
    missing_reports: number;
    ai_cost_micro: number;
    ai_cost_unknown: number;
  };
  financial: {
    assets_cents: number | null;
    liabilities_cents: number | null;
    equity_cents: number | null;
    revenue_cents: number | null;
    expenses_cents: number | null;
    profit_cents: number | null;
    cash_closing_cents: number | null;
    unclassified_cash_cents: number | null;
    known_books: number;
    missing_books: number;
    total_books?: number;
    managed_companies?: number;
    opened_companies?: number;
    reported_companies?: number;
    companies_without_books?: number;
    complete?: boolean;
    coverage_label?: string;
    mixed_standards?: boolean;
    totals_comparable?: boolean;
    totals_notice?: string;
    legacy_books?: number;
    trial_books?: number;
    management_summary_notice?: string;
    included_companies?: {
      company_id: string;
      company_name: string;
      period: string;
      report_status: string;
      report_mode: string;
      report_mode_label: string;
      standard_kind: string;
      standard_label: string;
      as_of: string | null;
    }[];
    missing_companies?: {
      company_id: string;
      company_name: string;
      book_id?: string | null;
      owner_name: string;
      top_blocker: string;
      has_trial_draft: boolean;
      reason?: string;
    }[];
    pending_book_reports?: {
      company_id: string;
      company_name: string;
      book_id?: string | null;
      top_blocker: string;
      reason?: string;
    }[];
    standards_breakdown?: {
      standard_kind: string;
      standard_label: string;
      books: number;
      assets_cents: number;
      liabilities_cents: number;
      equity_cents: number;
      revenue_cents: number;
      expenses_cents: number;
      profit_cents: number;
      cash_closing_cents: number;
    }[];
  };
  stages: Stage[];
  exceptions: Exception[];
  trends: Trend[];
  risk: Risk[];
  staff: Staff[];
};

type DrilldownTarget = {
  companyName: string;
  bookId?: string | null;
  pendingReason?: string;
  ownerName?: string;
};

type PieDimension = 'revenue' | 'expenses' | 'profit' | 'exceptions';

const STAGE_COLORS = ['#246b4f', '#3c8568', '#68a48c', '#98c3b3', '#c1dad0', '#ddebe5'];

export function FirmInsights({
  session,
  initialPeriod,
  onPeriodChange,
  onOpenBook,
}: {
  session: Session;
  initialPeriod?: string;
  onPeriodChange?: (period: string) => void;
  onOpenBook?: (bookId: string) => void;
}) {
  const [period, setPeriod] = useState(initialPeriod || '');
  const [ready, setReady] = useState(Boolean(initialPeriod));
  const [data, setData] = useState<Insights | null>(null);
  const [companyRows, setCompanyRows] = useState<ComparisonRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<'overview' | 'company' | 'compare' | 'timeline'>('overview');
  const [books, setBooks] = useState<{ id: string; period: string; company_name: string }[]>([]);
  const [bookId, setBookId] = useState('');

  // 图表交互状态
  const [trendMode, setTrendMode] = useState<'finance' | 'operations'>('finance');
  const [pieDimension, setPieDimension] = useState<PieDimension>('revenue');
  const [drilldown, setDrilldown] = useState<DrilldownTarget | null>(null);

  // 一键投大屏与自动轮播状态
  const [wallMode, setWallMode] = useState(false);
  const [autoRotate, setAutoRotate] = useState(true);
  const [wallSlide, setWallSlide] = useState(0);
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (initialPeriod && /^\d{4}-(0[1-9]|1[0-2])$/.test(initialPeriod)) {
      setPeriod(initialPeriod);
      setReady(true);
    }
  }, [initialPeriod]);

  useEffect(() => {
    let cancelled = false;
    void api<{ books: { id: string; period: string; company_name?: string }[] }>(
      '/workbench',
      session,
    )
      .then((workbench) => {
        if (cancelled) return;
        const latest = workbench.books
          .map((book) => book.period)
          .sort()
          .at(-1);
        const options = workbench.books.map((book) => ({
          id: book.id,
          period: book.period,
          company_name: book.company_name ?? book.id,
        }));
        setBooks(options);
        setBookId(options.find((book) => book.period === latest)?.id ?? options[0]?.id ?? '');
        setPeriod((prev) => prev || latest || new Date().toISOString().slice(0, 7));
      })
      .catch(() => {
        if (!cancelled) setPeriod((prev) => prev || new Date().toISOString().slice(0, 7));
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (!period) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void Promise.all([
      api<Insights>(`/firm/observability/insights?period=${encodeURIComponent(period)}`, session, {
        signal: controller.signal,
      }),
      api<{ rows: ComparisonRow[] }>(
        `/firm/observability/insights/companies?period=${encodeURIComponent(period)}`,
        session,
        { signal: controller.signal },
      ).catch(() => ({ rows: [] as ComparisonRow[] })),
    ])
      .then(([overviewData, companiesData]) => {
        if (controller.signal.aborted) return;
        setData(overviewData);
        setCompanyRows(companiesData.rows ?? []);
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setData(null);
        setCompanyRows([]);
        setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [period, revision, session]);

  const enterWallMode = useCallback(() => {
    setView('overview');
    setWallSlide(0);
    setWallMode(true);
    const el = rootRef.current;
    if (el && typeof el.requestFullscreen === 'function') {
      void el.requestFullscreen().catch(() => {
        // 部分浏览器限制原生全屏时，已由固定定位大屏样式无缝接管。
      });
    }
  }, []);

  const exitWallMode = useCallback(() => {
    setWallMode(false);
    if (document.fullscreenElement && typeof document.exitFullscreen === 'function') {
      void document.exitFullscreen().catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    const onFsChange = () => {
      if (!document.fullscreenElement && wallMode) {
        // 仅在用户主动按 Esc 退出原生全屏时同步退出大屏；若本来就是降级固定层则通过 Esc 键处理
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (!wallMode) return;
      if (event.key === 'Escape' && !drilldown) {
        event.preventDefault();
        exitWallMode();
      }
    };
    document.addEventListener('fullscreenchange', onFsChange);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('fullscreenchange', onFsChange);
      window.removeEventListener('keydown', onKey);
    };
  }, [wallMode, drilldown, exitWallMode]);

  // 合并已开账公司与缺报未开账公司，保证集团分母零遗漏
  const subsidiaryCards = useMemo(() => {
    const byName = new Map<
      string,
      {
        id: string;
        bookId?: string | null;
        companyName: string;
        known: boolean;
        revenueCents: number;
        expensesCents: number;
        profitCents: number;
        cashCents: number;
        revenueDeltaCents: number | null;
        profitDeltaCents: number | null;
        pendingReason?: string;
        ownerName?: string;
      }
    >();

    for (const row of companyRows) {
      byName.set(row.company_name, {
        id: row.book_id,
        bookId: row.book_id,
        companyName: row.company_name,
        known: row.known,
        revenueCents: row.revenue_cents,
        expensesCents: row.expenses_cents,
        profitCents: row.profit_cents,
        cashCents: row.cash_closing_cents,
        revenueDeltaCents: row.revenue_delta_cents,
        profitDeltaCents: row.profit_delta_cents,
      });
    }

    for (const miss of data?.financial.missing_companies ?? []) {
      const existing = byName.get(miss.company_name);
      if (existing) {
        existing.pendingReason = existing.pendingReason || miss.top_blocker || miss.reason;
        existing.ownerName = existing.ownerName || miss.owner_name;
      } else {
        byName.set(miss.company_name, {
          id: miss.book_id || miss.company_id,
          bookId: miss.book_id ?? null,
          companyName: miss.company_name,
          known: false,
          revenueCents: 0,
          expensesCents: 0,
          profitCents: 0,
          cashCents: 0,
          revenueDeltaCents: null,
          profitDeltaCents: null,
          pendingReason: miss.top_blocker || miss.reason || '本期尚未开账或未出复核报表',
          ownerName: miss.owner_name,
        });
      }
    }

    for (const risk of data?.risk ?? []) {
      const target = byName.get(risk.company_name);
      if (target) {
        target.ownerName = target.ownerName || risk.owner_name;
        if (!target.known && risk.blockers.length) {
          target.pendingReason = risk.blockers.map((b) => b.label).join('、');
        }
      }
    }

    return Array.from(byName.values());
  }, [companyRows, data]);

  const knownSubsidiaries = useMemo(
    () => subsidiaryCards.filter((item) => item.known),
    [subsidiaryCards],
  );

  // 投屏轮播总屏数：屏0全集团图表总览 + 屏1子公司健康墙与人效 + 每家已复核子公司单屏巡检
  const wallTotalSlides = 2 + knownSubsidiaries.length;

  useEffect(() => {
    if (!wallMode || !autoRotate || wallTotalSlides <= 1) return undefined;
    const timer = window.setInterval(() => {
      setWallSlide((prev) => (prev + 1) % wallTotalSlides);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [wallMode, autoRotate, wallTotalSlides]);

  const palette = wallMode ? WALL_CHART_PALETTE : CHART_PALETTE;
  const stageTotal = data?.stages.reduce((sum, stage) => sum + stage.count, 0) ?? 0;
  const staffMax = Math.max(1, ...(data?.staff.map((row) => row.books) ?? [1]));

  const trendComparable = (row: Trend) =>
    row.comparable !== undefined
      ? row.comparable && row.revenue_cents !== null && row.profit_cents !== null
      : row.reviewed_reports > 0 && row.revenue_cents !== null && row.profit_cents !== null;

  const financeTrendKnown = (data?.trends ?? []).some((row) => trendComparable(row));
  const effectiveTrendMode =
    trendMode === 'finance' && !financeTrendKnown ? 'operations' : trendMode;

  const trendSeries = useMemo(() => {
    const rows = data?.trends ?? [];
    if (effectiveTrendMode === 'finance') {
      return [
        {
          key: 'revenue',
          label: '已复核总收入',
          color: palette[0],
          area: true,
          values: rows.map((row) => (trendComparable(row) ? row.revenue_cents : null)),
        },
        {
          key: 'profit',
          label: '已复核净结余',
          color: palette[1],
          area: true,
          values: rows.map((row) => (trendComparable(row) ? row.profit_cents : null)),
        },
      ];
    }
    return [
      {
        key: 'books',
        label: '在办账套',
        color: palette[0],
        area: true,
        values: rows.map((row) => row.books),
      },
      {
        key: 'closed',
        label: '已结账锁期',
        color: palette[2],
        values: rows.map((row) => row.closed),
      },
      {
        key: 'reviewed',
        label: '已复核报表',
        color: palette[1],
        values: rows.map((row) => row.reviewed_reports),
      },
    ];
  }, [data?.trends, effectiveTrendMode, palette]);

  // 环形占比饼图数据
  const donutConfig = useMemo(() => {
    if (pieDimension === 'exceptions') {
      const items = data?.exceptions ?? [];
      const total = items.reduce((s, r) => s + r.count, 0);
      return {
        centerLabel: '未结异常',
        centerValue: `${total} 项`,
        emptyText: '本期没有登记中的异常阻断',
        slices: items.map((row, idx) => ({
          id: row.code,
          label: row.label,
          value: row.count,
          displayValue: `${row.count} 项`,
          color: palette[idx % palette.length],
          clickable: false,
        })),
      };
    }

    const labelMap = {
      revenue: { title: '收入合计', empty: '暂无已复核子公司的收入占比数据' },
      expenses: { title: '支出合计', empty: '暂无已复核子公司的支出占比数据' },
      profit: { title: '盈利贡献', empty: '暂无实现正盈利的已复核子公司数据' },
    } as const;

    const slices = knownSubsidiaries
      .map((item, idx) => {
        const raw =
          pieDimension === 'revenue'
            ? item.revenueCents
            : pieDimension === 'expenses'
              ? item.expensesCents
              : Math.max(0, item.profitCents);
        return {
          id: item.companyName,
          label: item.companyName,
          value: Math.max(0, raw),
          displayValue: formatCompactCny(raw),
          color: palette[idx % palette.length],
          clickable: true,
        };
      })
      .filter((s) => s.value > 0);

    const totalCents = slices.reduce((sum, s) => sum + s.value, 0);
    return {
      centerLabel: labelMap[pieDimension].title,
      centerValue: formatCompactCny(totalCents),
      emptyText: labelMap[pieDimension].empty,
      slices,
    };
  }, [data?.exceptions, knownSubsidiaries, palette, pieDimension]);

  const openDrilldownByCompanyName = useCallback(
    (name: string) => {
      const target = subsidiaryCards.find((item) => item.companyName === name || item.id === name);
      if (!target) return;
      setDrilldown({
        companyName: target.companyName,
        bookId: target.bookId,
        pendingReason: target.pendingReason,
        ownerName: target.ownerName,
      });
    },
    [subsidiaryCards],
  );

  const handlePeriodChange = (next: string) => {
    setPeriod(next);
    onPeriodChange?.(next);
  };

  const managedTotal = data?.kpis.managed_companies ?? data?.kpis.companies ?? 0;
  const reportedTotal = data?.kpis.reported_companies ?? data?.kpis.known_reports ?? 0;

  const heroKpis = data ? (
    <section className="firm-insights-hero-kpis" aria-label="集团四大核心经营指标">
      <article className="firm-insights-hero-card">
        <div className="firm-insights-hero-top">
          <span>集团本月总收入</span>
          <TrendingUp size={17} aria-hidden="true" />
        </div>
        <strong>
          {!data.kpis.known_reports
            ? '核算中'
            : data.financial.mixed_standards
              ? '见分准则'
              : cny(data.financial.revenue_cents)}
        </strong>
        <small>
          {data.financial.mixed_standards
            ? '旗下存在多套会计准则，分准则独立列示不混加'
            : `已纳入 ${data.financial.known_books} 套已复核报表`}
        </small>
      </article>

      <article className="firm-insights-hero-card">
        <div className="firm-insights-hero-top">
          <span>集团本月总支出</span>
          <Activity size={17} aria-hidden="true" />
        </div>
        <strong>
          {!data.kpis.known_reports
            ? '核算中'
            : data.financial.mixed_standards
              ? '见分准则'
              : cny(data.financial.expenses_cents)}
        </strong>
        <small>
          {data.kpis.exceptions > 0
            ? `另有 ${data.kpis.exceptions} 项异常补件处理中`
            : '当前无高风险阻断异常'}
        </small>
      </article>

      <article
        className={
          'firm-insights-hero-card accent' +
          (!data.financial.mixed_standards && (data.financial.profit_cents ?? 0) < 0 ? ' loss' : '')
        }
      >
        <div className="firm-insights-hero-top">
          <span>集团本月净结余 / 利润</span>
          <TrendingUp size={17} aria-hidden="true" />
        </div>
        <strong>
          {!data.kpis.known_reports
            ? '核算中'
            : data.financial.mixed_standards
              ? '见分准则'
              : cny(data.financial.profit_cents)}
        </strong>
        <small>
          {data.kpis.missing_reports > 0
            ? `另有 ${data.kpis.missing_reports} 套未出正式报表，不按 0 估算`
            : '全量已复核管理汇总（非审计法定合并表）'}
        </small>
      </article>

      <article className="firm-insights-hero-card">
        <div className="firm-insights-hero-ring-row">
          <div>
            <div className="firm-insights-hero-top">
              <span>资金余额与结账进度</span>
              <Wallet size={16} aria-hidden="true" />
            </div>
            <strong>
              {!data.kpis.known_reports
                ? '待复核'
                : data.financial.mixed_standards
                  ? `${reportedTotal} / ${managedTotal} 家`
                  : formatCompactCny(data.financial.cash_closing_cents)}
            </strong>
            <small>
              已复核 {reportedTotal} / 应报 {managedTotal} 家 · 已锁期 {data.kpis.closed} 套
            </small>
          </div>
          <ProgressRing value={reportedTotal} total={managedTotal} label="子公司复核覆盖率" />
        </div>
      </article>
    </section>
  ) : null;

  const chartsBlock = data ? (
    <section className="firm-insights-charts-grid" aria-label="集团经营可视化图表">
      <div className="firm-insights-chart-card wide">
        <div className="firm-insights-chart-head">
          <div>
            <h2>近 6 个月集团经营走势</h2>
            <p>平滑曲线直观反映收支趋势与月结推进节奏；缺报月份诚实断线不补 0</p>
          </div>
          <div className="firm-insights-pill-group" role="group" aria-label="走势图口径切换">
            <button
              type="button"
              className={effectiveTrendMode === 'finance' ? 'is-active' : ''}
              aria-pressed={effectiveTrendMode === 'finance'}
              onClick={() => setTrendMode('finance')}
            >
              财务收支走势
            </button>
            <button
              type="button"
              className={effectiveTrendMode === 'operations' ? 'is-active' : ''}
              aria-pressed={effectiveTrendMode === 'operations'}
              onClick={() => setTrendMode('operations')}
            >
              月结交付进度
            </button>
          </div>
        </div>
        <TrendAreaChart
          labels={data.trends.map((row) => `${row.period.slice(5)}月`)}
          series={trendSeries}
          valueFormatter={(value) =>
            effectiveTrendMode === 'finance' ? cny(value) : `${value} 套`
          }
          axisFormatter={(value) =>
            effectiveTrendMode === 'finance' ? formatCompactCny(value) : `${Math.round(value)}套`
          }
          ariaLabel={
            effectiveTrendMode === 'finance'
              ? '近六个月集团已复核收入与净结余走势图'
              : '近六个月账套关账与复核报表进度走势图'
          }
          emptyText="近六个月尚无已复核报表趋势，可切换查看月结交付进度"
          note={
            effectiveTrendMode === 'finance'
              ? '悬停月份查看精确到分金额'
              : '展示近 6 个月账套关账与复核趋势'
          }
          height={wallMode ? 248 : 224}
        />
        {!wallMode && (
          <details className="firm-insights-table-fallback">
            <summary>展开近 6 个月明细数据表</summary>
            <table>
              <thead>
                <tr>
                  <th>期间</th>
                  <th>账套</th>
                  <th>已关账</th>
                  <th>实报/应报子公司</th>
                  <th>口径状态</th>
                  <th>已归集收入</th>
                  <th>已归集净结余</th>
                </tr>
              </thead>
              <tbody>
                {data.trends.map((row) => {
                  const comparable = trendComparable(row);
                  const statusText =
                    row.status_label ??
                    (row.mixed_standards
                      ? '异准则不可混加'
                      : row.reviewed_reports > 0
                        ? '已复核'
                        : '缺报');
                  const fallbackCell = row.mixed_standards ? '异准则不可混加' : '缺报';
                  return (
                    <tr key={row.period}>
                      <td>{row.period}</td>
                      <td>{row.books}</td>
                      <td>{row.closed}</td>
                      <td>
                        {row.reported_companies !== undefined && row.managed_companies !== undefined
                          ? `${row.reported_companies}/${row.managed_companies} 家`
                          : `${row.reviewed_reports} 套`}
                      </td>
                      <td>{statusText}</td>
                      <td>
                        {comparable && row.revenue_cents !== null
                          ? cny(row.revenue_cents)
                          : fallbackCell}
                      </td>
                      <td>
                        {comparable && row.profit_cents !== null
                          ? cny(row.profit_cents)
                          : fallbackCell}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </details>
        )}
      </div>

      <div className="firm-insights-chart-card">
        <div className="firm-insights-chart-head">
          <div>
            <h2>各子公司结构占比环形图</h2>
            <p>鼠标悬停看占比，点击任意子公司扇区立即下钻经营明细</p>
          </div>
          <div className="firm-insights-pill-group" role="group" aria-label="环形图维度切换">
            {(
              [
                ['revenue', '收入'],
                ['expenses', '支出'],
                ['profit', '盈利'],
                ['exceptions', '异常'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={pieDimension === key ? 'is-active' : ''}
                aria-pressed={pieDimension === key}
                onClick={() => setPieDimension(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <DonutPieChart
          slices={donutConfig.slices}
          centerLabel={donutConfig.centerLabel}
          centerValue={donutConfig.centerValue}
          ariaLabel="各子公司结构占比环形饼图"
          emptyText={donutConfig.emptyText}
          onSelect={pieDimension === 'exceptions' ? undefined : openDrilldownByCompanyName}
        />
      </div>

      <div className="firm-insights-chart-card">
        <div className="firm-insights-chart-head">
          <div>
            <h2>各子公司收支盈亏对比柱状图</h2>
            <p>横向对比收入、支出与净结余；未出报表公司标“核算中”</p>
          </div>
        </div>
        <GroupedBarChart
          items={subsidiaryCards.map((item) => ({
            id: item.companyName,
            label: item.companyName,
            known: item.known,
            revenueCents: item.revenueCents,
            expensesCents: item.expensesCents,
            profitCents: item.profitCents,
          }))}
          ariaLabel="各子公司本月收入支出与净结余对比柱状图"
          onSelect={openDrilldownByCompanyName}
          presentation={wallMode}
        />
      </div>
    </section>
  ) : null;

  const subsidiariesWall = (
    <section className="firm-insights-subsidiaries" aria-label="各子公司经营健康卡片墙">
      <div className="firm-insights-section-bar">
        <h2>各子公司经营健康卡片（{subsidiaryCards.length} 家）</h2>
        <span>绿灯稳健盈利 · 红灯亏损预警 · 琥珀灯核算中；点击任意卡片下钻明细</span>
      </div>
      {subsidiaryCards.length ? (
        <div className="firm-insights-subsidiary-grid">
          {subsidiaryCards.map((item) => {
            const status = !item.known
              ? { cls: 'pending', text: '核算中' }
              : item.profitCents < 0
                ? { cls: 'loss', text: '亏损预警' }
                : { cls: 'healthy', text: '稳健盈利' };
            return (
              <button
                key={item.id}
                type="button"
                className="firm-insights-sub-card"
                onClick={() => openDrilldownByCompanyName(item.companyName)}
              >
                <div className="firm-insights-sub-head">
                  <strong title={item.companyName}>{item.companyName}</strong>
                  <span className={`firm-insights-lamp ${status.cls}`}>{status.text}</span>
                </div>
                {item.known ? (
                  <div className="firm-insights-sub-metrics">
                    <div>
                      <span>本月收入</span>
                      <b>{formatCompactCny(item.revenueCents)}</b>
                    </div>
                    <div>
                      <span>本月支出</span>
                      <b>{formatCompactCny(item.expensesCents)}</b>
                    </div>
                    <div>
                      <span>净结余</span>
                      <b className={item.profitCents < 0 ? 'is-loss' : ''}>
                        {formatCompactCny(item.profitCents)}
                      </b>
                    </div>
                    <div>
                      <span>资金余额</span>
                      <b>{formatCompactCny(item.cashCents)}</b>
                    </div>
                  </div>
                ) : (
                  <p className="firm-insights-sub-pending-text">
                    当前卡点：{item.pendingReason || '等待完成票据核对与凭证复核'}
                  </p>
                )}
                <div className="firm-insights-sub-foot">
                  <span>{item.ownerName ? `分管：${item.ownerName}` : `${period} 账期`}</span>
                  <span className="drill-cta">点击下钻明细 →</span>
                </div>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="firm-insights-empty">当前会计期间暂无已建档子公司记录。</p>
      )}
    </section>
  );

  const wallFocusedSubsidiary =
    wallMode && wallSlide >= 2 ? knownSubsidiaries[wallSlide - 2] ?? null : null;

  const wallSlideTitle =
    wallSlide === 0
      ? '第 1 屏 · 全集团经营总览与三大核心图表'
      : wallSlide === 1
        ? '第 2 屏 · 各子公司红绿灯健康墙与共享中心人效'
        : `第 ${wallSlide + 1} 屏 · ${wallFocusedSubsidiary?.companyName ?? '子公司'} 经营专屏`;

  return (
    <main
      ref={rootRef}
      className={'firm-insights' + (wallMode ? ' is-presentation-wall' : '')}
      aria-label="集团经营驾驶舱"
    >
      {wallMode ? (
        <header className="wall-command-bar" aria-label="投屏大屏指挥栏">
          <div className="wall-brand-block">
            <span className="wall-live-dot" aria-hidden="true" />
            <div>
              <h1>{session.firm_name || '集团共享财务中心'} · 经营数据大屏</h1>
              <span>
                {period} 账期 · {wallSlideTitle} · 刷新于 {data?.generated_on || '实时'}
              </span>
            </div>
          </div>
          <div className="wall-controls">
            <button
              type="button"
              className="wall-ctrl-btn"
              aria-label="上一屏"
              onClick={() =>
                setWallSlide((prev) => (prev - 1 + wallTotalSlides) % wallTotalSlides)
              }
            >
              <ChevronLeft size={16} />
              上一屏
            </button>
            <div className="wall-slide-dots" role="tablist" aria-label="大屏轮播屏序">
              {Array.from({ length: wallTotalSlides }, (_, idx) => (
                <button
                  key={idx}
                  type="button"
                  role="tab"
                  aria-selected={wallSlide === idx}
                  aria-label={`切换到第 ${idx + 1} 屏`}
                  className={wallSlide === idx ? 'is-active' : ''}
                  onClick={() => setWallSlide(idx)}
                />
              ))}
            </div>
            <button
              type="button"
              className="wall-ctrl-btn"
              aria-label="下一屏"
              onClick={() => setWallSlide((prev) => (prev + 1) % wallTotalSlides)}
            >
              下一屏
              <ChevronRight size={16} />
            </button>
            <button
              type="button"
              className={'wall-ctrl-btn' + (autoRotate ? ' active' : '')}
              aria-pressed={autoRotate}
              onClick={() => setAutoRotate((prev) => !prev)}
            >
              {autoRotate ? <Pause size={15} /> : <Play size={15} />}
              {autoRotate ? '自动轮播中 (15秒)' : '已暂停轮播'}
            </button>
            <button type="button" className="wall-ctrl-btn" onClick={exitWallMode}>
              <Minimize2 size={15} />
              退出大屏
            </button>
          </div>
        </header>
      ) : (
        <>
          <header className="firm-insights-head">
            <div>
              <span className="eyebrow">集团共享财务中心 · 经营决策总控</span>
              <h1>集团经营驾驶舱</h1>
              <p>
                一屏看清集团旗下各子公司收支盈亏、资金余额、结账进度与内部往来；未复核公司诚实标“核算中”，绝不按
                0 估算。
              </p>
            </div>
            <div className="firm-insights-controls">
              <label>
                会计期间
                <input
                  type="month"
                  value={period}
                  onChange={(event) => handlePeriodChange(event.target.value)}
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  clearApiReadCache();
                  setRevision((value) => value + 1);
                }}
              >
                <RefreshCw size={16} />
                刷新
              </button>
              <button type="button" className="firm-insights-wall-btn" onClick={enterWallMode}>
                <Maximize2 size={16} />
                一键投大屏
              </button>
            </div>
          </header>

          <nav className="firm-insights-tabs" aria-label="经营驾驶舱视图">
            {(
              [
                ['overview', '集团总览'],
                ['company', '子公司画像'],
                ['compare', '横向对比表'],
                ['timeline', '月结时间线'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={view === key ? 'is-active' : ''}
                aria-current={view === key ? 'page' : undefined}
                onClick={() => setView(key)}
              >
                {label}
              </button>
            ))}
            {view === 'company' ? (
              <label>
                选择子公司
                <select value={bookId} onChange={(event) => setBookId(event.target.value)}>
                  {books.map((book) => (
                    <option key={book.id} value={book.id}>
                      {book.company_name} · {book.period}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </nav>
        </>
      )}

      {loading || !ready ? (
        <div className="firm-insights-state">
          <Spinner />
          <span>正在读取集团经营数据…</span>
        </div>
      ) : null}
      {error ? (
        <div className="firm-insights-error" role="alert">
          {error}
        </div>
      ) : null}

      {wallMode && data ? (
        <>
          {wallSlide === 0 && (
            <>
              {heroKpis}
              {chartsBlock}
            </>
          )}
          {wallSlide === 1 && (
            <>
              {subsidiariesWall}
              <section className="firm-insights-grid">
                <div className="firm-insights-card">
                  <div className="firm-insights-card-head">
                    <h2>共享中心人员负荷</h2>
                    <span>当前分管账套与阻塞数</span>
                  </div>
                  {data.staff.length ? (
                    <ul className="firm-insights-staff">
                      {data.staff.map((row) => (
                        <li key={row.user_id ?? row.name}>
                          <div>
                            <span>{row.name}</span>
                            <strong>{row.books} 套</strong>
                          </div>
                          <div className="firm-insights-bar">
                            <i style={{ width: `${(row.books / staffMax) * 100}%` }} />
                          </div>
                          <small>
                            {row.blocked} 套阻断 · {row.overdue} 套逾期
                          </small>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="firm-insights-empty">本期暂未分配主办会计负载。</p>
                  )}
                </div>
                <div className="firm-insights-card">
                  <div className="firm-insights-card-head">
                    <h2>月结阶段分布</h2>
                    <span>按资料与复核进度实时统计</span>
                  </div>
                  {stageTotal ? (
                    <div className="firm-insights-stages">
                      {data.stages.map((stage, index) => (
                        <span key={stage.code}>
                          <i style={{ background: palette[index % palette.length] }} />
                          {stage.label}
                          <strong>{stage.count}</strong>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="firm-insights-empty">本期没有在办账套。</p>
                  )}
                </div>
              </section>
            </>
          )}
          {wallFocusedSubsidiary && (
            <section className="wall-focus-company" aria-label="大屏子公司巡检专屏">
              <div className="wall-focus-header">
                <div>
                  <h2>{wallFocusedSubsidiary.companyName}</h2>
                  <p>
                    {period} 已复核经营专屏 ·{' '}
                    {wallFocusedSubsidiary.profitCents < 0 ? '本期亏损预警' : '本期经营稳健盈利'}
                  </p>
                </div>
                <button
                  type="button"
                  className="wall-ctrl-btn active"
                  onClick={() => openDrilldownByCompanyName(wallFocusedSubsidiary.companyName)}
                >
                  展开深度明细抽屉
                  <ArrowUpRight size={15} />
                </button>
              </div>
              <section className="firm-insights-hero-kpis">
                <article className="firm-insights-hero-card">
                  <div className="firm-insights-hero-top">
                    <span>本月总收入</span>
                  </div>
                  <strong>{cny(wallFocusedSubsidiary.revenueCents)}</strong>
                  <small>
                    {wallFocusedSubsidiary.revenueDeltaCents !== null
                      ? `较上期变动 ${cny(wallFocusedSubsidiary.revenueDeltaCents)}`
                      : '已复核口径'}
                  </small>
                </article>
                <article className="firm-insights-hero-card">
                  <div className="firm-insights-hero-top">
                    <span>本月总支出</span>
                  </div>
                  <strong>{cny(wallFocusedSubsidiary.expensesCents)}</strong>
                  <small>全部费用与成本汇总</small>
                </article>
                <article
                  className={
                    'firm-insights-hero-card accent' +
                    (wallFocusedSubsidiary.profitCents < 0 ? ' loss' : '')
                  }
                >
                  <div className="firm-insights-hero-top">
                    <span>本月净结余 / 利润</span>
                  </div>
                  <strong>{cny(wallFocusedSubsidiary.profitCents)}</strong>
                  <small>
                    {wallFocusedSubsidiary.profitDeltaCents !== null
                      ? `较上期变动 ${cny(wallFocusedSubsidiary.profitDeltaCents)}`
                      : '已复核净利润'}
                  </small>
                </article>
                <article className="firm-insights-hero-card">
                  <div className="firm-insights-hero-top">
                    <span>期末资金余额</span>
                  </div>
                  <strong>{cny(wallFocusedSubsidiary.cashCents)}</strong>
                  <small>货币资金期末余额</small>
                </article>
              </section>
              {wallFocusedSubsidiary.bookId && (
                <CompanyProfile session={session} bookId={wallFocusedSubsidiary.bookId} />
              )}
            </section>
          )}
        </>
      ) : view === 'overview' && data ? (
        <>
          {heroKpis}
          {chartsBlock}
          {subsidiariesWall}

          <section className="firm-insights-finance" aria-label="集团已复核财务汇总与内部往来抵销">
            <div className="firm-insights-card-head">
              <h2>已复核报表财务汇总与内部往来抵销（管理汇总，非审计合并报表）</h2>
              <CoverageHead financial={data.financial} />
            </div>
            <CoverageCounts kpis={data.kpis} />
            {data.financial.totals_notice && (
              <p className="field-help">{data.financial.totals_notice}</p>
            )}
            {data.financial.mixed_standards && data.financial.standards_breakdown?.length ? (
              <StandardBreakdown financial={data.financial} />
            ) : (
              <dl>
                <div>
                  <dt>总资产</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.assets_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>总负债</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.liabilities_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>净资产 / 权益</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.equity_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>本月收入</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.revenue_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>本月支出</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.expenses_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>本月净结余</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.profit_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>期末资金</dt>
                  <dd>
                    {data.financial.known_books ? cny(data.financial.cash_closing_cents) : '未覆盖'}
                  </dd>
                </div>
                <div>
                  <dt>待分类资金</dt>
                  <dd>
                    {data.financial.known_books
                      ? cny(data.financial.unclassified_cash_cents)
                      : '未覆盖'}
                  </dd>
                </div>
              </dl>
            )}
            <MissingCompanies financial={data.financial} />
            <ConsolidationWorksheetPanel
              session={session}
              period={period}
              companyIds={worksheetCompanyIds(data.financial)}
              companyNames={worksheetCompanyNames(data.financial)}
            />
          </section>

          <section className="firm-insights-grid">
            <div className="firm-insights-card">
              <div className="firm-insights-card-head">
                <h2>月结阶段分布</h2>
                <span>按现有资料与复核进度实时投影</span>
              </div>
              {stageTotal ? (
                <>
                  <div className="firm-insights-stacked" role="img" aria-label="月结阶段分布">
                    {data.stages.map((stage, index) =>
                      stage.count ? (
                        <span
                          key={stage.code}
                          style={{
                            width: `${(stage.count / stageTotal) * 100}%`,
                            background: STAGE_COLORS[index % STAGE_COLORS.length],
                          }}
                          title={`${stage.label} ${stage.count}`}
                        />
                      ) : null,
                    )}
                  </div>
                  <div className="firm-insights-stages">
                    {data.stages.map((stage, index) => (
                      <span key={stage.code}>
                        <i style={{ background: STAGE_COLORS[index % STAGE_COLORS.length] }} />
                        {stage.label}
                        <strong>{stage.count}</strong>
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <p className="firm-insights-empty">本期没有在办账套。</p>
              )}
            </div>

            <div className="firm-insights-card">
              <div className="firm-insights-card-head">
                <h2>共享中心人员负荷</h2>
                <span>按分管会计账套数与阻塞数</span>
              </div>
              {data.staff.length ? (
                <ul className="firm-insights-staff">
                  {data.staff.map((row) => (
                    <li key={row.user_id ?? row.name}>
                      <div>
                        <span>{row.name}</span>
                        <strong>{row.books} 套</strong>
                      </div>
                      <div className="firm-insights-bar">
                        <i style={{ width: `${(row.books / staffMax) * 100}%` }} />
                      </div>
                      <small>
                        {row.blocked} 套阻断 · {row.overdue} 套逾期
                      </small>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="firm-insights-empty">本期没有已分配分管会计。</p>
              )}
            </div>

            <div className="firm-insights-card firm-insights-wide">
              <div className="firm-insights-card-head">
                <h2>重点风险与结账卡点</h2>
                <span>按阻断项与到期时间排序，点击公司名可穿透看明细</span>
              </div>
              {data.risk.length ? (
                <>
                  <div className="firm-insights-table-scroll firm-insights-risk-table">
                    <table>
                      <thead>
                        <tr>
                          <th>子公司</th>
                          <th>账期</th>
                          <th>当前阶段</th>
                          <th>分管会计</th>
                          <th>主要卡点</th>
                          <th>目标到期</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.risk.map((row) => (
                          <tr key={row.book_id}>
                            <td>
                              <button
                                type="button"
                                className="firm-insights-risk-btn"
                                onClick={() =>
                                  setDrilldown({
                                    companyName: row.company_name,
                                    bookId: row.book_id,
                                    ownerName: row.owner_name,
                                    pendingReason: row.blockers.map((b) => b.label).join('、'),
                                  })
                                }
                              >
                                {row.company_name}
                              </button>
                            </td>
                            <td>{row.period}</td>
                            <td>{row.stage_label}</td>
                            <td>{row.owner_name}</td>
                            <td>
                              {row.blockers.length
                                ? row.blockers.map((item) => item.label).join('、')
                                : '—'}
                            </td>
                            <td className={row.overdue ? 'is-overdue' : ''}>
                              {row.due_on ?? '待安排'}
                              {row.overdue ? ' · 逾期' : ''}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="firm-insights-risk-cards">
                    {data.risk.map((row) => (
                      <article key={row.book_id}>
                        <header>
                          <strong>{row.company_name}</strong>
                          <span className={row.overdue ? 'is-overdue' : ''}>
                            {row.overdue ? '已逾期' : '处理中'}
                          </span>
                        </header>
                        <p>
                          {row.period} · {row.stage_label} · 分管 {row.owner_name}
                        </p>
                        <p>
                          {row.blockers.length
                            ? row.blockers.map((item) => `${item.label} ${item.count}`).join('、')
                            : '暂无阻断'}
                        </p>
                        <small>到期 {row.due_on ?? '待安排'}</small>
                      </article>
                    ))}
                  </div>
                </>
              ) : (
                <p className="firm-insights-empty">本期没有登记中的风险卡点。</p>
              )}
            </div>
          </section>

          <footer className="firm-insights-note">
            <span>{data.note}</span>
            <span>
              生成于 {data.generated_on} · {data.period}
            </span>
          </footer>
        </>
      ) : null}

      {!wallMode && view === 'company' ? (
        <div className="firm-insights-view">
          <CompanyProfile session={session} bookId={bookId} />
        </div>
      ) : null}
      {!wallMode && view === 'compare' ? (
        <div className="firm-insights-view">
          <CompanyComparison session={session} period={period} />
        </div>
      ) : null}
      {!wallMode && view === 'timeline' ? (
        <div className="firm-insights-view">
          <DeliveryTimeline session={session} period={period} />
        </div>
      ) : null}
      {!loading && !data && !error ? (
        <p className="firm-insights-empty">当前会计期间没有可显示的经营数据。</p>
      ) : null}

      {drilldown && (
        <CompanyDrilldownDrawer
          session={session}
          companyName={drilldown.companyName}
          bookId={drilldown.bookId}
          period={period}
          pendingReason={drilldown.pendingReason}
          ownerName={drilldown.ownerName}
          onClose={() => setDrilldown(null)}
          onOpenFullProfile={(targetBookId) => {
            setWallMode(false);
            setBookId(targetBookId);
            setView('company');
          }}
          onOpenLedger={
            onOpenBook
              ? (targetBookId) => {
                  setWallMode(false);
                  onOpenBook(targetBookId);
                }
              : undefined
          }
        />
      )}
    </main>
  );
}
