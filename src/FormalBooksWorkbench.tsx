import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import type { Session } from './types';
import { ManualVoucherReviewPanel } from './ManualVoucherReviewPanel';
import {
  FormalBooksRequestGuard,
  formalBooksPath,
  formatFormalCents,
  parseFormalBooks,
  parseFormalVoucher,
} from './formal-books-types';
import type {
  FormalBalanceRow,
  FormalBookData,
  FormalBookKind,
  FormalBookScope,
  FormalEntry,
  FormalVoucher,
} from './formal-books-types';
import { FormalBooksRangePanel } from './FormalBooksRangePanel';
import './formal-books.css';

export interface FormalBooksWorkbenchProps {
  session: Session;
  bookId: string;
  bookName?: string;
  companyId?: string;
  period?: string;
  canDownload?: boolean;
  onBack: () => void;
}
const bookLabels: Record<FormalBookKind, string> = {
  trial_balance: '科目余额表',
  account_ledger: '科目明细账',
  daily_journal: '现金与银行日记账',
  aux_ledger: '辅助账',
};

export function FormalBooksWorkbench(props: FormalBooksWorkbenchProps) {
  // A new identity renders a new workspace before effects run: old values never
  // flash under a newly selected company, even if a transport ignores abort.
  const identity = JSON.stringify([
    props.session.scope_key,
    props.session.user_id,
    props.session.firm_id,
    props.bookId,
    props.companyId,
    props.period,
  ]);
  return <FormalBooksContent key={identity} {...props} />;
}

function failureText(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 401) return '登录已失效，请重新登录后返回本账套。';
    if (failure.code === 'admin_mfa_required')
      return '需要完成二次验证；请在管理入口验证后重新读取。';
    if (failure.status === 403)
      return '当前授权不足或已变更。请联系事务所管理员确认本账套权限，再重新读取。';
    if (failure.status === 404) return '指定账簿或凭证当前不可读取，请返回工作台选择获授权账套。';
    if (failure.status === 409)
      return `${failure.message} 请先由会计完成正式期初与账套配置，再重新读取。`;
  }
  return `${errorMessage(failure)} 可重新读取；仍失败时联系本账套会计。`;
}

function FormalBooksContent({
  session,
  bookId,
  bookName,
  companyId,
  period,
  canDownload = false,
  onBack,
}: FormalBooksWorkbenchProps) {
  const [kind, setKind] = useState<FormalBookKind>('trial_balance');
  const [manualOpen, setManualOpen] = useState(false);
  const [accountId, setAccountId] = useState('');
  const [asOf, setAsOf] = useState('');
  const [auxKey, setAuxKey] = useState('');
  const [auxValue, setAuxValue] = useState('');
  const [auxFilter, setAuxFilter] = useState<{ key: string; value: string } | null>(null);
  const [catalog, setCatalog] = useState<FormalBalanceRow[]>([]);
  const [resource, setResource] = useState<{ path: string; data: FormalBookData } | null>(null);
  const [confirmedScope, setConfirmedScope] = useState<FormalBookScope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [revoked, setRevoked] = useState(false);
  const [voucherId, setVoucherId] = useState('');
  const [voucher, setVoucher] = useState<FormalVoucher | null>(null);
  const [voucherError, setVoucherError] = useState('');
  const [voucherLoading, setVoucherLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState('');
  const [range, setRange] = useState(false);
  const reads = useRef(new FormalBooksRequestGuard());
  const details = useRef(new FormalBooksRequestGuard());
  const downloads = useRef(new FormalBooksRequestGuard());
  const boundScope = useRef<FormalBookScope | null>(null);
  const voucherPanel = useRef<HTMLElement | null>(null);
  const voucherTrigger = useRef<HTMLElement | null>(null);
  const scope = JSON.stringify([session.scope_key, session.user_id, session.firm_id, bookId]);
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const expected = useMemo(
    () => ({ bookId, firmId: session.firm_id, companyId, period }),
    [bookId, session.firm_id, companyId, period],
  );
  const query = useMemo(() => {
    if ((kind === 'account_ledger' && !accountId) || (kind === 'aux_ledger' && !auxFilter))
      return { path: '', issue: '' };
    try {
      return { path: formalBooksPath(kind, bookId, { accountId, asOf, ...auxFilter }), issue: '' };
    } catch {
      return { path: '', issue: '查询条件无效，请检查日期、科目或辅助项目。' };
    }
  }, [kind, bookId, accountId, asOf, auxFilter]);
  const liveQuery = useRef(query.path);
  liveQuery.current = query.path;
  const data = !revoked && resource?.path === query.path ? resource.data : null;
  const stop = useCallback(() => {
    reads.current.cancel();
    details.current.cancel();
    downloads.current.cancel();
  }, []);
  useEffect(() => {
    function forbidden(event: Event) {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      stop();
      setRevoked(true);
      setResource(null);
      setCatalog([]);
      setVoucher(null);
      setVoucherId('');
      setLoading(false);
      setDownloading(false);
      setError('当前授权已变更，请联系事务所管理员确认权限后重新读取。');
    }
    window.addEventListener('accounting:forbidden', forbidden);
    return () => {
      window.removeEventListener('accounting:forbidden', forbidden);
      stop();
    };
  }, [session.scope_key, stop]);
  useEffect(() => {
    reads.current.cancel();
    details.current.cancel();
    downloads.current.cancel();
    setResource(null);
    setVoucher(null);
    setVoucherId('');
    setNotice('');
    setDownloading(false);
    if (revoked || !query.path) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    const ticket = reads.current.begin(scope);
    void api<unknown>(query.path, session, { signal: ticket.signal })
      .then((raw) => {
        if (!reads.current.accepts(ticket, liveScope.current)) return;
        const binding = boundScope.current;
        const value = parseFormalBooks(kind, raw, {
          ...expected,
          companyId: binding?.company_id ?? expected.companyId,
          period: binding?.period ?? expected.period,
        });
        boundScope.current = value.scope;
        setConfirmedScope(value.scope);
        if (value.kind === 'trial_balance') setCatalog(value.rows);
        setResource({ path: query.path, data: value });
      })
      .catch((failure: unknown) => {
        if (!reads.current.accepts(ticket, liveScope.current)) return;
        setResource(null);
        setError(failureText(failure));
      })
      .finally(() => {
        if (reads.current.accepts(ticket, liveScope.current)) setLoading(false);
      });
    return () => reads.current.cancel();
  }, [query.path, kind, expected, session, scope, revision, revoked]);
  useEffect(() => {
    setVoucher(null);
    setVoucherError('');
    if (!voucherId || revoked || !confirmedScope) {
      setVoucherLoading(false);
      return;
    }
    const ticket = details.current.begin(scope);
    voucherPanel.current?.focus();
    setVoucherLoading(true);
    void api<unknown>(`/posting/vouchers/${encodeURIComponent(voucherId)}`, session, {
      signal: ticket.signal,
    })
      .then((raw) => {
        if (!details.current.accepts(ticket, liveScope.current)) return;
        setVoucher(
          parseFormalVoucher(
            raw,
            { ...expected, companyId: confirmedScope.company_id, period: confirmedScope.period },
            voucherId,
          ),
        );
      })
      .catch((failure: unknown) => {
        if (details.current.accepts(ticket, liveScope.current))
          setVoucherError(failureText(failure));
      })
      .finally(() => {
        if (details.current.accepts(ticket, liveScope.current)) setVoucherLoading(false);
      });
    return () => details.current.cancel();
  }, [voucherId, confirmedScope, expected, session, scope, revoked]);

  function refresh() {
    setRevoked(false);
    setRevision((value) => value + 1);
  }
  function openAccount(id: string) {
    setAccountId(id);
    setKind('account_ledger');
  }
  function closeVoucher() {
    details.current.cancel();
    setVoucherId('');
    setVoucher(null);
    setVoucherError('');
    if (voucherTrigger.current?.isConnected) voucherTrigger.current.focus();
  }
  function openVoucher(id: string) {
    voucherTrigger.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setVoucherId(id);
  }
  async function download() {
    if (!data || !canDownload || revoked || downloading) return;
    const ticket = downloads.current.begin(scope);
    const requestedPath = query.path;
    setDownloading(true);
    setNotice('');
    const params = new URLSearchParams({ kind });
    if (kind === 'account_ledger') params.set('account_id', accountId);
    if (kind === 'aux_ledger' && auxFilter) {
      params.set('key', auxFilter.key);
      params.set('value', auxFilter.value);
    }
    if ((kind === 'trial_balance' || kind === 'account_ledger') && asOf) params.set('as_of', asOf);
    try {
      const blob = await apiBlob(
        `/books/${encodeURIComponent(bookId)}/formal-books/export?${params}`,
        session,
        { signal: ticket.signal },
      );
      if (
        !downloads.current.accepts(ticket, liveScope.current) ||
        liveQuery.current !== requestedPath
      )
        return;
      if (!blob.size) throw new Error('下载内容为空。');
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `正式账簿-${data.scope.period}-${kind}.csv`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      setNotice('已下载当前账簿 CSV；这是账簿副本，不代表外部软件已接收。');
    } catch (failure) {
      if (
        downloads.current.accepts(ticket, liveScope.current) &&
        liveQuery.current === requestedPath
      )
        setError(failureText(failure));
    } finally {
      if (downloads.current.accepts(ticket, liveScope.current)) setDownloading(false);
    }
  }
  if (range)
    return (
      <FormalBooksRangePanel
        session={session}
        bookId={bookId}
        bookName={bookName}
        companyId={companyId ?? confirmedScope?.company_id}
        canDownload={canDownload}
        onBack={() => setRange(false)}
      />
    );
  const selectionNeeded =
    kind === 'account_ledger' && !accountId
      ? '请先选择科目，或从科目余额表点击“查看明细”。'
      : kind === 'aux_ledger' && !auxFilter
        ? '填写辅助项目和对应值，再查询正式期初与已记账分录。'
        : '';
  return (
    <main className="formal-books-workspace">
      <header className="formal-books-heading">
        <div>
          <p className="eyebrow">已记账数据 / 人民币</p>
          <h1>正式账簿</h1>
          <p>
            {bookName || '当前账套'}
            {confirmedScope ? ` · ${confirmedScope.period}` : period ? ` · ${period}` : ''}
          </p>
        </div>
        <div className="formal-books-heading-actions">
          <button type="button" className="button secondary" onClick={() => setRange(true)}>
            连续期间账簿
          </button>
          <button type="button" className="button secondary" onClick={onBack}>
            返回工作台
          </button>
        </div>
      </header>
      <p className="formal-books-boundary">
        余额来自已绑定的正式期初与正式凭证；未审核、未记账草稿不计入余额。查看与下载不会审核或记账。
      </p>
      <details
        className="review-section"
        onToggle={(event) => setManualOpen(event.currentTarget.open)}
      >
        <summary>手工凭证办理（按制单、复核、记账三步办理）</summary>
        {manualOpen && (
          <ManualVoucherReviewPanel session={session} bookId={bookId} companyId={companyId} />
        )}
      </details>
      <nav className="formal-books-tabs" aria-label="账簿类型">
        {(Object.entries(bookLabels) as [FormalBookKind, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className="button secondary"
            aria-pressed={kind === key}
            onClick={() => setKind(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <section className="formal-books-controls" aria-label="账簿查询条件">
        {kind === 'account_ledger' && (
          <label>
            科目
            <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
              <option value="">请选择科目</option>
              {catalog.map((row) => (
                <option key={row.account_id} value={row.account_id}>
                  {row.code} · {row.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {(kind === 'trial_balance' || kind === 'account_ledger') && (
          <label>
            截止日期
            <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
            <small>留空查看本期全部正式记录</small>
          </label>
        )}
        {kind === 'aux_ledger' && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setAuxFilter({ key: auxKey.trim(), value: auxValue.trim() });
            }}
          >
            <label>
              辅助项目
              <input
                value={auxKey}
                maxLength={40}
                placeholder="例如 counterparty"
                onChange={(event) => setAuxKey(event.target.value)}
                required
              />
            </label>
            <label>
              辅助值
              <input
                value={auxValue}
                maxLength={200}
                placeholder="已配置的辅助核算值"
                onChange={(event) => setAuxValue(event.target.value)}
                required
              />
            </label>
            <button className="button" disabled={!auxKey.trim() || !auxValue.trim()}>
              查询辅助账
            </button>
          </form>
        )}
        <div className="formal-books-actions">
          <button
            type="button"
            className="button secondary"
            disabled={loading || !query.path}
            onClick={refresh}
          >
            刷新当前账簿
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={!canDownload || !data || loading || downloading || revoked}
            onClick={() => void download()}
          >
            下载当前账簿 CSV{downloading ? '…' : ''}
          </button>
        </div>
      </section>
      {!canDownload && (
        <p className="formal-books-help">
          下载权限由账套授权决定；如需账簿副本，请联系获授权复核会计。
        </p>
      )}
      {query.issue && (
        <p className="formal-books-error" role="alert">
          {query.issue}
        </p>
      )}
      {error && (
        <section className="formal-books-error" role="alert">
          <p>{error}</p>
          <button type="button" className="button secondary" disabled={loading} onClick={refresh}>
            重新读取账簿
          </button>
          <button type="button" className="button text" onClick={onBack}>
            返回选择账套
          </button>
        </section>
      )}
      {notice && <p role="status">{notice}</p>}
      {loading && (
        <p className="formal-books-empty" role="status" aria-live="polite">
          正在读取正式账簿…
        </p>
      )}
      {!loading && selectionNeeded && <p className="formal-books-empty">{selectionNeeded}</p>}
      {data && (
        <section aria-label={bookLabels[data.kind]} aria-busy={loading}>
          <h2>{bookLabels[data.kind]}</h2>
          {data.kind === 'trial_balance' && (
            <>
              <p className="formal-books-help">
                借方余额为正，贷方余额为负；父级包含下级，合计仅按末级科目汇总。
              </p>
              {data.rows.length ? (
                <div
                  className="formal-books-table-wrap"
                  tabIndex={0}
                  role="region"
                  aria-label="科目余额表横向查看"
                >
                  <table>
                    <caption>本期正式科目余额 · 单位：人民币元</caption>
                    <thead>
                      <tr>
                        <th scope="col">科目</th>
                        <th scope="col">期初余额</th>
                        <th scope="col">本期借方</th>
                        <th scope="col">本期贷方</th>
                        <th scope="col">期末余额</th>
                        <th scope="col">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((row) => (
                        <tr key={row.account_id}>
                          <th scope="row">
                            {row.code} · {row.name}
                            {!row.is_leaf && <small>汇总科目</small>}
                          </th>
                          <td>{formatFormalCents(row.opening_cents)}</td>
                          <td>{formatFormalCents(row.debit_cents)}</td>
                          <td>{formatFormalCents(row.credit_cents)}</td>
                          <td>{formatFormalCents(row.closing_cents)}</td>
                          <td>
                            {row.is_leaf ? (
                              <button
                                type="button"
                                className="text-button"
                                aria-label={`查看 ${row.code} 明细`}
                                onClick={() => openAccount(row.account_id)}
                              >
                                查看明细
                              </button>
                            ) : (
                              '汇总展示'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <th scope="row">末级科目合计</th>
                        <td>{formatFormalCents(data.totals.opening_cents)}</td>
                        <td>{formatFormalCents(data.totals.debit_cents)}</td>
                        <td>{formatFormalCents(data.totals.credit_cents)}</td>
                        <td>{formatFormalCents(data.totals.closing_cents)}</td>
                        <td />
                      </tr>
                    </tfoot>
                  </table>
                </div>
              ) : (
                <p className="formal-books-empty">
                  本期暂无正式余额。请会计确认正式期初和已记账凭证；草稿不会在这里显示。
                </p>
              )}
            </>
          )}
          {data.kind === 'account_ledger' && (
            <>
              <p>
                期初余额 <strong>{formatFormalCents(data.opening_cents)}</strong> · 期末余额{' '}
                <strong>{formatFormalCents(data.closing_cents)}</strong> 元
              </p>
              <EntryTable entries={data.entries} onVoucher={openVoucher} />
            </>
          )}
          {data.kind === 'daily_journal' && (
            <>
              <p className="formal-books-help">
                当前口径为现金、银行科目（1001、1002
                及其下级）；余额按各科目分别累计，不能将不同账户混为一个余额。
              </p>
              <EntryTable entries={data.entries} onVoucher={openVoucher} showAccount />
            </>
          )}
          {data.kind === 'aux_ledger' && (
            <>
              <h3>匹配的正式期初</h3>
              {data.opening.length ? (
                <ul className="formal-books-openings">
                  {data.opening.map((row, index) => (
                    <li key={`${row.account_id}:${index}`}>
                      <span>
                        {row.code_snapshot ||
                          catalog.find((account) => account.account_id === row.account_id)?.code ||
                          row.account_id}{' '}
                        ·{' '}
                        {row.name_snapshot ||
                          catalog.find((account) => account.account_id === row.account_id)?.name ||
                          '辅助期初'}
                      </span>
                      <strong>{formatFormalCents(row.opening_cents)} 元</strong>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>该辅助条件下没有正式期初记录。</p>
              )}
              <h3>匹配的已记账分录</h3>
              <EntryTable entries={data.vouchers} onVoucher={openVoucher} showAccount />
            </>
          )}
        </section>
      )}
      {voucherId && (
        <section
          ref={voucherPanel}
          tabIndex={-1}
          className="formal-books-voucher"
          aria-label="正式凭证详情"
        >
          <header>
            <h2>正式凭证详情</h2>
            <button type="button" className="button secondary" onClick={closeVoucher}>
              关闭凭证详情
            </button>
          </header>
          {voucherLoading && <p role="status">正在读取所选原凭证…</p>}
          {voucherError && <p role="alert">{voucherError} 请关闭后重新打开原凭证。</p>}
          {voucher && voucher.voucher.id === voucherId && (
            <>
              <p>
                {voucher.voucher.voucher_date} · {voucher.voucher.voucher_type}-
                {voucher.voucher.voucher_no} · 已记账
              </p>
              <p>{voucher.voucher.description}</p>
              <div
                className="formal-books-table-wrap"
                tabIndex={0}
                role="region"
                aria-label="凭证分录横向查看"
              >
                <table>
                  <caption>正式凭证分录 · 单位：人民币元</caption>
                  <thead>
                    <tr>
                      <th scope="col">科目</th>
                      <th scope="col">借方</th>
                      <th scope="col">贷方</th>
                    </tr>
                  </thead>
                  <tbody>
                    {voucher.lines.map((line) => (
                      <tr key={line.line_no}>
                        <th scope="row">
                          {line.code_snapshot} · {line.name_snapshot}
                        </th>
                        <td>{formatFormalCents(line.debit_cents)}</td>
                        <td>{formatFormalCents(line.credit_cents)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="formal-books-help">
                原凭证为已记账记录。更正需沿用冲销或调整流程；此处仅供查看。
              </p>
            </>
          )}
        </section>
      )}
    </main>
  );
}

function EntryTable({
  entries,
  onVoucher,
  showAccount = false,
}: {
  entries: FormalEntry[];
  onVoucher: (id: string) => void;
  showAccount?: boolean;
}) {
  if (!entries.length)
    return (
      <p className="formal-books-empty">
        当前条件下暂无已记账分录。可调整查询条件；未记账草稿不会计入账簿。
      </p>
    );
  const hasBalance = entries.some((row) => row.balance_cents !== undefined);
  return (
    <div
      className="formal-books-table-wrap"
      tabIndex={0}
      role="region"
      aria-label="账簿明细横向查看"
    >
      <table>
        <caption>已记账分录 · 单位：人民币元</caption>
        <thead>
          <tr>
            <th scope="col">日期 / 凭证</th>
            {showAccount && <th scope="col">科目</th>}
            <th scope="col">摘要</th>
            <th scope="col">借方</th>
            <th scope="col">贷方</th>
            {hasBalance && <th scope="col">余额</th>}
          </tr>
        </thead>
        <tbody>
          {entries.map((row) => (
            <tr key={`${row.voucher_id}:${row.line_no}`}>
              <td>
                {row.voucher_date}
                <button
                  type="button"
                  className="text-button"
                  aria-label={`查看凭证 ${row.voucher_type}-${row.voucher_no}`}
                  onClick={() => onVoucher(row.voucher_id)}
                >
                  {row.voucher_type}-{row.voucher_no}
                </button>
              </td>
              {showAccount && (
                <td>
                  {row.code_snapshot} · {row.name_snapshot}
                </td>
              )}
              <td>{row.description || '—'}</td>
              <td>{formatFormalCents(row.debit_cents)}</td>
              <td>{formatFormalCents(row.credit_cents)}</td>
              {hasBalance && (
                <td>
                  {row.balance_cents === undefined ? '—' : formatFormalCents(row.balance_cents)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
