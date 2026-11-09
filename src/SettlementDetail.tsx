import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { useScopedQuery } from './useScopedQuery';
import { agingKinds, parseSettlement } from './finance-operations';

const money = (n: number) => `${Math.trunc(n / 100)}.${String(n % 100).padStart(2, '0')}`;
export function SettlementDetail({
  session,
  bookId,
  companyId,
  period,
  itemId,
  onBack,
  onChanged,
  onVoucher,
}: {
  session: Session;
  bookId: string;
  companyId: string;
  period: string;
  itemId: string;
  onBack: () => void;
  onChanged: () => void;
  onVoucher?: (id: string) => void;
}) {
  const query = useScopedQuery(
    (signal) =>
      api<unknown>(
        `/subledger/items/${encodeURIComponent(itemId)}/workspace?book_id=${encodeURIComponent(bookId)}`,
        session,
        { signal },
      ).then((raw) =>
        parseSettlement(raw, { firmId: session.firm_id, bookId, companyId, period }, itemId),
      ),
    `${bookId}:${itemId}`,
  );
  const [voucher, setVoucher] = useState(''),
    [value, setValue] = useState(''),
    [reason, setReason] = useState('');
  const [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [confirm, setConfirm] = useState<string | null>(null);
  const active = useRef(true),
    sending = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const data = query.data;
  const cents = /^\d+(\.\d{1,2})?$/.test(value)
    ? Number(value.split('.')[0]) * 100 + Number((value.split('.')[1] ?? '').padEnd(2, '0'))
    : NaN;
  const selected = data?.vouchers.find((row) => row.id === voucher);
  const valid =
    Number.isSafeInteger(cents) &&
    cents > 0 &&
    cents <= (data?.item.remaining_cents ?? 0) &&
    cents <= (selected?.available_cents ?? 0) &&
    reason.trim().length >= 3;
  async function execute(reverse?: string) {
    if (
      !data ||
      !data.can_allocate ||
      sending.current ||
      uncertain ||
      query.loading ||
      (!reverse && !valid)
    )
      return;
    sending.current = true;
    setBusy(true);
    setConfirm(null);
    setNotice('');
    try {
      await api(
        reverse
          ? `/subledger/allocations/${encodeURIComponent(reverse)}/reverse`
          : '/subledger/allocations',
        session,
        {
          method: 'POST',
          body: {
            reason,
            idempotency_key: `settlement-ui-${crypto.randomUUID()}`,
            expected_fingerprint: data.fingerprint,
            ...(reverse
              ? {}
              : {
                  book_id: bookId,
                  voucher_id: voucher,
                  parts: [{ item_id: itemId, amount: value }],
                }),
          },
        },
      );
      if (active.current) {
        setNotice(reverse ? '已撤销核销，原记录和理由保留。' : '已登记核销；没有新增付款凭证。');
        setValue('');
        setVoucher('');
        setReason('');
        query.reload();
        onChanged();
      }
    } catch (error) {
      if (active.current) {
        setNotice(`${errorMessage(error)} 请核对最新记录，不会自动重发。`);
        setUncertain(true);
        query.reload();
      }
    } finally {
      sending.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <section aria-label="往来事项办理" className="finance-settlement">
      <button type="button" className="button secondary" onClick={onBack}>
        返回账龄（保留筛选）
      </button>
      <button
        type="button"
        className="button secondary"
        disabled={busy || query.loading}
        onClick={() => {
          setConfirm(null);
          query.reload();
        }}
      >
        重新读取往来记录
      </button>
      {query.loading && <p role="status">正在核对事项、正式凭证及核销历史…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {notice && <p role="status">{notice}</p>}
      {data && (
        <>
          <h3>
            {data.item.counterparty} · {agingKinds[data.item.kind]}
          </h3>
          <p>
            当前未结 {money(data.item.remaining_cents)} 元 · 原始金额{' '}
            {money(data.item.amount_cents)} 元 · 已核销 {money(data.item.allocated_cents)} 元
          </p>
          <p>
            发生日期 {data.item.original_date}。这里显示最新办理结果；返回后仍按原截止日期查看账龄。
          </p>
          <p>
            {data.source.verified
              ? '已匹配本账套来源凭证版本，请继续核对原始业务依据。'
              : '来源为人工登记或尚未绑定可验证原件，请先核对原始业务依据。'}
          </p>
          {data.locked && <p>本期已锁定，仅可查看；更正请从原凭证办理。</p>}
          {!data.can_allocate && !data.locked && <p>请交本账套有复核权限的会计办理核销。</p>}
          {uncertain && !query.loading && (
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setUncertain(false);
                setNotice('已重新读取，请核对余额和历史后选择下一步。');
              }}
            >
              已核对最新记录，继续人工办理
            </button>
          )}
          {data.can_allocate && (
            <fieldset disabled={busy || uncertain || query.loading}>
              <legend>用已复核、已记账的收付款凭证核销</legend>
              <label htmlFor="settlement-voucher">收付款凭证</label>
              <select
                id="settlement-voucher"
                value={voucher}
                onChange={(event) => {
                  setVoucher(event.target.value);
                  setConfirm(null);
                }}
              >
                <option value="">请选择本账套凭证</option>
                {data.vouchers.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.voucher_date} · {row.description} · 可用 {money(row.available_cents)} 元
                  </option>
                ))}
              </select>
              {!data.vouchers.length && (
                <p>没有可用结算金额。请先核对本账套已记账凭证的控制科目、辅助维度和付款方向。</p>
              )}
              {selected && onVoucher && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => onVoucher(selected.id)}
                >
                  查看所选正式凭证
                </button>
              )}
              <label>
                本次核销金额（元）
                <input
                  inputMode="decimal"
                  value={value}
                  onChange={(event) => {
                    setValue(event.target.value);
                    setConfirm(null);
                  }}
                />
              </label>
              <label>
                核销或撤销理由
                <input
                  maxLength={1000}
                  value={reason}
                  onChange={(event) => {
                    setReason(event.target.value);
                    setConfirm(null);
                  }}
                />
              </label>
              <button
                type="button"
                className="button"
                disabled={!valid}
                onClick={() => setConfirm('allocate')}
              >
                核对后登记核销
              </button>
            </fieldset>
          )}
          <h4>核销与撤销历史</h4>
          {!data.allocations.length && <p>尚无核销记录。</p>}
          {data.allocations.map((row) => (
            <article className="review-section" key={row.id}>
              <p>
                {row.allocated_on} · {money(row.amount_cents)} 元 ·{' '}
                {row.reversal_id ? '已撤销' : '已核销'}
              </p>
              <p>{row.reason}</p>
              {onVoucher && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => onVoucher(row.voucher_id)}
                >
                  查看本笔正式凭证
                </button>
              )}
              {row.reversal_reason && <p>撤销理由：{row.reversal_reason}</p>}
              {data.can_allocate && !row.reversal_id && (
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy || uncertain || query.loading || reason.trim().length < 3}
                  onClick={() => setConfirm(row.id)}
                >
                  撤销这笔核销
                </button>
              )}
            </article>
          ))}
          {confirm && (
            <div role="group" aria-label="确认往来办理">
              <p>
                {confirm === 'allocate'
                  ? `将登记 ${value} 元核销，请确认所选凭证及金额。`
                  : '将恢复这笔核销对应的未结余额，保留原核销与撤销理由。'}
              </p>
              <button
                type="button"
                className="button"
                disabled={busy || uncertain || query.loading || reason.trim().length < 3}
                onClick={() => void execute(confirm === 'allocate' ? undefined : confirm)}
              >
                确认办理
              </button>
              <button type="button" className="button secondary" onClick={() => setConfirm(null)}>
                取消
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
