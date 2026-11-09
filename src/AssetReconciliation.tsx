import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { useScopedQuery } from './useScopedQuery';
import { parseAssetReconciliation } from './finance-operations';
import type { Session } from './types';
import { parseFormalVoucher, formatFormalCents } from './formal-books-types';

type HistoryRow = ReturnType<typeof parseAssetReconciliation>['history'][number];
function HistoryVoucher({
  session,
  companyId,
  row,
  onBack,
}: {
  session: Session;
  companyId: string;
  row: HistoryRow;
  onBack: () => void;
}) {
  const query = useScopedQuery(async (signal) => {
    const raw = await api<unknown>(`/posting/vouchers/${encodeURIComponent(row.id)}`, session, {
      signal,
    });
    const parsed = parseFormalVoucher(
      raw,
      { firmId: session.firm_id, companyId, bookId: row.book_id, period: row.period },
      row.id,
    );
    const fingerprint = (raw as { voucher: { content_fingerprint?: unknown } }).voucher
      .content_fingerprint;
    if (fingerprint !== row.fingerprint || parsed.voucher.voucher_date.slice(0, 7) !== row.period)
      throw new Error('原凭证与当前核对依据不一致，请返回重新读取。');
    return parsed;
  }, `${session.scope_key}:${row.book_id}:${row.id}:${row.fingerprint}`);
  return (
    <section aria-label="资产历史原凭证" className="review-section">
      <button className="button secondary" onClick={onBack}>
        返回资产差额核对
      </button>
      <h3>{row.period} · 原凭证</h3>
      {query.loading && <p role="status">正在核对原凭证…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {query.data && (
        <>
          <p>{query.data.voucher.description}</p>
          <ul>
            {query.data.lines.map((line) => (
              <li key={line.line_no}>
                {line.code_snapshot} {line.name_snapshot} · 借 {formatFormalCents(line.debit_cents)}{' '}
                / 贷 {formatFormalCents(line.credit_cents)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function AssetReconciliation({
  session,
  bookId,
  companyId,
  period,
  assetId,
  onBack,
  onChanged,
  onVoucher,
}: {
  session: Session;
  bookId: string;
  companyId: string;
  period: string;
  assetId: string;
  onBack: () => void;
  onChanged: () => void;
  onVoucher?: (id: string) => void;
}) {
  const [reason, setReason] = useState(''),
    [notice, setNotice] = useState(''),
    [uncertain, setUncertain] = useState(false),
    [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const active = useRef(true);
  const [history, setHistory] = useState<HistoryRow | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const path = `/periodic/assets/${encodeURIComponent(assetId)}/reconciliation`;
  const query = useScopedQuery(
    async (signal) =>
      parseAssetReconciliation(
        await api<unknown>(`${path}?book_id=${encodeURIComponent(bookId)}`, session, { signal }),
        { firmId: session.firm_id, companyId, bookId, period },
        assetId,
      ),
    `${session.scope_key}:${bookId}:${period}:${assetId}`,
  );
  const data = query.data;
  const money = (cents: number) => (cents / 100).toFixed(2);
  if (history)
    return (
      <HistoryVoucher
        session={session}
        companyId={companyId}
        row={history}
        onBack={() => setHistory(null)}
      />
    );
  return (
    <section className="review-section" aria-label="资产差异核对">
      <button className="button secondary" type="button" onClick={onBack}>
        返回资产与期末草稿
      </button>
      <h3>{data?.asset_name ?? '资产'} · 核对后继续计提</h3>
      <p>先核对原凭证与差额，再生成草稿，交另一位会计复核后记账。原卡片和凭证保留。</p>
      {query.loading && <p role="status">正在读取当前依据…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {notice && <p role="status">{notice}</p>}
      {data && (
        <>
          <dl className="finance-aging-summary">
            <div>
              <dt>历史原计提</dt>
              <dd>{money(data.prior_posted_cents)} 元</dd>
            </div>
            <div>
              <dt>计入冲销与调整后</dt>
              <dd>{money(data.net_posted_cents)} 元</dd>
            </div>
            <div>
              <dt>本期拟计提差额</dt>
              <dd>{money(data.amount_cents)} 元</dd>
            </div>
          </dl>
          <p>
            本期累计目标 {money(data.cumulative_cents)}{' '}
            元。这里只处理同一资产、同一对科目的计提差额；重分类、处置需先办理对应更正。
          </p>
          {data.accounts.map((a) => (
            <p key={a.id}>
              {a.name}：建卡版本 {a.original_version} → 本次版本 {a.version}
            </p>
          ))}
          <details>
            <summary>查看历史依据（{data.history.length} 张正式凭证）</summary>
            {data.history.map((r) => (
              <div key={r.id}>
                <span>
                  {r.period} · 对累计计提影响 {money(r.net_cents)} 元{' '}
                </span>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    r.book_id === bookId && onVoucher ? onVoucher(r.id) : setHistory(r)
                  }
                >
                  查看原凭证
                </button>
              </div>
            ))}
          </details>
          {data.blockers.map((b) => (
            <p key={b} role="alert">
              {b}
            </p>
          ))}
          {data.replaces_draft_id && (
            <p>本期旧草稿将保留为历史，并停止办理；新草稿仍需重新独立复核。</p>
          )}
          {data.can_generate && !uncertain && (
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                if (sending.current || reason.trim().length < 3) return;
                sending.current = true;
                setBusy(true);
                setNotice('');
                try {
                  await api(path, session, {
                    method: 'POST',
                    body: { book_id: bookId, expected_fingerprint: data.fingerprint, reason },
                  });
                  if (!active.current) return;
                  setNotice('核对草稿已生成，请返回期末草稿，由另一位会计复核。');
                  setUncertain(true);
                  onChanged();
                } catch (e) {
                  if (active.current) {
                    setNotice(`${errorMessage(e)} 请读取最新结果核对，不会自动重发。`);
                    setUncertain(true);
                  }
                } finally {
                  sending.current = false;
                  if (active.current) setBusy(false);
                }
              }}
            >
              <label>
                核对依据与理由
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  minLength={3}
                  maxLength={1000}
                />
              </label>
              <button
                type="submit"
                className="button"
                disabled={busy || query.loading || reason.trim().length < 3}
              >
                确认差额并生成待复核草稿
              </button>
            </form>
          )}
        </>
      )}
      <button
        type="button"
        className="button secondary"
        disabled={busy || query.loading}
        onClick={() => {
          query.reload();
        }}
      >
        重新读取核对依据
      </button>
      {uncertain && <p>请返回期末草稿核对办理结果；如需更换依据，重新打开本核对入口。</p>}
    </section>
  );
}
