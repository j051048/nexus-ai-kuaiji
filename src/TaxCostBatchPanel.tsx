import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import type { Session } from './types';
import { centsToApiString, formatCents, parseYuanToCents } from './money';
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
  version: number;
  status: string;
  confirmed_by: string | null;
  activated_at: string | null;
  superseded_at: string | null;
  reason: string | null;
  unit: string;
  currency: string;
  tax_included: boolean;
  logical_id: string;
};
type PackageContract = {
  id: string;
  contract_no: string;
  version: number;
  status: string;
  coverage_scope: string;
  vehicle_external_ids: string[];
  valid_from: string;
  valid_to: string;
  limit_cents: number;
  policy: string;
  reason: string;
};
type PackageSummary = {
  contract: PackageContract;
  limit_cents: number;
  consumed_cents: number;
  remaining_cents: number;
  usage: {
    id: string;
    kind: string;
    amount_cents: number;
    source_key: string;
    period: string;
    release_of: string | null;
  }[];
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
  const [packages, setPackages] = useState<PackageContract[]>([]);
  const [packageNo, setPackageNo] = useState('PKG-2026-001');
  const [packageLimit, setPackageLimit] = useState('10000');
  const [packageVehicles, setPackageVehicles] = useState('V1,V2');
  const [packageFrom, setPackageFrom] = useState(period);
  const [packageTo, setPackageTo] = useState(period);
  const [packageReason, setPackageReason] = useState('客户核定年度包干合同');
  const [packageSummary, setPackageSummary] = useState<PackageSummary | null>(null);
  const [usageContract, setUsageContract] = useState('');
  const [usageSource, setUsageSource] = useState('FP-PKG-001');
  const [usagePeriod, setUsagePeriod] = useState(period);
  const [usageVehicle, setUsageVehicle] = useState('V1');
  const [usageAmount, setUsageAmount] = useState('50.00');
  const [releaseTarget, setReleaseTarget] = useState('');
  const [releaseAmount, setReleaseAmount] = useState('');
  const [releaseReason, setReleaseReason] = useState('客户退回包干维修');
  const [vehicleType, setVehicleType] = useState('出租车');
  const [rateCents, setRateCents] = useState('5000');
  const [validFrom, setValidFrom] = useState(period);
  const [validTo, setValidTo] = useState(period);
  const [coverageScope, setCoverageScope] = useState('基础保养（不含轮胎）');
  const [rateReason, setRateReason] = useState('客户核定车型费率版本');
  const [rateTaxIncluded, setRateTaxIncluded] = useState(false);
  const loadSequence = useRef(0);

  async function load(signal?: AbortSignal) {
    const sequence = ++loadSequence.current;
    setFailure('');
    try {
      const [sourceData, batchData, rateData, packageData] = await Promise.all([
        scheduleSubpanel(
          () => api<{ items: Source[] }>(`${base}/cost-sources`, session, { signal }),
          signal,
        ),
        scheduleSubpanel(() => api<{ items: Batch[] }>(url, session, { signal }), signal),
        scheduleSubpanel(
          () => api<{ items: Rate[] }>(`${base}/repair-rates`, session, { signal }),
          signal,
        ),
        scheduleSubpanel(
          () => api<{ items: PackageContract[] }>(`${base}/repair-packages`, session, { signal }),
          signal,
        ),
      ]);
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setSources(sourceData.items.filter((item) => item.status === 'active'));
      setBatches(batchData.items);
      setRates(rateData.items);
      setPackages(packageData.items);
    } catch (error) {
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setFailure(error instanceof ApiError ? error.message : '成本批次读取失败。');
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      loadSequence.current++;
      controller.abort();
    };
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
      const created = await api<Rate>(`${base}/repair-rates/drafts`, session, {
        method: 'POST',
        body: {
          vehicle_type: vehicleType,
          rate_cents: Number(rateCents),
          valid_from: validFrom,
          valid_to: validTo,
          coverage_scope: coverageScope,
          source_ref: `客户核定费率/${vehicleType}/${validFrom}`,
          unit: 'per_vehicle',
          currency: 'CNY',
          tax_included: rateTaxIncluded,
          reason: rateReason,
        },
      });
      setNotice(`费率草稿 v${created.version} 已建立；需另一名有权限人员独立确认后才能生效。`);
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '费率草稿登记失败。');
    } finally {
      setBusy(false);
    }
  }

  async function rateCommand(rate: Rate, action: 'confirm' | 'activate' | 'deactivate') {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const body =
        action === 'deactivate'
          ? { expected_version: rate.version, reason: rateReason }
          : { expected_version: rate.version };
      const updated = await api<Rate>(`${base}/repair-rates/${rate.id}/${action}`, session, {
        method: 'POST',
        body,
      });
      setNotice(
        action === 'confirm'
          ? `费率 v${updated.version} 已独立确认，仍需生效。`
          : action === 'activate'
            ? `费率 v${updated.version} 已生效；历史版本保留。`
            : `费率 v${updated.version} 已停用；历史批次与凭证不删除。`,
      );
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '费率操作失败。',
      );
    } finally {
      setBusy(false);
    }
  }

  async function renewRate(rate: Rate) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const created = await api<Rate>(`${base}/repair-rates/${rate.id}/renew`, session, {
        method: 'POST',
        body: {
          vehicle_type: rate.vehicle_type,
          rate_cents: Number(rateCents),
          valid_from: validFrom,
          valid_to: validTo,
          coverage_scope: coverageScope,
          source_ref: `客户核定费率/${rate.vehicle_type}/${validFrom}`,
          unit: 'per_vehicle',
          currency: 'CNY',
          tax_included: rateTaxIncluded,
          reason: rateReason,
        },
      });
      setNotice(`续期草稿 v${created.version} 已建立；未确认前旧版本仍有效。`);
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '费率续期失败。');
    } finally {
      setBusy(false);
    }
  }

  async function rateImpact(rate: Rate) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const impact = await api<{ items: { source_key: string; status: string }[]; note: string }>(
        `${base}/repair-rates/${rate.id}/impact`,
        session,
      );
      setNotice(
        impact.items.length
          ? `影响 ${impact.items.length} 条维修来源：${impact.items.map((row) => `${row.source_key}（${row.status}）`).join('、')}。${impact.note}`
          : `当前没有维修来源引用该费率。${impact.note}`,
      );
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : '影响预览读取失败。');
    } finally {
      setBusy(false);
    }
  }

  async function addPackage() {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api<PackageContract>(`${base}/repair-packages/drafts`, session, {
        method: 'POST',
        body: {
          contract_no: packageNo,
          coverage_scope: '出租车基础保养包干（不含轮胎与事故维修）',
          vehicle_external_ids: packageVehicles
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
          valid_from: packageFrom,
          valid_to: packageTo,
          limit_cents: Number(packageLimit),
          policy: 'contract_cumulative',
          reason: packageReason,
        },
      });
      setNotice('包干合同草稿已建；须由另一名有复核权的人员确认后才能占用额度。');
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '包干合同创建失败。',
      );
    } finally {
      setBusy(false);
    }
  }

  async function packageCommand(contract: PackageContract, action: 'confirm' | 'activate') {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api<PackageContract>(
        `${base}/repair-packages/${encodeURIComponent(contract.id)}/${action}`,
        session,
        {
          method: 'POST',
          body: { expected_version: contract.version },
        },
      );
      setNotice(action === 'confirm' ? '包干合同已独立确认。' : '包干合同已生效，可登记占用。');
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '包干合同操作失败。',
      );
    } finally {
      setBusy(false);
    }
  }

  async function showPackageSummary(contract: PackageContract) {
    setFailure('');
    try {
      setPackageSummary(
        await api<PackageSummary>(
          `${base}/repair-packages/${encodeURIComponent(contract.id)}/summary`,
          session,
        ),
      );
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '包干额度读取失败。',
      );
    }
  }

  async function consumePackage(contract: PackageContract) {
    const parsed = parseYuanToCents(usageAmount);
    const cents = parsed === null ? 0 : Number(parsed);
    if (cents <= 0) {
      setFailure('包干占用金额必须大于 0。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(
        `${base}/repair-packages/${encodeURIComponent(contract.id)}/consumptions`,
        session,
        {
          method: 'POST',
          body: {
            source_key: usageSource,
            source_version: 1,
            period: usagePeriod,
            amount_cents: cents,
            vehicle_external_id: usageVehicle || null,
            idempotency_key: `pkg-consume-${contract.id}-${Date.now()}`,
          },
        },
      );
      setNotice('包干占用已登记；不生成费用凭证，也不改变实际成本。');
      setUsageContract('');
      await showPackageSummary(contract);
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '包干占用登记失败。',
      );
    } finally {
      setBusy(false);
    }
  }

  async function releasePackage(usageId: string) {
    const parsed = parseYuanToCents(releaseAmount);
    const cents = parsed === null ? 0 : Number(parsed);
    if (cents <= 0) {
      setFailure('释放金额必须大于 0。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(
        `${base}/repair-packages/consumptions/${encodeURIComponent(usageId)}/releases`,
        session,
        {
          method: 'POST',
          body: {
            amount_cents: cents,
            reason: releaseReason,
            idempotency_key: `pkg-release-${usageId}-${Date.now()}`,
          },
        },
      );
      setNotice('包干占用已释放；原占用与释放历史均保留。');
      setReleaseTarget('');
      if (packageSummary) {
        await showPackageSummary(packageSummary.contract);
      }
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '包干释放失败。',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="tax-cost-panel" aria-labelledby="tax-cost-title">
      <header>
        <div>
          <p className="tax-cost-kicker">行业专项 · 车辆与核定费用</p>
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

      <details className="tax-cost-rates" open>
        <summary>维修包干合同与额度（{packages.length}）</summary>
        <p>包干合同须独立确认后生效；额度按合同累计，占用/释放原子登记，不把合同额度当实际费用。</p>
        <div className="tax-cost-form">
          <label>
            合同号
            <input value={packageNo} onChange={(event) => setPackageNo(event.target.value)} />
          </label>
          <label>
            合同额度（分）
            <input value={packageLimit} onChange={(event) => setPackageLimit(event.target.value)} />
          </label>
          <label className="tax-cost-wide">
            覆盖车辆（逗号分隔）
            <input
              value={packageVehicles}
              onChange={(event) => setPackageVehicles(event.target.value)}
            />
          </label>
          <label>
            开始月
            <input
              type="month"
              value={packageFrom}
              onChange={(event) => setPackageFrom(event.target.value)}
            />
          </label>
          <label>
            结束月
            <input
              type="month"
              value={packageTo}
              onChange={(event) => setPackageTo(event.target.value)}
            />
          </label>
          <label className="tax-cost-wide">
            客户依据
            <input
              value={packageReason}
              onChange={(event) => setPackageReason(event.target.value)}
            />
          </label>
        </div>
        <button type="button" disabled={busy} onClick={() => void addPackage()}>
          新建包干合同草稿
        </button>
        {packages.length > 0 && (
          <div className="tax-cost-lines">
            {packages.map((contract) => (
              <article key={contract.id}>
                <strong>
                  {contract.contract_no} · ¥{formatCents(BigInt(contract.limit_cents))}
                  <span className="tax-rate-status">
                    {contract.status === 'active'
                      ? '已生效'
                      : contract.status === 'draft'
                        ? '待确认'
                        : contract.status === 'confirmed'
                          ? '已确认待生效'
                          : contract.status === 'superseded'
                            ? '已被替代'
                            : '已停用'}{' '}
                    · v{contract.version}
                  </span>
                </strong>
                <span>
                  {contract.valid_from} 至 {contract.valid_to} · {contract.coverage_scope}
                </span>
                <small>
                  {contract.vehicle_external_ids.join('、')} ·{' '}
                  {contract.policy === 'contract_cumulative' ? '合同累计' : '按月重置'}
                </small>
                <div className="tax-rate-actions">
                  {contract.status === 'draft' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void packageCommand(contract, 'confirm')}
                    >
                      独立确认
                    </button>
                  )}
                  {contract.status === 'confirmed' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void packageCommand(contract, 'activate')}
                    >
                      生效
                    </button>
                  )}
                  {contract.status === 'active' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        setUsageContract(usageContract === contract.id ? '' : contract.id)
                      }
                    >
                      登记占用
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void showPackageSummary(contract)}
                  >
                    查看额度
                  </button>
                </div>
                {usageContract === contract.id && (
                  <div className="tax-cost-form">
                    <label>
                      来源键
                      <input
                        value={usageSource}
                        onChange={(event) => setUsageSource(event.target.value)}
                      />
                    </label>
                    <label>
                      期间
                      <input
                        type="month"
                        value={usagePeriod}
                        onChange={(event) => setUsagePeriod(event.target.value)}
                      />
                    </label>
                    <label>
                      车辆
                      <input
                        value={usageVehicle}
                        onChange={(event) => setUsageVehicle(event.target.value)}
                      />
                    </label>
                    <label>
                      占用金额（元）
                      <input
                        value={usageAmount}
                        onChange={(event) => setUsageAmount(event.target.value)}
                      />
                    </label>
                    <div className="tax-rate-actions">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void consumePackage(contract)}
                      >
                        确认占用
                      </button>
                      <button type="button" disabled={busy} onClick={() => setUsageContract('')}>
                        取消
                      </button>
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
        {packageSummary && (
          <div className="tax-cost-note">
            <p>
              {packageSummary.contract.contract_no}：额度 ¥
              {formatCents(BigInt(packageSummary.limit_cents))}，已占用 ¥
              {formatCents(BigInt(packageSummary.consumed_cents))}，剩余 ¥
              {formatCents(BigInt(packageSummary.remaining_cents))}，记录{' '}
              {packageSummary.usage.length} 条。
            </p>
            <ul className="tax-package-usage">
              {packageSummary.usage.map((usage) => (
                <li key={usage.id}>
                  <span>
                    {usage.kind === 'consume' ? '占用' : '释放'} · {usage.source_key} ·{' '}
                    {usage.period} · ¥{formatCents(BigInt(usage.amount_cents))}
                  </span>
                  {usage.kind === 'consume' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setReleaseTarget(releaseTarget === usage.id ? '' : usage.id);
                        setReleaseAmount(centsToApiString(BigInt(usage.amount_cents)));
                      }}
                    >
                      释放
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {releaseTarget && (
              <div className="tax-cost-form">
                <label>
                  释放金额（元）
                  <input
                    value={releaseAmount}
                    onChange={(event) => setReleaseAmount(event.target.value)}
                  />
                </label>
                <label className="tax-cost-wide">
                  释放原因
                  <input
                    value={releaseReason}
                    onChange={(event) => setReleaseReason(event.target.value)}
                  />
                </label>
                <div className="tax-rate-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void releasePackage(releaseTarget)}
                  >
                    确认释放
                  </button>
                  <button type="button" disabled={busy} onClick={() => setReleaseTarget('')}>
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </details>
      <details className="tax-cost-rates">
        <summary>按车型维修费率卡（{rates.length}）</summary>
        <p>只登记客户核定的车型费率；实际维修引用费率卡时，金额必须与费率×数量一致。</p>
        <div className="tax-cost-form">
          <label>
            车型
            <input value={vehicleType} onChange={(event) => setVehicleType(event.target.value)} />
          </label>
          <label>
            费率（分）
            <input value={rateCents} onChange={(event) => setRateCents(event.target.value)} />
          </label>
          <label>
            生效月
            <input
              type="month"
              value={validFrom}
              onChange={(event) => setValidFrom(event.target.value)}
            />
          </label>
          <label>
            结束月
            <input
              type="month"
              value={validTo}
              onChange={(event) => setValidTo(event.target.value)}
            />
          </label>
          <label className="tax-cost-wide">
            覆盖范围
            <input
              value={coverageScope}
              onChange={(event) => setCoverageScope(event.target.value)}
            />
          </label>
          <label className="tax-cost-wide">
            版本依据
            <input value={rateReason} onChange={(event) => setRateReason(event.target.value)} />
          </label>
          <label>
            <input
              type="checkbox"
              checked={rateTaxIncluded}
              onChange={(event) => setRateTaxIncluded(event.target.checked)}
            />
            费率含税
          </label>
        </div>
        <button type="button" disabled={busy} onClick={() => void addRate()}>
          新建费率草稿
        </button>
        {rates.length > 0 && (
          <div className="tax-cost-lines">
            {rates.map((rate) => (
              <article key={rate.id}>
                <strong>
                  {rate.vehicle_type} · ¥{formatCents(BigInt(rate.rate_cents))}
                  <span className="tax-rate-status">
                    {rate.status === 'active'
                      ? '已生效'
                      : rate.status === 'draft'
                        ? '待确认'
                        : rate.status === 'confirmed'
                          ? '已确认待生效'
                          : rate.status === 'superseded'
                            ? '已被后续版本替代'
                            : '已停用'}{' '}
                    · v{rate.version}
                  </span>
                </strong>
                <span>
                  {rate.valid_from} 至 {rate.valid_to} · {rate.coverage_scope}
                </span>
                <small>
                  {rate.source_ref} · {rate.unit === 'per_vehicle' ? '按车辆' : rate.unit} ·{' '}
                  {rate.currency} · {rate.tax_included ? '含税' : '未税'}
                </small>
                <div className="tax-rate-actions">
                  {rate.status === 'draft' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void rateCommand(rate, 'confirm')}
                    >
                      独立确认
                    </button>
                  )}
                  {rate.status === 'confirmed' && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void rateCommand(rate, 'activate')}
                    >
                      生效
                    </button>
                  )}
                  {rate.status === 'active' && (
                    <>
                      <button type="button" disabled={busy} onClick={() => void renewRate(rate)}>
                        按表单续期
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void rateCommand(rate, 'deactivate')}
                      >
                        停用
                      </button>
                    </>
                  )}
                  <button type="button" disabled={busy} onClick={() => void rateImpact(rate)}>
                    影响
                  </button>
                </div>
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
          <textarea rows={7} value={payload} onChange={(event) => setPayload(event.target.value)} />
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
      <button
        type="button"
        className="tax-cost-primary"
        disabled={busy}
        onClick={() => void generate()}
      >
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
              <button
                type="button"
                className="tax-cost-primary"
                disabled={busy}
                onClick={() => void review()}
              >
                独立复核并冻结
              </button>
              <button type="button" disabled={busy} onClick={() => void invalidate()}>
                标记失效
              </button>
            </div>
          )}
          {batch.draft_id && (
            <p className="tax-cost-draft">
              草稿 {batch.draft_id.slice(0, 12)}… · {batch.draft_status} ·
              复核后在“审核与导出”按版本导出。
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
