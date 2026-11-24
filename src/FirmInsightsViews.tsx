import { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import { cny } from './value-baseline';

type BalanceItem = { section: string; code: string; name: string; amount_cents: number };
type ProfitItem = { code: string; name: string; amount_cents: number };
type History = { period: string; known: boolean; revenue_cents: number; profit_cents: number };
type CompanyProfileData = {
  book_id: string;
  company_name: string;
  period: string;
  known: boolean;
  report_status: string;
  report_version: number | null;
  balance: {
    assets_cents: number;
    liabilities_cents: number;
    equity_cents: number;
    unclosed_profit_cents: number;
    items: BalanceItem[];
  };
  profit: {
    revenue_cents: number;
    expenses_cents: number;
    profit_cents: number;
    items: ProfitItem[];
  };
  cash: {
    closing_cash_cents: number;
    opening_cash_cents: number;
    net_change_cents: number;
    unclassified_cents: number;
  };
  working_capital: { receivables_cents: number; payables_cents: number };
  aging: {
    known: boolean;
    buckets: Record<string, number>;
    items: { counterparty: string; amount_cents: number; age_days: number; bucket: string }[];
  };
  history: History[];
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

type TimelineRow = {
  book_id: string;
  company_name: string;
  period: string;
  due_on: string | null;
  lead_user_id: string | null;
};

const unknown = (known: boolean, value: string) => (known ? value : '暂无');

export function CompanyProfile({ session, bookId }: { session: Session; bookId: string }) {
  const [data, setData] = useState<CompanyProfileData | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!bookId) return undefined;
    const controller = new AbortController();
    setData(null);
    setError('');
    void api<CompanyProfileData>(
      `/firm/observability/insights/company?book_id=${encodeURIComponent(bookId)}`,
      session,
      { signal: controller.signal },
    )
      .then(setData)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [bookId, session]);
  if (error)
    return (
      <p className="firm-insights-error" role="alert">
        {error}
      </p>
    );
  if (!data)
    return (
      <div className="firm-insights-state">
        <Spinner />
        <span>正在读取企业画像…</span>
      </div>
    );
  const maxHistory = Math.max(1, ...data.history.map((row) => Math.abs(row.revenue_cents)));
  return (
    <section className="firm-insights-detail" aria-label={`${data.company_name} 财务画像`}>
      <div className="firm-insights-detail-head">
        <div>
          <span className="eyebrow">企业财务画像</span>
          <h2>{data.company_name}</h2>
          <p>
            {data.period} ·{' '}
            {data.known
              ? `报表 V${data.report_version} · ${data.report_status}`
              : '暂无已复核报表，以下财务指标不按 0 元展示'}
          </p>
        </div>
      </div>
      <div className="firm-insights-detail-metrics">
        <div>
          <span>资产</span>
          <strong>{unknown(data.known, cny(data.balance.assets_cents))}</strong>
        </div>
        <div>
          <span>负债</span>
          <strong>{unknown(data.known, cny(data.balance.liabilities_cents))}</strong>
        </div>
        <div>
          <span>权益</span>
          <strong>{unknown(data.known, cny(data.balance.equity_cents))}</strong>
        </div>
        <div>
          <span>收入</span>
          <strong>{unknown(data.known, cny(data.profit.revenue_cents))}</strong>
        </div>
        <div>
          <span>费用</span>
          <strong>{unknown(data.known, cny(data.profit.expenses_cents))}</strong>
        </div>
        <div>
          <span>利润</span>
          <strong>{unknown(data.known, cny(data.profit.profit_cents))}</strong>
        </div>
        <div>
          <span>应收</span>
          <strong>{unknown(data.known, cny(data.working_capital.receivables_cents))}</strong>
        </div>
        <div>
          <span>应付</span>
          <strong>{unknown(data.known, cny(data.working_capital.payables_cents))}</strong>
        </div>
      </div>
      <div className="firm-insights-detail-grid">
        <div className="firm-insights-card">
          <div className="firm-insights-card-head">
            <h3>资产负债结构</h3>
            <span>{data.balance.items.length} 行</span>
          </div>
          {data.balance.items.length ? (
            <div className="firm-insights-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>类别</th>
                    <th>科目</th>
                    <th>金额</th>
                  </tr>
                </thead>
                <tbody>
                  {data.balance.items.map((item) => (
                    <tr key={`${item.section}-${item.code}`}>
                      <td>
                        {item.section === 'asset'
                          ? '资产'
                          : item.section === 'liability'
                            ? '负债'
                            : '权益'}
                      </td>
                      <td>
                        {item.code} {item.name}
                      </td>
                      <td>{cny(item.amount_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="firm-insights-empty">暂无已复核报表结构。</p>
          )}
        </div>
        <div className="firm-insights-card">
          <div className="firm-insights-card-head">
            <h3>账龄</h3>
            <span>{data.aging.known ? '仅未核销应收' : '无账龄数据'}</span>
          </div>
          <div className="firm-insights-aging">
            {['0-30', '31-60', '61-90', '90+'].map((bucket) => (
              <div key={bucket}>
                <span>{bucket} 天</span>
                <strong>{data.aging.known ? cny(data.aging.buckets[bucket] ?? 0) : '—'}</strong>
              </div>
            ))}
          </div>
          {data.aging.items.length ? (
            <ul className="firm-insights-aging-list">
              {data.aging.items.slice(0, 8).map((item) => (
                <li key={`${item.counterparty}-${item.age_days}-${item.amount_cents}`}>
                  <span>{item.counterparty}</span>
                  <small>{item.age_days} 天</small>
                  <strong>{cny(item.amount_cents)}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="firm-insights-empty">没有未核销应收明细；账龄不等同于报表应收。</p>
          )}
        </div>
        <div className="firm-insights-card firm-insights-wide">
          <div className="firm-insights-card-head">
            <h3>近六个月收入与利润</h3>
            <span>无报表月份不补 0</span>
          </div>
          <div className="firm-insights-history">
            {data.history.map((row) => (
              <div key={row.period}>
                <span>{row.period}</span>
                <div>
                  <i
                    style={{
                      height: `${Math.max(2, (Math.abs(row.revenue_cents) / maxHistory) * 72)}px`,
                    }}
                  />
                  <i
                    className={row.profit_cents < 0 ? 'is-loss' : ''}
                    style={{
                      height: `${Math.max(2, (Math.abs(row.profit_cents) / maxHistory) * 72)}px`,
                    }}
                  />
                </div>
                <small>
                  {row.known
                    ? `收 ${cny(row.revenue_cents)} · 利 ${cny(row.profit_cents)}`
                    : '未复核'}
                </small>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export function CompanyComparison({ session, period }: { session: Session; period: string }) {
  const [rows, setRows] = useState<ComparisonRow[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    setError('');
    void api<{ rows: ComparisonRow[] }>(
      `/firm/observability/insights/companies?period=${encodeURIComponent(period)}`,
      session,
      { signal: controller.signal },
    )
      .then((value) => setRows(value.rows))
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [period, session]);
  const csv = useMemo(() => {
    const header = '企业,期间,报表状态,资产,负债,权益,收入,费用,利润,期末现金,应收,应付';
    const lines = (rows ?? []).map((row) =>
      [
        row.company_name,
        period,
        row.report_status,
        row.assets_cents,
        row.liabilities_cents,
        row.equity_cents,
        row.revenue_cents,
        row.expenses_cents,
        row.profit_cents,
        row.revenue_delta_cents ?? '—',
        row.profit_delta_cents ?? '—',
        row.cash_closing_cents,
        row.receivables_cents,
        row.payables_cents,
      ]
        .map((value) => `"${String(value).replace(/"/g, '""')}"`)
        .join(','),
    );
    return [header, ...lines].join('\n');
  }, [period, rows]);
  function download() {
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `firm-comparison-${period}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }
  if (error)
    return (
      <p className="firm-insights-error" role="alert">
        {error}
      </p>
    );
  if (!rows)
    return (
      <div className="firm-insights-state">
        <Spinner />
        <span>正在读取企业对比…</span>
      </div>
    );
  return (
    <section className="firm-insights-detail" aria-label="企业对比">
      <div className="firm-insights-detail-head">
        <div>
          <span className="eyebrow">多企业横向对比</span>
          <h2>企业对比</h2>
          <p>{period} · 只列已复核报表；未复核企业保留在表中但不按 0 参与经营判断。</p>
        </div>
        <button type="button" onClick={download} disabled={!rows.length}>
          导出 CSV
        </button>
      </div>
      <div className="firm-insights-table-scroll">
        <table className="firm-insights-compare">
          <thead>
            <tr>
              <th>企业</th>
              <th>状态</th>
              <th>资产</th>
              <th>负债</th>
              <th>权益</th>
              <th>收入</th>
              <th>费用</th>
              <th>利润</th>
              <th>收入环比</th>
              <th>利润环比</th>
              <th>应收</th>
              <th>应付</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.book_id}>
                <td>{row.company_name}</td>
                <td>{row.known ? '已复核' : '未复核'}</td>
                <td>{row.known ? cny(row.assets_cents) : '—'}</td>
                <td>{row.known ? cny(row.liabilities_cents) : '—'}</td>
                <td>{row.known ? cny(row.equity_cents) : '—'}</td>
                <td>{row.known ? cny(row.revenue_cents) : '—'}</td>
                <td>{row.known ? cny(row.expenses_cents) : '—'}</td>
                <td>{row.known ? cny(row.profit_cents) : '—'}</td>
                <td>
                  {row.known
                    ? row.previous_known && row.revenue_delta_cents !== null
                      ? cny(row.revenue_delta_cents)
                      : '—'
                    : '—'}
                </td>
                <td>
                  {row.known
                    ? row.previous_known && row.profit_delta_cents !== null
                      ? cny(row.profit_delta_cents)
                      : '—'
                    : '—'}
                </td>
                <td>{row.known ? cny(row.receivables_cents) : '—'}</td>
                <td>{row.known ? cny(row.payables_cents) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function DeliveryTimeline({ session, period }: { session: Session; period: string }) {
  const [rows, setRows] = useState<TimelineRow[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    setError('');
    void api<{ rows: TimelineRow[] }>(
      `/firm/observability/insights/timeline?period=${encodeURIComponent(period)}`,
      session,
      { signal: controller.signal },
    )
      .then((value) => setRows(value.rows))
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [period, session]);
  if (error)
    return (
      <p className="firm-insights-error" role="alert">
        {error}
      </p>
    );
  if (!rows)
    return (
      <div className="firm-insights-state">
        <Spinner />
        <span>正在读取交付时间线…</span>
      </div>
    );
  return (
    <section className="firm-insights-detail" aria-label="交付时间线">
      <div className="firm-insights-detail-head">
        <div>
          <span className="eyebrow">月度交付时间线</span>
          <h2>交付时间线</h2>
          <p>{period} · 按账套期间与到期日展示；没有到期日的账套不推断日期。</p>
        </div>
      </div>
      {rows.length ? (
        <div className="firm-insights-gantt">
          {rows.map((row) => {
            const hasDue = Boolean(row.due_on && row.due_on.startsWith(period));
            const dueDay = hasDue ? Number(row.due_on!.slice(8, 10)) : 28;
            const width = hasDue ? Math.min(100, Math.max(8, (dueDay / 28) * 100)) : 8;
            return (
              <div className="firm-insights-gantt-row" key={row.book_id}>
                <span>{row.company_name}</span>
                <div>
                  <i
                    style={{ width: `${width}%` }}
                    className={
                      !hasDue
                        ? 'is-unplanned'
                        : row.due_on && row.due_on < new Date().toISOString().slice(0, 10)
                          ? 'is-overdue'
                          : ''
                    }
                  />
                  <b>到期 {row.due_on ?? '待安排'}</b>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="firm-insights-empty">本期没有账套时间线。</p>
      )}
    </section>
  );
}
