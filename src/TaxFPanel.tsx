import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import type { Session } from './types';
import { centsToApiString } from './money';
import { FleetMetricsPanel } from './FleetMetricsPanel';
import { InlineReviewDrawer } from './InlineReviewDrawer';
import './tax-f.css';

type LeasePlan = {
  period: string;
  kind: string;
  amount_cents: number;
  status: string;
  draft_id: string | null;
  advance_cents: number;
};
type LeaseConfirmation = { kind: string; period: string; amount_cents: number; reference: string };
type LeaseEvent = {
  kind: string;
  period: string;
  amount_cents: number;
  reason: string;
  created_at: string;
};
type InvoiceRed = {
  original_reference: string;
  red_reference: string;
  period: string;
  amount_cents: number;
  reason: string;
};
type Lease = {
  id: string;
  customer_name: string;
  vehicle_external_id: string;
  start_period: string;
  end_period: string;
  monthly_cents: number;
  status: string;
  version: number;
  approved_version: number;
  stale_approval: boolean;
  plan: LeasePlan[];
  confirmations: LeaseConfirmation[];
  events: LeaseEvent[];
  invoice_reds: InvoiceRed[];
};
type Basis = {
  id: string;
  period: string;
  status: string;
  version: number;
  base_cents: number;
  tax_cents: number;
  adjustments: { code: string; amount_cents: number; reason: string }[];
  manual_evidence: string;
  policy: { version: string; taxpayer_type: string; rate_bp: number; surtax_bp: number };
};
type ProfitLine = {
  line_code: string;
  line_name: string;
  line_kind: string;
  amount_cents: number;
  item_count: number;
};
type ProfitItem = {
  line_code: string;
  voucher_no: string | null;
  account_code: string;
  account_name: string;
  net_cents: number;
  evidence_ref: string;
};
type Profit = {
  period: string;
  mapping_version: number;
  lines: ProfitLine[];
  items: ProfitItem[];
  totals: {
    revenue_cents: number;
    cost_and_expense_cents: number;
    operating_profit_cents: number;
    nonoperating_cents: number;
    income_tax_cents: number | null;
    net_profit_cents: number | null;
  };
  checks: { unmapped_count: number; mixed_voucher_count: number; net_profit_known: boolean };
};

const money = (cents: number | null) =>
  cents === null
    ? '未知'
    : `¥${(Math.abs(cents) / 100).toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
const statusLabel: Record<string, string> = {
  active: '有效',
  planned: '待确认',
  recognized: '已确认',
  cancelled: '已取消',
  draft: '待复核',
  reviewed: '已复核',
  invalidated: '已失效',
};

const LEASE_EVENT_LABELS: Record<string, string> = {
  business: '业务确认',
  receipt: '收款确认',
  invoice: '发票确认',
  recognize: '确认收入',
  refund: '退款',
  amend: '合同改版',
  approve: '版本确认',
  terminate: '终止',
};
function eventLabel(kind: string) {
  return LEASE_EVENT_LABELS[kind] ?? kind;
}

export function TaxFPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const leasesUrl = `/tax/books/${encodeURIComponent(bookId)}/leases`;
  const basisUrl = `/tax/books/${encodeURIComponent(bookId)}/tax-basis`;
  const [leases, setLeases] = useState<Lease[]>([]);
  const [selectedLease, setSelectedLease] = useState('');
  const [basis, setBasis] = useState<Basis[]>([]);
  const [profit, setProfit] = useState<Profit | null>(null);
  const [baselineBook, setBaselineBook] = useState('');
  const [variance, setVariance] = useState<{
    current_period: string;
    baseline_period: string;
    lines: {
      line_code: string;
      line_name: string;
      current_cents: number;
      baseline_cents: number;
      delta_cents: number;
    }[];
    note: string;
  } | null>(null);
  const [varianceError, setVarianceError] = useState('');
  const [selectedLine, setSelectedLine] = useState('');
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [reviewDraft, setReviewDraft] = useState('');
  const [exportingLease, setExportingLease] = useState<string | null>(null);

  const [customerName, setCustomerName] = useState('虚构租赁客户');
  const [vehicle, setVehicle] = useState('V-LEASE-UI');
  const [startPeriod, setStartPeriod] = useState(period);
  const [endPeriod, setEndPeriod] = useState(period);
  const [monthly, setMonthly] = useState('1000.00');
  const [invoiceRef, setInvoiceRef] = useState('');
  const [redRef, setRedRef] = useState('');
  const [redReason, setRedReason] = useState('客户退租，原发票全额红冲');
  const [advanceAccount, setAdvanceAccount] = useState('2203');
  const [base, setBase] = useState('100000.00');
  const [rateBp, setRateBp] = useState('300');
  const [surtaxBp, setSurtaxBp] = useState('200');
  const [manualEvidence, setManualEvidence] = useState('客户申报表与完税凭证复印件，专员人工核定');

  const current = leases.find((row) => row.id === selectedLease) ?? null;

  async function load() {
    setFailure('');
    try {
      const [leaseData, basisData, profitData] = await Promise.all([
        api<{ items: Lease[] }>(leasesUrl, session),
        api<{ items: Basis[] }>(`${basisUrl}?period=${encodeURIComponent(period)}`, session),
        api<Profit>(
          `/reports/profit?book_id=${encodeURIComponent(bookId)}&period=${encodeURIComponent(period)}`,
          session,
        ),
      ]);
      setLeases(leaseData.items);
      setSelectedLease((value) => value || leaseData.items[0]?.id || '');
      setBasis(basisData.items);
      setProfit(profitData);
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '租赁、税费与利润数据读取失败。');
    }
  }
  useEffect(() => {
    void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [bookId, period, session]);

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await action();
      setNotice(success);
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '操作失败，请重新核对后再试。');
    } finally {
      setBusy(false);
    }
  }

  async function createLease() {
    await run(async () => {
      const created = await api<Lease>(leasesUrl, session, {
        method: 'POST',
        body: {
          customer_name: customerName,
          customer_external_id: `UI-${customerName}`,
          vehicle_external_id: vehicle,
          start_period: startPeriod,
          end_period: endPeriod,
          monthly,
        },
      });
      setSelectedLease(created.id);
      setLeases((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      void load();
    }, '租赁合同已登记，收款与收入分别确认后才生成凭证。');
  }

  async function redInvoice() {
    if (!current) return;
    const invoice = current.confirmations.find((row) => row.kind === 'invoice');
    if (!invoice) {
      setFailure('请先确认本期发票。');
      return;
    }
    if (!redRef.trim()) {
      setFailure('请填写红字发票号。');
      return;
    }
    await run(async () => {
      await api(`${leasesUrl}/${encodeURIComponent(current.id)}/invoice-reds`, session, {
        method: 'POST',
        body: {
          original_reference: invoice.reference,
          red_reference: redRef.trim(),
          period: invoice.period,
          amount: centsToApiString(BigInt(invoice.amount_cents)),
          reason: redReason.trim(),
        },
      });
      setRedRef('');
      await load();
    }, '发票红冲事实已登记；原发票事实保留，不自动冲减收入。');
  }

  async function confirmLease(kind: 'business' | 'receipt' | 'invoice') {
    if (!current) return;
    if (kind === 'invoice' && !invoiceRef.trim()) {
      setFailure('请填写发票号后再确认发票。');
      return;
    }
    await run(
      async () => {
        await api(`${leasesUrl}/${encodeURIComponent(current.id)}/confirm`, session, {
          method: 'POST',
          body: {
            kind,
            period,
            amount: monthly,
            reference:
              kind === 'invoice'
                ? invoiceRef.trim()
                : `${kind === 'receipt' ? '收款' : '业务'}-${period}`,
          },
        });
        await load();
      },
      kind === 'receipt'
        ? '收款事实已登记。'
        : kind === 'invoice'
          ? '发票事实已登记。'
          : '业务确认已登记。',
    );
  }

  async function recognizeLease() {
    if (!current) return;
    await run(async () => {
      await api(`${leasesUrl}/${encodeURIComponent(current.id)}/recognize`, session, {
        method: 'POST',
        body: {
          period,
          policy: {
            version: 'lease-ui-v1',
            revenue_account: '6001',
            advance_account: advanceAccount,
            receivable_account: '1122',
            bank_account: '1002',
            refund_account: '6001',
          },
          reason: '按合同、业务确认和收款事实确认当期租赁收入',
        },
      });
      await load();
    }, '当期租赁收入已生成草稿；复核后导出。');
  }

  async function generateBasis() {
    await run(async () => {
      await api(basisUrl, session, {
        method: 'POST',
        body: {
          period,
          policy: {
            version: 'tax-ui-v1',
            taxpayer_type: 'small_scale',
            rate_bp: Number(rateBp),
            surtax_bp: Number(surtaxBp),
            effective_from: '2026-01',
            authority: '用户当前选择的虚构税务口径',
            source_ref: '客户确认-2026',
          },
          base,
          adjustments: [],
          manual: true,
          manual_evidence: manualEvidence,
          source_refs: [],
          workpaper_id: '',
        },
      });
      await load();
    }, '税费核定草稿已生成，需另一名有复核权限的会计复核。');
  }

  async function reviewBasis(row: Basis) {
    await run(async () => {
      await api(`${basisUrl}/${encodeURIComponent(row.id)}/review`, session, {
        method: 'POST',
        body: {
          expected_version: row.version,
          reason: '独立核对政策、基数、调整与人工依据',
        },
      });
      await load();
    }, '税费核定已独立复核。');
  }

  const drillItems = selectedLine
    ? (profit?.items.filter((row) => row.line_code === selectedLine) ?? [])
    : [];

  async function approveLease() {
    if (!current) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(`${leasesUrl}/${encodeURIComponent(current.id)}/approve`, session, {
        method: 'POST',
        body: { expected_version: current.version },
      });
      await load();
      setNotice('租赁合同当前版本已确认。');
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : '合同版本确认失败');
    } finally {
      setBusy(false);
    }
  }

  async function exportLease(draftId: string) {
    if (!session || exportingLease) return;
    setExportingLease(draftId);
    setNotice('');
    try {
      const result = await api<{ csv: string; filename: string }>(
        `/books/${bookId}/exports`,
        session,
        {
          method: 'POST',
          body: { draft_ids: [draftId], idempotency_key: `lease-export-${draftId}` },
        },
      );
      const url = URL.createObjectURL(new Blob([result.csv], { type: 'text/csv;charset=utf-8' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice('租赁凭证已导出。导出后仍需按台账版本留档。');
      await load();
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : '租赁凭证导出失败');
    } finally {
      setExportingLease(null);
    }
  }

  return (
    <section className="tax-f-panel" aria-label="租赁、税费与利润下钻">
      <header className="tax-f-head">
        <div>
          <p className="tax-f-eyebrow">TAX13–15 · 合同、计税与利润</p>
          <h3>租赁、税费与利润下钻</h3>
          <p>
            业务确认、收款、发票和收入确认分开保存；税费政策、基数与人工核定留痕；利润按凭证行下钻。
          </p>
        </div>
        <button type="button" className="tax-f-ghost" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>

      {notice ? (
        <p className="tax-f-notice" role="status">
          {notice}
        </p>
      ) : null}
      {failure ? (
        <p className="tax-f-failure" role="alert">
          {failure}
        </p>
      ) : null}

      <div className="tax-f-grid">
        <article className="tax-f-card">
          <h4>01 租赁合同与收入计划</h4>
          <div className="tax-f-fields">
            <label>
              客户
              <input
                value={customerName}
                onChange={(event) => setCustomerName(event.target.value)}
              />
            </label>
            <label>
              车辆外部编号
              <input value={vehicle} onChange={(event) => setVehicle(event.target.value)} />
            </label>
            <label>
              开始月
              <input
                type="month"
                value={startPeriod}
                onChange={(event) => setStartPeriod(event.target.value)}
              />
            </label>
            <label>
              结束月
              <input
                type="month"
                value={endPeriod}
                onChange={(event) => setEndPeriod(event.target.value)}
              />
            </label>
            <label>
              月租
              <input
                value={monthly}
                onChange={(event) => setMonthly(event.target.value)}
                inputMode="decimal"
              />
            </label>
            <label>
              预收科目
              <input
                value={advanceAccount}
                onChange={(event) => setAdvanceAccount(event.target.value)}
              />
            </label>
          </div>
          <button type="button" onClick={() => void createLease()} disabled={busy}>
            登记租赁合同
          </button>

          {leases.length ? (
            <div className="tax-f-list" aria-label="租赁合同列表">
              {leases.map((row) => (
                <button
                  type="button"
                  key={row.id}
                  className={row.id === selectedLease ? 'active' : ''}
                  onClick={() => setSelectedLease(row.id)}
                >
                  <strong>{row.customer_name}</strong>
                  <span>
                    {row.start_period}–{row.end_period} · v{row.version} ·{' '}
                    {statusLabel[row.status] ?? row.status}
                  </span>
                  <em>{money(row.monthly_cents)}/月</em>
                </button>
              ))}
            </div>
          ) : (
            <p className="tax-f-empty">尚未登记租赁合同。</p>
          )}

          {current ? (
            <div className="tax-f-detail">
              {current.stale_approval ? (
                <div className="tax-f-stale" role="alert">
                  <p>合同已改版，当前版本 v{current.version} 尚未确认；确认前不能继续确认收入。</p>
                  <button type="button" onClick={() => void approveLease()} disabled={busy}>
                    确认当前版本
                  </button>
                </div>
              ) : null}
              <div className="tax-f-actions">
                <button type="button" onClick={() => void confirmLease('business')} disabled={busy}>
                  确认本期业务
                </button>
                <button type="button" onClick={() => void confirmLease('receipt')} disabled={busy}>
                  确认本期收款
                </button>
                <button type="button" onClick={() => void recognizeLease()} disabled={busy}>
                  确认本期收入
                </button>
              </div>
              <div className="tax-f-invoice">
                <label>
                  发票号
                  <input
                    value={invoiceRef}
                    onChange={(event) => setInvoiceRef(event.target.value)}
                    placeholder="如 INV-2026-10-001"
                  />
                </label>
                <button type="button" onClick={() => void confirmLease('invoice')} disabled={busy}>
                  确认本期发票
                </button>
              </div>
              {current.confirmations.some((row) => row.kind === 'invoice') ? (
                <div className="tax-f-invoice">
                  <label>
                    红字发票号
                    <input
                      value={redRef}
                      onChange={(event) => setRedRef(event.target.value)}
                      placeholder="如 RED-2026-10-001"
                    />
                  </label>
                  <label>
                    红冲原因
                    <input
                      value={redReason}
                      onChange={(event) => setRedReason(event.target.value)}
                    />
                  </label>
                  <button type="button" onClick={() => void redInvoice()} disabled={busy}>
                    登记发票红冲
                  </button>
                </div>
              ) : null}
              <p className="tax-f-hint">
                已确认 {current.confirmations.length} 条事实；预收只在实际提供服务并确认后才转收入。
              </p>
              {current.confirmations.length ? (
                <ul className="tax-f-confirmations" aria-label="租赁确认事实">
                  {current.confirmations.map((row) => (
                    <li key={`${row.kind}-${row.period}-${row.reference}`}>
                      <span>
                        {row.kind === 'business'
                          ? '业务'
                          : row.kind === 'receipt'
                            ? '收款'
                            : '发票'}
                      </span>
                      <span>{row.period}</span>
                      <span>{money(row.amount_cents)}</span>
                      <span>{row.reference}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {current.invoice_reds.length ? (
                <ul className="tax-f-confirmations" aria-label="发票红冲事实">
                  {current.invoice_reds.map((row) => (
                    <li key={row.red_reference}>
                      <span>红冲</span>
                      <span>{row.period}</span>
                      <span>{money(row.amount_cents)}</span>
                      <span>
                        {row.original_reference} → {row.red_reference}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {current.events.length ? (
                <details className="tax-f-events">
                  <summary>合同版本记录 {current.events.length}</summary>
                  <ul>
                    {current.events.map((event) => (
                      <li key={`${event.kind}-${event.created_at}`}>
                        <strong>{eventLabel(event.kind)}</strong>
                        <span>{event.period || '—'}</span>
                        <span>{event.amount_cents ? money(event.amount_cents) : '—'}</span>
                        <span>{event.reason || '无说明'}</span>
                        <time>{event.created_at.slice(0, 16).replace('T', ' ')}</time>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <div className="tax-f-table-wrap">
                <table>
                  <caption>
                    {current.customer_name} · {current.vehicle_external_id}
                  </caption>
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th>计划</th>
                      <th>状态</th>
                      <th>已用预收</th>
                      <th>草稿</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.plan.map((row) => (
                      <tr key={`${row.period}-${row.kind}`}>
                        <td>{row.period}</td>
                        <td>{money(row.amount_cents)}</td>
                        <td>{statusLabel[row.status] ?? row.status}</td>
                        <td>{money(row.advance_cents)}</td>
                        <td>
                          {row.draft_id ? (
                            <div className="tax-f-inline">
                              <button
                                type="button"
                                className="tax-f-link"
                                onClick={() => setReviewDraft(row.draft_id!)}
                              >
                                复核租赁凭证
                              </button>
                              <button
                                type="button"
                                className="tax-f-link"
                                disabled={!!exportingLease || current.stale_approval}
                                title={
                                  current.stale_approval
                                    ? '合同已改版，旧审批版本不能导出'
                                    : undefined
                                }
                                onClick={() => void exportLease(row.draft_id!)}
                              >
                                {exportingLease === row.draft_id ? '导出中…' : '导出租赁凭证'}
                              </button>
                            </div>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </article>

        <article className="tax-f-card">
          <h4>02 税费政策、基数与人工核定</h4>
          <div className="tax-f-fields">
            <label>
              基数
              <input
                value={base}
                onChange={(event) => setBase(event.target.value)}
                inputMode="decimal"
              />
            </label>
            <label>
              税率（bp）
              <input
                value={rateBp}
                onChange={(event) => setRateBp(event.target.value)}
                inputMode="numeric"
              />
            </label>
            <label>
              附加（bp）
              <input
                value={surtaxBp}
                onChange={(event) => setSurtaxBp(event.target.value)}
                inputMode="numeric"
              />
            </label>
          </div>
          <label className="tax-f-wide">
            人工核定依据
            <textarea
              value={manualEvidence}
              onChange={(event) => setManualEvidence(event.target.value)}
              rows={2}
            />
          </label>
          <button type="button" onClick={() => void generateBasis()} disabled={busy}>
            生成税费核定草稿
          </button>
          <div className="tax-f-list" aria-label="税费核定列表">
            {basis.map((row) => (
              <div className="tax-f-basis" key={row.id}>
                <div>
                  <strong>
                    {row.period} · v{row.version} · {statusLabel[row.status] ?? row.status}
                  </strong>
                  <span>
                    基数 {money(row.base_cents)} · 税额 {money(row.tax_cents)}
                  </span>
                  <small>
                    {row.policy.version} · {row.policy.rate_bp}bp + {row.policy.surtax_bp}bp ·{' '}
                    {row.manual_evidence}
                  </small>
                </div>
                {row.status === 'draft' ? (
                  <button type="button" onClick={() => void reviewBasis(row)} disabled={busy}>
                    独立复核
                  </button>
                ) : null}
              </div>
            ))}
            {!basis.length ? <p className="tax-f-empty">本期尚未核定税费。</p> : null}
          </div>
        </article>
      </div>

      <article className="tax-f-card tax-f-profit" aria-label="确定性账簿偏差">
        <h4>04 确定性偏差归因</h4>
        <p>只对比同一企业、同一事务所的另一账套；展示凭证/来源差异，不推断原因。</p>
        <div className="tax-f-actions">
          <input
            aria-label="对比账套 ID"
            placeholder="对比账套 ID"
            value={baselineBook}
            onChange={(event) => setBaselineBook(event.target.value)}
          />
          <button
            type="button"
            className="secondary"
            disabled={!baselineBook.trim()}
            onClick={async () => {
              setVarianceError('');
              try {
                const data = await api<{
                  current_period: string;
                  baseline_period: string;
                  lines: {
                    line_code: string;
                    line_name: string;
                    current_cents: number;
                    baseline_cents: number;
                    delta_cents: number;
                  }[];
                  note: string;
                }>(
                  `/reports/profit/variance?book_id=${encodeURIComponent(bookId)}&baseline_book_id=${encodeURIComponent(baselineBook.trim())}`,
                  session,
                );
                setVariance(data);
              } catch (error) {
                setVariance(null);
                setVarianceError(error instanceof ApiError ? error.message : '偏差读取失败');
              }
            }}
          >
            对比账簿
          </button>
        </div>
        {varianceError ? (
          <p className="inline-error" role="alert">
            {varianceError}
          </p>
        ) : null}
        {variance ? (
          <div className="tax-f-table-wrap" aria-label="偏差行">
            <p>
              {variance.current_period} 对比 {variance.baseline_period}
            </p>
            <table>
              <thead>
                <tr>
                  <th>项目</th>
                  <th>当前</th>
                  <th>对比</th>
                  <th>差异</th>
                </tr>
              </thead>
              <tbody>
                {variance.lines.slice(0, 12).map((row) => (
                  <tr key={row.line_code}>
                    <td>{row.line_name}</td>
                    <td>{money(row.current_cents)}</td>
                    <td>{money(row.baseline_cents)}</td>
                    <td>{money(row.delta_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="tax-f-note">{variance.note}</p>
          </div>
        ) : null}
      </article>

      <article className="tax-f-card tax-f-profit">
        <h4>03 利润项目与凭证行下钻</h4>
        {profit ? (
          <>
            <div className="tax-f-totals">
              <span>
                营业收入 <strong>{money(profit.totals.revenue_cents)}</strong>
              </span>
              <span>
                成本费用 <strong>{money(profit.totals.cost_and_expense_cents)}</strong>
              </span>
              <span>
                营业利润 <strong>{money(profit.totals.operating_profit_cents)}</strong>
              </span>
              <span>
                所得税 <strong>{money(profit.totals.income_tax_cents)}</strong>
              </span>
              <span>
                净利润 <strong>{money(profit.totals.net_profit_cents)}</strong>
              </span>
              <span>
                映射 <strong>v{profit.mapping_version}</strong>
              </span>
            </div>
            <div className="tax-f-profit-grid">
              <div className="tax-f-table-wrap">
                <table>
                  <caption>利润项目（{profit.period}）</caption>
                  <thead>
                    <tr>
                      <th>项目</th>
                      <th>金额</th>
                      <th>凭证行</th>
                      <th>下钻</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profit.lines.map((row) => (
                      <tr key={row.line_code}>
                        <td>{row.line_name}</td>
                        <td>{money(row.amount_cents)}</td>
                        <td>{row.item_count}</td>
                        <td>
                          <button type="button" onClick={() => setSelectedLine(row.line_code)}>
                            查看依据
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="tax-f-table-wrap" aria-label="利润下钻凭证行">
                <table>
                  <caption>
                    {selectedLine ? `下钻：${selectedLine}` : '选择左侧项目查看凭证行'}
                  </caption>
                  <thead>
                    <tr>
                      <th>凭证</th>
                      <th>科目</th>
                      <th>净额</th>
                      <th>来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drillItems.map((row) => (
                      <tr key={`${row.voucher_no}-${row.account_code}-${row.net_cents}`}>
                        <td>{row.voucher_no ?? '—'}</td>
                        <td>
                          {row.account_code} {row.account_name}
                        </td>
                        <td>{money(row.net_cents)}</td>
                        <td>{row.evidence_ref || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <p className="tax-f-hint" role="note">
              未映射 {profit.checks.unmapped_count} 行；混合凭证 {profit.checks.mixed_voucher_count}{' '}
              张； 所得税和净利润未知时显示“未知”，不补零。年累计尚不完整。
            </p>
          </>
        ) : (
          <p className="tax-f-empty">利润数据读取中。</p>
        )}
      </article>
      <FleetMetricsPanel session={session} bookId={bookId} period={period} />
      {reviewDraft ? (
        <InlineReviewDrawer
          session={session}
          bookId={bookId}
          draftId={reviewDraft}
          onClose={() => setReviewDraft('')}
          onChanged={() => void load()}
        />
      ) : null}
    </section>
  );
}
