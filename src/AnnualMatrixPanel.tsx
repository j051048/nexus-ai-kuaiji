import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight, LoaderCircle } from 'lucide-react';
import type { Session } from './types';
import { useMonthlyData } from './useMonthlyData';
import type { AnnualMatrix, AnnualMonthCell } from './monthly-workbench-types';
import './annual-matrix.css';

const stageLabels: Record<AnnualMonthCell['stage'], string> = {
  no_book: '未建账',
  closed: '已锁期',
  delivered: '已签收',
  open: '可办理',
  review: '待复核',
  materials: '待补资料',
  delivery: '待签收',
};

function cellDetail(cell: AnnualMonthCell): string {
  const books = cell.books
    .map(
      (book) =>
        `${book.book_name}：${stageLabels[book.stage]}，阻断 ${book.blockers} 项，${book.owner}`,
    )
    .join('；');
  return books || `${cell.label}，${cell.next}`;
}

export function AnnualMatrixPanel({
  session,
  year,
  revision = 0,
  onOpenMonth,
}: {
  session: Session;
  year: number;
  revision?: number;
  onOpenMonth: (period: string) => void;
}) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const { data, loading, error, revoked } = useMonthlyData<AnnualMatrix>(
    session,
    `/monthly/annual?year=${year}`,
    revision,
  );
  if (loading) {
    return (
      <div className="annual-matrix-state" role="status" aria-live="polite" aria-busy="true">
        <LoaderCircle size={18} aria-hidden="true" />
        正在读取年度矩阵…
      </div>
    );
  }
  if (error) {
    return (
      <p className="inline-error" role="alert">
        {error}
      </p>
    );
  }
  if (!data) return null;
  return (
    <section className="annual-matrix-panel" aria-label={`${year} 年企业月度矩阵`}>
      <div className="annual-matrix-heading">
        <div>
          <h2>{year} 年度矩阵</h2>
          <p>按当前授权账套投影；未建账月份不按 0 或已完成处理。点击格子可进入对应月份清单。</p>
        </div>
        <span>{revoked ? '授权已更新，请返回工作台' : `${data.rows.length} 家企业 · 12 个月`}</span>
      </div>
      <div className="annual-matrix-scroll">
        <table className="annual-matrix-table">
          <caption className="sr-only">
            {year} 年企业月度阶段矩阵；行是企业，列是月份，格内为阶段、阻断数和负责人。
          </caption>
          <thead>
            <tr>
              <th scope="col">企业</th>
              {data.months.map((period) => (
                <th scope="col" key={period}>
                  {Number(period.slice(5))} 月
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => {
              const open = expanded.includes(row.company_id);
              return (
                <Fragment key={row.company_id}>
                  <tr data-company={row.company_id}>
                    <th scope="row" className="annual-company">
                      <button
                        type="button"
                        className="annual-expand"
                        aria-expanded={open}
                        aria-label={`${open ? '收起' : '展开'} ${row.company_name} 各账套明细`}
                        onClick={() =>
                          setExpanded((old) =>
                            open
                              ? old.filter((id) => id !== row.company_id)
                              : [...old, row.company_id],
                          )
                        }
                      >
                        {open ? (
                          <ChevronDown size={14} aria-hidden="true" />
                        ) : (
                          <ChevronRight size={14} aria-hidden="true" />
                        )}
                      </button>
                      <span>{row.company_name}</span>
                    </th>
                    {row.months.map((cell) => (
                      <td key={cell.period}>
                        <button
                          type="button"
                          className={`annual-cell stage-${cell.stage}`}
                          disabled={revoked}
                          title={cellDetail(cell)}
                          aria-label={`${row.company_name} ${Number(cell.month)} 月，${cell.label}，阻断 ${cell.blockers} 项，${cell.owner}，${cell.next}`}
                          onClick={() => onOpenMonth(cell.period)}
                        >
                          <strong>{cell.label}</strong>
                          <small>
                            {cell.book_count > 1
                              ? `${cell.book_count} 账套`
                              : cell.blockers
                                ? `${cell.blockers} 项阻断`
                                : cell.owner}
                          </small>
                        </button>
                      </td>
                    ))}
                  </tr>
                  {open && (
                    <tr className="annual-detail-row">
                      <td colSpan={data.months.length + 1}>
                        <div className="annual-detail-grid">
                          {row.months
                            .filter((cell) => cell.books.length)
                            .map((cell) => (
                              <div className="annual-detail-month" key={cell.period}>
                                <strong>{Number(cell.month)} 月</strong>
                                {cell.books.map((book) => (
                                  <p key={book.book_id}>
                                    {book.book_name} · {stageLabels[book.stage]} · 阻断{' '}
                                    {book.blockers} 项 · {book.owner}
                                  </p>
                                ))}
                              </div>
                            ))}
                          {!row.months.some((cell) => cell.books.length) && (
                            <p>该企业本年度没有当前可见账套。</p>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="annual-matrix-note" role="note">
        {data.notes[0]}
      </p>
    </section>
  );
}
