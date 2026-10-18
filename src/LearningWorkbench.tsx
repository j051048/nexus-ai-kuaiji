import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDown,
  BookOpen,
  CheckCircle2,
  FileCheck2,
  History,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import {
  definitionFromSample,
  learningBusinessTypes,
  learningCategories,
  learningStatus,
  ruleUsageSummary,
} from './learning-types';
import type {
  LearningDefinition,
  LearningDetail,
  LearningEvaluation,
  LearningGovernance,
  LearningLine,
  LearningOverview,
  LearningRule,
  LearningRuleVersion,
} from './learning-types';
import './learning.css';

const encoded = encodeURIComponent;
const message = (error: unknown) =>
  error instanceof Error ? error.message : '操作未完成，请读取最新状态后重试。';
type Account = { code: string; name: string; active: boolean };

export function LearningWorkbench({
  session,
  bookId,
  onBack,
  onBusyChange,
  onChanged,
  embedded = false,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => void;
  embedded?: boolean;
}) {
  const [overview, setOverview] = useState<LearningOverview | null>(null),
    [accounts, setAccounts] = useState<Account[]>([]);
  const [governance, setGovernance] = useState<LearningGovernance | null>(null);
  const [offset, setOffset] = useState(0),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [selected, setSelected] = useState(''),
    [negativeIds, setNegativeIds] = useState<string[]>([]),
    [evaluation, setEvaluation] = useState<LearningEvaluation | null>(null);
  const [detail, setDetail] = useState<LearningDetail | null>(null),
    [editor, setEditor] = useState<{
      definition: LearningDefinition;
      sampleIds: string[];
      rule?: LearningRule;
    } | null>(null);
  const [history, setHistory] = useState<{ name: string; versions: LearningRuleVersion[] } | null>(
    null,
  );
  const [command, setCommand] = useState<'publish' | 'retire' | null>(null),
    [reason, setReason] = useState('');
  const epoch = useRef(0),
    commandEpoch = useRef(0),
    scopeEpoch = useRef(0),
    active = useRef(true),
    locked = useRef(false);
  const target = `${session.scope_key}:${bookId}`,
    targetRef = useRef(target);
  targetRef.current = target;
  const path = `/books/${encoded(bookId)}/learning`;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange?.(busy || !!editor || !!command);
    return () => onBusyChange?.(false);
  }, [busy, editor, command, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!bookId) return;
      const current = ++epoch.current;
      setLoading(true);
      try {
        const [data, catalog, policy] = await Promise.all([
          api<LearningOverview>(
            `/books/${encoded(bookId)}/learning?limit=20&offset=${offset}`,
            session,
            { signal },
          ),
          api<{ accounts: Account[] }>(`/books/${encoded(bookId)}/catalog`, session, { signal }),
          api<LearningGovernance>(`/books/${encoded(bookId)}/learning/governance`, session, {
            signal,
          }),
        ]);
        if (active.current && !signal?.aborted && epoch.current === current) {
          setOverview(data);
          setAccounts(catalog.accounts.filter((account) => account.active));
          setGovernance(policy);
          setError('');
        }
      } catch (failure) {
        if (active.current && !signal?.aborted && epoch.current === current) {
          setOverview(null);
          setGovernance(null);
          setAccounts([]);
          setError(message(failure));
        }
      } finally {
        if (active.current && !signal?.aborted && epoch.current === current) setLoading(false);
      }
    },
    [bookId, offset, session],
  );
  useEffect(() => {
    scopeEpoch.current += 1;
    commandEpoch.current += 1;
    locked.current = false;
    setBusy(false);
    setOffset(0);
    setOverview(null);
    setGovernance(null);
    setAccounts([]);
    setSelected('');
    setNegativeIds([]);
    setEvaluation(null);
    setDetail(null);
    setHistory(null);
    setEditor(null);
    setCommand(null);
    setReason('');
    setError('');
    setNotice('');
  }, [target]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  const rule = overview?.rules.find((item) => item.id === selected) ?? null;
  const usage = rule ? ruleUsageSummary(governance?.usage ?? [], rule.id) : null;

  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    const currentTarget = target,
      currentCommand = ++commandEpoch.current;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      if (
        active.current &&
        currentTarget === targetRef.current &&
        commandEpoch.current === currentCommand
      )
        setError(message(failure));
    } finally {
      if (commandEpoch.current === currentCommand) locked.current = false;
      if (
        active.current &&
        currentTarget === targetRef.current &&
        commandEpoch.current === currentCommand
      )
        setBusy(false);
    }
  }
  async function openSample(id: string, build = false) {
    const currentTarget = target,
      currentScope = scopeEpoch.current;
    await run(async () => {
      const item = await api<LearningDetail>(`${path}/samples/${encoded(id)}`, session);
      if (
        currentTarget !== targetRef.current ||
        !active.current ||
        currentScope !== scopeEpoch.current
      )
        return;
      if (build) {
        setEditor({ definition: definitionFromSample(item), sampleIds: [item.id] });
        setReason('');
      } else setDetail(item);
    });
  }
  async function saveDefinition(
    definition: LearningDefinition,
    sampleIds: string[],
    reasonText: string,
  ) {
    const currentTarget = target,
      currentScope = scopeEpoch.current,
      currentEditor = editor;
    if (!currentEditor) return;
    await run(async () => {
      const result = await api<LearningRule>(
        `${path}/rules${currentEditor.rule ? `/${encoded(currentEditor.rule.id)}` : ''}`,
        session,
        {
          method: currentEditor.rule ? 'PUT' : 'POST',
          body: {
            definition,
            sample_ids: sampleIds,
            reason: reasonText,
            ...(currentEditor.rule
              ? {
                  expected_version: currentEditor.rule.current_version,
                  expected_state_version: currentEditor.rule.state_version,
                }
              : {}),
          },
        },
      );
      if (
        currentTarget !== targetRef.current ||
        !active.current ||
        currentScope !== scopeEpoch.current
      )
        return;
      setEditor(null);
      setSelected(result.id);
      setNegativeIds([]);
      setEvaluation(null);
      setNotice('已保存待发布版本。须选择真实反例并完成评估，规则才可发布。');
      await load();
    });
  }
  async function openHistory() {
    const currentTarget = target,
      currentScope = scopeEpoch.current,
      currentRule = rule;
    if (!currentRule) return;
    await run(async () => {
      const result = await api<LearningRule & { versions: LearningRuleVersion[] }>(
        `${path}/rules/${encoded(currentRule.id)}`,
        session,
      );
      if (
        active.current &&
        currentTarget === targetRef.current &&
        currentScope === scopeEpoch.current
      )
        setHistory({ name: result.definition.name, versions: result.versions });
    });
  }
  async function evaluateRule() {
    const currentTarget = target,
      currentScope = scopeEpoch.current,
      currentRule = rule;
    if (!currentRule || !negativeIds.length) return;
    await run(async () => {
      const result = await api<LearningEvaluation>(
        `${path}/rules/${encoded(currentRule.id)}/evaluate`,
        session,
        {
          method: 'POST',
          body: { expected_version: currentRule.current_version, negative_sample_ids: negativeIds },
        },
      );
      if (
        active.current &&
        currentTarget === targetRef.current &&
        currentScope === scopeEpoch.current
      )
        setEvaluation(result);
    });
  }
  async function changeState() {
    const currentTarget = target,
      currentScope = scopeEpoch.current,
      currentRule = rule,
      currentCommand = command;
    if (!currentRule || !currentCommand || !reason.trim()) return;
    await run(async () => {
      await api(`${path}/rules/${encoded(currentRule.id)}/${currentCommand}`, session, {
        method: 'POST',
        body: {
          expected_version: currentRule.current_version,
          expected_state_version: currentRule.state_version,
          reason: reason.trim(),
          ...(currentCommand === 'publish' ? { evaluation_id: evaluation?.id } : {}),
        },
      });
      if (
        !active.current ||
        currentTarget !== targetRef.current ||
        currentScope !== scopeEpoch.current
      )
        return;
      setCommand(null);
      setReason('');
      setEvaluation(null);
      setNegativeIds([]);
      setNotice(
        currentCommand === 'publish'
          ? '规则已显式发布。后续匹配产生待审核草稿，最终仍由会计复核。'
          : '规则已停用，后续事项停止使用；已生成凭证保留原版本依据。',
      );
      await load();
      onChanged?.();
    });
  }
  function chooseRule(id: string) {
    setSelected(id);
    setNegativeIds([]);
    setEvaluation(null);
    setError('');
  }

  const Wrapper = embedded ? 'div' : 'main';
  return (
    <Wrapper className={'learning-workbench' + (embedded ? ' embedded' : '')}>
      {embedded ? (
        <section className="book-embedded-heading">
          <div>
            <h2>审核样例与规则</h2>
            <p>从已审核凭证积累样例，经过正反例评估后才会被用于后续草稿。</p>
          </div>
          <button
            className="button secondary small"
            disabled={busy || loading || !!editor || !!command}
            onClick={() => {
              setEvaluation(null);
              void load();
            }}
          >
            <RefreshCw size={15} />
            读取最新状态
          </button>
        </section>
      ) : (
        <section className="page-heading">
          <div>
            <p className="eyebrow">REVIEWED EXPERIENCE</p>
            <h1>把修正，变成下一次的依据。</h1>
            <p className="page-description">
              从已审核凭证积累企业样例；规则经过正反例评估和会计发布后，才用于后续草稿。
            </p>
          </div>
          <div className="learning-actions">
            <button
              className="button secondary"
              disabled={busy || !!editor || !!command}
              onClick={onBack}
            >
              返回账套
            </button>
            <button
              className="button secondary"
              disabled={busy || loading || !!editor || !!command}
              onClick={() => {
                setEvaluation(null);
                void load();
              }}
            >
              <RefreshCw size={15} />
              读取最新状态
            </button>
          </div>
        </section>
      )}
      {error && !editor && !command && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {!bookId ? (
        <section className="panel empty-state">
          <BookOpen size={28} />
          <strong>当前没有获授权的账套</strong>
        </section>
      ) : loading && !overview ? (
        <div className="loading-block">
          <LoaderCircle size={17} className="spinner" />
          读取本账套审核经验…
        </div>
      ) : (
        overview && (
          <>
            <section className="learning-boundary">
              <div>
                <ShieldCheck size={18} />
                <strong>
                  {overview.book_name} · {overview.period}
                </strong>
              </div>
              <p>
                本页样例和规则限定当前企业、账套及期间。会计修正保存为待发布建议，停用或修订可立即暂停后续使用。
              </p>
            </section>
            {governance && (
              <section className="learning-governance" aria-label="企业规则治理">
                <div className="learning-governance-metrics">
                  <div>
                    <strong>{governance.counterparties.length}</strong>
                    <span>有效往来方</span>
                  </div>
                  <div>
                    <strong>{governance.conflicts.length}</strong>
                    <span>范围冲突待处理</span>
                  </div>
                  <div>
                    <strong>{governance.duplicate_groups.length}</strong>
                    <span>历史同义候选组</span>
                  </div>
                </div>
                <p>
                  稳定往来方规则仅使用当前有效主数据和独立审核别名。重复候选不合并原件；发布前重新检查范围冲突。
                </p>
                {governance.conflicts.map((item) => (
                  <button
                    className="learning-governance-conflict"
                    key={item.rule_id}
                    disabled={busy}
                    onClick={() => chooseRule(item.rule_id)}
                  >
                    <TriangleAlert size={15} />
                    <span>
                      {overview.rules.find((value) => value.id === item.rule_id)?.definition.name ??
                        '企业规则'}
                      ：与 {item.conflicts.map((value) => value.name).join('、')} 范围重叠
                    </span>
                  </button>
                ))}
              </section>
            )}
            <div className="learning-columns">
              <section className="panel learning-examples">
                <div className="panel-heading">
                  <div>
                    <h2>
                      审核样例<span className="count-label">{overview.pagination.total}</span>
                    </h2>
                    <p>只收录完整审核通过的凭证，保留原处理与最终处理。</p>
                  </div>
                  <FileCheck2 size={21} />
                </div>
                {overview.samples.length ? (
                  <div className="learning-sample-list">
                    {overview.samples.map((sample) => (
                      <article key={sample.id} className="learning-sample">
                        <div>
                          <strong>{sample.counterparty || '未填写交易对方'}</strong>
                          <span className={`badge ${sample.corrected ? 'exception' : 'approved'}`}>
                            {sample.corrected ? '经会计修正' : '审核通过'}
                          </span>
                        </div>
                        <p>
                          {sample.source_kind === 'bank'
                            ? '银行流水'
                            : learningBusinessTypes[
                                sample.business_type as keyof typeof learningBusinessTypes
                              ]}{' '}
                          · {sample.direction === 'expense' ? '支出' : '收入'}{' '}
                          <b>¥ {sample.amount}</b>
                        </p>
                        <small>
                          审核 v{sample.reviewed_version} · {sample.created_at.slice(0, 10)}
                        </small>
                        {sample.rule_scope_reason && (
                          <p className="learning-scope-note">{sample.rule_scope_reason}</p>
                        )}
                        <div className="learning-sample-actions">
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => void openSample(sample.id)}
                          >
                            <History size={13} />
                            查看原处理与修正
                          </button>
                          {overview.can_manage && (
                            <button
                              className="text-button"
                              disabled={busy || sample.ordinary_rule_eligible === false}
                              onClick={() => void openSample(sample.id, true)}
                            >
                              <Plus size={13} />
                              以此建立规则
                            </button>
                          )}
                        </div>
                        {rule?.status === 'draft' && overview.can_manage && (
                          <label className="learning-counterexample">
                            <input
                              type="checkbox"
                              aria-label={`选为反例 ${sample.counterparty} ${sample.id}`}
                              disabled={
                                busy ||
                                sample.ordinary_rule_eligible === false ||
                                rule.sample_ids.includes(sample.id)
                              }
                              checked={negativeIds.includes(sample.id)}
                              onChange={(event) => {
                                setNegativeIds((value) =>
                                  event.target.checked
                                    ? [...value, sample.id]
                                    : value.filter((id) => id !== sample.id),
                                );
                                setEvaluation(null);
                              }}
                            />
                            <span>
                              {rule.sample_ids.includes(sample.id)
                                ? '此样例为当前规则正例'
                                : '选为当前规则的真实反例'}
                            </span>
                          </label>
                        )}
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="learning-empty">
                    <FileCheck2 size={25} />
                    <p>还没有已审核样例</p>
                    <small>通过会计复核后，系统在同一事务内保存处理依据。</small>
                  </div>
                )}
                <div className="learning-pagination">
                  <span>
                    {overview.pagination.total
                      ? `${offset + 1}–${offset + overview.samples.length}`
                      : '0'}{' '}
                    / {overview.pagination.total}
                  </span>
                  <button
                    className="button secondary small"
                    disabled={busy || loading || offset === 0}
                    onClick={() => setOffset((value) => Math.max(0, value - 20))}
                  >
                    上一页
                  </button>
                  <button
                    className="button secondary small"
                    disabled={busy || loading || !overview.pagination.has_more}
                    onClick={() => setOffset((value) => value + 20)}
                  >
                    下一页
                  </button>
                </div>
              </section>
              <section className="panel learning-rules">
                <div className="panel-heading">
                  <div>
                    <h2>
                      企业规则<span className="count-label">{overview.rules.length}</span>
                    </h2>
                    <p>精确条件匹配，金额按总额 / 不含税额 / 税额计算。</p>
                  </div>
                </div>
                {overview.rules.length ? (
                  <div className="learning-rule-list">
                    {overview.rules.map((item) => (
                      <button
                        className={`learning-rule-choice ${selected === item.id ? 'selected' : ''}`}
                        key={item.id}
                        disabled={busy || !!editor || !!command}
                        onClick={() => chooseRule(item.id)}
                      >
                        <span>
                          <strong>{item.definition.name}</strong>
                          <small>
                            {item.definition.source_kind === 'bank' ? '流水' : '业务事项'} ·{' '}
                            {item.definition.conditions.counterparty} · v{item.current_version}
                          </small>
                        </span>
                        <span
                          className={`badge ${item.status === 'published' ? 'approved' : item.status === 'draft' ? 'exception' : ''}`}
                        >
                          {learningStatus[item.status]}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="learning-empty">
                    <BookOpen size={25} />
                    <p>会计经验尚未发布为规则</p>
                    <small>选择一个已审核样例，填写处理依据后建立待发布版本。</small>
                  </div>
                )}
                {rule && (
                  <div className="learning-rule-detail">
                    <div className="learning-rule-facts">
                      <span>{learningBusinessTypes[rule.definition.conditions.business_type]}</span>
                      <span>
                        {rule.definition.conditions.direction === 'expense' ? '支出' : '收入'}
                      </span>
                      <span>
                        {rule.definition.conditions.payment_status === 'paid' ? '已支付' : '未支付'}
                      </span>
                      {rule.definition.conditions.category && (
                        <span>{learningCategories[rule.definition.conditions.category]}</span>
                      )}
                    </div>
                    <div className="learning-formula">
                      {rule.definition.lines.map((line, index) => (
                        <p key={index}>
                          <strong>
                            {line.side === 'debit' ? '借' : '贷'} {line.account_code}
                          </strong>
                          <span>
                            {line.amount_field === 'gross'
                              ? '总额'
                              : line.amount_field === 'net'
                                ? '不含税额'
                                : '税额'}
                          </span>
                        </p>
                      ))}
                    </div>
                    <p className="learning-reason">处理依据：{rule.reason}</p>
                    {usage && (
                      <div className="learning-usage">
                        <strong>真实审核后的规则命中</strong>
                        <p>
                          已复核 {usage.reviewed_hits} 张 · 会计修改分录 {usage.accountant_modified}{' '}
                          张
                          {usage.render_unavailable > 0 &&
                            ` · 当前科目下无法复算 ${usage.render_unavailable} 张`}
                        </p>
                        <small>
                          按每次生成时的规则版本对照最终分录；待审核草稿与历史回放不计入命中。
                        </small>
                      </div>
                    )}
                    <small>
                      正例 {rule.sample_ids.length} 条 · 当前定义 v{rule.current_version} · 状态 v
                      {rule.state_version}
                    </small>
                    <div className="learning-rule-actions">
                      <button
                        className="button secondary small"
                        disabled={busy}
                        onClick={() => void openHistory()}
                      >
                        <History size={13} />
                        查看版本历史
                      </button>
                      {overview.can_manage && (
                        <>
                          <button
                            className="button secondary small"
                            disabled={busy}
                            onClick={() => {
                              setEditor({
                                rule,
                                definition: rule.definition,
                                sampleIds: rule.sample_ids,
                              });
                              setReason(rule.reason);
                            }}
                          >
                            修订为待发布版本
                          </button>
                          {rule.status !== 'retired' && (
                            <button
                              className="button secondary small"
                              disabled={busy}
                              onClick={() => {
                                setCommand('retire');
                                setReason('');
                              }}
                            >
                              停用规则
                            </button>
                          )}
                        </>
                      )}
                    </div>
                    {overview.can_manage && rule.status === 'draft' && (
                      <div className="learning-evaluation">
                        <strong>发布前评估</strong>
                        <p>
                          从左侧选择不会命中本规则的已审核事项作为反例。评估还会核对本账套已有样例，避免伤害其他交易。
                        </p>
                        <button
                          className="button secondary"
                          disabled={busy || !negativeIds.length}
                          onClick={() => void evaluateRule()}
                        >
                          <FileCheck2 size={15} />
                          评估正例与 {negativeIds.length} 条反例
                        </button>
                        {evaluation && (
                          <div
                            className={`learning-evaluation-result ${evaluation.passed ? 'passed' : 'failed'}`}
                            role="status"
                          >
                            <strong>
                              {evaluation.passed ? '已通过当前样例评估' : '评估未通过，请修订规则'}
                            </strong>
                            <p>
                              正例 {evaluation.positive_count} · 反例 {evaluation.negative_count} ·
                              已核对 {evaluation.scanned_count} · 匹配 {evaluation.matched_count}
                            </p>
                            {evaluation.failures.map((failure, index) => (
                              <p key={index}>
                                {failure.reason}
                                {failure.sample_id && (
                                  <button
                                    className="text-button"
                                    disabled={busy}
                                    onClick={() => void openSample(failure.sample_id)}
                                  >
                                    查看相关样例
                                  </button>
                                )}
                              </p>
                            ))}
                          </div>
                        )}
                        <button
                          className="button primary"
                          disabled={busy || !evaluation?.passed}
                          onClick={() => {
                            setCommand('publish');
                            setReason('');
                          }}
                        >
                          <CheckCircle2 size={15} />
                          填写依据并发布
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </section>
            </div>
          </>
        )
      )}
      {detail && (
        <LearningSampleDialog detail={detail} busy={busy} onClose={() => setDetail(null)} />
      )}
      {history && <LearningHistoryDialog history={history} onClose={() => setHistory(null)} />}
      {editor && (
        <LearningRuleEditor
          initial={editor.definition}
          sampleIds={editor.sampleIds}
          initialReason={reason}
          accounts={accounts}
          counterparties={governance?.counterparties ?? []}
          busy={busy}
          error={error}
          revising={!!editor.rule}
          onSave={saveDefinition}
          onClose={() => {
            if (!busy) {
              setEditor(null);
              setError('');
            }
          }}
        />
      )}
      {command && rule && (
        <LearningCommandDialog
          command={command}
          rule={rule}
          reason={reason}
          onReason={setReason}
          error={error}
          busy={busy}
          onSave={changeState}
          onClose={() => {
            if (!busy) {
              setCommand(null);
              setError('');
            }
          }}
        />
      )}
    </Wrapper>
  );
}

function LearningHistoryDialog({
  history,
  onClose,
}: {
  history: { name: string; versions: LearningRuleVersion[] };
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      className="modal learning-modal"
      ref={dialog}
      aria-labelledby="learning-history-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="learning-history-title">企业规则版本历史</h2>
          <p>{history.name} · 定义与样例来源保留历史记录</p>
        </div>
        <button className="icon-button" aria-label="关闭企业规则历史" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="form-content">
        {history.versions.map((item) => (
          <article className="learning-lineage" key={item.version}>
            <h3>
              v{item.version} · {item.definition.name}
            </h3>
            <p>
              {item.definition.conditions.counterparty} ·{' '}
              {item.definition.source_kind === 'bank' ? '流水' : '业务事项'} · 正例{' '}
              {item.sample_ids.length} 条
            </p>
            <div className="learning-formula">
              {item.definition.lines.map((line, index) => (
                <p key={index}>
                  <strong>
                    {line.side === 'debit' ? '借' : '贷'} {line.account_code}
                  </strong>
                  <span>
                    {line.amount_field === 'gross'
                      ? '总额'
                      : line.amount_field === 'net'
                        ? '不含税额'
                        : '税额'}
                  </span>
                </p>
              ))}
            </div>
            <p>{item.reason}</p>
            <small>
              {item.created_by} · {item.created_at.slice(0, 10)}
            </small>
          </article>
        ))}
      </div>
      <div className="modal-footer">
        <button className="button secondary" onClick={onClose}>
          关闭
        </button>
      </div>
    </dialog>
  );
}

function Lines({ value }: { value: LearningLine[] }) {
  return (
    <div className="learning-journal-lines">
      {value.map((line, index) => (
        <div key={index}>
          <strong>{line.account_code}</strong>
          <span>
            借 {line.debit} / 贷 {line.credit}
          </span>
          {Object.entries(line.auxiliary_values ?? {}).map(([key, item]) => (
            <small key={key}>
              {key}：{item}
            </small>
          ))}
        </div>
      ))}
    </div>
  );
}
function LearningSampleDialog({
  detail,
  busy,
  onClose,
}: {
  detail: LearningDetail;
  busy: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      className="modal learning-modal"
      ref={dialog}
      aria-labelledby="learning-sample-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="learning-sample-title">已审核处理依据</h2>
          <p>
            {detail.counterparty} · ¥ {detail.amount} · 审核版本 {detail.reviewed_version}
          </p>
        </div>
        <button className="icon-button" disabled={busy} aria-label="关闭审核样例" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="form-content">
        <div className="learning-comparison">
          <section>
            <h3>原处理</h3>
            <p>{detail.original.description}</p>
            <Lines value={detail.original.lines} />
          </section>
          <ArrowDown size={20} />
          <section>
            <h3>会计复核后的处理</h3>
            <p>{detail.final.description}</p>
            <Lines value={detail.final.lines} />
          </section>
        </div>
        <section className="learning-lineage">
          <h3>修正与审核记录</h3>
          {detail.corrections.length ? (
            detail.corrections.map((item, index) => (
              <p key={index}>
                v{item.version} · {item.user_id}
                <br />
                {item.reason || '历史修正未填写依据；发布规则时须补充。'}
              </p>
            ))
          ) : (
            <p>本次未发生人工分录修正。</p>
          )}
          <p>
            审核人：{detail.reviewed_by} · {detail.created_at.slice(0, 10)}
          </p>
          <p>事实版本：{detail.snapshot.fact_version ?? '银行来源固定事实'}</p>
          <small>证据指纹：{detail.evidence_fingerprint}</small>
        </section>
      </div>
      <div className="modal-footer">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          关闭
        </button>
      </div>
    </dialog>
  );
}

function LearningRuleEditor({
  initial,
  sampleIds,
  initialReason,
  accounts,
  counterparties,
  busy,
  error,
  revising,
  onSave,
  onClose,
}: {
  initial: LearningDefinition;
  sampleIds: string[];
  initialReason: string;
  accounts: Account[];
  counterparties: LearningGovernance['counterparties'];
  busy: boolean;
  error: string;
  revising: boolean;
  onSave: (definition: LearningDefinition, sampleIds: string[], reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    [definition, setDefinition] = useState(initial),
    [reason, setReason] = useState(initialReason);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const condition = definition.conditions;
  function changeLine(index: number, value: Partial<LearningDefinition['lines'][number]>) {
    setDefinition((current) => ({
      ...current,
      lines: current.lines.map((line, ordinal) =>
        ordinal === index ? { ...line, ...value } : line,
      ),
    }));
  }
  return (
    <dialog
      className="modal learning-modal"
      ref={dialog}
      aria-labelledby="learning-editor-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="learning-editor-title">{revising ? '修订企业规则' : '从已审核样例建立规则'}</h2>
          <p>保存为待发布版本。修订后暂停原规则，重新评估通过后再发布。</p>
        </div>
        <button
          className="icon-button"
          aria-label="关闭企业规则编辑"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <div className="form-content learning-editor">
        <label>
          <span className="field-label">规则名称</span>
          <input
            aria-label="规则名称"
            maxLength={120}
            disabled={busy}
            value={definition.name}
            onChange={(event) => setDefinition((value) => ({ ...value, name: event.target.value }))}
          />
        </label>
        <label>
          <span className="field-label">交易对方原文</span>
          <input
            aria-label="规则交易对方"
            maxLength={200}
            disabled={busy}
            value={condition.counterparty}
            onChange={(event) =>
              setDefinition((value) => ({
                ...value,
                conditions: { ...value.conditions, counterparty: event.target.value },
              }))
            }
          />
        </label>
        <label>
          <span className="field-label">往来方匹配范围</span>
          <select
            aria-label="规则稳定往来方"
            disabled={busy}
            value={condition.counterparty_id ?? ''}
            onChange={(event) =>
              setDefinition((value) => ({
                ...value,
                conditions: { ...value.conditions, counterparty_id: event.target.value || null },
              }))
            }
          >
            <option value="">仅精确原文（兼容历史规则）</option>
            {condition.counterparty_id &&
              !counterparties.some((item) => item.id === condition.counterparty_id) && (
                <option value={condition.counterparty_id}>原往来方当前不可用</option>
              )}
            {counterparties.map((item) => (
              <option key={item.id} value={item.id}>
                {item.code} · {item.name}
              </option>
            ))}
          </select>
        </label>
        <p className="field-help">
          选择稳定往来方后，原文须唯一对应该主数据或已独立审核别名；未经审核的别名和歧义名称不会自动匹配。
        </p>
        <p className="learning-editor-scope">
          {definition.source_kind === 'bank'
            ? '银行流水'
            : learningBusinessTypes[condition.business_type]}{' '}
          · {condition.direction === 'expense' ? '支出' : '收入'} ·{' '}
          {condition.payment_status === 'paid' ? '已支付' : '未支付'}
          {condition.category && ` · ${learningCategories[condition.category]}`} · 已审核正例{' '}
          {sampleIds.length} 条
        </p>
        <div className="learning-editor-lines">
          {definition.lines.map((line, index) => (
            <div key={index}>
              <select
                aria-label={`规则第 ${index + 1} 行方向`}
                disabled={busy}
                value={line.side}
                onChange={(event) =>
                  changeLine(index, { side: event.target.value as 'debit' | 'credit' })
                }
              >
                <option value="debit">借</option>
                <option value="credit">贷</option>
              </select>
              <select
                aria-label={`规则第 ${index + 1} 行科目`}
                disabled={busy}
                value={line.account_code}
                onChange={(event) => changeLine(index, { account_code: event.target.value })}
              >
                {!accounts.some((account) => account.code === line.account_code) && (
                  <option value={line.account_code}>{line.account_code}（当前不可用）</option>
                )}
                {accounts.map((account) => (
                  <option key={account.code} value={account.code}>
                    {account.code} {account.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`规则第 ${index + 1} 行金额`}
                disabled={busy}
                value={line.amount_field}
                onChange={(event) =>
                  changeLine(index, { amount_field: event.target.value as 'gross' | 'net' | 'tax' })
                }
              >
                <option value="gross">总额</option>
                {definition.source_kind === 'business' && (
                  <>
                    <option value="net">不含税额</option>
                    <option value="tax">税额</option>
                  </>
                )}
              </select>
              <button
                className="icon-button"
                aria-label={`删除规则第 ${index + 1} 行`}
                disabled={busy || definition.lines.length <= 2}
                onClick={() =>
                  setDefinition((value) => ({
                    ...value,
                    lines: value.lines.filter((_, ordinal) => ordinal !== index),
                  }))
                }
              >
                <X size={15} />
              </button>
            </div>
          ))}
        </div>
        <button
          className="text-button"
          disabled={busy || definition.lines.length >= 20 || !accounts.length}
          onClick={() =>
            setDefinition((value) => ({
              ...value,
              lines: [
                ...value.lines,
                { account_code: accounts[0].code, side: 'debit', amount_field: 'gross' },
              ],
            }))
          }
        >
          <Plus size={14} />
          增加分录行
        </button>
        <label>
          <span className="field-label">会计处理依据</span>
          <textarea
            aria-label="规则会计处理依据"
            rows={3}
            maxLength={1000}
            disabled={busy}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="说明业务实质、科目选择及适用边界"
          />
        </label>
        <p className="field-help">
          金额使用当前事项的精确金额。税额拆分必须有已核对票据；辅助核算沿用客户科目要求。
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="modal-footer">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="button primary"
          disabled={
            busy || !reason.trim() || !definition.name.trim() || !condition.counterparty.trim()
          }
          onClick={() => void onSave(definition, sampleIds, reason.trim())}
        >
          {busy && <LoaderCircle size={15} className="spinner" />}保存待发布版本
        </button>
      </div>
    </dialog>
  );
}

function LearningCommandDialog({
  command,
  rule,
  reason,
  onReason,
  error,
  busy,
  onSave,
  onClose,
}: {
  command: 'publish' | 'retire';
  rule: LearningRule;
  reason: string;
  onReason: (value: string) => void;
  error: string;
  busy: boolean;
  onSave: () => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      className="modal learning-modal"
      ref={dialog}
      aria-labelledby="learning-command-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="learning-command-title">
            {command === 'publish' ? '发布经评估的企业规则' : '停用企业规则'}
          </h2>
          <p>
            {rule.definition.name} · v{rule.current_version}
          </p>
        </div>
        <button className="icon-button" disabled={busy} aria-label="关闭规则操作" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="form-content">
        <p>
          {command === 'publish'
            ? '发布时再次核对当前授权、期间、科目和所有已审核样例。发布后的结果仍是待会计复核草稿。'
            : '后续事项停止使用此规则；已生成草稿和已审核凭证保留生成依据。'}
        </p>
        <label>
          <span className="field-label">{command === 'publish' ? '发布依据' : '停用原因'}</span>
          <textarea
            aria-label={command === 'publish' ? '规则发布依据' : '规则停用原因'}
            rows={4}
            maxLength={1000}
            value={reason}
            disabled={busy}
            onChange={(event) => onReason(event.target.value)}
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="modal-footer">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="button primary"
          disabled={busy || !reason.trim()}
          onClick={() => void onSave()}
        >
          {busy && <LoaderCircle size={15} className="spinner" />}
          {command === 'publish' ? '确认发布' : '确认停用'}
        </button>
      </div>
    </dialog>
  );
}
