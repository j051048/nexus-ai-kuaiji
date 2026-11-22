import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

/** One accrual entry as the service returns it: a side plus integer cents. */
export interface Entry {
  account: string;
  side: 'debit' | 'credit';
  cents: number;
  memo: string;
}

export interface AccrualPreview {
  entries: Entry[];
  debit_cents: number;
  credit_cents: number;
  refund_note: string;
  surtax_basis?: SurtaxBasis;
  posted: false;
  note: string;
}

/**
 * What the server returns after the accrual was turned into a *request*.
 * The voucher itself does not exist yet: it is created only after another
 * person with review rights approves, so nothing here may claim it was posted.
 */
export interface SurtaxBasis {
  source: string;
  rate_bp: number | null;
}

export interface AccrualRequest {
  request: { id: string; status: string; version: number };
  voucher: {
    command_key: string;
    voucher_date: string;
    line_count: number;
    debit_cents: number;
    credit_cents: number;
  };
  refund_note: string;
  surtax_basis: SurtaxBasis;
  surtax_cents: number;
  /** Set once a formal voucher exists for this request; the status alone cannot say it. */
  posted_voucher_id?: string | null;
  posted: false;
  note: string;
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待复核',
  approved: '已复核，待过账',
  posted: '已过账',
  withdrawn: '已撤回',
};

const yuan = (cents: number) => (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2 });

const percent = (rateBp: number) =>
  (rateBp / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });

/**
 * Parse a user-entered yuan amount into integer cents without floating point.
 * ``0.1 + 0.2`` style drift must never reach a voucher; invalid text returns
 * null so the caller can refuse instead of guessing.
 */
export function yuanToCents(text: string): number | null {
  const value = text.trim().replace(/,/g, '');
  if (!/^-?\d{1,15}(?:\.\d{1,2})?$/.test(value)) return null;
  const negative = value.startsWith('-');
  const digits = negative ? value.slice(1) : value;
  const [whole, fraction = ''] = digits.split('.');
  const cents = Number(whole) * 100 + Number(`${fraction}00`.slice(0, 2));
  return negative ? -cents : cents;
}

export const previewTotals = (data: { debit_cents: number; credit_cents: number }) =>
  `借方 ${yuan(data.debit_cents)} · 贷方 ${yuan(data.credit_cents)}`;

export const requestStatus = (outcome: AccrualRequest) =>
  outcome.posted_voucher_id
    ? '已过账'
    : (STATUS_LABEL[outcome.request.status] ?? outcome.request.status);

export const surtaxBasisLabel = (basis: SurtaxBasis) =>
  basis.source === 'tax_profile'
    ? `附加税按税务档案核定税率 ${percent(basis.rate_bp ?? 0)}% 计征`
    : '附加税按请求中的政策条目计征';

export function AccrualPreviewView({
  data,
  blocked,
}: {
  data: AccrualPreview | null;
  blocked: string;
}) {
  if (blocked)
    return (
      <p className="tax-accrual-blocked" role="note">
        {blocked}
      </p>
    );
  if (!data || data.entries.length === 0)
    return <p className="tax-accrual-empty">本期没有可计提的税金，或底稿尚未复核。</p>;
  return (
    <div className="tax-accrual-preview">
      <p className="tax-accrual-totals">{previewTotals(data)}</p>
      <table>
        <thead>
          <tr>
            <th scope="col">科目</th>
            <th scope="col">摘要</th>
            <th scope="col">借方</th>
            <th scope="col">贷方</th>
          </tr>
        </thead>
        <tbody>
          {data.entries.map((entry) => (
            <tr key={`${entry.account}-${entry.side}`}>
              <td>{entry.account}</td>
              <td>{entry.memo}</td>
              <td>{entry.side === 'debit' ? yuan(entry.cents) : ''}</td>
              <td>{entry.side === 'credit' ? yuan(entry.cents) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="tax-accrual-note">{data.note}</p>
      {data.surtax_basis ? (
        <p className="tax-accrual-note">{surtaxBasisLabel(data.surtax_basis)}。</p>
      ) : null}
      <p className="tax-accrual-note">
        预览不写账：计提凭证仍须走「建议 → 复核 → 过账」，制单人与复核人必须是不同的人。
      </p>
    </div>
  );
}

export function AccrualRequestView({
  outcome,
  error,
}: {
  outcome: AccrualRequest | null;
  error: string;
}) {
  if (error)
    return (
      <p className="tax-accrual-blocked" role="alert">
        {error}
      </p>
    );
  if (!outcome) return null;
  return (
    <div className="tax-accrual-preview" role="status">
      <p className="tax-accrual-totals">
        {`已生成申请 #${outcome.request.id.slice(0, 8)}：${requestStatus(outcome)}`}
      </p>
      <p className="tax-accrual-note">
        {`凭证日期 ${outcome.voucher.voucher_date}，${outcome.voucher.line_count} 行分录，` +
          `借方 ${yuan(outcome.voucher.debit_cents)} · 贷方 ${yuan(outcome.voucher.credit_cents)}；` +
          `${surtaxBasisLabel(outcome.surtax_basis)}。`}
      </p>
      {outcome.refund_note ? <p className="tax-accrual-note">{outcome.refund_note}</p> : null}
      {outcome.posted_voucher_id ? (
        <p className="tax-accrual-note">
          已生成正式凭证，记账完成；正式凭证不能修改，记错须另开冲销凭证。
        </p>
      ) : (
        <p className="tax-accrual-note">
          申请尚未过账：到「正式账簿 →
          手工凭证办理」由另一名有复核权限的人批准后才能记账，制单人不能复核本人申请。
        </p>
      )}
    </div>
  );
}

export function TaxAccrualPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const [data, setData] = useState<AccrualPreview | null>(null);
  const [blocked, setBlocked] = useState('');
  const [loading, setLoading] = useState(true);
  const [profit, setProfit] = useState('');
  const [outcome, setOutcome] = useState<AccrualRequest | null>(null);
  const [requestError, setRequestError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const profitCents = yuanToCents(profit);

  const load = useCallback(
    (cents: number | null, signal?: AbortSignal) => {
      const body = { book_id: bookId, period, cumulative_profit_cents: cents };
      api<AccrualPreview>('/tax/accrual-preview', session, {
        method: 'POST',
        body,
        signal,
      })
        .then((raw) => {
          setData(raw);
          setBlocked('');
        })
        .catch((error) => {
          // A missing reviewed workpaper or a missing profit is a normal state,
          // not a failure: show the server's own explanation instead of
          // inventing numbers.
          setData(null);
          setBlocked(errorMessage(error));
        })
        .finally(() => setLoading(false));
    },
    [session, bookId, period],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    // Typing a profit must not fire one request per keystroke; invalid text
    // still reloads immediately so the operator sees why it is refused.
    const timer = setTimeout(() => load(profitCents, controller.signal), 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [load, profitCents]);

  const requestVoucher = useCallback(() => {
    if (profitCents === null) return;
    setSubmitting(true);
    setRequestError('');
    api<AccrualRequest>('/tax/accrual-manual-request', session, {
      method: 'POST',
      body: { book_id: bookId, period, cumulative_profit_cents: profitCents },
    })
      .then((raw) => {
        setOutcome(raw);
        setRequestError('');
      })
      .catch((error) => {
        // 服务端拒绝（科目缺失、期间不符、底稿未复核）要说人话，不要假装成功。
        setOutcome(null);
        setRequestError(errorMessage(error));
      })
      .finally(() => setSubmitting(false));
  }, [session, bookId, period, profitCents]);

  const refreshStatus = useCallback(() => {
    const identifier = outcome?.request.id;
    if (!identifier) return;
    api<{ requests: { id: string; status: string; posted_voucher_id: string | null }[] }>(
      `/books/${bookId}/manual-review-requests`,
      session,
    )
      .then((raw) => {
        const found = raw.requests.find((item) => item.id === identifier);
        if (found)
          setOutcome((previous) =>
            previous
              ? {
                  ...previous,
                  // 过账后请求状态仍是 approved：只有正式凭证存在才代表已记账。
                  posted_voucher_id: found.posted_voucher_id ?? null,
                  request: { ...previous.request, status: found.status },
                }
              : previous,
          );
      })
      .catch((error) => setRequestError(errorMessage(error)));
  }, [outcome, bookId, session]);

  const canRequest =
    !loading && !submitting && !blocked && profitCents !== null && Boolean(data?.entries.length);

  return (
    <section className="tax-accrual" aria-label="四税计提预览" aria-busy={loading}>
      <header>
        <h3>四税计提预览</h3>
        <button className="button secondary" disabled={loading} onClick={() => load(profitCents)}>
          重新计算预览
        </button>
        <button className="button secondary" disabled={!canRequest} onClick={requestVoucher}>
          {submitting ? '正在提交申请…' : '生成计提手工凭证申请'}
        </button>
        {outcome ? (
          <button className="button secondary" onClick={refreshStatus}>
            刷新申请状态
          </button>
        ) : null}
      </header>
      <p className="tax-boundary">
        只按已复核的增值税底稿派生附加税、所得税与印花税；金额为整数分，不在这里过账。
      </p>
      <p>
        <label>
          本期累计利润（元）
          <input
            aria-label="本期累计利润（元）"
            inputMode="decimal"
            value={profit}
            onChange={(event) => setProfit(event.target.value)}
          />
        </label>
        <span className="tax-accrual-note">
          企业所得税预缴按累计利润计征；底稿里的账载税额不能代替利润，未填写时服务端会拒绝计提所得税。
        </span>
      </p>
      <AccrualPreviewView data={data} blocked={blocked} />
      <AccrualRequestView outcome={outcome} error={requestError} />
    </section>
  );
}
