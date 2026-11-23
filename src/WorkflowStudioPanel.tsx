import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import {
  amountLabel,
  BOX_HEIGHT,
  BOX_WIDTH,
  centsFromYuanInput,
  clip,
  diffLines,
  layoutWorkflow,
  nodeLabel,
  roleLabel,
  rollbackBlockReason,
  routingIssues,
  routingLabel,
  shortFingerprint,
  statusLabel,
  actionLabel,
  yuanInputFromCents,
  type WorkflowDefinition,
  type WorkflowDiff,
  type WorkflowInstance,
  type WorkflowInstanceList,
  type WorkflowNode,
  type WorkflowOverview,
  type WorkflowRouting,
} from './workflow-studio';
import './workflow-studio.css';

// 输入框默认填写原因，因此「人工接管/挂起/回退」不会因为原因为空被服务端拒绝。
const DEFAULT_ACTION_REASON = '人工接管：核对异常节点';

const ROLE_OPTIONS = ['creator', 'reviewer', 'manager', 'finance', 'cashier'];

/** 纯展示：原生 SVG 渲染 DAG 节点、条件分支与状态标识（零第三方图形库）。 */
export function WorkflowDiagram({
  nodes,
  ariaLabel,
}: {
  nodes: WorkflowNode[];
  ariaLabel: string;
}) {
  const layout = useMemo(() => layoutWorkflow(nodes), [nodes]);
  return (
    <svg
      className="workflow-svg"
      role="img"
      aria-label={ariaLabel}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      width={layout.width}
      height={layout.height}
    >
      <defs>
        <marker
          id="workflow-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" className="workflow-arrow" />
        </marker>
      </defs>
      {layout.links.map((link) => (
        <g key={`${link.from}-${link.to}-${link.label}`}>
          <path
            d={link.path}
            className={link.fallback ? 'workflow-edge fallback' : 'workflow-edge'}
            markerEnd="url(#workflow-arrow)"
          />
          <text x={link.labelX} y={link.labelY} className="workflow-edge-label">
            {link.label}
          </text>
        </g>
      ))}
      {layout.boxes.map((box) => (
        <g key={box.id} transform={`translate(${box.x},${box.y})`}>
          <rect
            width={BOX_WIDTH}
            height={BOX_HEIGHT}
            rx="10"
            className={[
              'workflow-node',
              box.start ? 'start' : '',
              box.terminal ? 'terminal' : '',
              box.approval ? 'approval' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          />
          <text x="12" y="24" className="workflow-node-label">
            {box.label}
          </text>
          <text x="12" y="44" className="workflow-node-meta">
            {box.role}
            {box.approval ? ' · 需审批' : ''}
            {box.terminal ? ' · 结束' : ''}
            {box.irreversible ? ' · 不可逆' : ''}
            {box.external_locked ? ' · 已报外部' : ''}
          </text>
          {box.start && (
            <text x={BOX_WIDTH - 12} y="20" textAnchor="end" className="workflow-node-badge">
              起点
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

/** 纯展示：版本差异行，口径全部由服务端 diff 结构派生。 */
export function DiffList({ diff, nodes }: { diff: WorkflowDiff; nodes?: WorkflowNode[] }) {
  const lines = diffLines(diff, nodes ?? []);
  return (
    <ul className="workflow-diff">
      {diff.from && diff.to && (
        <li className="workflow-diff-head">
          版本 {diff.from.version}（{statusLabel(diff.from.status)}） → 版本 {diff.to.version}（
          {statusLabel(diff.to.status)}）
        </li>
      )}
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

type Draft = {
  auto: string;
  dual: string;
  hours: string;
  note: string;
  roles: Record<string, string>;
};

function draftFrom(rule: { nodes: WorkflowNode[]; routing: WorkflowRouting }): Draft {
  const roles: Record<string, string> = {};
  for (const node of rule.nodes) roles[node.id] = node.role;
  return {
    auto: yuanInputFromCents(rule.routing.auto_approve_below_cents),
    dual: yuanInputFromCents(rule.routing.dual_approval_from_cents),
    hours: String(rule.routing.escalate_after_hours),
    note: '',
    roles,
  };
}

export default function WorkflowStudioPanel({
  session,
  companyId,
  companyName,
  onBusy,
  onAudit,
}: {
  session: Session;
  companyId: string;
  companyName: string;
  onBusy?: (busy: boolean) => void;
  onAudit?: () => void;
}) {
  const [overview, setOverview] = useState<WorkflowOverview>();
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [kind, setKind] = useState('expense_reimbursement');
  const [viewId, setViewId] = useState('');
  const [draft, setDraft] = useState<Draft>();
  const [diffFrom, setDiffFrom] = useState('');
  const [diffTo, setDiffTo] = useState('');
  const [diff, setDiff] = useState<WorkflowDiff>();
  const [rollbackId, setRollbackId] = useState('');
  const [rollbackNote, setRollbackNote] = useState('');
  const [reasons, setReasons] = useState<Record<string, string>>({});

  useEffect(() => {
    onBusy?.(busy);
  }, [busy, onBusy]);

  const process = useMemo(
    () => overview?.processes.find((item) => item.process_kind === kind),
    [overview, kind],
  );
  const view: WorkflowDefinition | undefined = useMemo(
    () =>
      process?.versions.find((item) => item.id === viewId) ??
      process?.active ??
      process?.versions[0],
    [process, viewId],
  );

  const load = useCallback(
    async (signal?: AbortSignal) => {
      const [next, list] = await Promise.all([
        api<WorkflowOverview>(
          `/workflow/overview?company_id=${encodeURIComponent(companyId)}`,
          session,
          { signal },
        ),
        api<WorkflowInstanceList>(
          `/workflow/instances?company_id=${encodeURIComponent(companyId)}`,
          session,
          { signal },
        ),
      ]);
      if (signal?.aborted) return;
      setOverview(next);
      setInstances(list.items ?? []);
      setLoading(false);
    },
    [companyId, session],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) {
        setError(errorMessage(failure));
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!process) return;
    const preferred =
      process.versions.find((item) => item.id === viewId) ?? process.active ?? process.versions[0];
    setViewId(preferred?.id ?? '');
    // 尚未发布任何版本时用内置模板做编辑底稿，保证第一版可以直接发布。
    setDraft(draftFrom(preferred ?? process.template));
    setDiff(undefined);
    setDiffFrom('');
    setDiffTo('');
    setRollbackId('');
    // viewId 变化自身会重算 draft，这里只在切换流程类型/数据刷新时重置。
  }, [process, viewId]);

  async function refresh() {
    setBusy(true);
    try {
      await load();
      setError('');
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function save(publish: boolean) {
    if (!process || !draft) return;
    const auto = centsFromYuanInput(draft.auto);
    const dual = centsFromYuanInput(draft.dual);
    const hours = Number(draft.hours);
    if (auto === null || dual === null) {
      setError('门槛金额只能填元，最多两位小数，不能为负数');
      return;
    }
    if (!Number.isInteger(hours) || hours < 1 || hours > 720) {
      setError('超时升级时限必须是 1 到 720 之间的整数小时');
      return;
    }
    const routing: WorkflowRouting = {
      auto_approve_below_cents: auto,
      dual_approval_from_cents: dual,
      escalate_after_hours: hours,
    };
    const issues = routingIssues(routing);
    if (issues.length) {
      setError(issues[0]);
      return;
    }
    const base = view ?? process.template;
    const nodes = base.nodes.map((node) => ({ ...node, role: draft.roles[node.id] ?? node.role }));
    setBusy(true);
    setError('');
    try {
      const created = await api<WorkflowDefinition>('/workflow/definitions', session, {
        method: 'POST',
        body: {
          company_id: companyId,
          process_kind: process.process_kind,
          nodes,
          routing,
          note: draft.note,
          publish,
        },
      });
      await load();
      setViewId(created.id);
      setDraft(draftFrom(created));
      setNotice(
        publish
          ? `已发布新版本 v${created.version}；进行中的流程仍按启动时锁定的版本走完`
          : `已保存草稿 v${created.version}`,
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function publishExisting(definition: WorkflowDefinition) {
    setBusy(true);
    setError('');
    try {
      const published = await api<WorkflowDefinition>(
        `/workflow/definitions/${encodeURIComponent(definition.id)}/publish`,
        session,
        { method: 'POST', body: { note: '' } },
      );
      await load();
      setViewId(published.id);
      setNotice(`版本 v${published.version} 已生效，旧生效版已归档`);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function rollback(definition: WorkflowDefinition) {
    if (rollbackNote.trim().length < 2) {
      setError('回滚必须填写原因，便于审计追溯');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const created = await api<WorkflowDefinition>(
        `/workflow/definitions/${encodeURIComponent(definition.id)}/rollback`,
        session,
        { method: 'POST', body: { note: rollbackNote } },
      );
      await load();
      setViewId(created.id);
      setRollbackId('');
      setRollbackNote('');
      setNotice(
        `已按版本 v${definition.version} 创建新生效版 v${created.version}；历史版本行与指纹未改动`,
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function compare() {
    if (!diffFrom || !diffTo) {
      setError('请选择要对比的两个版本');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const value = await api<WorkflowDiff>(
        `/workflow/diff?from_id=${encodeURIComponent(diffFrom)}&to_id=${encodeURIComponent(diffTo)}`,
        session,
      );
      setDiff(value);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function act(instance: WorkflowInstance, action: string) {
    const reason = (reasons[instance.id] ?? DEFAULT_ACTION_REASON).trim();
    if (
      (action === 'rollback' || action === 'suspend' || action === 'resume') &&
      reason.length < 2
    ) {
      setError('该动作必须填写原因（至少两个字）');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api<WorkflowInstance>(
        `/workflow/instances/${encodeURIComponent(instance.id)}/actions`,
        session,
        { method: 'POST', body: { action, reason } },
      );
      await load();
      setNotice(
        action === 'rollback'
          ? '已回退一步，被回退节点的审批作废'
          : action === 'resume'
            ? '已人工接管并恢复办理'
            : '已挂起，等待人工处理',
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="loading-block">
        <Spinner />
        正在读取流程编排中台…
      </div>
    );
  }
  if (!overview) {
    return (
      <p className="form-error" role="alert">
        {error || '流程编排中台数据不可用'}
      </p>
    );
  }

  const active = process?.active ?? null;
  const canManage = overview.can_manage;
  const kindInstances = instances.filter((item) => item.process_kind === kind);

  return (
    <section className="workflow-studio" aria-label={`流程编排中台 - ${companyName}`}>
      <header className="workflow-studio-head">
        <div>
          <h2>流程编排中台</h2>
          <p className="field-help">
            规则版本 {overview.routing_rule_version}
            ；发版后存量流程按启动时锁定的版本走完，新流程才用新生效版。
          </p>
        </div>
        <button className="button secondary small" disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={14} />
          刷新
        </button>
      </header>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="workflow-notice" role="status">
          {notice}
          {onAudit && (
            <button className="text-button" onClick={onAudit}>
              查看操作记录
            </button>
          )}
        </p>
      )}

      <div className="workflow-kinds" role="tablist" aria-label="四类财务流程">
        {overview.processes.map((item) => (
          <button
            key={item.process_kind}
            role="tab"
            aria-selected={item.process_kind === kind}
            disabled={busy}
            onClick={() => {
              setKind(item.process_kind);
              setViewId('');
            }}
          >
            {item.label}
            <span className="workflow-kind-meta">
              {item.active ? `v${item.active.version}` : '未发布'}
            </span>
          </button>
        ))}
      </div>

      {process && (
        <>
          <p className="workflow-summary">{process.summary}</p>
          <div className="workflow-grid">
            <section aria-label="版本与可视化流程">
              <h3>
                {process.label} · {view ? `版本 v${view.version}` : '尚未发布'}
                {active && view && active.id === view.id ? '（生效中）' : ''}
              </h3>
              <div className="workflow-canvas">
                <WorkflowDiagram
                  nodes={view?.nodes ?? process.template.nodes}
                  ariaLabel={`${process.label}流程图`}
                />
              </div>
              {view && (
                <p className="workflow-fingerprint" title={view.content_fingerprint}>
                  指纹 {shortFingerprint(view.content_fingerprint)}
                  {view.rollback_from_version ? ` · 回滚自 v${view.rollback_from_version}` : ''}
                  {view.note ? ` · 备注 ${view.note}` : ''}
                </p>
              )}
              <table className="workflow-table">
                <caption>版本历史（历史行与指纹不可篡改）</caption>
                <thead>
                  <tr>
                    <th scope="col">版本</th>
                    <th scope="col">状态</th>
                    <th scope="col">发布时间</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {process.versions.length === 0 && (
                    <tr>
                      <td colSpan={4}>尚未创建版本，可直接用内置模板发布第一版。</td>
                    </tr>
                  )}
                  {process.versions.map((item) => (
                    <tr key={item.id} className={item.id === view?.id ? 'selected' : undefined}>
                      <td>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() => {
                            setViewId(item.id);
                            setDraft(draftFrom(item));
                          }}
                        >
                          v{item.version}
                        </button>
                      </td>
                      <td>{statusLabel(item.status)}</td>
                      <td>
                        {item.published_at ? item.published_at.replace('T', ' ').slice(0, 16) : '—'}
                      </td>
                      <td className="workflow-actions">
                        {item.status === 'draft' && canManage && (
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => void publishExisting(item)}
                          >
                            发布
                          </button>
                        )}
                        {item.status !== 'active' && canManage && (
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => {
                              setRollbackId(item.id === rollbackId ? '' : item.id);
                              setRollbackNote('');
                            }}
                          >
                            回滚到此版本
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rollbackId && (
                <form
                  className="workflow-inline-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const target = process.versions.find((item) => item.id === rollbackId);
                    if (target) void rollback(target);
                  }}
                >
                  <label htmlFor="workflow-rollback-note">回滚原因</label>
                  <input
                    id="workflow-rollback-note"
                    value={rollbackNote}
                    maxLength={120}
                    onChange={(event) => setRollbackNote(event.target.value)}
                    placeholder="例如：新版审批门槛写错，回退到上一版规则"
                  />
                  <button className="button small" type="submit" disabled={busy}>
                    确认回滚（新建版本号）
                  </button>
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => {
                      setRollbackId('');
                      setRollbackNote('');
                    }}
                  >
                    取消
                  </button>
                </form>
              )}
            </section>

            <section aria-label="规则编辑与版本对比">
              <h3>门槛与审批角色</h3>
              {!canManage && (
                <p className="field-help">只有事务所管理员或企业管理员可以编辑并发布流程规则。</p>
              )}
              {draft && (
                <div className="workflow-editor">
                  <label htmlFor="workflow-auto">
                    {routingLabel('auto_approve_below_cents')}（元，低于此金额直通草稿）
                    <input
                      id="workflow-auto"
                      inputMode="decimal"
                      value={draft.auto}
                      disabled={!canManage || busy}
                      onChange={(event) => setDraft({ ...draft, auto: event.target.value })}
                    />
                  </label>
                  <label htmlFor="workflow-dual">
                    {routingLabel('dual_approval_from_cents')}（元，达到此金额需双人审批）
                    <input
                      id="workflow-dual"
                      inputMode="decimal"
                      value={draft.dual}
                      disabled={!canManage || busy}
                      onChange={(event) => setDraft({ ...draft, dual: event.target.value })}
                    />
                  </label>
                  <label htmlFor="workflow-hours">
                    {routingLabel('escalate_after_hours')}（小时）
                    <input
                      id="workflow-hours"
                      inputMode="numeric"
                      value={draft.hours}
                      disabled={!canManage || busy}
                      onChange={(event) => setDraft({ ...draft, hours: event.target.value })}
                    />
                  </label>
                  <fieldset className="workflow-roles">
                    <legend>审批角色</legend>
                    {(view?.nodes ?? process.template.nodes)
                      .filter((node) => node.approval)
                      .map((node) => (
                        <label key={node.id} htmlFor={`workflow-role-${node.id}`}>
                          {node.label}
                          <select
                            id={`workflow-role-${node.id}`}
                            value={draft.roles[node.id] ?? node.role}
                            disabled={!canManage || busy}
                            onChange={(event) =>
                              setDraft({
                                ...draft,
                                roles: { ...draft.roles, [node.id]: event.target.value },
                              })
                            }
                          >
                            {ROLE_OPTIONS.map((role) => (
                              <option key={role} value={role}>
                                {roleLabel(role)}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                  </fieldset>
                  <label htmlFor="workflow-note">
                    变更说明
                    <input
                      id="workflow-note"
                      value={draft.note}
                      maxLength={200}
                      disabled={!canManage || busy}
                      placeholder="例如：把报销双人审批门槛由 5,000 元下调到 3,000 元"
                      onChange={(event) => setDraft({ ...draft, note: event.target.value })}
                    />
                  </label>
                  <div className="workflow-editor-actions">
                    <button
                      className="button secondary small"
                      type="button"
                      disabled={!canManage || busy}
                      onClick={() => void save(false)}
                    >
                      保存为新草稿版本
                    </button>
                    <button
                      className="button small"
                      type="button"
                      disabled={!canManage || busy}
                      onClick={() => void save(true)}
                    >
                      保存并发布
                    </button>
                  </div>
                </div>
              )}

              <h3>版本 Diff</h3>
              <div className="workflow-diff-picker">
                <label htmlFor="workflow-diff-from">
                  起始版本
                  <select
                    id="workflow-diff-from"
                    value={diffFrom}
                    onChange={(event) => setDiffFrom(event.target.value)}
                  >
                    <option value="">请选择</option>
                    {process.versions.map((item) => (
                      <option key={item.id} value={item.id}>
                        v{item.version} · {statusLabel(item.status)}
                      </option>
                    ))}
                  </select>
                </label>
                <label htmlFor="workflow-diff-to">
                  目标版本
                  <select
                    id="workflow-diff-to"
                    value={diffTo}
                    onChange={(event) => setDiffTo(event.target.value)}
                  >
                    <option value="">请选择</option>
                    {process.versions.map((item) => (
                      <option key={item.id} value={item.id}>
                        v{item.version} · {statusLabel(item.status)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="button secondary small"
                  type="button"
                  disabled={busy}
                  onClick={() => void compare()}
                >
                  对比
                </button>
              </div>
              {diff && <DiffList diff={diff} nodes={view?.nodes} />}
            </section>
          </div>

          <section aria-label="流程实例" className="workflow-instances">
            <h3>
              流程实例（{process.label} {kindInstances.length} 条）
            </h3>
            {kindInstances.length === 0 && <p className="field-help">该流程暂时没有实例。</p>}
            {kindInstances.map((instance) => {
              const blocked = rollbackBlockReason(instance);
              const reason = reasons[instance.id] ?? DEFAULT_ACTION_REASON;
              return (
                <article key={instance.id} className="workflow-instance">
                  <header>
                    <strong>{amountLabel(instance.amount_cents)}</strong>
                    <span>{statusLabel(instance.status)}</span>
                    <span>锁定版本 v{instance.definition_version}</span>
                    <span>当前节点 {nodeLabel(view?.nodes ?? [], instance.current_node)}</span>
                  </header>
                  <p className="field-help">
                    路径：{instance.path.map((id) => nodeLabel(view?.nodes ?? [], id)).join(' → ')}
                  </p>
                  {instance.suspended_reason && (
                    <p className="workflow-suspended">挂起原因：{instance.suspended_reason}</p>
                  )}
                  {blocked && <p className="workflow-blocked">不可回退：{blocked}</p>}
                  {instance.logs && instance.logs.length > 0 && (
                    <ol className="workflow-logs">
                      {instance.logs.map((log, index) => (
                        <li key={`${log.created_at}-${index}`}>
                          {log.created_at.replace('T', ' ').slice(0, 16)} ·{' '}
                          {actionLabel(log.action)}
                          {log.reason ? ` · ${clip(log.reason, 40)}` : ''}
                        </li>
                      ))}
                    </ol>
                  )}
                  {instance.status !== 'completed' && instance.status !== 'rejected' && (
                    <div className="workflow-instance-actions">
                      <label htmlFor={`workflow-reason-${instance.id}`}>
                        办理原因
                        <input
                          id={`workflow-reason-${instance.id}`}
                          value={reason}
                          maxLength={120}
                          disabled={busy}
                          onChange={(event) =>
                            setReasons({ ...reasons, [instance.id]: event.target.value })
                          }
                        />
                      </label>
                      {instance.status === 'suspended' && (
                        <button
                          className="button small"
                          type="button"
                          disabled={busy}
                          onClick={() => void act(instance, 'resume')}
                        >
                          人工接管并恢复
                        </button>
                      )}
                      {instance.status === 'running' && (
                        <button
                          className="button secondary small"
                          type="button"
                          disabled={busy}
                          onClick={() => void act(instance, 'suspend')}
                        >
                          挂起等待人工处理
                        </button>
                      )}
                      <button
                        className="button secondary small"
                        type="button"
                        disabled={busy || blocked !== null}
                        onClick={() => void act(instance, 'rollback')}
                      >
                        安全回退一步
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        </>
      )}
    </section>
  );
}
