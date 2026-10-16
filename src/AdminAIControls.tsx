import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  ArrowRight,
  CirclePause,
  Play,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { EmptyState, Modal, Spinner } from './AppPrimitives';
import { OperationsPolicyDialog } from './OperationsPolicyDialog';
import {
  budgetPercentage,
  currentOperationsPeriod,
  formatOperatingCost,
  formatTokenCount,
  operatingCostStateLabels,
  operatingKindLabel,
  operationsAlertStateLabels,
} from './operations-helpers';
import type { Session } from './types';
import type { OperationsPolicy, OperationsPolicyInput } from './operations-types';
import type { AdminAIOverview } from './admin-ai-types';
import './admin-ai.css';

type Confirmation =
  { kind: 'control'; enabled: boolean } | { kind: 'policy'; input: OperationsPolicyInput };

function dateLabel(value?: string | null) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '时间待核对'
    : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

export default function AdminAIControls({
  session,
  companyId,
  companyName,
  firmManager,
  onBusy,
  onAudit,
}: {
  session: Session;
  companyId: string;
  companyName: string;
  firmManager: boolean;
  onBusy: (value: boolean) => void;
  onAudit: () => void;
}) {
  const [period, setPeriod] = useState(currentOperationsPeriod);
  const [loaded, setLoaded] = useState<{ key: string; value: AdminAIOverview }>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [policyOpen, setPolicyOpen] = useState(false);
  const [pendingPolicy, setPendingPolicy] = useState<OperationsPolicyInput>();
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const target = `${session.scope_key}:${companyId}:${period}`;
  const currentTarget = useRef(target);
  currentTarget.current = target;
  const epoch = useRef(0);
  const authorityEpoch = useRef(0);
  const active = useRef(true);
  const locked = useRef(false);
  const readController = useRef<AbortController>();
  const value = loaded?.key === target ? loaded.value : undefined;
  const overview = value?.active ? value : undefined;
  const canControl = overview?.ai.can_manage === true;
  // 代账包干制：预算与费用策略是事务所内部管理项，客户企业管理员只能暂停/恢复外发。
  const canBudget = firmManager && canControl && overview?.can_manage_budget === true;
  // 客户侧响应为元数据级（无 usage/policy），进度只在事务所视图里计算。
  const percentage = firmManager && overview ? budgetPercentage(overview) : null;

  const clearSensitive = useCallback(() => {
    epoch.current++;
    authorityEpoch.current++;
    readController.current?.abort();
    setLoaded(undefined);
    setPolicyOpen(false);
    setPendingPolicy(undefined);
    setConfirmation(undefined);
    setReason('');
    setConfirmed(false);
    setNotice('');
    setLoading(false);
    setBusy(false);
  }, []);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
      readController.current?.abort();
      onBusy(false);
    };
  }, [onBusy]);
  useEffect(() => {
    onBusy(busy || policyOpen || !!confirmation);
    return () => onBusy(false);
  }, [busy, policyOpen, confirmation, onBusy]);
  useEffect(() => {
    const revoke = () => {
      clearSensitive();
      setError('管理授权已更新，企业用量与设置已清空。请返回企业列表重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [clearSensitive]);

  const load = useCallback(async () => {
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const generation = ++epoch.current;
    const started = target;
    setLoading(true);
    setLoaded(undefined);
    try {
      const result = await api<AdminAIOverview>(
        `/admin/companies/${encodeURIComponent(companyId)}/ai-control?period=${encodeURIComponent(period)}`,
        session,
        { signal: controller.signal },
      );
      if (
        !active.current ||
        controller.signal.aborted ||
        generation !== epoch.current ||
        currentTarget.current !== started
      )
        return false;
      if (result.company.id !== companyId)
        throw new Error('企业统计范围不匹配，请返回企业列表重新读取。');
      setLoaded({ key: started, value: result });
      return true;
    } catch (failure) {
      if (
        !active.current ||
        controller.signal.aborted ||
        generation !== epoch.current ||
        currentTarget.current !== started
      )
        return false;
      clearSensitive();
      setError(errorMessage(failure));
      return false;
    } finally {
      if (active.current && !controller.signal.aborted && currentTarget.current === started)
        setLoading(false);
    }
  }, [companyId, period, session, target, clearSensitive]);
  useEffect(() => {
    clearSensitive();
    setError('');
    void load();
    return () => {
      epoch.current++;
      readController.current?.abort();
    };
  }, [load, clearSensitive]);

  function openConfirmation(next: Confirmation) {
    setConfirmation(next);
    setReason('');
    setConfirmed(false);
    setError('');
    setNotice('');
  }
  async function save() {
    if (!overview || !confirmation || locked.current || !confirmed || reason.trim().length < 2)
      return;
    if (confirmation.kind === 'control' ? !canControl : !canBudget) return;
    locked.current = true;
    setBusy(true);
    setError('');
    const started = target;
    const authority = authorityEpoch.current;
    const sameTarget = () =>
      active.current && currentTarget.current === started && authorityEpoch.current === authority;
    try {
      await api(
        `/admin/companies/${encodeURIComponent(companyId)}/ai-control${confirmation.kind === 'policy' ? '/policy' : ''}`,
        session,
        {
          method: confirmation.kind === 'control' ? 'PATCH' : 'PUT',
          body:
            confirmation.kind === 'control'
              ? {
                  enabled: confirmation.enabled,
                  reason: reason.trim(),
                  expected_enabled: overview.ai.enabled,
                  expected_updated_at: overview.ai.updated_at,
                  expected_company_version: overview.company.version,
                }
              : { ...confirmation.input, reason: reason.trim() },
        },
      );
      if (!sameTarget()) return;
      const message =
        confirmation.kind === 'policy'
          ? '费用限额与提醒阈值已保存，后续模型请求按新策略核验。'
          : confirmation.enabled
            ? firmManager
              ? '该企业AI已恢复，后续请求仍受费用限额与人工复核规则约束。'
              : '本企业智能识别已恢复，结果仍须会计复核后使用。'
            : firmManager
              ? '该企业AI已暂停后续模型外发。已送达供应商的请求无法撤回，费用仍需计入；常规录入、人工复核及审核后导出继续可用。'
              : '本企业已暂停后续资料外发。已送达的请求无法撤回；常规录入、人工复核及审核后导出继续可用。';
      setConfirmation(undefined);
      setPolicyOpen(false);
      setPendingPolicy(undefined);
      setReason('');
      setConfirmed(false);
      const refreshed = await load();
      if (sameTarget())
        setNotice(refreshed ? message : `${message} 用量暂未刷新，请重新读取后核对。`);
    } catch (failure) {
      if (!sameTarget()) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        clearSensitive();
        setError(errorMessage(failure));
      } else if (failure instanceof ApiError && failure.status === 409) {
        setConfirmation(undefined);
        setPendingPolicy(undefined);
        setReason('');
        await load();
        if (sameTarget())
          setError('企业状态或策略已被其他管理员更新。已重新读取，请核对最新值后重新打开操作。');
      } else {
        setError(
          `${errorMessage(failure)} 如结果未知，请关闭弹窗并刷新，先核对当前状态与操作记录后再决定是否重试。`,
        );
      }
    } finally {
      locked.current = false;
      if (active.current && currentTarget.current === started) setBusy(false);
    }
  }

  const editorPolicy: OperationsPolicy | undefined =
    overview && pendingPolicy
      ? { ...overview.policy, ...pendingPolicy, version: pendingPolicy.expected_version }
      : overview?.policy;
  return (
    <section className="admin-ai" aria-label={firmManager ? '企业AI用量与控制' : '智能识别与隐私'}>
      <div className="admin-section-heading">
        <div>
          <h2>{firmManager ? 'AI用量与控制' : '智能识别与隐私'}</h2>
          <p>
            {firmManager
              ? '只查看本企业的调用与费用汇总，暂停和限额变更均保留操作依据。'
              : '本企业可自主决定是否允许使用智能识别；费用与运行由事务所统一管理。'}
          </p>
        </div>
        <div className="admin-inline-actions">
          {firmManager && (
            <label className="admin-ai-period">
              统计月份
              <input
                type="month"
                aria-label="AI用量统计月份"
                value={period}
                disabled={busy || policyOpen || !!confirmation}
                onChange={(event) => {
                  if (/^20\d{2}-(?:0[1-9]|1[0-2])$/.test(event.target.value))
                    setPeriod(event.target.value);
                }}
              />
            </label>
          )}
          <button
            className="icon-button"
            aria-label="刷新企业AI用量"
            disabled={loading || busy || policyOpen || !!confirmation}
            onClick={() => {
              setError('');
              setNotice('');
              void load();
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>
      {error && !confirmation && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在读取企业AI用量与设置…
        </div>
      ) : value && !value.active ? (
        <EmptyState
          title={value.company.status === 'archived' ? '企业已归档' : '企业已暂停服务'}
          icon={<ShieldCheck size={26} />}
        >
          {firmManager
            ? '原始资料与操作记录继续保留。恢复企业服务后，可重新查看用量并管理AI设置。'
            : '原始资料与操作记录继续保留。恢复企业服务后可重新查看智能识别设置。'}
        </EmptyState>
      ) : overview ? (
        <>
          <section
            className={`admin-ai-state ${overview.ai.enabled ? 'enabled' : 'paused'}`}
            aria-label="企业AI运行状态"
          >
            <div className="admin-ai-state-title">
              <Sparkles size={22} />
              <div>
                <h3>
                  {firmManager
                    ? overview.ai.enabled
                      ? 'AI已开启'
                      : '管理员已暂停AI'
                    : overview.ai.enabled
                      ? '智能识别已开启'
                      : '智能识别已暂停'}
                </h3>
                <p>
                  {firmManager
                    ? overview.ai.enabled
                      ? '获授权人员可使用已配置的模型服务，费用限额和会计复核继续生效。'
                      : '阻止该企业后续模型外发，保留历史结果、配置与报价。已送达供应商的请求无法撤回，费用仍需计入；常规录入、人工复核及审核后导出继续可用。'
                    : overview.ai.enabled
                      ? '获授权人员可使用智能识别与记账建议；结果仍由会计核对原件后入账。'
                      : '本企业已暂停后续资料外发，保留历史结果与配置。已送达的请求无法撤回；常规录入、人工复核及审核后导出继续可用。'}
                </p>
              </div>
            </div>
            {canControl && (
              <button
                className={`button ${overview.ai.enabled ? 'secondary' : 'primary'}`}
                disabled={busy}
                onClick={() => openConfirmation({ kind: 'control', enabled: !overview.ai.enabled })}
              >
                {overview.ai.enabled ? <CirclePause size={15} /> : <Play size={15} />}
                {overview.ai.enabled ? '暂停企业AI' : '恢复企业AI'}
              </button>
            )}
            {firmManager && (
              <>
                <div className="admin-ai-services" aria-label="模型服务配置状态">
                  {[
                    ['票据识别', overview.ai.ocr_ready],
                    ['分录建议', overview.ai.journal_ready],
                    ['会计对话', overview.ai.chat_ready],
                  ].map(([label, ready]) => (
                    <span key={String(label)}>
                      <i className={ready ? 'ready' : 'unconfigured'} />
                      {label}
                      <strong>{ready ? '服务已配置' : '服务未配置'}</strong>
                    </span>
                  ))}
                </div>
                {(!overview.ai.ocr_ready ||
                  !overview.ai.journal_ready ||
                  !overview.ai.chat_ready) && (
                  <p className="admin-ai-caption">
                    企业开关与供应商配置分别管理。服务未配置时，需要系统管理员补齐对应供应商；恢复企业AI不会自动配置模型。
                  </p>
                )}
              </>
            )}
          </section>
          {firmManager && (
            <>
              <div className="admin-ai-metrics" aria-label="企业Token与费用汇总">
                {[
                  [
                    '输入Token',
                    formatTokenCount(overview.usage.input_tokens),
                    '已返回用量回执的输入',
                  ],
                  [
                    '输出Token',
                    formatTokenCount(overview.usage.output_tokens),
                    '已返回用量回执的输出',
                  ],
                  [
                    '累计调用',
                    formatTokenCount(overview.usage.requests),
                    `${overview.usage.reported_requests} 次有用量回执`,
                  ],
                  [
                    '按用量估算',
                    formatOperatingCost(overview.usage.estimated_cost),
                    'Token × 已配置单价',
                  ],
                  [
                    '账单人工核定',
                    formatOperatingCost(overview.usage.confirmed_cost),
                    '以供应商账单和核对依据为准',
                  ],
                  [
                    '当前费用预留',
                    formatOperatingCost(overview.usage.reserved_cost),
                    '用于控制并发费用风险',
                  ],
                ].map(([label, amount, help]) => (
                  <article key={label}>
                    <span>{label}</span>
                    <strong>{amount}</strong>
                    <small>{help}</small>
                  </article>
                ))}
              </div>
              <p className="admin-ai-caption" aria-label="企业Token回执说明">
                已报告总Token：{formatTokenCount(overview.usage.total_tokens)}
                。未返回的用量按待核对处理，不作为零用量。
              </p>
              <section className="admin-ai-budget" aria-label="企业AI费用限额">
                <div className="admin-section-heading">
                  <div>
                    <h3>
                      {overview.policy.quota_enabled ? '月度费用限额已开启' : '月度费用限额未开启'}
                    </h3>
                    <p>当前占用包含处理预留与往月待核定费用。预留不代表实际结算金额。</p>
                  </div>
                  {canBudget && (
                    <button
                      className="button secondary small"
                      disabled={busy}
                      onClick={() => {
                        setPendingPolicy(undefined);
                        setError('');
                        setNotice('');
                        setPolicyOpen(true);
                      }}
                    >
                      <Settings2 size={15} />
                      设置费用限额与提醒
                    </button>
                  )}
                </div>
                <div className="admin-ai-budget-numbers">
                  <span>
                    当前占用 <strong>{formatOperatingCost(overview.usage.exposure)}</strong>
                  </span>
                  <span>
                    月度限额{' '}
                    <strong>
                      {overview.policy.quota_enabled
                        ? formatOperatingCost(overview.policy.monthly_limit)
                        : '未限制'}
                    </strong>
                  </span>
                  <span>
                    可用额度{' '}
                    <strong>
                      {overview.policy.quota_enabled
                        ? formatOperatingCost(overview.usage.available)
                        : '未限制'}
                    </strong>
                  </span>
                </div>
                {percentage !== null && (
                  <progress
                    aria-label="企业AI费用额度占用"
                    max="100"
                    value={Math.min(100, percentage)}
                  />
                )}
                <p className="admin-ai-caption">
                  达到费用限额时，系统阻止新增模型请求；未返回用量的请求不会按零费用计算。金额以人民币显示
                  6 位小数，估算费用仍需核对供应商账单。
                </p>
              </section>
              <section className="admin-ai-alerts" aria-label="企业AI异常提醒">
                <div className="admin-section-heading">
                  <div>
                    <h3>
                      <Activity size={17} />
                      异常与待核对
                    </h3>
                    <p>核对失败、积压和用量回执，按实际情况暂停AI或调整运行阈值。</p>
                  </div>
                </div>
                {overview.usage.uncertain_requests > 0 && (
                  <p className="admin-ai-warning">
                    <TriangleAlert size={16} />
                    {overview.usage.uncertain_requests} 次请求费用待核定，请核对供应商回执与账单。
                  </p>
                )}
                {overview.usage.overrun_count > 0 && (
                  <p className="admin-ai-warning">
                    <TriangleAlert size={16} />
                    发现 {overview.usage.overrun_count} 次费用超限记录。恢复前请先核对费用及额度。
                  </p>
                )}
                {overview.alerts.length ? (
                  <div className="admin-ai-alert-list">
                    {overview.alerts.map((alert) => (
                      <article key={alert.id}>
                        <div>
                          <TriangleAlert size={15} />
                          <strong>{operationsAlertStateLabels[alert.state] ?? '状态待核对'}</strong>
                          <span>{dateLabel(alert.last_seen_at)}</span>
                        </div>
                        <p>{alert.message}</p>
                        {(alert.reason || alert.acknowledgement_reason) && (
                          <small>处置依据：{alert.reason || alert.acknowledgement_reason}</small>
                        )}
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className="field-help">
                    当前没有已记录的运行提醒。暂未记录提醒不代表所有请求都已完成核对。
                  </p>
                )}
              </section>
              <section className="admin-ai-costs" aria-label="近期模型调用记录">
                <h3>近期模型调用</h3>
                <div className="admin-ai-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>处理场景</th>
                        <th>状态</th>
                        <th>输入 / 输出Token</th>
                        <th>估算 / 核定费用</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.costs.map((cost) => (
                        <tr key={cost.id}>
                          <td>
                            <strong>{operatingKindLabel(cost.kind)}</strong>
                            <small>{dateLabel(cost.created_at)}</small>
                          </td>
                          <td>{operatingCostStateLabels[cost.state] ?? '待核对'}</td>
                          <td>
                            {formatTokenCount(cost.input_tokens)} /{' '}
                            {formatTokenCount(cost.output_tokens)}
                          </td>
                          <td>
                            {formatOperatingCost(cost.estimated_cost)}
                            <small>{formatOperatingCost(cost.confirmed_cost)}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!overview.costs.length && <p className="field-help">本期尚无模型调用记录。</p>}
              </section>
            </>
          )}
          {!firmManager && (
            <section className="admin-ai-external" aria-label="资料外发说明">
              <h3>
                <ShieldCheck size={17} />
                资料外发说明
              </h3>
              <p>
                开启智能识别后，本企业票据图片与必要字段会发送至事务所统一配置的模型服务，用于识别与记账建议；
                结果须经会计核对原件后才能入账，复核通过前不会导出。你可随时暂停，暂停后不再新增外发；已送达的请求无法撤回。
              </p>
              <p className="admin-ai-caption">
                模型配置、费用与运行监控由事务所统一管理，本企业无需设置预算或预留额。
              </p>
            </section>
          )}
          <div className="admin-ai-footer">
            <span>
              <ShieldCheck size={14} />
              最终凭证仍由会计复核后导出。
            </span>
            <button className="text-button" onClick={onAudit} disabled={busy}>
              查看操作审计
              <ArrowRight size={14} />
            </button>
          </div>
        </>
      ) : null}
      {policyOpen && overview && editorPolicy && canBudget && (
        <OperationsPolicyDialog
          policy={editorPolicy}
          companyName={companyName}
          busy={busy}
          error={error}
          pricingReady={overview.pricing.ocr.configured && overview.pricing.proposal.configured}
          onClose={() => {
            setPolicyOpen(false);
            setPendingPolicy(undefined);
            setError('');
          }}
          onSave={async (input) => {
            setPendingPolicy(input);
            setPolicyOpen(false);
            openConfirmation({ kind: 'policy', input });
          }}
        />
      )}
      {confirmation && overview && (
        <Modal
          title={
            confirmation.kind === 'policy'
              ? '确认费用限额与提醒变更'
              : confirmation.enabled
                ? '恢复企业AI'
                : '暂停企业AI'
          }
          subtitle={companyName}
          busy={busy}
          error={error}
          onClose={() => {
            setConfirmation(undefined);
            setReason('');
            setConfirmed(false);
            setError('');
          }}
        >
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <p className="admin-info">
              {confirmation.kind === 'policy'
                ? `月度限额：${confirmation.input.quota_enabled ? formatOperatingCost(confirmation.input.monthly_limit) : '未限制'}。后续模型请求按新策略核验，保存会记录操作者和变更依据。`
                : confirmation.enabled
                  ? firmManager
                    ? '恢复后沿用当前供应商配置、报价与费用限额。恢复AI不会赋予任何人凭证复核或导出权限。'
                    : '恢复后沿用事务所当前配置。恢复智能识别不会赋予任何人凭证复核或导出权限。'
                  : firmManager
                    ? '阻止该企业后续模型外发。原件、识别结果、草稿和供应商配置继续保留；已送达供应商的请求无法撤回，费用仍需计入。常规录入、历史查看、人工复核与审核后导出继续可用。'
                    : '阻止本企业后续资料外发。原件、识别结果与草稿继续保留；已送达的请求无法撤回。常规录入、历史查看、人工复核与审核后导出继续可用。'}
            </p>
            <label>
              操作原因
              <textarea
                aria-label="AI控制操作原因"
                value={reason}
                minLength={2}
                maxLength={1000}
                required
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <label className="admin-checkbox">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={busy}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              我确认对「{companyName}」执行此次变更。
            </label>
            <div className="admin-form-actions">
              {confirmation.kind === 'policy' && (
                <button
                  className="button secondary"
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setConfirmation(undefined);
                    setPolicyOpen(true);
                    setError('');
                  }}
                >
                  返回修改
                </button>
              )}
              <button
                className="button primary"
                disabled={busy || !confirmed || reason.trim().length < 2}
              >
                {busy && <Spinner />}
                {confirmation.kind === 'policy'
                  ? '确认保存策略'
                  : confirmation.enabled
                    ? '确认恢复AI'
                    : '确认暂停AI'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}
