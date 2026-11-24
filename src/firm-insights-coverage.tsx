import { cny } from './value-baseline';

/** 法人口径与准则口径的展示片段：抽成纯组件，便于单测直接渲染核对文案。 */
export type CoverageFinancial = {
  coverage_label?: string;
  known_books: number;
  total_books?: number;
  managed_companies?: number;
  opened_companies?: number;
  reported_companies?: number;
  companies_without_books?: number;
  mixed_standards?: boolean;
  totals_comparable?: boolean;
  standards_breakdown?: {
    standard_kind: string;
    standard_label: string;
    books: number;
    assets_cents: number;
    revenue_cents: number;
    profit_cents: number;
    cash_closing_cents: number;
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
};

export type CoverageKpis = {
  companies: number;
  managed_companies?: number;
  opened_companies?: number;
  reported_companies?: number;
  known_reports: number;
};

export function CoverageCounts({ kpis }: { kpis: CoverageKpis }) {
  const managed = kpis.managed_companies ?? kpis.companies;
  const opened = kpis.opened_companies ?? kpis.companies;
  const reported = kpis.reported_companies ?? kpis.known_reports;
  return (
    <p className="firm-insights-coverage-counts">
      已纳管 <strong>{managed}</strong> 家 · 当期已开账 <strong>{opened}</strong> 家 · 已报送{' '}
      <strong>{reported}</strong> 家
    </p>
  );
}

export function CoverageHead({ financial }: { financial: CoverageFinancial }) {
  const label =
    financial.coverage_label ??
    (financial.known_books
      ? `${financial.known_books} 套账套已覆盖`
      : '暂无已复核报表，未按 0 元展示');
  return <span>{label}</span>;
}

export function StandardBreakdown({ financial }: { financial: CoverageFinancial }) {
  if (!financial.mixed_standards || !financial.standards_breakdown?.length) return null;
  return (
    <div className="firm-insights-table-scroll">
      <p className="field-help">
        下表按会计准则口径分别小计；不同准则口径的金额不得相加，页面不提供混算总计。
      </p>
      <table>
        <thead>
          <tr>
            <th>会计准则口径</th>
            <th>纳入套数</th>
            <th>资产</th>
            <th>收入</th>
            <th>利润</th>
            <th>期末现金</th>
          </tr>
        </thead>
        <tbody>
          {financial.standards_breakdown.map((row) => (
            <tr key={row.standard_kind}>
              <td>{row.standard_label}</td>
              <td>{row.books} 套</td>
              <td>{cny(row.assets_cents)}</td>
              <td>{cny(row.revenue_cents)}</td>
              <td>{cny(row.profit_cents)}</td>
              <td>{cny(row.cash_closing_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MissingCompanies({ financial }: { financial: CoverageFinancial }) {
  const missing = financial.missing_companies ?? [];
  const pending = financial.pending_book_reports ?? [];
  return (
    <>
      {missing.length > 0 && (
        <details className="firm-insights-table-fallback" open>
          <summary>未纳入合计法人（{missing.length} 家，未按 0 补齐，仍留在分母里）</summary>
          <ul>
            {missing.map((row) => (
              <li key={row.company_id} data-reason={row.reason ?? 'report_missing'}>
                {row.company_name} ·{' '}
                {row.reason === 'not_opened'
                  ? '本期未开账（没有账套，不是零申报）'
                  : `负责人：${row.owner_name} · 当前阻断：${row.top_blocker}`}
                {row.has_trial_draft ? '（仅有试算/草稿，未入正式汇总）' : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      {pending.length > 0 && (
        <details className="firm-insights-table-fallback">
          <summary>已有法人报送、但仍有账套未复核（{pending.length} 套，未计入合计）</summary>
          <ul>
            {pending.map((row) => (
              <li key={`${row.company_id}:${row.book_id ?? 'none'}`}>
                {row.company_name} · 当前阻断：{row.top_blocker}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
