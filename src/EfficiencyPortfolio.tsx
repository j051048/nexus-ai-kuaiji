import { useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronLeft, ChevronRight, RefreshCw, Timer } from 'lucide-react';
import { api, errorMessage } from './api';
import { durationLabel } from './efficiency-helpers';
import { parseEfficiencyPortfolio } from './efficiency-portfolio-contract';
import type { Pagination, Session } from './types';

export interface EfficiencyPortfolioPage {
  period: string;
  notice: string;
  pagination: Pagination;
  items: {
    company_id: string;
    company_name: string;
    period: string;
    credited_seconds: number | null;
    session_count: number;
    reviewed_drafts: number;
    draft_count: number;
    books: {
      book_id: string;
      name: string;
      credited_seconds: number | null;
      session_count: number;
      reviewed_drafts: number;
      draft_count: number;
    }[];
  }[];
}

export function EfficiencyPortfolio({
  session,
  onOpenBook,
  onBack,
}: {
  session: Session;
  onOpenBook?: (bookId: string) => void;
  onBack?: () => void;
}) {
  const [period, setPeriod] = useState(() => {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  });
  const [offset, setOffset] = useState(0),
    [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<{ scope: string; value: EfficiencyPortfolioPage } | null>(null);
  const [error, setError] = useState(''),
    [loading, setLoading] = useState(true);
  const scope = `${session.scope_key}:${session.user_id}:${period}:${offset}`;
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const current = data?.scope === scope ? data.value : null;
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    setLoading(true);
    void api<unknown>(
      `/efficiency/portfolio?${new URLSearchParams({ period, limit: '20', offset: String(offset) })}`,
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted && scope === liveScope.current)
          setData({ scope, value: parseEfficiencyPortfolio(value, { period, limit: 20, offset }) });
      })
      .catch((failure) => {
        if (!controller.signal.aborted && scope === liveScope.current)
          setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted && scope === liveScope.current) setLoading(false);
      });
    const revoke = () => {
      controller.abort();
      setData(null);
      setLoading(false);
      setError('当前授权已更新，请返回工作台重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      controller.abort();
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session, scope, period, offset, refresh]);
  return (
    <main className="efficiency-workbench efficiency-portfolio">
      <section className="page-heading">
        <div>
          <p className="eyebrow">全所服务 · 实际投入</p>
          <h1>看清每家企业的人工负担</h1>
          <p className="page-description">
            按会计月份汇总当前有复核权限的账套，进入账套查看或开启实际工作计时。
          </p>
        </div>
        <div className="efficiency-actions">
          <label>
            会计月份
            <input
              aria-label="效率统计月份"
              type="month"
              value={period}
              min="2000-01"
              max="2099-12"
              onChange={(event) => {
                if (/^20\d{2}-(0[1-9]|1[0-2])$/.test(event.target.value)) {
                  setPeriod(event.target.value);
                  setOffset(0);
                }
              }}
            />
          </label>
          <button
            className="button secondary"
            disabled={loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新
          </button>
          {onBack && (
            <button className="button secondary" onClick={onBack}>
              返回工作台
            </button>
          )}
        </div>
      </section>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">正在读取当前授权企业的实际工作记录…</p>}
      {current && (
        <>
          <p className="efficiency-measurement">
            <Timer size={17} />
            {current.notice}
          </p>
          <section className="efficiency-history" aria-label="企业月度人工投入">
            <div className="efficiency-table">
              <table>
                <thead>
                  <tr>
                    <th>企业与账套</th>
                    <th>已确认人工时间</th>
                    <th>计时样本</th>
                    <th>已审核／全部凭证</th>
                    <th>查看</th>
                  </tr>
                </thead>
                <tbody>
                  {current.items.map((item) => (
                    <tr key={item.company_id}>
                      <td>
                        <strong>{item.company_name}</strong>
                        <small className="efficiency-book-count">
                          {item.period} · {item.books.length} 个可复核账套
                        </small>
                      </td>
                      <td data-label="已确认人工时间">
                        {item.credited_seconds === null ? (
                          <span className="muted">暂无计时样本</span>
                        ) : (
                          durationLabel(item.credited_seconds)
                        )}
                      </td>
                      <td data-label="计时样本">{item.session_count} 次</td>
                      <td data-label="已审核／全部凭证">
                        {item.reviewed_drafts} / {item.draft_count}
                      </td>
                      <td>
                        <details>
                          <summary>账套明细</summary>
                          <ul className="efficiency-book-list">
                            {item.books.map((book) => (
                              <li key={book.book_id}>
                                <span>
                                  {book.name}
                                  <small>
                                    {book.credited_seconds === null
                                      ? '暂无计时样本'
                                      : durationLabel(book.credited_seconds)}{' '}
                                    · {book.session_count} 次
                                  </small>
                                </span>
                                {onOpenBook && (
                                  <button
                                    className="button secondary"
                                    aria-label={`查看${book.name}的计时`}
                                    onClick={() => onOpenBook(book.book_id)}
                                  >
                                    进入
                                    <ArrowRight size={14} />
                                  </button>
                                )}
                              </li>
                            ))}
                          </ul>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!current.items.length && (
              <p className="efficiency-empty">当前月份没有可复核账套；请确认月份和账套授权。</p>
            )}
            <div className="efficiency-actions efficiency-pagination">
              <span>共 {current.pagination.total} 家已授权企业</span>
              <button
                className="button secondary"
                disabled={loading || offset === 0}
                onClick={() => setOffset((value) => Math.max(0, value - 20))}
              >
                <ChevronLeft size={15} />
                上一页
              </button>
              <button
                className="button secondary"
                disabled={loading || !current.pagination.has_more}
                onClick={() => setOffset((value) => value + 20)}
              >
                下一页
                <ChevronRight size={15} />
              </button>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
