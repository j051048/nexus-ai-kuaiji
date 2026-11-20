import { Fragment, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { Session } from './types';
import { centsToApiString, parseYuanToCents } from './money';
import './tax-fee.css';

type Source = {
  id: string;
  book_id: string;
  version: number;
  status: string;
  filename: string;
  source_system: string;
  external_id: string;
};
type Row = {
  id: string;
  source_row_number: number;
  external_id: string;
  driver_id: string | null;
  vehicle_id: string | null;
  gross_cents: number;
  discount_cents: number;
  adjustment_cents: number;
  net_cents: number;
  status: string;
  reason: string;
  line_ordinal: number | null;
};
type Group = { id: string; name: string; row_count: number; net_cents: number };
type Receivable = {
  receivable_id: string;
  batch_id: string;
  period: string;
  source_row_number: number;
  external_id: string;
  driver_id: string | null;
  driver_name: string;
  vehicle_name: string;
  amount_cents: number;
  settlement_status: string;
};
type BankSource = {
  source_id: string;
  date: string;
  description: string;
  counterparty: string;
  amount_cents: number;
  remaining_cents: number;
  draft_version: number;
};
type Allocation = {
  allocation_id: string;
  receivable_id: string;
  driver_name: string;
  period: string;
  amount_cents: number;
  payer_name: string | null;
  collection_basis: string | null;
  created_at: string;
  reversed_at: string | null;
  reversal_reason: string | null;
  receivable_status: string;
};
type Refund = {
  refund_id: string;
  receivable_id: string;
  driver_name: string;
  amount_cents: number;
  reason: string;
  created_at: string;
  receivable_status: string;
};
type Batch = {
  id: string;
  book_id: string;
  period: string;
  status: string;
  version: number;
  draft_id: string;
  total_cents: number;
  row_count: number;
  skipped_count: number;
  policy: { version: string; driver_receivable_account: string; vehicle_revenue_account: string };
  policy_fingerprint: string;
  source_fingerprint: string;
  batch_fingerprint: string;
  stale: boolean;
  stale_reason: string;
  source_files: {
    id: string;
    filename: string;
    external_id: string;
    version: number;
    sha256: string;
  }[];
  rows: Row[];
  exceptions: Row[];
  by_driver: Group[];
  by_vehicle: Group[];
};
const money = (cents: number) =>
  `${cents < 0 ? '−' : ''}¥${(Math.abs(cents) / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const labels: Record<string, string> = {
  adopted: '已采用',
  zero: '零额',
  rejected: '拒绝',
  draft: '待复核',
  reviewed: '已复核',
  invalidated: '已失效',
};

type Props = { session: Session; bookId: string; period: string };
export function TaxFeeBatchPanel(props: Props) {
  return (
    <ScopedTaxFeeBatchPanel
      key={`${props.session.scope_key}:${props.session.user_id}:${props.bookId}:${props.period}`}
      {...props}
    />
  );
}

function ScopedTaxFeeBatchPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const url = `/tax/books/${encodeURIComponent(bookId)}/fee-batches`;
  const [sources, setSources] = useState<Source[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [receivables, setReceivables] = useState<Receivable[]>([]);
  const [banks, setBanks] = useState<BankSource[]>([]);
  const [refundBanks, setRefundBanks] = useState<BankSource[]>([]);
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  const [refunds, setRefunds] = useState<Refund[]>([]);
  const [refunding, setRefunding] = useState<string | null>(null);
  const [refundBank, setRefundBank] = useState('');
  const [refundAmount, setRefundAmount] = useState('');
  const [refundReason, setRefundReason] = useState('');
  const [reversing, setReversing] = useState<string | null>(null);
  const [reverseReason, setReverseReason] = useState('');
  const [allocating, setAllocating] = useState<string | null>(null);
  const [bankChoice, setBankChoice] = useState('');
  const [allocAmount, setAllocAmount] = useState('');
  const [allocPayer, setAllocPayer] = useState('');
  const [allocBasis, setAllocBasis] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [policyVersion, setPolicyVersion] = useState('fee-v1');
  const [receivable, setReceivable] = useState('1122');
  const [revenue, setRevenue] = useState('6001');
  const [reason, setReason] = useState('独立复核规费批次');
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const revision = useRef(0);
  function checked(data: Batch) {
    if (data.book_id !== bookId || data.period !== period)
      throw new ApiError('返回的批次范围不一致，请刷新后核对。', 409);
    return data;
  }

  async function load() {
    const current = ++revision.current;
    setFailure('');
    try {
      const [
        sourceData,
        batchData,
        receivableData,
        bankData,
        refundBankData,
        allocationData,
        refundData,
      ] = await Promise.all([
        api<{ items: Source[] }>(`/tax/books/${encodeURIComponent(bookId)}/sources`, session),
        api<{ items: Batch[] }>(url, session),
        api<{ items: Receivable[] }>(
          `/tax/books/${encodeURIComponent(bookId)}/fee-receivables?period=${encodeURIComponent(period)}`,
          session,
        ),
        api<{ items: BankSource[] }>(
          `/tax/books/${encodeURIComponent(bookId)}/fee-bank-sources?period=${encodeURIComponent(period)}`,
          session,
        ),
        api<{ items: BankSource[] }>(
          `/tax/books/${encodeURIComponent(bookId)}/fee-bank-sources?period=${encodeURIComponent(period)}&direction=expense`,
          session,
        ),
        api<{ items: Allocation[] }>(
          `/tax/books/${encodeURIComponent(bookId)}/fee-allocations?period=${encodeURIComponent(period)}`,
          session,
        ),
        api<{ items: Refund[] }>(
          `/tax/books/${encodeURIComponent(bookId)}/fee-refunds?period=${encodeURIComponent(period)}`,
          session,
        ),
      ]);
      if (current !== revision.current) return;
      if (sourceData.items.some((item) => item.book_id !== bookId))
        throw new ApiError('返回的来源范围不一致，请刷新后核对。', 409);
      batchData.items.forEach(checked);
      setSources(sourceData.items.filter((item) => item.status === 'active'));
      setBatches(batchData.items);
      setReceivables(receivableData.items);
      setBanks(bankData.items);
      setRefundBanks(refundBankData.items);
      setAllocations(allocationData.items);
      setRefunds(refundData.items);
      setBatch((previous) =>
        previous ? (batchData.items.find((item) => item.id === previous.id) ?? null) : null,
      );
    } catch (error) {
      if (current !== revision.current) return;
      setSources([]);
      setBatches([]);
      setReceivables([]);
      setBanks([]);
      setRefundBanks([]);
      setAllocations([]);
      setRefunds([]);
      setSelected([]);
      setBatch(null);
      setNotice('');
      setFailure(error instanceof ApiError ? error.message : '规费批次读取失败。');
    }
  }
  useEffect(() => {
    void load();
    return () => {
      revision.current++;
    };
  }, [bookId, session]);

  async function allocate(item: Receivable) {
    const bank = banks.find((row) => row.source_id === bankChoice);
    const parsed = parseYuanToCents(allocAmount);
    const amount = parsed === null ? 0 : Number(parsed);
    if (!bank || amount <= 0) {
      setFailure('请选择银行到账并填写大于 0 的核销金额。');
      return;
    }
    if (amount > bank.remaining_cents || amount > item.amount_cents) {
      setFailure('核销金额不能超过银行到账余额或驾驶员应收余额。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(
        `/tax/books/${encodeURIComponent(bookId)}/fee-receivables/${encodeURIComponent(item.receivable_id)}/allocations`,
        session,
        {
          method: 'POST',
          body: {
            bank_source_id: bank.source_id,
            amount_cents: amount,
            idempotency_key: `fee-alloc-${item.receivable_id}-${bank.source_id}-${amount}-${bank.draft_version}`,
            payer_name: allocPayer || null,
            collection_basis: allocBasis || null,
          },
        },
      );
      setNotice(`已登记 ${item.driver_name} 的到账核销 ${money(amount)}；银行凭证仍需独立复核。`);
      setAllocating(null);
      setBankChoice('');
      setAllocAmount('');
      setAllocPayer('');
      setAllocBasis('');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '核销登记失败。');
    } finally {
      setBusy(false);
    }
  }

  async function reverseAllocation(row: Allocation) {
    const reason = reverseReason.trim();
    if (reason.length < 3) {
      setFailure('撤销原因至少 3 个字。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(
        `/tax/books/${encodeURIComponent(bookId)}/fee-allocations/${encodeURIComponent(row.allocation_id)}/reverse`,
        session,
        {
          method: 'POST',
          body: { reason },
        },
      );
      setNotice(
        `已撤销 ${row.driver_name} 的到账核销 ${money(row.amount_cents)}；原记录保留，应收余额已恢复。`,
      );
      setReversing(null);
      setReverseReason('');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '撤销核销失败。');
    } finally {
      setBusy(false);
    }
  }

  async function refund(item: Receivable) {
    const bank = refundBanks.find((row) => row.source_id === refundBank);
    const parsed = parseYuanToCents(refundAmount);
    const amount = parsed === null ? 0 : Number(parsed);
    const reason = refundReason.trim();
    if (!bank || amount <= 0) {
      setFailure('请选择银行付款并填写大于 0 的退款金额。');
      return;
    }
    if (amount > bank.remaining_cents) {
      setFailure('退款金额不能超过银行付款余额。');
      return;
    }
    if (reason.length < 3) {
      setFailure('退款原因至少 3 个字。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(
        `/tax/books/${encodeURIComponent(bookId)}/fee-receivables/${encodeURIComponent(item.receivable_id)}/refunds`,
        session,
        {
          method: 'POST',
          body: {
            bank_source_id: bank.source_id,
            amount_cents: amount,
            reason,
            idempotency_key: `fee-refund-${item.receivable_id}-${bank.source_id}-${amount}-${bank.draft_version}`,
          },
        },
      );
      setNotice(
        `已登记 ${item.driver_name} 的退款 ${money(amount)}；银行付款凭证仍需独立复核，应收余额已按净额重算。`,
      );
      setRefunding(null);
      setRefundBank('');
      setRefundAmount('');
      setRefundReason('');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '退款登记失败。');
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!selected.length) {
      setFailure('请选择至少一个已采用来源。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(url, session, {
        method: 'POST',
        body: {
          period,
          source_file_ids: selected,
          policy: {
            version: policyVersion,
            driver_receivable_account: receivable,
            vehicle_revenue_account: revenue,
          },
        },
      });
      setBatch(checked(data));
      setNotice(
        data.status === 'draft'
          ? '规费批次草稿已生成；须由另一名有复核权的会计独立复核。'
          : '已返回同一批次。',
      );
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '规费批次生成失败。',
      );
    } finally {
      setBusy(false);
    }
  }
  async function review() {
    if (!batch) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(`${url}/${encodeURIComponent(batch.id)}/review`, session, {
        method: 'POST',
        body: { expected_version: batch.version, reason },
      });
      setBatch(checked(data));
      setNotice('批次已独立复核；凭证版本已冻结。');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? `${error.message}（${error.status}）` : '复核失败。');
    } finally {
      setBusy(false);
    }
  }
  async function invalidate() {
    if (!batch) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(`${url}/${encodeURIComponent(batch.id)}/invalidate`, session, {
        method: 'POST',
        body: { expected_version: batch.version, reason: '来源或规则变化，停止继续使用' },
      });
      setBatch(checked(data));
      setNotice('批次已失效；历史记录保留，不能继续复核或导出。');
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '失效操作失败。',
      );
    } finally {
      setBusy(false);
    }
  }
  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }
  return (
    <section className="tax-fee-panel" aria-labelledby="tax-fee-title">
      <header>
        <div>
          <p className="tax-fee-kicker">TAX06 · 出租车规费</p>
          <h3 id="tax-fee-title">规费批次与独立复核</h3>
        </div>
        <button type="button" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>
      <p className="tax-fee-note">
        仅使用来源台账中“已采用”的行；驾驶员应收与单车收入分开，优惠和调整必须保留源行理由。生成草稿不代表已复核，银行到账也不在这里记收入。
      </p>
      <div className="tax-fee-form">
        <label>
          策略版本
          <input value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} />
        </label>
        <label>
          驾驶员应收科目
          <input value={receivable} onChange={(event) => setReceivable(event.target.value)} />
        </label>
        <label>
          单车收入科目
          <input value={revenue} onChange={(event) => setRevenue(event.target.value)} />
        </label>
      </div>
      <div className="tax-fee-sources">
        <strong>选择来源</strong>
        {sources.length === 0 ? (
          <span>当前账套没有活动来源，请先在来源台账导入并核定。</span>
        ) : (
          sources.map((source) => (
            <label key={source.id}>
              <input
                type="checkbox"
                checked={selected.includes(source.id)}
                onChange={() => toggle(source.id)}
              />{' '}
              {source.filename} · v{source.version} · {source.external_id}
            </label>
          ))
        )}
      </div>
      <button
        type="button"
        className="tax-fee-primary"
        disabled={busy}
        onClick={() => void generate()}
      >
        生成规费批次草稿
      </button>
      {(notice || failure) && (
        <div
          role={failure ? 'alert' : 'status'}
          className={failure ? 'tax-fee-error' : 'tax-fee-ok'}
        >
          {failure || notice}
        </div>
      )}
      {batch && (
        <div className="tax-fee-batch">
          <div className="tax-fee-summary">
            <div>
              <span>状态</span>
              <strong>{labels[batch.status] ?? batch.status}</strong>
            </div>
            <div>
              <span>批次金额</span>
              <strong>{money(batch.total_cents)}</strong>
            </div>
            <div>
              <span>采用行 / 跳过</span>
              <strong>
                {batch.row_count} / {batch.skipped_count}
              </strong>
            </div>
            <div>
              <span>版本</span>
              <strong>v{batch.version}</strong>
            </div>
          </div>
          <p className="tax-fee-fingerprint">
            策略 {batch.policy.version} · 批次指纹 {batch.batch_fingerprint.slice(0, 12)}… ·
            来源指纹 {batch.source_fingerprint.slice(0, 12)}…
          </p>
          <div className="tax-fee-receivables">
            <h4>驾驶员应收与到账核销</h4>
            <p>已复核规费按驾驶员拆分；可登记银行到账分配，银行凭证仍需独立复核。</p>
            {receivables.length ? (
              <div className="tax-fee-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>期间</th>
                      <th>驾驶员</th>
                      <th>车辆</th>
                      <th>来源行</th>
                      <th>金额</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {receivables.map((item) => (
                      <Fragment key={item.receivable_id}>
                        <tr>
                          <td>{item.period}</td>
                          <td>{item.driver_name}</td>
                          <td>{item.vehicle_name}</td>
                          <td>
                            {item.external_id} · L{item.source_row_number}
                          </td>
                          <td>{money(item.amount_cents)}</td>
                          <td>{item.settlement_status === 'settled' ? '已结清' : '待收'}</td>
                          <td>
                            <div className="tax-fee-row-actions">
                              {item.settlement_status === 'settled' ? null : (
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={() => {
                                    setAllocating(item.receivable_id);
                                    setRefunding(null);
                                    setBankChoice(banks[0]?.source_id ?? '');
                                    setAllocAmount(centsToApiString(BigInt(item.amount_cents)));
                                    setAllocPayer('');
                                    setAllocBasis('');
                                  }}
                                >
                                  登记到账核销
                                </button>
                              )}
                              <button
                                type="button"
                                className="secondary"
                                onClick={() => {
                                  setRefunding(item.receivable_id);
                                  setAllocating(null);
                                  setRefundBank(refundBanks[0]?.source_id ?? '');
                                  setRefundAmount('');
                                  setRefundReason('');
                                }}
                              >
                                登记退款
                              </button>
                            </div>
                          </td>
                        </tr>
                        {allocating === item.receivable_id && (
                          <tr>
                            <td colSpan={7}>
                              <div className="tax-fee-allocation">
                                <label>
                                  银行到账
                                  <select
                                    value={bankChoice}
                                    onChange={(event) => setBankChoice(event.target.value)}
                                  >
                                    {banks.map((bank) => (
                                      <option key={bank.source_id} value={bank.source_id}>
                                        {bank.date} · {bank.counterparty} · 余额{' '}
                                        {money(bank.remaining_cents)}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                                <label>
                                  核销金额
                                  <input
                                    inputMode="decimal"
                                    value={allocAmount}
                                    onChange={(event) => setAllocAmount(event.target.value)}
                                  />
                                </label>
                                <label>
                                  付款人
                                  <input
                                    value={allocPayer}
                                    onChange={(event) => setAllocPayer(event.target.value)}
                                    placeholder={
                                      banks.find((row) => row.source_id === bankChoice)
                                        ?.counterparty ?? '默认银行付款人'
                                    }
                                  />
                                </label>
                                <label>
                                  代收依据
                                  <input
                                    value={allocBasis}
                                    onChange={(event) => setAllocBasis(event.target.value)}
                                    placeholder="付款人非驾驶员时必填"
                                  />
                                </label>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void allocate(item)}
                                >
                                  确认核销
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={() => setAllocating(null)}
                                >
                                  取消
                                </button>
                                <span>
                                  只登记到账分配，不自动复核银行凭证；付款人非驾驶员时须有代收核定依据。
                                </span>
                              </div>
                            </td>
                          </tr>
                        )}
                        {refunding === item.receivable_id && (
                          <tr>
                            <td colSpan={7}>
                              <div className="tax-fee-allocation">
                                <label>
                                  银行付款
                                  <select
                                    value={refundBank}
                                    onChange={(event) => setRefundBank(event.target.value)}
                                  >
                                    {refundBanks.map((bank) => (
                                      <option key={bank.source_id} value={bank.source_id}>
                                        {bank.date} · {bank.counterparty} · 余额{' '}
                                        {money(bank.remaining_cents)}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                                <label>
                                  退款金额
                                  <input
                                    inputMode="decimal"
                                    value={refundAmount}
                                    onChange={(event) => setRefundAmount(event.target.value)}
                                    placeholder="退款不超过已核销净额"
                                  />
                                </label>
                                <label>
                                  退款原因
                                  <input
                                    value={refundReason}
                                    onChange={(event) => setRefundReason(event.target.value)}
                                    placeholder="至少 3 个字"
                                  />
                                </label>
                                <button
                                  type="button"
                                  disabled={busy || refundBanks.length === 0}
                                  onClick={() => void refund(item)}
                                >
                                  确认退款
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={() => setRefunding(null)}
                                >
                                  取消
                                </button>
                                <span>
                                  {refundBanks.length === 0
                                    ? '当前没有可用的银行付款流水。'
                                    : '只登记退款，不修改原收款记录；银行付款凭证仍需独立复核。'}
                                </span>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="tax-fee-empty">本期暂无已复核的驾驶员应收。</p>
            )}
          </div>
          {allocations.length > 0 && (
            <div className="tax-fee-receivables">
              <h4>到账核销记录</h4>
              <div className="tax-fee-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>驾驶员</th>
                      <th>付款人</th>
                      <th>代收依据</th>
                      <th>金额</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allocations.map((row) => (
                      <Fragment key={row.allocation_id}>
                        <tr>
                          <td>{row.created_at.slice(0, 10)}</td>
                          <td>{row.driver_name}</td>
                          <td>{row.payer_name || '—'}</td>
                          <td>{row.collection_basis || '—'}</td>
                          <td>{money(row.amount_cents)}</td>
                          <td>{row.reversed_at ? '已撤销' : '有效'}</td>
                          <td>
                            {row.reversed_at ? (
                              row.reversal_reason
                            ) : (
                              <button
                                type="button"
                                className="secondary"
                                onClick={() => {
                                  setReversing(row.allocation_id);
                                  setReverseReason('');
                                }}
                              >
                                撤销核销
                              </button>
                            )}
                          </td>
                        </tr>
                        {reversing === row.allocation_id && (
                          <tr>
                            <td colSpan={7}>
                              <div className="tax-fee-allocation">
                                <label>
                                  撤销原因
                                  <input
                                    value={reverseReason}
                                    onChange={(event) => setReverseReason(event.target.value)}
                                    placeholder="至少 3 个字"
                                  />
                                </label>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void reverseAllocation(row)}
                                >
                                  确认撤销
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={() => setReversing(null)}
                                >
                                  取消
                                </button>
                                <span>只撤销分配记录，不删除历史；银行凭证仍需独立复核。</span>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {refunds.length > 0 && (
            <div className="tax-fee-receivables">
              <h4>退款记录</h4>
              <p>退款与原收款分开保存；应收状态按“到账核销−退款”净额重算。</p>
              <div className="tax-fee-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>驾驶员</th>
                      <th>金额</th>
                      <th>原因</th>
                      <th>应收状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {refunds.map((row) => (
                      <tr key={row.refund_id}>
                        <td>{row.created_at.slice(0, 10)}</td>
                        <td>{row.driver_name}</td>
                        <td>{money(row.amount_cents)}</td>
                        <td>{row.reason}</td>
                        <td>{row.receivable_status === 'settled' ? '已结清' : '待收'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {batch.stale && (
            <div role="alert" className="tax-fee-error">
              {batch.stale_reason}
            </div>
          )}
          <p className="tax-fee-fingerprint">
            原件索引：
            {batch.source_files.map((file) => (
              <span key={file.id}>
                {file.filename} · v{file.version} · {file.external_id} · sha{' '}
                {file.sha256.slice(0, 12)}…{' '}
              </span>
            ))}
          </p>
          <div className="tax-fee-groups" aria-label="按驾驶员和车辆对平">
            <div>
              <strong>按驾驶员</strong>
              {batch.by_driver.map((item) => (
                <span key={item.id}>
                  {item.name || item.id.slice(0, 6)} · {item.row_count} 行 · {money(item.net_cents)}
                </span>
              ))}
            </div>
            <div>
              <strong>按车辆</strong>
              {batch.by_vehicle.map((item) => (
                <span key={item.id}>
                  {item.name || item.id.slice(0, 6)} · {item.row_count} 行 · {money(item.net_cents)}
                </span>
              ))}
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>来源行</th>
                <th>驾驶员/车辆</th>
                <th>毛额</th>
                <th>优惠</th>
                <th>调整</th>
                <th>净额</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {batch.rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.external_id} · {row.source_row_number}
                  </td>
                  <td>
                    {row.driver_id?.slice(0, 6) ?? '—'} / {row.vehicle_id?.slice(0, 6) ?? '—'}
                  </td>
                  <td>{money(row.gross_cents)}</td>
                  <td>{money(row.discount_cents)}</td>
                  <td>{money(row.adjustment_cents)}</td>
                  <td>{money(row.net_cents)}</td>
                  <td>
                    {labels[row.status] ?? row.status}
                    {row.reason ? ` · ${row.reason}` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {batch.status === 'draft' && (
            <div className="tax-fee-review">
              <label>
                复核理由
                <input value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
              <button type="button" disabled={busy} onClick={() => void review()}>
                独立复核并冻结
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void invalidate()}
              >
                标记失效
              </button>
            </div>
          )}
        </div>
      )}
      <details className="tax-fee-history">
        <summary>历史批次（{batches.length}）</summary>
        {batches.map((item) => (
          <button type="button" key={item.id} onClick={() => setBatch(item)}>
            {item.period} · v{item.version} · {labels[item.status] ?? item.status} ·{' '}
            {money(item.total_cents)}
          </button>
        ))}
      </details>
    </section>
  );
}
