import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import { FormalBooksRequestGuard, formatFormalCents } from './formal-books-types';
import {
  blockerLines,
  csvFingerprint,
  parseRangeAccountLedger,
  parseRangeTrialBalance,
  rangeAccountLedgerPath,
  rangeExportPath,
  rangeSummary,
  rangeTrialBalancePath,
} from './formal-books-range';
import type { RangeAccountLedger, RangeTrialBalance } from './formal-books-range';
import type { Session } from './types';
import './formal-books-range.css';

export interface FormalBooksRangePanelProps {
  session: Session;
  bookId: string;
  bookName?: string;
  companyId?: string;
  canDownload?: boolean;
  onBack: () => void;
}

interface LedgerPeriod {
  period: string;
  status: string;
}

function failureText(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 403)
      return '当前账号无权连续查看该范围，请联系事务所管理员确认期间授权。';
    if (failure.status === 404) return '该长期账套或期间不在当前账号可见范围内。';
    if (failure.status === 422) return failure.message;
  }
  return `${errorMessage(failure)} 可重新读取；仍失败时联系本账套会计。`;
}

function periodsOf(value: unknown): { ledgerId: string; periods: LedgerPeriod[] } {
  if (!value || typeof value !== 'object') throw new Error('长期账套信息无法确认，请重新读取。');
  const payload = value as { id?: unknown; periods?: unknown };
  if (typeof payload.id !== 'string' || !Array.isArray(payload.periods))
    throw new Error('长期账套信息无法确认，请重新读取。');
  const periods = payload.periods.map((raw) => {
    const row = raw as { period?: unknown; status?: unknown };
    if (typeof row?.period !== 'string' || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(row.period))
      throw new Error('长期账套期间无法确认，请重新读取。');
    return { period: row.period, status: typeof row.status === 'string' ? row.status : '' };
  });
  return { ledgerId: payload.id, periods };
}

export function FormalBooksRangePanel({
  session,
  bookId,
  bookName,
  companyId,
  canDownload = false,
  onBack,
}: FormalBooksRangePanelProps) {
  const [ledgerId, setLedgerId] = useState('');
  const [periods, setPeriods] = useState<LedgerPeriod[]>([]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [balance, setBalance] = useState<RangeTrialBalance | null>(null);
  const [accountId, setAccountId] = useState('');
  const [ledger, setLedger] = useState<RangeAccountLedger | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [notice, setNotice] = useState('');
  const [downloading, setDownloading] = useState('');
  const reads = useRef(new FormalBooksRequestGuard());
  const details = useRef(new FormalBooksRequestGuard());
  const downloads = useRef(new FormalBooksRequestGuard());
  const liveScope = useRef('');
  const scope = JSON.stringify([session.scope_key, session.user_id, session.firm_id, bookId]);
  liveScope.current = scope;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    void api<unknown>(`/books/${encodeURIComponent(bookId)}/ledger`, session)
      .then((raw) => {
        if (cancelled) return;
        const overview = periodsOf(raw);
        setLedgerId(overview.ledgerId);
        setPeriods(overview.periods);
        if (overview.periods.length) {
          setStart(overview.periods[0].period);
          setEnd(overview.periods[overview.periods.length - 1].period);
        }
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(failureText(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bookId, session]);

  const range = useMemo(
    () => (ledgerId && start && end && start <= end ? { start, end } : null),
    [ledgerId, start, end],
  );

  useEffect(() => {
    if (!range || !ledgerId) {
      setBalance(null);
      return;
    }
    const ticket = reads.current.begin(scope);
    setLoading(true);
    setError('');
    void api<unknown>(rangeTrialBalancePath(ledgerId, range.start, range.end), session, {
      signal: ticket.signal,
    })
      .then((raw) => {
        if (!reads.current.accepts(ticket, liveScope.current)) return;
        setBalance(
          parseRangeTrialBalance(raw, {
            ledgerId,
            firmId: session.firm_id,
            companyId,
            start: range.start,
            end: range.end,
          }),
        );
      })
      .catch((failure: unknown) => {
        if (!reads.current.accepts(ticket, liveScope.current)) return;
        setBalance(null);
        setError(failureText(failure));
      })
      .finally(() => {
        if (reads.current.accepts(ticket, liveScope.current)) setLoading(false);
      });
    return () => reads.current.cancel();
  }, [range, ledgerId, session, scope, companyId]);

  const drill = useCallback(
    (account: string) => {
      if (!range || !ledgerId) return;
      setAccountId(account);
      const ticket = details.current.begin(scope);
      setLedger(null);
      setDetailError('');
      setDetailLoading(true);
      void api<unknown>(
        rangeAccountLedgerPath(ledgerId, account, range.start, range.end),
        session,
        {
          signal: ticket.signal,
        },
      )
        .then((raw) => {
          if (!details.current.accepts(ticket, liveScope.current)) return;
          setLedger(
            parseRangeAccountLedger(raw, {
              ledgerId,
              firmId: session.firm_id,
              companyId,
              start: range.start,
              end: range.end,
              accountId: account,
            }),
          );
        })
        .catch((failure: unknown) => {
          if (details.current.accepts(ticket, liveScope.current))
            setDetailError(failureText(failure));
        })
        .finally(() => {
          if (details.current.accepts(ticket, liveScope.current)) setDetailLoading(false);
        });
    },
    [range, ledgerId, session, scope, companyId],
  );

  async function exportCsv(kind: 'trial_balance' | 'account_ledger') {
    if (!range || !ledgerId || !canDownload || downloading) return;
    if (kind === 'account_ledger' && !accountId) {
      setNotice('请先点击科目行查看明细，再导出该科目连续明细账。');
      return;
    }
    const shown = kind === 'trial_balance' ? balance : ledger;
    if (!shown) return;
    const ticket = downloads.current.begin(scope);
    setDownloading(kind);
    setNotice('');
    try {
      const blob = await apiBlob(
        rangeExportPath(ledgerId, kind, range.start, range.end, accountId),
        session,
        { signal: ticket.signal },
      );
      if (!downloads.current.accepts(ticket, liveScope.current)) return;
      if (!blob.size) throw new Error('下载内容为空。');
      const digest = csvFingerprint(await blob.text());
      if (digest !== shown.fingerprint)
        throw new Error('CSV 与页面显示的数据来源不一致，已停止使用该文件，请重新导出并人工核对。');
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `连续账簿-${kind === 'trial_balance' ? '科目余额表' : '科目明细账'}-${range.start}-${range.end}.csv`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      setNotice('已下载，CSV 首行指纹与页面一致；这是账簿副本，不代表外部软件已接收。');
    } catch (failure) {
      if (downloads.current.accepts(ticket, liveScope.current)) setError(failureText(failure));
    } finally {
      if (downloads.current.accepts(ticket, liveScope.current)) setDownloading('');
    }
  }

  const blockers = balance ? blockerLines(balance.blockers) : [];

  return (
    <section className="range-books" aria-labelledby="range-books-title">
      <header className="range-books-head">
        <div>
          <p className="eyebrow">跨月核对 / 人民币</p>
          <h2 id="range-books-title">连续期间账簿</h2>
          <p className="range-books-sub">
            {bookName || '当前账套'}
            {balance ? ` · ${balance.start} 至 ${balance.end}` : ''}
          </p>
        </div>
        <div className="range-books-actions">
          {canDownload ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => void exportCsv('trial_balance')}
              disabled={!balance || downloading !== ''}
            >
              {downloading === 'trial_balance' ? '正在导出…' : '导出连续余额表 CSV'}
            </button>
          ) : null}
          <button type="button" className="button secondary" onClick={onBack}>
            返回工作台
          </button>
        </div>
      </header>

      <div className="range-books-controls">
        <label>
          <span>起始月份</span>
          <select value={start} onChange={(event) => setStart(event.target.value)}>
            {periods.map((item) => (
              <option key={item.period} value={item.period}>
                {item.period}
                {item.status === 'closed' ? '（已关账）' : '（未关账）'}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>截止月份</span>
          <select value={end} onChange={(event) => setEnd(event.target.value)}>
            {periods.map((item) => (
              <option key={item.period} value={item.period}>
                {item.period}
                {item.status === 'closed' ? '（已关账）' : '（未关账）'}
              </option>
            ))}
          </select>
        </label>
        <p className="range-books-hint">
          只累加一次期初，逐月核对期初与期末；不可用的月份会单独列出，不会并进合计。
        </p>
      </div>

      {error ? (
        <p className="range-books-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="range-books-notice">{notice}</p> : null}
      {loading && !balance ? <p className="range-books-hint">正在读取连续期间账簿…</p> : null}

      {balance ? (
        <>
          <div className={balance.complete ? 'range-books-state ok' : 'range-books-state warn'}>
            <p>
              <strong>{balance.complete ? '完整' : '不完整'}</strong>
              {' · '}
              {rangeSummary(balance)}
            </p>
            <p className="range-books-fingerprint">
              数据来源指纹 <code>{balance.fingerprint.slice(0, 16)}…</code>
            </p>
            {blockers.length ? (
              <ul>
                {blockers.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="range-books-scroll">
            <table className="range-books-table">
              <caption>按月覆盖（合计只在完整时代表账簿，不完整时必须分开核对）</caption>
              <thead>
                <tr>
                  <th scope="col">月份</th>
                  <th scope="col">状态</th>
                  <th scope="col">期初</th>
                  <th scope="col">本期借方</th>
                  <th scope="col">本期贷方</th>
                  <th scope="col">期末</th>
                </tr>
              </thead>
              <tbody>
                {balance.coverage.map((item) => (
                  <tr key={item.period} className={item.status === 'ok' ? '' : 'unavailable'}>
                    <td>{item.period}</td>
                    <td>
                      {item.status === 'ok'
                        ? '可用'
                        : item.status === 'missing'
                          ? '缺账套'
                          : item.status === 'unauthorized'
                            ? '无权查看'
                            : '未绑期初'}
                    </td>
                    <td>{formatFormalCents(item.opening_cents)}</td>
                    <td>{formatFormalCents(item.debit_cents)}</td>
                    <td>{formatFormalCents(item.credit_cents)}</td>
                    <td>{formatFormalCents(item.closing_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="range-books-scroll">
            <table className="range-books-table">
              <caption>
                科目余额（{balance.start} 至 {balance.end}）
              </caption>
              <thead>
                <tr>
                  <th scope="col">科目</th>
                  <th scope="col">名称</th>
                  <th scope="col">期初</th>
                  <th scope="col">借方</th>
                  <th scope="col">贷方</th>
                  <th scope="col">期末</th>
                  <th scope="col">明细</th>
                </tr>
              </thead>
              <tbody>
                {balance.rows.map((row) => (
                  <tr key={row.account_id}>
                    <td>{row.code}</td>
                    <td>
                      {row.name}
                      {row.renamed ? <span className="range-books-tag">更名</span> : null}
                    </td>
                    <td>{formatFormalCents(row.opening_cents)}</td>
                    <td>{formatFormalCents(row.debit_cents)}</td>
                    <td>{formatFormalCents(row.credit_cents)}</td>
                    <td>{formatFormalCents(row.closing_cents)}</td>
                    <td>
                      <button
                        type="button"
                        className="button secondary compact"
                        onClick={() => drill(row.account_id)}
                      >
                        查看明细
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {accountId ? (
            <div className="range-books-detail">
              <h3>
                连续明细账
                {ledger ? ` · ${ledger.account.code} ${ledger.account.name}` : ''}
              </h3>
              {detailError ? (
                <p className="range-books-error" role="alert">
                  {detailError}
                </p>
              ) : null}
              {detailLoading ? <p className="range-books-hint">正在读取明细…</p> : null}
              {ledger ? (
                <>
                  <p className="range-books-hint">
                    期初 {formatFormalCents(ledger.opening_cents)} · 期末{' '}
                    {formatFormalCents(ledger.closing_cents)} · 指纹{' '}
                    <code>{ledger.fingerprint.slice(0, 16)}…</code>
                  </p>
                  {ledger.blockers.length ? (
                    <ul className="range-books-blockers">
                      {blockerLines(ledger.blockers).map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="range-books-scroll">
                    <table className="range-books-table">
                      <thead>
                        <tr>
                          <th scope="col">月份</th>
                          <th scope="col">日期</th>
                          <th scope="col">凭证</th>
                          <th scope="col">摘要</th>
                          <th scope="col">借方</th>
                          <th scope="col">贷方</th>
                          <th scope="col">余额</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ledger.entries.map((row) => (
                          <tr key={`${row.voucher_id}-${row.line_no}`}>
                            <td>{row.period}</td>
                            <td>{row.voucher_date}</td>
                            <td>
                              {row.voucher_type}-{row.voucher_no}
                            </td>
                            <td>{row.description || '—'}</td>
                            <td>{formatFormalCents(row.debit_cents)}</td>
                            <td>{formatFormalCents(row.credit_cents)}</td>
                            <td>{formatFormalCents(row.balance_cents ?? 0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {canDownload ? (
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() => void exportCsv('account_ledger')}
                      disabled={downloading !== ''}
                    >
                      {downloading === 'account_ledger' ? '正在导出…' : '导出该科目连续明细账 CSV'}
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
