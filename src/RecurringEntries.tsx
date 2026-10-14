import { useEffect, useState } from 'react';
import { CalendarClock, CheckCheck, LoaderCircle, Plus, RefreshCw } from 'lucide-react';
import type { JournalLine, Session } from './types';
import type { MonthlyOverview, RecurringPlan } from './monthly-types';
import { recurringLinesIssue, validMonthlyPeriod } from './monthly-helpers';
import { JournalEntryGrid } from './JournalEntryGrid';
import { useMonthlyCommand } from './useMonthlyCommand';
import { useMonthlyData } from './useMonthlyData';
import './monthly.css';

const blankLines = (): JournalLine[] => [
  { account_code: '', debit: '0.00', credit: '0.00' },
  { account_code: '', debit: '0.00', credit: '0.00' },
];
const statusLabels: Record<string, string> = {
  draft: '待会计复核',
  approved: '已审核待导出',
  exported: '已导出',
};
type Props = {
  session: Session;
  bookId: string;
  onBack: () => void;
  onOpenDraft: (draftId: string) => void;
  onBusyChange?: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onChanged?: () => void;
};
export function RecurringEntries(props: Props) {
  return <RecurringContent key={props.session.scope_key + ':' + props.bookId} {...props} />;
}
function RecurringContent({
  session,
  bookId,
  onBack,
  onOpenDraft,
  onBusyChange,
  onDirtyChange,
  onChanged,
}: Props) {
  const { data, loading, error, revoked, load } = useMonthlyData<MonthlyOverview>(
    session,
    `/books/${encodeURIComponent(bookId)}/monthly`,
  );
  const command = useMonthlyCommand(session, bookId, { revoked, load, onBusyChange, onChanged });
  const [open, setOpen] = useState(false),
    [editing, setEditing] = useState<RecurringPlan | null>(null);
  const [name, setName] = useState(''),
    [start, setStart] = useState(''),
    [end, setEnd] = useState(''),
    [day, setDay] = useState(1);
  const [description, setDescription] = useState(''),
    [evidence, setEvidence] = useState(''),
    [reason, setReason] = useState('');
  const [lines, setLines] = useState<JournalLine[]>(blankLines()),
    [approvalReasons, setApprovalReasons] = useState<Record<string, string>>({});
  const dirty = open || Object.values(approvalReasons).some((value) => value.trim());
  useEffect(() => {
    onDirtyChange?.(dirty && !revoked);
    return () => onDirtyChange?.(false);
  }, [dirty, revoked, onDirtyChange]);
  useEffect(() => {
    if (revoked) {
      setOpen(false);
      setEditing(null);
      setName('');
      setStart('');
      setEnd('');
      setDescription('');
      setEvidence('');
      setReason('');
      setLines(blankLines());
      setApprovalReasons({});
    }
  }, [revoked]);
  const locked = command.busy || loading || revoked;
  const accounts = (data?.accounts ?? [])
    .filter((account) => account.active)
    .map((account) => ({
      ...account,
      auxiliary_requirements: JSON.stringify(account.auxiliary_requirements),
    }));
  const issue = recurringLinesIssue(lines);
  function close() {
    setOpen(false);
    setEditing(null);
    setName('');
    setDescription('');
    setEvidence('');
    setReason('');
    setLines(blankLines());
  }
  function newForm(plan?: RecurringPlan) {
    if (locked || dirty) return;
    setEditing(plan ?? null);
    setName(plan?.name ?? '');
    setStart(plan?.start_period ?? data?.period ?? '');
    setEnd(plan?.end_period ?? '');
    setDay(plan?.day ?? 1);
    setDescription(plan?.description ?? '');
    setEvidence(plan?.evidence_ref ?? '');
    setReason('');
    setLines(
      plan
        ? plan.lines.map((line) => ({ ...line, auxiliary_values: { ...line.auxiliary_values } }))
        : blankLines(),
    );
    setOpen(true);
  }
  return (
    <main className="monthly-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">RECURRING / REVIEWED TEMPLATES</p>
          <h1>重复事项，复用明确的处理方案。</h1>
          <p className="page-description">
            先写清真实科目、金额、期间与原件，再由另一位会计审批。每月生成一次未审草稿。
          </p>
        </div>
        <div className="monthly-actions">
          <button className="button secondary" disabled={locked || dirty} onClick={onBack}>
            返回账套
          </button>
          <button className="button secondary" disabled={locked} onClick={() => void load()}>
            <RefreshCw size={15} />
            刷新计划
          </button>
        </div>
      </section>
      {(error || command.error) && (
        <p className="inline-error" role="alert">
          {error || command.error}
        </p>
      )}
      {command.notice && (
        <p role="status" className="monthly-inline-note">
          {command.notice}
        </p>
      )}
      {loading && !data ? (
        <p className="loading-block" role="status">
          <LoaderCircle size={16} className="spinner" />
          正在读取本账套授权的固定事项…
        </p>
      ) : data ? (
        <>
          <section className="monthly-period-banner">
            <CalendarClock size={22} />
            <div>
              <h2>
                {data.period} · {data.period_status === 'closed' ? '期间已锁定' : '开放期间'}
              </h2>
              <p>
                计划属于长期账套；新月份须通过科目与辅助项配置校验。已生成、已审核的凭证不会被计划修订替换。
              </p>
            </div>
          </section>
          <section className="monthly-panel">
            <div className="monthly-record-heading">
              <div>
                <h2>
                  {open
                    ? editing
                      ? `修订计划 · 以 v${editing.version} 创建新版本`
                      : '新建固定事项'
                    : '本账套固定事项'}
                </h2>
                <p>
                  租金、订阅等固定金额事项可复用；工资、税额、折旧等有变化时应按真实依据另建版本。
                </p>
              </div>
              {!open && (
                <button
                  className="button secondary small"
                  disabled={locked || !data.can_edit || dirty}
                  onClick={() => newForm()}
                >
                  <Plus size={14} />
                  新建计划
                </button>
              )}
            </div>
            {open && (
              <form
                aria-label="固定事项计划录入"
                onSubmit={(event) => {
                  event.preventDefault();
                  void command.run(
                    '/plans',
                    {
                      ...(editing
                        ? { plan_id: editing.id, expected_version: editing.version }
                        : {}),
                      name: name.trim(),
                      start_period: start,
                      end_period: end || null,
                      day,
                      description: description.trim(),
                      evidence_ref: evidence.trim(),
                      reason: reason.trim(),
                      lines,
                    },
                    '新计划版本已保存，等待独立审批。',
                    close,
                    true,
                  );
                }}
              >
                <div className="monthly-form">
                  <label>
                    计划名称
                    <input
                      aria-label="固定事项名称"
                      value={name}
                      maxLength={120}
                      disabled={locked}
                      onChange={(event) => setName(event.target.value)}
                      placeholder="例如：办公室租金"
                    />
                  </label>
                  <label>
                    事项发生日
                    <input
                      aria-label="固定事项发生日"
                      type="number"
                      min={1}
                      max={28}
                      value={day}
                      disabled={locked}
                      onChange={(event) => setDay(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    开始月份
                    <input
                      aria-label="固定事项开始月份"
                      type="month"
                      min="2000-01"
                      max="2099-12"
                      value={start}
                      disabled={locked}
                      onChange={(event) => setStart(event.target.value)}
                    />
                  </label>
                  <label>
                    结束月份（可选）
                    <input
                      aria-label="固定事项结束月份"
                      type="month"
                      min="2000-01"
                      max="2099-12"
                      value={end}
                      disabled={locked}
                      onChange={(event) => setEnd(event.target.value)}
                    />
                  </label>
                  <label className="monthly-wide">
                    凭证摘要
                    <input
                      aria-label="固定事项凭证摘要"
                      value={description}
                      maxLength={500}
                      disabled={locked}
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  </label>
                  <label>
                    原件编号或附件引用
                    <input
                      aria-label="固定事项原件依据"
                      value={evidence}
                      maxLength={500}
                      disabled={locked}
                      onChange={(event) => setEvidence(event.target.value)}
                      placeholder="例如：经核验的合同或付款约定"
                    />
                  </label>
                  <label>
                    本版本依据
                    <input
                      aria-label="固定事项版本依据"
                      value={reason}
                      maxLength={1000}
                      disabled={locked}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                </div>
                <JournalEntryGrid
                  lines={lines}
                  accounts={accounts}
                  readonly={!data.can_edit}
                  busy={locked}
                  onChange={setLines}
                />
                <div className="monthly-actions">
                  <button
                    type="button"
                    className="text-button"
                    disabled={locked || lines.length >= 100}
                    onClick={() =>
                      setLines((old) => [
                        ...old,
                        { account_code: '', debit: '0.00', credit: '0.00' },
                      ])
                    }
                  >
                    增加分录行
                  </button>
                  <button
                    className="button primary"
                    disabled={
                      locked ||
                      !data.can_edit ||
                      !name.trim() ||
                      !description.trim() ||
                      !evidence.trim() ||
                      reason.trim().length < 3 ||
                      !validMonthlyPeriod(start) ||
                      (!!end && (!validMonthlyPeriod(end) || end < start)) ||
                      !Number.isInteger(day) ||
                      day < 1 ||
                      day > 28 ||
                      !!issue
                    }
                  >
                    保存并等待独立审批
                  </button>
                  <button
                    className="button secondary"
                    type="button"
                    disabled={locked}
                    onClick={close}
                  >
                    放弃本次编辑
                  </button>
                </div>
                {issue && <p className="field-help">{issue}</p>}
              </form>
            )}
          </section>
          <section className="monthly-records" aria-label="固定事项版本与生成记录">
            {data.plans.map((plan) => (
              <article className="monthly-record" key={plan.id} data-plan-id={plan.id}>
                <div className="monthly-record-heading">
                  <div>
                    <h3>
                      {plan.name} · v{plan.version}
                    </h3>
                    <p>
                      {plan.start_period} 至 {plan.end_period ?? '长期有效'} · 每月 {plan.day} 日 ·{' '}
                      {plan.description}
                    </p>
                    <small>
                      {plan.evidence_ref} · {plan.reason}
                    </small>
                  </div>
                  <span className={'monthly-chip ' + (!plan.approved ? 'attention' : '')}>
                    {plan.approved ? `${plan.approved_name ?? '独立会计'}已审批` : '等待独立审批'}
                  </span>
                </div>
                {plan.issues.map((item, index) => (
                  <p key={index} className="inline-error">
                    {item}
                  </p>
                ))}
                {!plan.applicable && <p className="field-help">该计划不适用于当前月份。</p>}
                <details>
                  <summary>查看本版本分录</summary>
                  <JournalEntryGrid
                    lines={plan.lines}
                    accounts={accounts}
                    readonly
                    busy={false}
                    onChange={() => {}}
                  />
                </details>
                {plan.can_approve ? (
                  <div className="monthly-record-reason">
                    <input
                      aria-label={`审批 ${plan.name} v${plan.version} 依据`}
                      placeholder="核对科目、金额、期间及原件后填写依据"
                      value={approvalReasons[plan.id] ?? ''}
                      disabled={locked || open}
                      maxLength={1000}
                      onChange={(event) =>
                        setApprovalReasons((old) => ({ ...old, [plan.id]: event.target.value }))
                      }
                    />
                    <button
                      className="button secondary small"
                      disabled={
                        locked || open || (approvalReasons[plan.id] ?? '').trim().length < 3
                      }
                      onClick={() =>
                        void command.run(
                          `/plans/${encodeURIComponent(plan.id)}/approve`,
                          { version: plan.version, reason: approvalReasons[plan.id].trim() },
                          '固定事项版本已独立审批；凭证草稿仍须会计复核。',
                          () =>
                            setApprovalReasons((old) => {
                              const next = { ...old };
                              delete next[plan.id];
                              return next;
                            }),
                        )
                      }
                    >
                      <CheckCheck size={14} />
                      审批当前版本
                    </button>
                  </div>
                ) : !plan.approved && plan.created_by === session.user_id ? (
                  <p className="field-help">本人创建的计划须由另一位有本账套复核权限的会计审批。</p>
                ) : null}
                <div className="monthly-actions">
                  {plan.generated_current ? (
                    <>
                      <span className="monthly-chip">
                        {data.period} 已生成 ·{' '}
                        {statusLabels[plan.generated_current.status] ?? '状态待核实'} · 计划 v
                        {plan.generated_current.plan_version}
                      </span>
                      <button
                        className="button secondary small"
                        disabled={locked || dirty}
                        onClick={() => onOpenDraft(plan.generated_current!.draft_id)}
                      >
                        打开本月凭证
                      </button>
                    </>
                  ) : (
                    <button
                      className="button primary small"
                      disabled={locked || dirty || !plan.can_generate}
                      onClick={() =>
                        void command.run(
                          `/plans/${encodeURIComponent(plan.id)}/generate`,
                          { version: plan.version },
                          '本月未审草稿已生成，请进入凭证复核。',
                          undefined,
                          true,
                        )
                      }
                    >
                      生成 {data.period} 草稿
                    </button>
                  )}
                  <button
                    className="button secondary small"
                    disabled={locked || dirty || !data.can_edit}
                    onClick={() => newForm(plan)}
                  >
                    创建修订版本
                  </button>
                </div>
              </article>
            ))}
            {!data.plans.length && (
              <p className="monthly-empty">
                尚无固定事项计划。明确金额与证据后新建，独立审批后再生成草稿。
              </p>
            )}
          </section>
          <p className="monthly-boundary">
            重复点击或重试不会重复生成同一计划同月的凭证。生成只进入草稿阶段，会计复核与交付回执仍沿用现有流程。
          </p>
        </>
      ) : null}
    </main>
  );
}
