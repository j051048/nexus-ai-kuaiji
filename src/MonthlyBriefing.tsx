import { useScopedQuery } from './useScopedQuery';
import { ArrowLeft, Printer, ShieldCheck, TrendingDown, TrendingUp, Wallet } from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, Spinner } from './AppPrimitives';
import { formatMoney } from './csv';
import type { Session } from './types';
import './monthly.css';
import { MonthlyPackagePanel } from './MonthlyPackagePanel';

type BriefingAxis = { done: boolean; evidence: string };

type BriefingSource = {
  state: string;
  reason: string;
  report?: { version: number; content_hash: string; published_at: string } | null;
  close?: { status: string } | null;
  axes?: Record<string, BriefingAxis>;
  axis_labels?: Record<string, string>;
  axis_bases?: Record<string, string>;
};

type Briefing = {
  book: {
    id: string;
    name: string;
    period: string;
    company_id: string;
    company_name: string;
    period_status: string;
  };
  flows: { income: string; expense: string; net: string; basis: string } | null;
  top_expenses: { account_code: string; name: string; amount: string }[];
  balances: {
    receivable: string | null;
    payable: string | null;
    basis: string;
  };
  tax: { change: string | null; note: string };
  client_visible?: boolean;
  pending_label?: string;
  source?: BriefingSource;
  risks: { kind: string; text: string }[];
  notes: string[];
  generated_at: string;
};

const AXIS_ORDER = [
  'internal_close',
  'external_receipt',
  'tax_filing',
  'notification',
  'client_sign',
];

/** 未发布时只说明状态与五条独立轴线，不展示任何金额。 */
export function BriefingPending({ label, source }: { label: string; source?: BriefingSource }) {
  return (
    <section className="briefing-pending" aria-label="简报待发布">
      <h2>{label || '待会计复核 / 发布'}</h2>
      <p className="field-help">
        客户可见口径只认已发布且与锁期一致的冻结报表；未发布时这里没有金额。
      </p>
      {source?.axes ? (
        <dl className="briefing-axes">
          {AXIS_ORDER.filter((key) => source.axes?.[key]).map((key) => (
            <div key={key} data-done={source.axes?.[key]?.done ? 'true' : 'false'}>
              <dt>{source.axis_labels?.[key] ?? key}</dt>
              <dd>
                {source.axes?.[key]?.done ? '已完成' : '未完成'}
                <small>{source.axis_bases?.[key] ?? ''}</small>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}

/** 锁期后的一页纸财税简报：只读聚合，向企业管理者交付月度经营概览。 */
export function MonthlyBriefing({
  session,
  bookId,
  onBack,
  embedded = false,
  onBusyChange,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  embedded?: boolean;
  onBusyChange?: (value: boolean) => void;
}) {
  const { data, loading, error } = useScopedQuery(
    (signal) =>
      api<Briefing>(`/monthly/books/${encodeURIComponent(bookId)}/briefing`, session, { signal }),
    `${session.scope_key}:${bookId}`,
  );

  const Wrapper = embedded ? 'div' : 'main';
  return (
    <Wrapper className={'briefing' + (embedded ? ' embedded' : '')}>
      <div className={embedded ? 'book-embedded-heading' : 'briefing-actions'}>
        {embedded ? (
          <div>
            <h2>月度财税简报</h2>
            <p>已锁期账套的一页纸经营概览，可打印或导出 PDF。</p>
          </div>
        ) : (
          <button className="text-button" onClick={onBack}>
            <ArrowLeft size={14} />
            返回账套
          </button>
        )}
        <button
          type="button"
          className="button secondary small"
          disabled={!data}
          onClick={() => window.print()}
        >
          <Printer size={14} />
          打印 / 导出 PDF
        </button>
      </div>
      {bookId && (
        <MonthlyPackagePanel
          key={`${session.scope_key}:${bookId}`}
          session={session}
          bookId={bookId}
          clientOnly
          onBusyChange={onBusyChange}
        />
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在生成本月简报…
        </div>
      ) : error ? (
        <EmptyState title="暂不能查看月度财税简报" icon={<ShieldCheck size={26} />}>
          <span>{error}</span>
          <span className="field-help">上方月度交付包独立核验，以会计冻结的版本为准。</span>
        </EmptyState>
      ) : data && data.client_visible === false ? (
        <article className="briefing-card">
          <header className="briefing-heading">
            <div>
              <p className="eyebrow">MONTHLY BRIEFING</p>
              <h1>
                {data.book.company_name || data.book.name} · {data.book.period} 财税简报
              </h1>
              <p className="page-description">
                本月正式成果尚未发布；草稿、旧期间关闭与人工余额都不能替代正式报表金额。
              </p>
            </div>
            <span className="briefing-badge">
              <ShieldCheck size={14} />
              {data.pending_label ?? '待会计复核 / 发布'}
            </span>
          </header>
          <BriefingPending label={data.pending_label ?? '待会计复核 / 发布'} source={data.source} />
          <footer className="briefing-notes">
            {data.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
          </footer>
        </article>
      ) : data && data.flows ? (
        <article className="briefing-card">
          <header className="briefing-heading">
            <div>
              <p className="eyebrow">MONTHLY BRIEFING</p>
              <h1>
                {data.book.company_name || data.book.name} · {data.book.period} 财税简报
              </h1>
              <p className="page-description">
                基于已锁期账套的银行流水、已审核凭证与人工确认余额生成，供管理者快速了解本月经营概况。
              </p>
            </div>
            <span className="briefing-badge">
              <ShieldCheck size={14} />
              本期已锁期
            </span>
          </header>
          <section className="briefing-flows" aria-label="本月资金流入流出">
            <div className="briefing-flow income">
              <TrendingUp size={18} />
              <span>银行流入</span>
              <strong>{formatMoney(data.flows.income)}</strong>
            </div>
            <div className="briefing-flow expense">
              <TrendingDown size={18} />
              <span>银行流出</span>
              <strong>{formatMoney(data.flows.expense)}</strong>
            </div>
            <div className="briefing-flow net">
              <Wallet size={18} />
              <span>净流入</span>
              <strong>{formatMoney(data.flows.net)}</strong>
            </div>
            <p className="briefing-basis">{data.flows.basis}</p>
          </section>
          <section className="briefing-top" aria-label="前五大费用">
            <h2>前五大费用（已审核凭证）</h2>
            {data.top_expenses.length ? (
              <ol>
                {data.top_expenses.map((item) => (
                  <li key={item.account_code}>
                    <span>
                      {item.name}
                      <small>{item.account_code}</small>
                    </span>
                    <strong>{formatMoney(item.amount)}</strong>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="field-help">本期没有已审核的费用凭证。</p>
            )}
          </section>
          <section className="briefing-balances" aria-label="往来与税额提示">
            <div>
              <span>应收账款余额</span>
              <strong>
                {data.balances.receivable === null
                  ? '未提供'
                  : formatMoney(data.balances.receivable)}
              </strong>
            </div>
            <div>
              <span>应付账款余额</span>
              <strong>
                {data.balances.payable === null ? '未提供' : formatMoney(data.balances.payable)}
              </strong>
            </div>
            <div>
              <span>应交税费类净变动</span>
              <strong>{data.tax.change === null ? '待发布' : formatMoney(data.tax.change)}</strong>
              <small>{data.tax.note}</small>
            </div>
            <p className="briefing-basis">{data.balances.basis}</p>
          </section>
          <section className="briefing-risks" aria-label="风险与待办提示">
            <h2>风险与待办提示</h2>
            {data.risks.length ? (
              <ul>
                {data.risks.map((item) => (
                  <li key={item.kind}>{item.text}</li>
                ))}
              </ul>
            ) : (
              <p className="field-help">当前没有需要提示的风险项。</p>
            )}
          </section>
          <footer className="briefing-notes">
            {data.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
            <p className="briefing-basis">生成时间：{data.generated_at}</p>
          </footer>
        </article>
      ) : null}
    </Wrapper>
  );
}
