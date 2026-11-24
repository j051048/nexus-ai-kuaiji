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
  /**
   * ``unprovided`` means no contract data was supplied: stamp duty is *unknown*,
   * not zero. ``zero_confirmed`` means a human confirmed there are no taxable
   * contracts this period.
   */
  stamp_duty_status?: 'computed' | 'unprovided' | 'zero_confirmed' | string;
  stamp_duty_notice?: string;
  notices?: string[];
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
  stamp_duty_status?: string;
  stamp_duty_notice?: string;
  notices?: string[];
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

/**
 * 「没给合同资料」和「确认没有合同」是两件事：前者印花税未知，后者才是 0。
 * 界面必须让会计看出这一点，否则一个空白的 0 元会被当成已经算过。
 */
export const stampDutyLabel = (status?: string, notice?: string) => {
  if (status === 'unprovided') return notice || '未提供合同资料，未计提印花税';
  if (status === 'zero_confirmed') return notice || '已确认本期无应税合同（零业务）';
  if (status === 'computed') return '印花税已按合同明细计提';
  return '';
};

/** 累计利润参考来源：服务端只返回可追溯的报表快照，没有来源时金额为 null。 */
export interface ProfitSource {
  suggested_profit_cents: number | null;
  source_kind: 'reviewed_report' | 'trial_report' | null;
  source_label: string | null;
  source_period: string | null;
  source_version: number | null;
  is_final_report: boolean;
  report_run_id: string | null;
  notice: string | null;
}

/** 累计利润的来源说明：必须让人看出这是哪一份报表、哪个期间、哪一版。 */
export const profitSourceLabel = (source: ProfitSource | null) => {
  if (!source) return '';
  if (source.suggested_profit_cents === null) return source.notice || '没有可用的利润表快照';
  const parts = [
    `来源：${source.source_label ?? '利润表快照'}`,
    `期间 ${source.source_period ?? '未知'}`,
    `版本 ${source.source_version ?? '未知'}`,
  ];
  if (!source.is_final_report) parts.push('正式报表尚未复核');
  return parts.join(' · ');
};

/**
 * 人工改过的累计利润必须留下原因；不改就不要求填，避免多一步无意义操作。
 */
export const needsAdjustReason = (cents: number | null, source: ProfitSource | null) =>
  source?.suggested_profit_cents !== null &&
  source?.suggested_profit_cents !== undefined &&
  cents !== source.suggested_profit_cents;

/** 分转成输入框里的元，仍走字符串拼接，不引入浮点乘法。 */
export const centsToYuanInput = (cents: number) => {
  const negative = cents < 0;
  const digits = String(Math.abs(cents)).padStart(3, '0');
  const text = `${digits.slice(0, -2)}.${digits.slice(-2)}`;
  return negative ? `-${text}` : text;
};

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
  if (!data) return null;
  const stampNote = stampDutyLabel(data.stamp_duty_status, data.stamp_duty_notice);
  if (data.entries.length === 0)
    return (
      <>
        <p className="tax-accrual-empty">本期没有可计提的税金，或底稿尚未复核。</p>
        {stampNote ? (
          <p className="tax-accrual-note" data-stamp-status={data.stamp_duty_status}>
            {stampNote}。
          </p>
        ) : null}
      </>
    );
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
      {stampNote ? (
        <p className="tax-accrual-note" data-stamp-status={data.stamp_duty_status}>
          {stampNote}。
        </p>
      ) : null}
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
      {stampDutyLabel(outcome.stamp_duty_status, outcome.stamp_duty_notice) ? (
        <p className="tax-accrual-note" data-stamp-status={outcome.stamp_duty_status}>
          {stampDutyLabel(outcome.stamp_duty_status, outcome.stamp_duty_notice)}。
        </p>
      ) : null}
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
  const [source, setSource] = useState<ProfitSource | null>(null);
  const [adjustReason, setAdjustReason] = useState('');
  const [stampZero, setStampZero] = useState(false);
  const [outcome, setOutcome] = useState<AccrualRequest | null>(null);
  const [requestError, setRequestError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const profitCents = yuanToCents(profit);
  const adjustRequired = needsAdjustReason(profitCents, source);

  useEffect(() => {
    const controller = new AbortController();
    // 切换账套或期间时必须清掉旧账套的累计利润与申请结果，防止跨账套串数。
    setProfit('');
    setSource(null);
    setAdjustReason('');
    setStampZero(false);
    setOutcome(null);
    setRequestError('');
    api<ProfitSource>(
      `/tax/books/${encodeURIComponent(bookId)}/cumulative-profit?period=${encodeURIComponent(period)}`,
      session,
      { signal: controller.signal },
    )
      .then((raw) => {
        if (controller.signal.aborted) return;
        setSource(raw);
        // 有可追溯来源就预填；用户已经输入过则不动，避免覆盖手工值。
        if (raw.suggested_profit_cents !== null) {
          setProfit((current) =>
            current.trim() ? current : centsToYuanInput(raw.suggested_profit_cents as number),
          );
        }
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        // 拿不到参考值不是失败：输入框保持为空，由会计人工填写并说明原因。
        setSource(null);
      });
    return () => controller.abort();
  }, [session, bookId, period]);

  const load = useCallback(
    (cents: number | null, signal?: AbortSignal) => {
      const body = {
        book_id: bookId,
        period,
        cumulative_profit_cents: cents,
        stamp_zero_confirmed: stampZero,
      };
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
    [session, bookId, period, stampZero],
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
  }, [load, profitCents, stampZero]);

  const requestVoucher = useCallback(() => {
    if (profitCents === null) return;
    setSubmitting(true);
    setRequestError('');
    api<AccrualRequest>('/tax/accrual-manual-request', session, {
      method: 'POST',
      body: {
        book_id: bookId,
        period,
        cumulative_profit_cents: profitCents,
        stamp_zero_confirmed: stampZero,
        reason: adjustReason.trim() || undefined,
      },
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
  }, [session, bookId, period, profitCents, stampZero, adjustReason]);

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
    !loading &&
    !submitting &&
    !blocked &&
    profitCents !== null &&
    Boolean(data?.entries.length) &&
    (!adjustRequired || adjustReason.trim().length >= 4);

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
      {source ? (
        <p className="tax-accrual-note" data-profit-source={source.source_kind ?? 'none'}>
          {profitSourceLabel(source)}
        </p>
      ) : null}
      {adjustRequired ? (
        <p>
          <label>
            人工调整原因
            <input
              aria-label="人工调整原因"
              maxLength={500}
              value={adjustReason}
              onChange={(event) => setAdjustReason(event.target.value)}
            />
          </label>
          <span className="tax-accrual-note">
            人工改过的累计利润必须说明依据（如：补记未入账收入、剔除非本期损益），会随计提申请留痕。
          </span>
        </p>
      ) : null}
      <p>
        <label>
          <input
            aria-label="已核实本期无应税合同（确认零业务）"
            checked={stampZero}
            type="checkbox"
            onChange={(event) => setStampZero(event.target.checked)}
          />
          已核实本期无应税合同（确认零业务）
        </label>
        <span className="tax-accrual-note" data-stamp-hint={stampZero ? 'zero' : 'unprovided'}>
          {stampZero
            ? '已确认本期无应税合同：印花税按 0 元计提，并会在回执中留痕。'
            : '未提供合同资料，未计提印花税；勾选此项前请先确认本期确实没有应税合同。'}
        </span>
      </p>
      <AccrualPreviewView data={data} blocked={blocked} />
      <AccrualRequestView outcome={outcome} error={requestError} />
    </section>
  );
}
