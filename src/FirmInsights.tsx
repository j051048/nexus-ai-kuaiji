import { useEffect, useState } from 'react';
import { Activity, BarChart3, Building2, RefreshCw, TrendingUp } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import { cny } from './value-baseline';
import { CompanyComparison, CompanyProfile, DeliveryTimeline } from './FirmInsightsViews';
import './firm-insights.css';

type Stage = { code: string; label: string; count: number };
type Exception = { code: string; label: string; count: number };
type Trend = {
  period: string;
  books: number;
  closed: number;
  reviewed_reports: number;
  revenue_cents: number;
  profit_cents: number;
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
type Staff = { user_id: string | null; name: string; books: number; blocked: number; overdue: number };
type Insights = {
  period: string;
  generated_on: string;
  stage_projection: true;
  note: string;
  kpis: {
    companies: number; books: number; blocked: number; overdue: number; closed: number;
    review_pending: number; materials_pending: number; exceptions: number;
    notification_unknown: number; known_reports: number; missing_reports: number;
    ai_cost_micro: number; ai_cost_unknown: number;
  };
  financial: {
    assets_cents: number; liabilities_cents: number; equity_cents: number;
    revenue_cents: number; expenses_cents: number; profit_cents: number;
    cash_closing_cents: number; unclassified_cash_cents: number;
    known_books: number; missing_books: number;
  };
  stages: Stage[];
  exceptions: Exception[];
  trends: Trend[];
  risk: Risk[];
  staff: Staff[];
};

const STAGE_COLORS = ['#2f6f5e', '#4c8b76', '#73a995', '#9fc7b9', '#c7ddd6', '#e5efeb'];

function TrendChart({ rows }: { rows: Trend[] }) {
  const [mode, setMode] = useState<'operations' | 'finance'>('operations');
  if (!rows.length) return <p className="firm-insights-empty">该期间没有趋势数据。</p>;
  const width = 640;
  const height = 190;
  const pad = { left: 42, right: 14, top: 14, bottom: 28 };
  const maxBooks = Math.max(1, ...rows.map((row) => row.books));
  const maxMoney = Math.max(1, ...rows.map((row) => Math.max(row.reviewed_reports ? Math.abs(row.revenue_cents) : 0, row.reviewed_reports ? Math.abs(row.profit_cents) : 0)));
  const xAt = (index: number) => pad.left + (index * (width - pad.left - pad.right)) / Math.max(1, rows.length - 1);
  const yAt = (value: number, max: number) => height - pad.bottom - (value / max) * (height - pad.top - pad.bottom);
  const linePoints = (value: (row: Trend) => number | null, max: number) => {
    const segments: string[] = [];
    let current: string[] = [];
    rows.forEach((row, index) => {
      const v = value(row);
      if (v === null) {
        if (current.length) segments.push(current.join(' '));
        current = [];
        return;
      }
      current.push(`${xAt(index)},${yAt(v, max)}`);
    });
    if (current.length) segments.push(current.join(' '));
    return segments;
  };
  const series = mode === 'operations'
    ? [
        { key: 'books', label: '账套', color: '#2f6f5e', points: linePoints((row) => row.books, maxBooks) },
        { key: 'closed', label: '已关账', color: '#3f7fa8', points: linePoints((row) => row.closed, maxBooks) },
        { key: 'reports', label: '已复核报表', color: '#b1782f', points: linePoints((row) => row.reviewed_reports, maxBooks) },
      ]
    : [
        { key: 'revenue', label: '已复核收入', color: '#2f6f5e', points: linePoints((row) => row.reviewed_reports ? row.revenue_cents : null, maxMoney) },
        { key: 'profit', label: '已复核利润', color: '#b1782f', points: linePoints((row) => row.reviewed_reports ? row.profit_cents : null, maxMoney) },
      ];
  const financeKnown = rows.some((row) => row.reviewed_reports > 0);
  return (
    <div className="firm-insights-chart">
      <div className="firm-insights-chart-toggle" role="group" aria-label="趋势口径">
        <button type="button" className={mode === 'operations' ? 'is-active' : ''} aria-pressed={mode === 'operations'} onClick={() => setMode('operations')}>运营</button>
        <button type="button" className={mode === 'finance' ? 'is-active' : ''} aria-pressed={mode === 'finance'} onClick={() => setMode('finance')}>财务</button>
      </div>
      {mode === 'finance' && !financeKnown ? (
        <p className="firm-insights-empty">近六个月没有已复核报表，财务趋势不补 0。</p>
      ) : (
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={mode === 'operations' ? '近六个月交付趋势' : '近六个月已复核收入与利润趋势'}>
        {[0, 0.5, 1].map((tick) => {
          const y = pad.top + tick * (height - pad.top - pad.bottom);
          return <line key={tick} x1={pad.left} x2={width - pad.right} y1={y} y2={y} className="firm-insights-grid" />;
        })}
        {series.flatMap((item) => item.points.map((points, index) => (
          <polyline key={`${item.key}-${index}`} points={points} fill="none" stroke={item.color} strokeWidth="2.5" />
        )))}
        {rows.map((row, index) => (
          <text key={row.period} x={xAt(index)} y={height - 8} textAnchor="middle" className="firm-insights-axis">
            {row.period.slice(5)}月
          </text>
        ))}
        <text x={pad.left} y={12} className="firm-insights-axis">
          {mode === 'operations' ? `峰值 ${maxBooks} 套` : `峰值 ${cny(maxMoney)}`}
        </text>
      </svg>
      )}
      <div className="firm-insights-legend">
        {series.map((item) => (
          <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>
        ))}
        {mode === 'finance' && <span className="firm-insights-legend-note">无已复核报表的月份不补 0</span>}
      </div>
    </div>
  );
}

function Donut({ rows }: { rows: Exception[] }) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  if (!total) return <p className="firm-insights-empty">本期没有登记中的异常。</p>;
  let cursor = 0;
  const stops = rows.map((row, index) => {
    const start = cursor;
    cursor += (row.count / total) * 100;
    return `${STAGE_COLORS[index % STAGE_COLORS.length]} ${start}% ${cursor}%`;
  });
  return (
    <div className="firm-insights-donut-wrap">
      <div className="firm-insights-donut" style={{ background: `conic-gradient(${stops.join(',')})` }} role="img" aria-label="异常分布">
        <div><strong>{total}</strong><span>异常项</span></div>
      </div>
      <ul className="firm-insights-donut-list">
        {rows.map((row, index) => (
          <li key={row.code}>
            <i style={{ background: STAGE_COLORS[index % STAGE_COLORS.length] }} />
            <span>{row.label}</span><strong>{row.count}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FirmInsights({ session }: { session: Session }) {
  const [period, setPeriod] = useState('');
  const [ready, setReady] = useState(false);
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<'overview' | 'company' | 'compare' | 'timeline'>('overview');
  const [books, setBooks] = useState<{ id: string; period: string; company_name: string }[]>([]);
  const [bookId, setBookId] = useState('');

  useEffect(() => {
    let cancelled = false;
    void api<{ books: { id: string; period: string; company_name?: string }[] }>('/workbench', session)
      .then((workbench) => {
        if (cancelled) return;
        const latest = workbench.books.map((book) => book.period).sort().at(-1);
        const options = workbench.books.map((book) => ({ id: book.id, period: book.period, company_name: book.company_name ?? book.id }));
        setBooks(options);
        setBookId(options.find((book) => book.period === latest)?.id ?? options[0]?.id ?? '');
        setPeriod(latest ?? new Date().toISOString().slice(0, 7));
      })
      .catch(() => {
        if (!cancelled) setPeriod(new Date().toISOString().slice(0, 7));
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => { cancelled = true; };
  }, [session]);

  useEffect(() => {
    if (!period) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<Insights>(`/firm/observability/insights?period=${encodeURIComponent(period)}`, session, {
      signal: controller.signal,
    })
      .then((value) => setData(value))
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [period, revision, session]);

  const stageTotal = data?.stages.reduce((sum, stage) => sum + stage.count, 0) ?? 0;
  const staffMax = Math.max(1, ...(data?.staff.map((row) => row.books) ?? [1]));

  return (
    <main className="firm-insights" aria-label="经营看板">
      <header className="firm-insights-head">
        <div>
          <span className="eyebrow">FIRM INSIGHTS</span>
          <h1>经营看板</h1>
          <p>全所交付、异常与已复核报表的只读汇总。财务数字不带未复核账套，不推断原因。</p>
        </div>
        <div className="firm-insights-controls">
          <label>期间<input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} /></label>
          <button type="button" onClick={() => setRevision((value) => value + 1)}><RefreshCw size={16} />刷新</button>
        </div>
      </header>

      <nav className="firm-insights-tabs" aria-label="经营看板视图">
        {([['overview', '全所总览'], ['company', '企业画像'], ['compare', '企业对比'], ['timeline', '交付时间线']] as const).map(([key, label]) => (
          <button key={key} type="button" className={view === key ? 'is-active' : ''} aria-current={view === key ? 'page' : undefined} onClick={() => setView(key)}>{label}</button>
        ))}
        {view === 'company' ? <label>企业<select value={bookId} onChange={(event) => setBookId(event.target.value)}>{books.map((book) => <option key={book.id} value={book.id}>{book.company_name} · {book.period}</option>)}</select></label> : null}
      </nav>

      {loading || !ready ? <div className="firm-insights-state"><Spinner /><span>正在读取经营数据…</span></div> : null}
      {error ? <div className="firm-insights-error" role="alert">{error}</div> : null}

      {view === 'overview' && data ? (
        <>
          <section className="firm-insights-kpis" aria-label="全所关键指标">
            <article><Building2 size={18} /><span>服务企业</span><strong>{data.kpis.companies}</strong><small>{data.kpis.books} 套在期账套</small></article>
            <article><BarChart3 size={18} /><span>应交付 / 已交付</span><strong>{data.kpis.books} / {data.kpis.closed}</strong><small>{data.kpis.overdue} 套逾期</small></article>
            <article><Activity size={18} /><span>待复核凭证</span><strong>{data.kpis.review_pending}</strong><small>{data.kpis.materials_pending} 项资料未验收</small></article>
            <article><Activity size={18} /><span>异常与补件</span><strong>{data.kpis.exceptions}</strong><small>{data.kpis.blocked} 套账套被阻断</small></article>
            <article><TrendingUp size={18} /><span>财务已覆盖</span><strong>{data.kpis.known_reports} / {data.kpis.books}</strong><small>{data.kpis.missing_reports} 套未复核，未按 0 计</small></article>
            <article><span>已归集收入</span><strong>{data.kpis.known_reports ? cny(data.financial.revenue_cents) : '暂无'}</strong><small>{data.financial.known_books} 套已复核报表</small></article>
            <article><span>已归集利润</span><strong>{data.kpis.known_reports ? cny(data.financial.profit_cents) : '暂无'}</strong><small>不是全所净利润；未复核账套不含</small></article>
            <article><span>AI 已发生成本</span><strong>{data.kpis.ai_cost_micro ? cny(Math.round(data.kpis.ai_cost_micro / 1000)) : '暂无'}</strong><small>{data.kpis.ai_cost_unknown ? `${data.kpis.ai_cost_unknown} 笔结果未知` : '只含模型费用，不含人工'}</small></article>
          </section>

          <section className="firm-insights-finance" aria-label="已复核报表财务汇总">
            <div className="firm-insights-card-head"><h2>已复核报表财务汇总</h2><span>{data.financial.known_books ? `${data.financial.known_books} 套账套已覆盖` : '暂无已复核报表，未按 0 元展示'}</span></div>
            <dl>
              <div><dt>资产</dt><dd>{data.financial.known_books ? cny(data.financial.assets_cents) : '未覆盖'}</dd></div>
              <div><dt>负债</dt><dd>{data.financial.known_books ? cny(data.financial.liabilities_cents) : '未覆盖'}</dd></div>
              <div><dt>权益</dt><dd>{data.financial.known_books ? cny(data.financial.equity_cents) : '未覆盖'}</dd></div>
              <div><dt>收入</dt><dd>{data.financial.known_books ? cny(data.financial.revenue_cents) : '未覆盖'}</dd></div>
              <div><dt>费用</dt><dd>{data.financial.known_books ? cny(data.financial.expenses_cents) : '未覆盖'}</dd></div>
              <div><dt>利润</dt><dd>{data.financial.known_books ? cny(data.financial.profit_cents) : '未覆盖'}</dd></div>
              <div><dt>期末现金</dt><dd>{data.financial.known_books ? cny(data.financial.cash_closing_cents) : '未覆盖'}</dd></div>
              <div><dt>未分类现金</dt><dd>{data.financial.known_books ? cny(data.financial.unclassified_cash_cents) : '未覆盖'}</dd></div>
            </dl>
          </section>

          <section className="firm-insights-grid">
            <div className="firm-insights-card">
              <div className="firm-insights-card-head"><h2>交付阶段</h2><span>按现有资料与报表状态投影</span></div>
              {stageTotal ? (
                <>
                  <div className="firm-insights-stacked" role="img" aria-label="交付阶段分布">
                    {data.stages.map((stage, index) => stage.count ? (
                      <span key={stage.code} style={{ width: `${(stage.count / stageTotal) * 100}%`, background: STAGE_COLORS[index] }} title={`${stage.label} ${stage.count}`} />
                    ) : null)}
                  </div>
                  <div className="firm-insights-stages">
                    {data.stages.map((stage, index) => <span key={stage.code}><i style={{ background: STAGE_COLORS[index] }} />{stage.label}<strong>{stage.count}</strong></span>)}
                  </div>
                </>
              ) : <p className="firm-insights-empty">本期没有在办账套。</p>}
            </div>

            <div className="firm-insights-card">
              <div className="firm-insights-card-head"><h2>异常分布</h2><span>只投影现有阻断</span></div>
              <Donut rows={data.exceptions} />
            </div>

            <div className="firm-insights-card firm-insights-wide">
              <div className="firm-insights-card-head"><h2>近六个月趋势</h2><span>运营口径 / 已复核财务口径，可切换</span></div>
              <TrendChart rows={data.trends} />
              <details className="firm-insights-table-fallback"><summary>查看趋势数据表</summary>
                <table><thead><tr><th>期间</th><th>账套</th><th>已关账</th><th>已复核报表</th><th>已归集收入</th></tr></thead>
                  <tbody>{data.trends.map((row) => <tr key={row.period}><td>{row.period}</td><td>{row.books}</td><td>{row.closed}</td><td>{row.reviewed_reports}</td><td>{row.reviewed_reports ? cny(row.revenue_cents) : "—"}</td><td>{row.reviewed_reports ? cny(row.profit_cents) : "—"}</td></tr>)}</tbody>
                </table>
              </details>
            </div>

            <div className="firm-insights-card firm-insights-wide">
              <div className="firm-insights-card-head"><h2>风险与逾期</h2><span>最多 12 套，按阻断与到期排序</span></div>
              {data.risk.length ? (
                <>
                  <div className="firm-insights-table-scroll firm-insights-risk-table"><table><thead><tr><th>企业</th><th>期间</th><th>阶段</th><th>负责人</th><th>阻断</th><th>到期</th></tr></thead>
                    <tbody>{data.risk.map((row) => <tr key={row.book_id}><td>{row.company_name}</td><td>{row.period}</td><td>{row.stage_label}</td><td>{row.owner_name}</td><td>{row.blockers.length ? row.blockers.map((item) => item.label).join('、') : '—'}</td><td className={row.overdue ? 'is-overdue' : ''}>{row.due_on ?? '待安排'}{row.overdue ? ' · 逾期' : ''}</td></tr>)}</tbody>
                  </table></div>
                  <div className="firm-insights-risk-cards">
                    {data.risk.map((row) => <article key={row.book_id}><header><strong>{row.company_name}</strong><span className={row.overdue ? 'is-overdue' : ''}>{row.overdue ? '已逾期' : '处理中'}</span></header><p>{row.period} · {row.stage_label} · 负责人 {row.owner_name}</p><p>{row.blockers.length ? row.blockers.map((item) => `${item.label} ${item.count}`).join('、') : '暂无阻断'}</p><small>到期 {row.due_on ?? '待安排'}</small></article>)}
                  </div>
                </>
              ) : <p className="firm-insights-empty">本期没有登记中的风险。</p>}
            </div>

            <div className="firm-insights-card">
              <div className="firm-insights-card-head"><h2>人员负载</h2><span>按主办会计</span></div>
              {data.staff.length ? <ul className="firm-insights-staff">{data.staff.map((row) => (
                <li key={row.user_id ?? row.name}><div><span>{row.name}</span><strong>{row.books} 套</strong></div><div className="firm-insights-bar"><i style={{ width: `${(row.books / staffMax) * 100}%` }} /></div><small>{row.blocked} 套阻断 · {row.overdue} 套逾期</small></li>
              ))}</ul> : <p className="firm-insights-empty">本期没有已分配负责人。</p>}
            </div>
          </section>

          <footer className="firm-insights-note">
            <span>{data.note}</span>
            <span>生成于 {data.generated_on} · {data.period}</span>
          </footer>
        </>
      ) : null}
      {view === 'company' ? <div className="firm-insights-view"><CompanyProfile session={session} bookId={bookId} /></div> : null}
      {view === 'compare' ? <div className="firm-insights-view"><CompanyComparison session={session} period={period} /></div> : null}
      {view === 'timeline' ? <div className="firm-insights-view"><DeliveryTimeline session={session} period={period} /></div> : null}
      {!loading && !data && !error ? <p className="firm-insights-empty">没有可显示的看板数据。</p> : null}
    </main>
  );
}
