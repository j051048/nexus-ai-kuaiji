import { useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

/**
 * 集团管理合并试算底稿（管理口径）。
 *
 * 这个视图只读：接口本身只开只读事务，不会改动任何单体账簿或凭证。
 * 页面固定标注「非经审计法定合并报表」，并且区分「同口径可比合计」与
 * 「异准则未合并往来」——界面上从不把两套准则的金额加成一个数。
 */

export interface WorksheetPair {
  kind: string;
  counterpart_kind: string;
  company_id?: string;
  counterparty_company_id?: string;
  left_company_name?: string;
  right_company_name?: string;
  matched_cents: number;
  matched: string;
  difference_cents: number;
  difference: string;
  status: string;
}

export interface WorksheetStandardTotal {
  standard: string;
  standard_label: string;
  pairs: number;
  matched_cents: number;
  matched: string;
  difference_cents: number;
  difference: string;
  open_pairs: number;
}

export interface WorksheetTotals {
  pairs: number;
  matched_cents: number | null;
  matched: string | null;
  difference_cents: number | null;
  difference: string | null;
  open_pairs: number;
  comparable: boolean;
  scope: string;
}

export type WorksheetCompanyRef =
  string | { company_id: string; company_name?: string; name?: string; reason?: string };

export interface WorksheetSheet {
  period: string;
  statement: string;
  statement_note?: string;
  included_companies: {
    company_id: string;
    name?: string;
    company_name?: string;
    standard_kind?: string;
  }[];
  missing_companies: WorksheetCompanyRef[];
  denied_companies?: WorksheetCompanyRef[];
  totals: WorksheetTotals;
  totals_by_standard: WorksheetStandardTotal[];
  not_merged_totals?: {
    pairs: number;
    matched_cents: number;
    matched: string;
    difference_cents: number;
    difference: string;
    label: string;
  };
  not_merged_pairs?: WorksheetPair[];
  open_differences?: WorksheetPair[];
  pending_eliminations?: { label?: string; count?: number } | unknown[];
  elimination_totals?: { approved: string; approved_count: number; pending_count: number };
}

function companyDisplayName(entry: WorksheetCompanyRef, names?: Record<string, string>): string {
  if (typeof entry === 'string') return names?.[entry] || entry;
  return entry.company_name || entry.name || names?.[entry.company_id] || entry.company_id;
}

/** 试算口径需要显式列出法人：已报与缺报的法人都在分母里。 */
export function worksheetCompanyIds(financial: {
  included_companies?: { company_id: string }[];
  missing_companies?: { company_id: string }[];
}): string[] {
  const ids = new Set<string>();
  for (const company of financial.included_companies ?? []) {
    if (company.company_id) ids.add(company.company_id);
  }
  for (const company of financial.missing_companies ?? []) {
    if (company.company_id) ids.add(company.company_id);
  }
  return [...ids];
}

export function worksheetCompanyNames(financial: {
  included_companies?: { company_id: string; company_name?: string }[];
  missing_companies?: { company_id: string; company_name?: string }[];
}): Record<string, string> {
  const map: Record<string, string> = {};
  for (const company of financial.included_companies ?? []) {
    if (company.company_id && company.company_name) map[company.company_id] = company.company_name;
  }
  for (const company of financial.missing_companies ?? []) {
    if (company.company_id && company.company_name) map[company.company_id] = company.company_name;
  }
  return map;
}

/** 用纯文本描述试算结论，便于单测断言与阅读。 */
export function consolidationHighlights(
  sheet: WorksheetSheet,
  companyNames?: Record<string, string>,
): string[] {
  const lines: string[] = [
    `${sheet.statement} · ${sheet.period} · 纳入 ${sheet.included_companies.length} 家法人`,
    sheet.totals.comparable && sheet.totals.matched !== null && sheet.totals.difference !== null
      ? `${sheet.totals.scope}：匹配 ${sheet.totals.matched} 元，未达差额 ${sheet.totals.difference} 元（${sheet.totals.open_pairs} 对未平）`
      : `${sheet.totals.scope}（${sheet.totals.pairs} 对同口径往来，${sheet.totals.open_pairs} 对未平）`,
  ];
  for (const row of sheet.totals_by_standard ?? []) {
    lines.push(
      `${row.standard_label}：匹配 ${row.matched} 元，未达差额 ${row.difference} 元（${row.pairs} 对）`,
    );
  }
  if (sheet.not_merged_totals) {
    lines.push(
      `${sheet.not_merged_totals.label}：匹配 ${sheet.not_merged_totals.matched} 元（${sheet.not_merged_totals.pairs} 对）`,
    );
  }
  if (sheet.missing_companies.length) {
    const names = sheet.missing_companies
      .map((company) => companyDisplayName(company, companyNames))
      .join('、');
    lines.push(`缺报法人 ${sheet.missing_companies.length} 家：${names}`);
  }
  if (sheet.denied_companies?.length) {
    lines.push(`无权访问法人 ${sheet.denied_companies.length} 家，未计入任何合计`);
  }
  return lines;
}

export function ConsolidationWorksheetView({
  sheet,
  error,
  loading,
  companyNames,
}: {
  sheet: WorksheetSheet | null;
  error: string;
  loading: boolean;
  companyNames?: Record<string, string>;
}) {
  if (loading) return <p className="text-button">正在读取合并试算底稿…</p>;
  if (error) return <p className="form-error">{error}</p>;
  if (!sheet) return null;
  const knownNames: Record<string, string> = { ...(companyNames ?? {}) };
  for (const row of sheet.included_companies) {
    const label = row.company_name || row.name;
    if (label) knownNames[row.company_id] = label;
  }
  return (
    <div className="consolidation-worksheet" data-period={sheet.period}>
      <p className="consolidation-statement">{sheet.statement}</p>
      {sheet.statement_note ? <p className="text-button">{sheet.statement_note}</p> : null}
      <ul className="consolidation-lines">
        {consolidationHighlights(sheet, knownNames).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <table className="consolidation-table">
        <caption>{sheet.totals.scope}</caption>
        <thead>
          <tr>
            <th scope="col">会计准则口径</th>
            <th scope="col">对平对数</th>
            <th scope="col">匹配金额</th>
            <th scope="col">未达差额</th>
            <th scope="col">未平对数</th>
          </tr>
        </thead>
        <tbody>
          {(sheet.totals_by_standard ?? []).map((row) => (
            <tr key={row.standard}>
              <td>{row.standard_label}</td>
              <td>{row.pairs}</td>
              <td>{row.matched} 元</td>
              <td>{row.difference} 元</td>
              <td>{row.open_pairs}</td>
            </tr>
          ))}
        </tbody>
        {sheet.totals.comparable &&
        sheet.totals.matched !== null &&
        sheet.totals.difference !== null ? (
          <tfoot>
            <tr>
              <td>同口径可比合计</td>
              <td>{sheet.totals.pairs}</td>
              <td>{sheet.totals.matched} 元</td>
              <td>{sheet.totals.difference} 元</td>
              <td>{sheet.totals.open_pairs}</td>
            </tr>
          </tfoot>
        ) : null}
      </table>
      {sheet.not_merged_totals ? (
        <p className="consolidation-cross-standard" data-cross-standard="true">
          {sheet.not_merged_totals.label}：匹配 {sheet.not_merged_totals.matched} 元（
          {sheet.not_merged_totals.pairs} 对），不进入上面的可比合计。
        </p>
      ) : null}
      {(sheet.open_differences ?? []).length ? (
        <details className="consolidation-differences">
          <summary>未达差额明细 {sheet.open_differences?.length} 对</summary>
          <ul>
            {(sheet.open_differences ?? []).map((pair, index) => {
              const left =
                pair.left_company_name ||
                (pair.company_id ? knownNames[pair.company_id] || pair.company_id : '未知法人');
              const right =
                pair.right_company_name ||
                (pair.counterparty_company_id
                  ? knownNames[pair.counterparty_company_id] || pair.counterparty_company_id
                  : '未知法人');
              return (
                <li key={`${left}-${right}-${index}`}>
                  {left} ↔ {right}：差额 {pair.difference} 元
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
      {sheet.elimination_totals ? (
        <p className="text-button">
          已独立复核的抵消分录 {sheet.elimination_totals.approved_count} 条（
          {sheet.elimination_totals.approved} 元）；待复核 {sheet.elimination_totals.pending_count}{' '}
          条未进入试算。
        </p>
      ) : null}
    </div>
  );
}

export default function ConsolidationWorksheetPanel({
  session,
  period,
  companyIds,
  companyNames,
}: {
  session: Session;
  period: string;
  companyIds: string[];
  companyNames?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const [sheet, setSheet] = useState<WorksheetSheet | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const key = companyIds.join(',');

  useEffect(() => {
    if (!open || !period || !key) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<WorksheetSheet>(
      `/consolidation/worksheet?period=${encodeURIComponent(period)}&company_ids=${encodeURIComponent(key)}`,
      session,
      { signal: controller.signal },
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        setSheet(data);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, period, key, session]);

  return (
    <details
      className="consolidation-panel"
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>集团合并试算底稿（管理口径，非经审计法定合并报表）</summary>
      {open ? (
        key ? (
          <ConsolidationWorksheetView
            sheet={sheet}
            error={error}
            loading={loading}
            companyNames={companyNames}
          />
        ) : (
          <p className="text-button">还没有已纳管法人，暂时无法生成合并试算底稿。</p>
        )
      ) : null}
    </details>
  );
}
