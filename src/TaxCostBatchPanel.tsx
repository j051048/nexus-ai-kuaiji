import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import type { Session } from './types';
import './tax-cost.css';

type Kind = 'repair' | 'insurance' | 'oa';
type Source = {
  id: string;
  kind: Kind;
  source_key: string;
  source_version: number;
  period: string;
  subject_label: string;
  original_ref: string;
  amount: string;
  status: string;
  payload: Record<string, unknown>;
};
type Rate = {
  id: string;
  vehicle_type: string;
  rate_cents: number;
  rate: string;
  valid_from: string;
  valid_to: string;
  coverage_scope: string;
  source_ref: string;
};
type Row = {
  source_id: string;
  row_number: number;
  amount: string;
  status: string;
  reason: string;
  line_ordinal: number | null;
};
type Batch = {
  id: string;
  kind: Kind;
  period: string;
  status: string;
  version: number;
  draft_id: string;
  draft_status: string | null;
  amount: string;
  amount_cents: number;
  row_count: number;
  policy: { version: string; expense_account_code: string; credit_account_code: string };
  source_fingerprint: string;
  batch_fingerprint: string;
  stale: boolean;
  stale_reason: string;
  sources: Source[];
  rows: Row[];
};
const labels: Record<string, string> = {
  repair: '维修',
  insurance: '保险/摊销',
  oa: 'OA/工资',
  draft: '待复核',
  reviewed: '已复核',
  invalidated: '已失效',
  adopted: '已采用',
  rejected: '拒绝',
};
const templates: Record<Kind, string> = {
  repair: JSON.stringify(
    {
      invoice_ref: 'FP-001',
      vehicle_external_id: 'V1',
      coverage: 'per_vehicle',
      rate_cents: 5000,
      quantity_milli: 2000,
      allocation_basis: 'customer_approved_per_vehicle',
    },
    null,
    2,
  ),
  insurance: JSON.stringify(
    {
      policy_no: 'P-2026-01',
      premium_cents: 120000,
      start_period: '2026-01',
      end_period: '2026-12',
      total_months: 12,
      algorithm: 'straight_line',
      vehicle_external_id: 'V1',
      allocation_basis: 'customer_confirmed_policy_terms',
    },
    null,
    2,
  ),
  oa: JSON.stringify(
    {
      oa_event_id: 'OA-2026-10-1',
      oa_version: 3,
      approved_by: '部门负责人',
      approved_period: '2026-10',
      lines: [
        { account_code: '6602.03', amount_cents: 6000 },
        { account_code: '6602.04', amount_cents: 4000 },
      ],
    },
    null,
    2,
  ),
};

export function TaxCostBatchPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const base = `/tax/books/${encodeURIComponent(bookId)}`;
  const url = `${base}/cost-batches`;
  const [sources, setSources] = useState<Source[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [kind, setKind] = useState<Kind>('repair');
  const [sourceKey, setSourceKey] = useState('FP-001');
  const [subject, setSubject] = useState('京A1');
  const [amount, setAmount] = useState('100.00');
  const [originalRef, setOriginalRef] = useState('原件/维修结算单.pdf');
  const [payload, setPayload] = useState(templates.repair);
  const [policyVersion, setPolicyVersion] = useState('cost-v1');
  const [expense, setExpense] = useState('6602.03');
  const [credit, setCredit] = useState('2202');
  const [reason, setReason] = useState('独立复核成本批次');
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [rates, setRates] = useState<Rate[]>([]);
  const [vehicleType, setVehicleType] = useState('出租车');
  const [rateCents, setRateCents] = useState('5000');
  const [validFrom, setValidFrom] = useState(period);
  const [validTo, setValidTo] = useState(period);
  const [coverageScope, setCoverageScope] = useState('基础保养（不含轮胎）');

  async function load() {
    setFailure('');
    try {
      const [sourceData, batchData, rateData] = await Promise.all([
        api<{ items: Source[] }>(`${base}/cost-sources`, session),
        api<{ items: Batch[] }>(url, session),
        api<{ items: Rate[] }>(`${base}/repair-rates`, session),
      ]);
      setSources(sourceData.items.filter((item) => item.status === 'active'));
      setBatches(batchData.items);
      setRates(rateData.items);
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '成本批次读取失败。');
    }
  }
  useEffect(() => {
    void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [bookId, session]);

  function changeKind(next: Kind) {
    setKind(next);
    setSelected([]);
    setBatch(null);
    setPayload(templates[next]);
    setSourceKey(next === 'repair' ? 'FP-001' : next === 'insurance' ? 'P-1' : 'OA-2026-10-1');
    setOriginalRef(
      next === 'repair'
        ? '原件/维修结算单.pdf'
        : next === 'insurance'
          ? '原件/保单.pdf'
          : 'OA/审批快照.json',
    );
  }

  async function addSource() {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const parsed = JSON.parse(payload) as Record<string, unknown>;
      await api<Source>(`${base}/cost-sources`, session, {
        method: 'POST',
        body: {
          kind,
          source_key: sourceKey,
          source_version: 1,
          period,
          subject_label: subject,
          original_ref: originalRef,
          sha256: 'a'.repeat(64),
          amount,
          payload: parsed,
        },
      });
      setNotice('来源已登记并采用；请选择来源生成批次草稿。');
      await load();
    } catch (error) {
      const message =
        error instanceof SyntaxError
          ? '来源依据不是有效 JSON。'
          : error instanceof ApiError
            ? `${error.message}（${error.status}）`
            : '来源登记失败。';
      setFailure(message);
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!selected.length) {
      setFailure('请选择至少一个已登记来源。');
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
          kind,
          source_ids: selected,
          policy: {
            version: policyVersion,
            expense_account_code: expense,
            credit_account_code: credit,
          },
          reason: '按已核定来源生成成本批次',
        },
      });
      setBatch(data);
      setNotice('成本批次草稿已生成；须由另一名有复核权的会计独立复核。');
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '成本批次生成失败。',
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
        body: { expected_version: batch.version, review_reason: reason },
      });
      setBatch(data);
      setNotice('批次已独立复核，凭证版本冻结；可在审核与导出中按版本导出。');
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
      setBatch(data);
      setNotice('批次已失效，历史记录保留。');
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

  async function addRate() {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api<Rate>(`${base}/repair-rates`, session, {
        method: 'POST',
        body: {
          vehicle_type: vehicleType,
          rate_cents: Number(rateCents),
          valid_from: validFrom,
          valid_to: validTo,
          coverage_scope: coverageScope,
          source_ref: `客户核定费率/${vehicleType}/${validFrom}`,
        },
      });
      setNotice('费率卡已登记；同车型有效期不能重叠。');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '费率卡登记失败。');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="tax-cost-panel" aria-labelledby="tax-cost-title">
      <header>
        <div>
          <p className="tax-cost-kicker">TAX10–12 · 车辆与核定费用</p>
          <h3 id="tax-cost-title">维修、保险与 OA 成本批次</h3>
        </div>
        <button type="button" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>
      <p className="tax-cost-note">
        只采用人工核定且有原件的来源；金额由确定性代码计算。生成草稿不代表复核；复核后版本冻结。
        包干、保险算法或工资明细缺失时，必须由客户书面核定，系统不会反推。
      </p>
      <details className="tax-cost-rates">
        <summary>按车型维修费率卡（{rates.length}）</summary>
        <p>只登记客户核定的车型费率；实际维修引用费率卡时，金额必须与费率×数量一致。</p>
        <div className="tax-cost-form">
          <label>车型<input value={vehicleType} onChange={(event) => setVehicleType(event.target.value)} /></label>
          <label>费率（分）<input value={rateCents} onChange={(event) => setRateCents(event.target.value)} /></label>
          <label>生效月<input type="month" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} /></label>
          <label>结束月<input type="month" value={validTo} onChange={(event) => setValidTo(event.target.value)} /></label>
          <label className="tax-cost-wide">覆盖范围<input value={coverageScope} onChange={(event) => setCoverageScope(event.target.value)} /></label>
        </div>
        <button type="button" disabled={busy} onClick={() => void addRate()}>登记费率卡</button>
        {rates.length > 0 && (
          <div className="tax-cost-lines">
            {rates.map((rate) => (
              <article key={rate.id}>
                <strong>{rate.vehicle_type} · ¥{(rate.rate_cents / 100).toFixed(2)}</strong>
                <span>{rate.valid_from} 至 {rate.valid_to} · {rate.coverage_scope}</span>
                <small>{rate.source_ref}</small>
              </article>
            ))}
          </div>
        )}
      </details>
      <div className="tax-cost-form">
        <label>
          类别
          <select value={kind} onChange={(event) => changeKind(event.target.value as Kind)}>
            <option value="repair">维修</option>
            <option value="insurance">保险/摊销</option>
            <option value="oa">OA/工资</option>
          </select>
        </label>
        <label>
          来源键
          <input value={sourceKey} onChange={(event) => setSourceKey(event.target.value)} />
        </label>
        <label>
          车辆/对象
          <input value={subject} onChange={(event) => setSubject(event.target.value)} />
        </label>
        <label>
          金额
          <input value={amount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label className="tax-cost-wide">
          原件引用
          <input value={originalRef} onChange={(event) => setOriginalRef(event.target.value)} />
        </label>
        <label className="tax-cost-wide">
          核定依据（JSON）
          <textarea
            rows={7}
            value={payload}
            onChange={(event) => setPayload(event.target.value)}
          />
        </label>
      </div>
      <button type="button" disabled={busy} onClick={() => void addSource()}>
        登记并采用来源
      </button>
      <div className="tax-cost-form">
        <label>
          策略版本
          <input value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} />
        </label>
        <label>
          费用科目
          <input value={expense} onChange={(event) => setExpense(event.target.value)} />
        </label>
        <label>
          贷方科目
          <input value={credit} onChange={(event) => setCredit(event.target.value)} />
        </label>
        <label>
          复核说明
          <input value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
      </div>
      <div className="tax-cost-sources">
        <strong>选择来源（{labels[kind]}）</strong>
        {sources.filter((source) => source.kind === kind).length === 0 ? (
          <span>当前类别没有活动来源，请先登记客户核定依据。</span>
        ) : (
          sources
            .filter((source) => source.kind === kind)
            .map((source) => (
              <label key={source.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(source.id)}
                  onChange={() => toggle(source.id)}
                />{' '}
                {source.subject_label} · v{source.source_version} · {source.original_ref} ·{' '}
                {source.amount}
              </label>
            ))
        )}
      </div>
      <button type="button" className="tax-cost-primary" disabled={busy} onClick={() => void generate()}>
        生成成本批次草稿
      </button>
      {(notice || failure) && (
        <div
          role={failure ? 'alert' : 'status'}
          className={failure ? 'tax-cost-error' : 'tax-cost-ok'}
        >
          {failure || notice}
        </div>
      )}
      {batch && (
        <div className="tax-cost-batch">
          <div className="tax-cost-summary">
            <div>
              <span>状态</span>
              <strong>{labels[batch.status] ?? batch.status}</strong>
            </div>
            <div>
              <span>批次金额</span>
              <strong>¥{batch.amount}</strong>
            </div>
            <div>
              <span>采用行</span>
              <strong>{batch.row_count}</strong>
            </div>
            <div>
              <span>版本</span>
              <strong>v{batch.version}</strong>
            </div>
          </div>
          <p className="tax-cost-fingerprint">
            {batch.kind} · {batch.policy.version} · {batch.policy.expense_account_code} /{' '}
            {batch.policy.credit_account_code} · 批次指纹 {batch.batch_fingerprint.slice(0, 12)}…
          </p>
          {batch.stale && (
            <div role="alert" className="tax-cost-error">
              {batch.stale_reason}
            </div>
          )}
          <div className="tax-cost-lines">
            {batch.sources.map((source) => (
              <article key={source.id}>
                <strong>
                  {source.subject_label} · {source.original_ref}
                </strong>
                <span>
                  {source.amount} · {source.source_key} · v{source.source_version}
                </span>
                <small>{JSON.stringify(source.payload)}</small>
              </article>
            ))}
          </div>
          {batch.status === 'draft' && (
            <div className="tax-cost-actions">
              <button type="button" className="tax-cost-primary" disabled={busy} onClick={() => void review()}>
                独立复核并冻结
              </button>
              <button type="button" disabled={busy} onClick={() => void invalidate()}>
                标记失效
              </button>
            </div>
          )}
          {batch.draft_id && (
            <p className="tax-cost-draft">
              草稿 {batch.draft_id.slice(0, 12)}… · {batch.draft_status} · 复核后在“审核与导出”按版本导出。
            </p>
          )}
          <details className="tax-cost-history">
            <summary>历史批次（{batches.length}）</summary>
            {batches.map((item) => (
              <button
                type="button"
                key={item.id}
                onClick={() => setBatch(item)}
                className={item.id === batch.id ? 'tax-cost-current' : ''}
              >
                {item.period} · {labels[item.kind]} · v{item.version} ·{' '}
                {labels[item.status] ?? item.status} · {item.amount}
              </button>
            ))}
          </details>
        </div>
      )}
    </section>
  );
}
