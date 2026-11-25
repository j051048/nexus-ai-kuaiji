import { useEffect, useState } from 'react';
import { ArrowUpRight, Building2, ShieldAlert, X } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import {
  CHART_PALETTE,
  DonutPieChart,
  TrendAreaChart,
  formatCompactCny,
} from './ExecutiveCharts';
import type { Session } from './types';
import { cny } from './value-baseline';
import './company-drilldown.css';

interface BalanceItem {
  section: string;
  code: string;
  name: string;
  amount_cents: number;
}
interface ProfitItem {
  code: string;
  name: string;
  amount_cents: number;
}
interface HistoryPoint {
  period: string;
  known: boolean;
  revenue_cents: number;
  profit_cents: number;
}
interface CompanyProfileData {
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
  history: HistoryPoint[];
}

export function CompanyDrilldownDrawer({
  session,
  companyName,
  bookId,
  period,
  pendingReason,
  ownerName,
  onClose,
  onOpenFullProfile,
  onOpenLedger,
}: {
  session: Session;
  companyName: string;
  bookId?: string | null;
  period: string;
  pendingReason?: string;
  ownerName?: string;
  onClose: () => void;
  onOpenFullProfile?: (bookId: string) => void;
  onOpenLedger?: (bookId: string) => void;
}) {
  const [data, setData] = useState<CompanyProfileData | null>(null);
  const [loading, setLoading] = useState(Boolean(bookId));
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!bookId) {
      setData(null);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setData(null);
    void api<CompanyProfileData>(
      `/firm/observability/insights/company?book_id=${encodeURIComponent(bookId)}`,
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [bookId, session]);

  const expenseSlices = (data?.profit.items ?? [])
    .filter((item) => Math.abs(item.amount_cents) > 0)
    .sort((a, b) => Math.abs(b.amount_cents) - Math.abs(a.amount_cents))
    .slice(0, 5)
    .map((item, idx) => ({
      id: item.code,
      label: item.name,
      value: Math.abs(item.amount_cents),
      displayValue: formatCompactCny(Math.abs(item.amount_cents)),
      color: CHART_PALETTE[idx % CHART_PALETTE.length],
      clickable: false,
    }));

  const fallbackBalanceSlices = (data?.balance.items ?? [])
    .filter((item) => item.section === 'asset' && Math.abs(item.amount_cents) > 0)
    .sort((a, b) => Math.abs(b.amount_cents) - Math.abs(a.amount_cents))
    .slice(0, 5)
    .map((item, idx) => ({
      id: item.code,
      label: item.name,
      value: Math.abs(item.amount_cents),
      displayValue: formatCompactCny(Math.abs(item.amount_cents)),
      color: CHART_PALETTE[idx % CHART_PALETTE.length],
      clickable: false,
    }));

  const structureSlices = expenseSlices.length ? expenseSlices : fallbackBalanceSlices;

  return (
    <div
      className="company-drilldown-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="company-drilldown-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`${companyName} 经营明细穿透`}
      >
        <header className="company-drilldown-head">
          <div className="company-drilldown-title">
            <span className="company-drilldown-icon">
              <Building2 size={18} aria-hidden="true" />
            </span>
            <div>
              <h2>{companyName}</h2>
              <p>
                {period} · {ownerName ? `分管责任人：${ownerName}` : '子公司穿透式经营明细'}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭明细抽屉"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>

        <div className="company-drilldown-body">
          {loading ? (
            <div className="company-drilldown-state" role="status">
              <Spinner />
              <span>正在读取 {companyName} 的经营明细…</span>
            </div>
          ) : error ? (
            <div className="company-drilldown-alert" role="alert">
              <ShieldAlert size={16} aria-hidden="true" />
              <span>{error}</span>
            </div>
          ) : !bookId || (data && !data.known) ? (
            <div className="company-drilldown-pending">
              <ShieldAlert size={20} aria-hidden="true" />
              <div>
                <strong>该公司本期仍在核算中，尚未生成已复核报表</strong>
                <p>
                  {pendingReason ||
                    '当前期间尚缺已复核财务报表，为保证决策真实可靠，系统不展示估算或假零数据。'}
                </p>
              </div>
            </div>
          ) : data ? (
            <>
              <section className="company-drilldown-kpis" aria-label="本公司四大核心数">
                <article>
                  <span>本月总收入</span>
                  <strong>{cny(data.profit.revenue_cents)}</strong>
                </article>
                <article>
                  <span>本月总支出</span>
                  <strong>{cny(data.profit.expenses_cents)}</strong>
                </article>
                <article className={data.profit.profit_cents < 0 ? 'is-loss' : 'is-profit'}>
                  <span>本月净结余 / 利润</span>
                  <strong>{cny(data.profit.profit_cents)}</strong>
                </article>
                <article>
                  <span>期末资金余额</span>
                  <strong>{cny(data.cash.closing_cash_cents)}</strong>
                  <small>应收 {formatCompactCny(data.working_capital.receivables_cents)}</small>
                </article>
              </section>

              <section className="company-drilldown-card">
                <div className="company-drilldown-card-head">
                  <h3>近 6 个月收入与净结余走势</h3>
                  <span>未出报表月份自动断线，不补 0</span>
                </div>
                <TrendAreaChart
                  labels={data.history.map((item) => `${item.period.slice(5)}月`)}
                  series={[
                    {
                      key: 'revenue',
                      label: '收入',
                      color: CHART_PALETTE[0],
                      area: true,
                      values: data.history.map((item) => (item.known ? item.revenue_cents : null)),
                    },
                    {
                      key: 'profit',
                      label: '净结余',
                      color: CHART_PALETTE[1],
                      area: true,
                      values: data.history.map((item) => (item.known ? item.profit_cents : null)),
                    },
                  ]}
                  ariaLabel={`${companyName} 近六个月收支走势`}
                  emptyText="近六个月暂无已复核历史趋势"
                  height={196}
                />
              </section>

              <section className="company-drilldown-card">
                <div className="company-drilldown-card-head">
                  <h3>{expenseSlices.length ? '损益核心构成前 5 项' : '主要资产分布前 5 项'}</h3>
                  <span>来源：已复核报表 V{data.report_version ?? 1}</span>
                </div>
                <DonutPieChart
                  slices={structureSlices}
                  centerLabel={expenseSlices.length ? '净结余' : '总资产'}
                  centerValue={formatCompactCny(
                    expenseSlices.length ? data.profit.profit_cents : data.balance.assets_cents,
                  )}
                  ariaLabel={`${companyName} 经营构成分布`}
                  emptyText="暂无细项明细数据"
                />
              </section>

              <section className="company-drilldown-card">
                <div className="company-drilldown-card-head">
                  <h3>应收款账龄与欠款提醒</h3>
                  <span>{data.aging.known ? '未核销应收统计' : '暂无未核销应收'}</span>
                </div>
                <div className="company-drilldown-aging">
                  {['0-30', '31-60', '61-90', '90+'].map((bucket) => (
                    <div key={bucket}>
                      <span>{bucket} 天</span>
                      <strong>
                        {data.aging.known ? formatCompactCny(data.aging.buckets[bucket] ?? 0) : '—'}
                      </strong>
                    </div>
                  ))}
                </div>
                {data.aging.items.length > 0 && (
                  <ul className="company-drilldown-aging-list">
                    {data.aging.items.slice(0, 5).map((item) => (
                      <li key={`${item.counterparty}-${item.age_days}-${item.amount_cents}`}>
                        <span>{item.counterparty}</span>
                        <small>{item.age_days} 天</small>
                        <strong>{cny(item.amount_cents)}</strong>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : null}
        </div>

        <footer className="company-drilldown-foot">
          {bookId && onOpenFullProfile && (
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                onOpenFullProfile(bookId);
                onClose();
              }}
            >
              查看完整财务画像
            </button>
          )}
          {bookId && onOpenLedger && (
            <button
              type="button"
              className="button primary"
              onClick={() => {
                onOpenLedger(bookId);
                onClose();
              }}
            >
              穿透查总账与凭证
              <ArrowUpRight size={15} aria-hidden="true" />
            </button>
          )}
        </footer>
      </aside>
    </div>
  );
}
