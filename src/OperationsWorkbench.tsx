import { OperationsPerformance } from './OperationsPerformance';
import { operatingKindLabel } from './operations-helpers';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  Coins,
  FileCheck2,
  LoaderCircle,
  RefreshCw,
  Settings2,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { shouldReadAfterFailure } from './intelligence-helpers';
import {
  budgetPercentage,
  canReconcileOperatingCost,
  currentOperationsPeriod,
  formatOperatingCost,
  formatTokenCount,
  normalizeOperatingAmount,
  operatingCostStateLabels,
  operationsAlertStateLabels,
  validateCostReconciliation,
} from './operations-helpers';
import { OperationsPolicyDialog } from './OperationsPolicyDialog';
import type {
  OperationsAlert,
  OperationsCompany,
  OperationsCost,
  OperationsOverview,
  OperationsPolicyInput,
  OperationsPricing,
} from './operations-types';
import type { Session } from './types';

const encoded = encodeURIComponent;
const pollMs = 10000;
function dateLabel(value: string | null | undefined) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '时间待核对'
    : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
export function OperationsPricingRecord({
  pricing,
  label,
}: {
  pricing: OperationsPricing;
  label: string;
}) {
  return (
    <article className="operations-price-record">
      <div>
        <strong>{label}</strong>
        <span className={`badge ${pricing.configured ? 'approved' : 'draft'}`}>
          {pricing.configured ? '已配置报价' : '等待管理员配置报价'}
        </span>
      </div>
      <p>{pricing.model || '尚未配置模型'}</p>
      <dl>
        <div>
          <dt>每百万输入 token</dt>
          <dd>{formatOperatingCost(pricing.input_per_million)}</dd>
        </div>
        <div>
          <dt>每百万输出 token</dt>
          <dd>{formatOperatingCost(pricing.output_per_million)}</dd>
        </div>
      </dl>
    </article>
  );
}
export function OperationsBudgetSummary({ overview }: { overview: OperationsOverview }) {
  const percentage = budgetPercentage(overview);
  return (
    <section className="operations-budget-summary" aria-label="企业模型费用与额度">
      <div className="operations-budget-heading">
        <div>
          <Coins size={21} />
          <h2>每一份处理，都有费用依据。</h2>
        </div>
        <span className={`badge ${overview.policy.quota_enabled ? 'approved' : 'draft'}`}>
          {overview.policy.quota_enabled ? '额度控制已开启' : '额度控制未开启'}
        </span>
      </div>
      <div className="operations-cost-metrics">
        <div>
          <span>按用量估算</span>
          <strong>{formatOperatingCost(overview.usage.estimated_cost)}</strong>
          <small>供应商报告 token × 配置单价</small>
        </div>
        <div>
          <span>账单人工核定</span>
          <strong>{formatOperatingCost(overview.usage.confirmed_cost)}</strong>
          <small>以核对依据和供应商账单为准</small>
        </div>
        <div>
          <span>当前处理预留</span>
          <strong>{formatOperatingCost(overview.usage.reserved_cost)}</strong>
          <small>含处理请求与未确认费用风险</small>
        </div>
      </div>
      <div className="operations-budget-line">
        <div>
          <span>额度占用 {formatOperatingCost(overview.usage.exposure)}</span>
          <strong>
            {overview.policy.quota_enabled
              ? `月度额度 ${formatOperatingCost(overview.policy.monthly_limit)}`
              : '当前未限制月度额度'}
          </strong>
        </div>
        {percentage !== null && (
          <progress aria-label="企业月度额度占用" max="100" value={Math.min(100, percentage)} />
        )}
        <p>
          <span>
            {overview.policy.quota_enabled
              ? `可用额度 ${formatOperatingCost(overview.usage.available)}`
              : '启用额度控制后，新请求先预留费用'}
          </span>
          <span>往月待核定预留 {formatOperatingCost(overview.usage.carried_reserved_cost)}</span>
          <span>
            {overview.usage.uncertain_requests} 次费用待核定
            {overview.usage.overrun_count > 0 && ` · ${overview.usage.overrun_count} 次超额记录`}
          </span>
        </p>
      </div>
      <p className="operations-cost-caption">
        费用以人民币显示 6
        位小数，估算与人工核定分别列示；供应商外部账单仍需核对，预留不代表实际结算金额。当前额度占用含往月待核定预留；历史月份按当前核定状态汇总。
      </p>
    </section>
  );
}

export function OperationsWorkbench({
  session,
  companies,
  onBusyChange,
}: {
  session: Session;
  companies: OperationsCompany[];
  onBusyChange: (value: boolean) => void;
}) {
  const [companyId, setCompanyId] = useState(companies[0]?.id ?? '');
  const [period, setPeriod] = useState(() => currentOperationsPeriod());
  const [overview, setOverview] = useState<OperationsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [policyOpen, setPolicyOpen] = useState(false);
  const [editor, setEditor] = useState<
    { kind: 'ack'; alert: OperationsAlert } | { kind: 'cost'; cost: OperationsCost } | null
  >(null);
  const [reason, setReason] = useState('');
  const [confirmedCost, setConfirmedCost] = useState('');
  const active = useRef(true);
  const epoch = useRef(0);
  const lock = useRef(false);
  const target = `${session.scope_key}:${companyId}:${period}`;
  const targetRef = useRef(target);
  targetRef.current = target;
  const canManage = !!overview?.can_manage_budget;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange(!!busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  useEffect(() => {
    if (!companies.some((company) => company.id === companyId)) {
      setOverview(null);
      setCompanyId(companies[0]?.id ?? '');
    }
  }, [companies, companyId]);
  const load = useCallback(
    async (signal?: AbortSignal, quiet = false) => {
      if (!companyId) {
        setLoading(false);
        return;
      }
      const generation = ++epoch.current;
      if (!quiet) setLoading(true);
      try {
        const result = await api<OperationsOverview>(
          `/operations/companies/${encoded(companyId)}/overview?period=${encoded(period)}`,
          session,
          { signal },
        );
        if (signal?.aborted || !active.current || epoch.current !== generation) return;
        setOverview(result);
        setError('');
      } catch (failure) {
        if (!signal?.aborted && active.current && epoch.current === generation) {
          if (!quiet || (failure instanceof ApiError && [401, 403, 404].includes(failure.status)))
            setOverview(null);
          setError(errorMessage(failure) + (quiet ? ' 最新监控暂未读取成功，请刷新后核对。' : ''));
        }
      } finally {
        if (!signal?.aborted && active.current && epoch.current === generation && !quiet)
          setLoading(false);
      }
    },
    [companyId, period, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    setOverview(null);
    setError('');
    setNotice('');
    setPolicyOpen(false);
    setEditor(null);
    setReason('');
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    if (!companyId || busy || policyOpen || editor) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      await load(controller.signal, true);
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), pollMs);
    };
    timer = window.setTimeout(() => void poll(), pollMs);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [load, companyId, busy, policyOpen, editor]);
  async function act(name: string, path: string, body: unknown, success: string, method = 'POST') {
    if (lock.current) return;
    lock.current = true;
    setBusy(name);
    setError('');
    setNotice('');
    const operationTarget = target;
    const current = () => active.current && operationTarget === targetRef.current;
    try {
      await api(path, session, { method, body });
      if (!current()) return;
      await load(undefined, true);
      if (!current()) return;
      setNotice(success);
      setPolicyOpen(false);
      setEditor(null);
      setReason('');
      setConfirmedCost('');
    } catch (failure) {
      if (!current() || (failure instanceof DOMException && failure.name === 'AbortError')) return;
      let message = errorMessage(failure);
      if (shouldReadAfterFailure(failure)) {
        await load(undefined, true);
        message += ' 已读取最新状态，请核对后重新打开操作，避免覆盖他人的处理。';
      }
      if (current()) setError(message);
    } finally {
      if (current()) {
        lock.current = false;
        setBusy('');
      }
    }
  }
  function changeScope(nextCompany: string, nextPeriod: string) {
    epoch.current += 1;
    setOverview(null);
    setError('');
    setNotice('');
    setEditor(null);
    setPolicyOpen(false);
    setReason('');
    setConfirmedCost('');
    setCompanyId(nextCompany);
    setPeriod(nextPeriod);
  }
  async function saveEditor() {
    if (!editor) return;
    if (editor.kind === 'cost') {
      const validation = validateCostReconciliation(confirmedCost, reason);
      if (validation) {
        setError(validation);
        return;
      }
      await act(
        'reconcile',
        `/operations/costs/${encoded(editor.cost.id)}/reconcile`,
        {
          confirmed_cost: normalizeOperatingAmount(confirmedCost),
          reason: reason.trim(),
          expected_version: editor.cost.version,
        },
        '已记录供应商费用核定依据，原 token 用量和估算仍保留。',
      );
    } else {
      if (!reason.trim()) {
        setError('请填写提醒处置依据。');
        return;
      }
      await act(
        'acknowledge',
        `/operations/alerts/${encoded(editor.alert.id)}/acknowledge`,
        { reason: reason.trim(), expected_version: editor.alert.version },
        '已记录站内提醒处置依据；实际问题恢复后，提醒才会标记恢复。',
      );
    }
  }
  const queue = overview?.queue;
  return (
    <main className="operations-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">COST, WITH ACCOUNTABILITY</p>
          <h1>费用可核对，运行可掌握。</h1>
          <p className="page-description">
            按企业查看模型用量、费用风险与后台状态，把异常留在站内处理。
          </p>
        </div>
        <div className="operations-heading-actions">
          <button
            type="button"
            className="button secondary"
            disabled={loading || !!busy || !companyId}
            onClick={() => void load()}
          >
            <RefreshCw size={15} />
            读取最新监控
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={loading || !!busy || !overview || period !== currentOperationsPeriod()}
            title={
              period !== currentOperationsPeriod()
                ? '汇总提醒针对本月运行状态，历史月份只读取记录'
                : undefined
            }
            onClick={() =>
              void act(
                'refresh',
                `/operations/companies/${encoded(companyId)}/refresh`,
                {},
                '已汇总本企业当前运行状况和站内提醒。',
              )
            }
          >
            <Activity size={15} />
            汇总站内提醒
          </button>
        </div>
      </section>
      <section className="operations-scope-bar">
        <label>
          <span>客户企业</span>
          <select
            aria-label="监控客户企业"
            disabled={!!busy}
            value={companyId}
            onChange={(event) => changeScope(event.target.value, period)}
          >
            {companies.map((company) => (
              <option key={company.id} value={company.id}>
                {company.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>费用月份</span>
          <input
            aria-label="监控费用月份"
            type="month"
            min="2000-01"
            max="2099-12"
            disabled={!!busy}
            value={period}
            onChange={(event) => {
              if (/^\d{4}-(0[1-9]|1[0-2])$/.test(event.target.value))
                changeScope(companyId, event.target.value);
            }}
          />
        </label>
        <p>
          <ShieldCheck size={14} />
          企业聚合监控，不开放员工票据原件
        </p>
      </section>
      {error && (!policyOpen || !overview) && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {notice && (
        <p className="operations-notice form-success" role="status">
          <CheckCircle2 size={15} />
          {notice}
        </p>
      )}
      {!companies.length ? (
        <div className="panel empty-state">
          <div className="empty-icon">
            <ShieldCheck size={27} />
          </div>
          <strong>当前没有可监控的客户企业</strong>
          <p>仅事务所管理者及获授权的企业管理员、财务人员可查看。</p>
        </div>
      ) : loading ? (
        <div className="loading-block">
          <LoaderCircle className="spinner" size={16} />
          正在读取企业费用与运行状态…
        </div>
      ) : (
        overview && (
          <>
            <OperationsBudgetSummary overview={overview} />
            {canManage && (
              <OperationsPerformance
                key={`${session.scope_key}:${companyId}`}
                session={session}
                companyId={companyId}
              />
            )}
            <section className="operations-health-grid" aria-label="后台与核对运行状况">
              <article className="panel">
                <Activity size={20} />
                <h2>后台处理</h2>
                <strong>{overview.workers.available ? '后台可用' : '暂无可用后台'}</strong>
                <p>最近心跳 {dateLabel(overview.workers.last_heartbeat_at)}</p>
                <dl>
                  <div>
                    <dt>等待处理</dt>
                    <dd>{queue!.queued}</dd>
                  </div>
                  <div>
                    <dt>正在处理</dt>
                    <dd>{queue!.running}</dd>
                  </div>
                  <div>
                    <dt>等待重试</dt>
                    <dd>{queue!.retry_wait}</dd>
                  </div>
                  <div>
                    <dt>处理中断</dt>
                    <dd>{queue!.interrupted}</dd>
                  </div>
                </dl>
                <small>最早待处理 {dateLabel(queue!.oldest_pending_at)}</small>
                {overview.assistant && (
                  <dl className="assistant-runtime-summary">
                    <div>
                      <dt>会计对话等待 / 运行</dt>
                      <dd>
                        {overview.assistant.queue.queued} / {overview.assistant.queue.running}
                      </dd>
                    </div>
                    <div>
                      <dt>助手后台</dt>
                      <dd>{overview.assistant.workers.available ? '可用' : '暂不可用'}</dd>
                    </div>
                  </dl>
                )}
              </article>
              <article className="panel">
                <TriangleAlert size={20} />
                <h2>异常与失败</h2>
                <strong>{overview.exceptions.events} 件事项待核对</strong>
                <p>
                  阻断 {overview.exceptions.blocking} 项 · 提醒 {overview.exceptions.warning} 项
                </p>
                <dl>
                  <div>
                    <dt>近期失败</dt>
                    <dd>{queue!.recent_failures}</dd>
                  </div>
                  <div>
                    <dt>处理失败</dt>
                    <dd>{queue!.failed}</dd>
                  </div>
                  <div>
                    <dt>依据变更</dt>
                    <dd>{queue!.stale}</dd>
                  </div>
                  <div>
                    <dt>已取消</dt>
                    <dd>{queue!.cancelled}</dd>
                  </div>
                </dl>
                <small>
                  最早异常 {dateLabel(overview.exceptions.oldest_opened_at)}
                  <br />
                  核对汇总 {dateLabel(overview.exceptions.updated_at)}
                </small>
              </article>
              <article className="panel">
                <FileCheck2 size={20} />
                <h2>供应商报告用量</h2>
                <strong>
                  {overview.usage.reported_requests} / {overview.usage.requests} 次有用量回执
                </strong>
                <p>没有返回的 token 保持未知，费用仍需核对。</p>
                <dl>
                  <div>
                    <dt>输入 token</dt>
                    <dd>{formatTokenCount(overview.usage.input_tokens)}</dd>
                  </div>
                  <div>
                    <dt>输出 token</dt>
                    <dd>{formatTokenCount(overview.usage.output_tokens)}</dd>
                  </div>
                  <div>
                    <dt>费用待核定</dt>
                    <dd>{overview.usage.uncertain_requests}</dd>
                  </div>
                  <div>
                    <dt>处理完成</dt>
                    <dd>{queue!.succeeded}</dd>
                  </div>
                </dl>
                <small>用量回执不等于供应商实际扣费账单。</small>
              </article>
            </section>
            <div className="operations-lower-grid">
              <section className="panel operations-alert-panel">
                <div className="panel-heading">
                  <div>
                    <h2>
                      站内运行提醒
                      <span className="count-label">
                        {overview.alerts.filter((alert) => alert.state === 'open').length}
                      </span>
                    </h2>
                    <p>记录处置依据，恢复状态由后续汇总确认</p>
                  </div>
                </div>
                {overview.alerts.length ? (
                  <div className="operations-alert-list">
                    {overview.alerts.map((alert) => (
                      <article key={alert.id} className={`operations-alert ${alert.state}`}>
                        <div>
                          <TriangleAlert size={16} />
                          <strong>
                            {operationsAlertStateLabels[alert.state] ?? '提醒状态待核对'}
                          </strong>
                          <span>
                            {alert.severity === 'critical' || alert.severity === 'blocking'
                              ? '需优先处理'
                              : '运行提醒'}
                          </span>
                        </div>
                        <p>{alert.message}</p>
                        <small>
                          首次 {dateLabel(alert.opened_at)} · 最近 {dateLabel(alert.last_seen_at)}
                        </small>
                        {(alert.reason || alert.acknowledgement_reason) && (
                          <p className="operations-recorded-reason">
                            处置依据：{alert.reason || alert.acknowledgement_reason}
                          </p>
                        )}
                        {alert.state === 'open' && alert.can_acknowledge !== false && (
                          <button
                            type="button"
                            className="text-button"
                            disabled={!!busy}
                            onClick={() => {
                              setEditor({ kind: 'ack', alert });
                              setReason('');
                              setConfirmedCost('');
                              setError('');
                            }}
                          >
                            记录提醒处置依据
                          </button>
                        )}
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="operations-empty">
                    <CheckCircle2 size={21} />
                    <p>当前没有已记录的运行提醒</p>
                    <small>
                      “汇总站内提醒”根据当前运行状态登记和更新提醒，读取页面不会新增提醒。
                    </small>
                  </div>
                )}
              </section>
              <section className="panel operations-policy-panel">
                <div className="panel-heading">
                  <div>
                    <h2>预算与报价</h2>
                    <p>企业当前策略 · 第 {overview.policy.version} 版</p>
                  </div>
                  {canManage && overview.policy.can_manage && (
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!busy}
                      onClick={() => {
                        setError('');
                        setPolicyOpen(true);
                      }}
                    >
                      <Settings2 size={14} />
                      设置预算策略
                    </button>
                  )}
                </div>
                <div className="operations-policy-summary">
                  <dl>
                    <div>
                      <dt>月度额度</dt>
                      <dd>{formatOperatingCost(overview.policy.monthly_limit)}</dd>
                    </div>
                    <div>
                      <dt>票据识别预留 / 次</dt>
                      <dd>{formatOperatingCost(overview.policy.ocr_reserve)}</dd>
                    </div>
                    <div>
                      <dt>分录建议预留 / 次</dt>
                      <dd>{formatOperatingCost(overview.policy.proposal_reserve)}</dd>
                    </div>
                    <div>
                      <dt>会计对话预留 / 次</dt>
                      <dd>{formatOperatingCost(overview.policy.chat_reserve ?? '0.00')}</dd>
                    </div>
                  </dl>
                  <p className="field-help">
                    报价由系统管理员配置。价格缺失或费用超额时，开启控制的企业暂停新增模型请求。
                  </p>
                  <OperationsPricingRecord pricing={overview.pricing.ocr} label="票据识别模型" />
                  <OperationsPricingRecord
                    pricing={overview.pricing.proposal}
                    label="分录建议模型"
                  />
                  {overview.pricing.chat && (
                    <OperationsPricingRecord pricing={overview.pricing.chat} label="会计对话模型" />
                  )}
                </div>
              </section>
            </div>
            <section className="panel operations-cost-panel">
              <div className="panel-heading">
                <div>
                  <h2>模型费用记录</h2>
                  <p>预留、按用量估算与账单核定保持独立记录</p>
                </div>
                <span className="operations-currency">CNY · 6 位费用精度</span>
              </div>
              {overview.costs.length ? (
                <div className="operations-cost-table-wrap">
                  <table className="operations-cost-table">
                    <thead>
                      <tr>
                        <th>处理类型 / 时间</th>
                        <th>状态 / 模型</th>
                        <th>处理预留</th>
                        <th>按用量估算</th>
                        <th>账单人工核定</th>
                        <th>输入 / 输出 token</th>
                        <th>核对依据</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.costs.map((cost) => (
                        <tr key={cost.id}>
                          <td>
                            <strong>{operatingKindLabel(cost.kind)}</strong>
                            <small>{dateLabel(cost.created_at)}</small>
                          </td>
                          <td>
                            <span
                              className={`badge ${cost.state === 'reconciled' ? 'approved' : cost.state === 'uncertain' ? 'exception' : 'draft'}`}
                            >
                              {operatingCostStateLabels[cost.state] ?? '费用状态待核对'}
                            </span>
                            <small>{cost.model || '模型待核对'}</small>
                          </td>
                          <td>{formatOperatingCost(cost.reserved_cost)}</td>
                          <td>{formatOperatingCost(cost.estimated_cost)}</td>
                          <td>{formatOperatingCost(cost.confirmed_cost)}</td>
                          <td>
                            {formatTokenCount(cost.input_tokens)} /{' '}
                            {formatTokenCount(cost.output_tokens)}
                          </td>
                          <td>
                            {cost.reason && <p>{cost.reason}</p>}
                            {canReconcileOperatingCost(cost, canManage) && (
                              <button
                                type="button"
                                className="text-button"
                                disabled={!!busy}
                                onClick={() => {
                                  setEditor({ kind: 'cost', cost });
                                  setReason('');
                                  setConfirmedCost('');
                                  setError('');
                                }}
                              >
                                核定此项费用
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="operations-empty">
                  <Coins size={23} />
                  <p>本月没有可见模型费用记录</p>
                  <small>实际处理后的用量回执、预留与待核定费用将在这里保存。</small>
                </div>
              )}
            </section>
          </>
        )
      )}
      {policyOpen && overview && (
        <OperationsPolicyDialog
          policy={overview.policy}
          companyName={overview.company.name}
          busy={!!busy}
          error={error}
          pricingReady={overview.pricing.ocr.configured && overview.pricing.proposal.configured}
          onClose={() => {
            setPolicyOpen(false);
            setError('');
          }}
          onSave={(value: OperationsPolicyInput) =>
            act(
              'policy',
              `/operations/companies/${encoded(companyId)}/policy`,
              value,
              '企业预算与运行策略已保存，后续请求按新策略检查。',
              'PUT',
            )
          }
        />
      )}
      {editor && overview && (
        <OperationsRecordDialog
          kind={editor.kind}
          title={editor.kind === 'ack' ? '记录站内提醒处置依据' : '核定供应商模型费用'}
          description={
            editor.kind === 'ack'
              ? editor.alert.message
              : `${operatingKindLabel(editor.cost.kind)} · ${editor.cost.model || '模型待核对'}`
          }
          busy={!!busy}
          reason={reason}
          amount={confirmedCost}
          error={error}
          onReason={setReason}
          onAmount={setConfirmedCost}
          onClose={() => {
            setEditor(null);
            setReason('');
            setConfirmedCost('');
            setError('');
          }}
          onSave={saveEditor}
        />
      )}
    </main>
  );
}

function OperationsRecordDialog({
  kind,
  title,
  description,
  busy,
  reason,
  amount,
  error,
  onReason,
  onAmount,
  onClose,
  onSave,
}: {
  kind: 'ack' | 'cost';
  title: string;
  description: string;
  busy: boolean;
  reason: string;
  amount: string;
  error: string;
  onReason: (value: string) => void;
  onAmount: (value: string) => void;
  onClose: () => void;
  onSave: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal operations-record-modal"
      aria-labelledby="operations-record-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="operations-record-title">{title}</h2>
          <p>{description}</p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="关闭监控处理记录"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void onSave();
        }}
      >
        <div className="form-content operations-record-content">
          {kind === 'cost' && (
            <label>
              <span className="field-label">供应商账单核定金额（人民币元）</span>
              <input
                aria-label="供应商费用核定金额"
                inputMode="decimal"
                value={amount}
                disabled={busy}
                onChange={(event) => onAmount(event.target.value)}
                placeholder="例如 1.25，可填 0.00"
              />
            </label>
          )}
          <label>
            <span className="field-label">
              {kind === 'cost' ? '供应商账单或核对依据' : '提醒处置依据'}
            </span>
            <textarea
              aria-label={kind === 'cost' ? '供应商费用核对依据' : '站内提醒处置依据'}
              rows={4}
              maxLength={1000}
              required
              disabled={busy}
              value={reason}
              onChange={(event) => onReason(event.target.value)}
              placeholder={
                kind === 'cost'
                  ? '填写供应商账单编号、核对范围和金额依据'
                  : '填写核对结果、采取的措施及后续处理依据'
              }
            />
          </label>
          {error && (
            <p className="form-error" role="alert">
              <TriangleAlert size={15} />
              {error}
            </p>
          )}
          <p className="field-help">
            {kind === 'cost'
              ? '人工核定仅更新模型服务费用记录，原 token 用量与估算仍保留，不修改会计账务。'
              : '记录处置不代表问题已恢复。后台继续核对实际运行状态，提醒不会自动写入外部应用。'}
          </p>
        </div>
        <div className="modal-footer">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !reason.trim() || (kind === 'cost' && !amount.trim())}
          >
            {busy ? <LoaderCircle className="spinner" size={15} /> : <CheckCircle2 size={15} />}
            {kind === 'cost' ? '保存费用核定' : '保存提醒处置'}
          </button>
        </div>
      </form>
    </dialog>
  );
}
