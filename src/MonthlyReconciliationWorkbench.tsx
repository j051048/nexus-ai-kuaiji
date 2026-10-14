import { useEffect, useRef, useState } from 'react';
import {
  CheckCheck,
  ClipboardCheck,
  Download,
  LoaderCircle,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { apiBlob, errorMessage } from './api';
import type { Session } from './types';
import { monthlyMoney, signedMonthlyCents } from './monthly-helpers';
import { latestMonthlyBaselines, monthlyAuxiliaryLabels } from './monthly-types';
import type {
  MonthlyBalanceLine,
  MonthlyBaseline,
  MonthlyOverview,
  MonthlyReference,
} from './monthly-types';
import { MonthlyBaselineImport } from './MonthlyBaselineImport';
import { useMonthlyData } from './useMonthlyData';
import { useMonthlyCommand } from './useMonthlyCommand';
import './monthly.css';

function ReferenceTable({ value }: { value: MonthlyReference }) {
  return (
    <div className="monthly-table-wrap">
      <table className="monthly-table" aria-label="月度参考余额分层核对">
        <thead>
          <tr>
            <th>科目 / 辅助项</th>
            <th>外部期初</th>
            <th>已核验交付净额</th>
            <th>参考期末</th>
            <th>外部期末</th>
            <th>差异</th>
            <th>已审待交付</th>
            <th>已导出待回执</th>
            <th>未审核</th>
          </tr>
        </thead>
        <tbody>
          {value.lines.map((line) => (
            <tr key={line.account_code + JSON.stringify(line.auxiliary_values)}>
              <td>
                {line.account_code} · {line.account_name}
                <small>
                  {Object.entries(line.auxiliary_values)
                    .map(([key, entry]) => `${monthlyAuxiliaryLabels[key] ?? key} ${entry}`)
                    .join(' · ') || '科目级余额'}
                </small>
              </td>
              {[
                line.baseline,
                line.verified_delivery.net,
                line.reference_closing,
                line.actual,
                line.difference,
                line.approved_pending_delivery.net,
                line.exported_pending_receipt.net,
                line.unreviewed.net,
              ].map((amount, index) => (
                <td
                  key={index}
                  className={
                    'amount ' +
                    (amount == null
                      ? 'unknown'
                      : index === 4 && amount !== '0.00'
                        ? 'attention'
                        : '')
                  }
                >
                  {monthlyMoney(amount)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!value.lines.length && (
        <p className="monthly-empty">尚无可核对的科目事实。请录入并独立确认外部余额。</p>
      )}
    </div>
  );
}
const blankLine = (): MonthlyBalanceLine => ({
  account_code: '',
  auxiliary_values: {},
  balance: '',
});
export function MonthlyReconciliationWorkbench(props: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onChanged?: () => void;
}) {
  return <MonthlyBalanceContent key={props.session.scope_key + ':' + props.bookId} {...props} />;
}
function MonthlyBalanceContent({
  session,
  bookId,
  onBack,
  onBusyChange,
  onDirtyChange,
  onChanged,
}: Parameters<typeof MonthlyReconciliationWorkbench>[0]) {
  const resource = useMonthlyData<MonthlyOverview>(
    session,
    `/books/${encodeURIComponent(bookId)}/monthly`,
  );
  const { data, loading, error, revoked, load } = resource;
  const command = useMonthlyCommand(session, bookId, { revoked, load, onChanged });
  const [importBusy, setImportBusy] = useState(false),
    [importDirty, setImportDirty] = useState(false),
    [originalBusy, setOriginalBusy] = useState(''),
    [originalError, setOriginalError] = useState('');
  const active = useRef(true);
  const revokedRef = useRef(revoked);
  revokedRef.current = revoked;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(command.busy || importBusy || !!originalBusy);
    return () => onBusyChange?.(false);
  }, [command.busy, importBusy, originalBusy, onBusyChange]);
  const [formOpen, setFormOpen] = useState(false),
    [kind, setKind] = useState<'opening' | 'closing'>('opening');
  const [external, setExternal] = useState(''),
    [evidence, setEvidence] = useState(''),
    [reason, setReason] = useState('');
  const [lines, setLines] = useState<MonthlyBalanceLine[]>([blankLine()]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const dirty =
    (formOpen &&
      (!!external ||
        !!evidence ||
        !!reason ||
        lines.some((line) => line.account_code || line.balance))) ||
    Object.values(reasons).some((value) => value.trim()) ||
    importDirty;
  useEffect(() => {
    onDirtyChange?.(dirty && !revoked);
    return () => onDirtyChange?.(false);
  }, [dirty, revoked, onDirtyChange]);
  useEffect(() => {
    if (revoked) {
      setFormOpen(false);
      setExternal('');
      setEvidence('');
      setReason('');
      setLines([blankLine()]);
      setReasons({});
    }
  }, [revoked]);
  const locked = command.busy || importBusy || !!originalBusy || loading || revoked;
  const latest = latestMonthlyBaselines(data?.baselines ?? []);
  const balanceIssue = lines.some(
    (line) => !line.account_code || signedMonthlyCents(line.balance) === null,
  )
    ? '请填写真实科目和精确余额'
    : '';
  function resetForm() {
    setFormOpen(false);
    setExternal('');
    setEvidence('');
    setReason('');
    setLines([blankLine()]);
  }
  function updateLine(index: number, patch: Partial<MonthlyBalanceLine>) {
    setLines((old) =>
      old.map((line, position) => (position === index ? { ...line, ...patch } : line)),
    );
  }
  async function downloadOriginal(baseline: MonthlyBaseline) {
    if (locked || !baseline.original) return;
    setOriginalBusy(baseline.id);
    setOriginalError('');
    try {
      const blob = await apiBlob(
        `/books/${encodeURIComponent(bookId)}/monthly/baselines/${encodeURIComponent(baseline.id)}/original`,
        session,
      );
      if (!active.current || revokedRef.current) return;
      const url = URL.createObjectURL(blob),
        link = document.createElement('a');
      link.href = url;
      link.download = baseline.original.filename.replace(/[<>:"/\\|?*]/g, '_');
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      if (active.current) {
        setOriginalError(errorMessage(failure));
        await load();
      }
    } finally {
      if (active.current) setOriginalBusy('');
    }
  }
  return (
    <main className="monthly-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">MONTHLY / EXTERNAL REFERENCE</p>
          <h1>用真实外部余额，核清月度差异。</h1>
          <p className="page-description">
            外部期初加已核验交付，与外部期末逐项比较。未审核和未核验交付单独保留。
          </p>
        </div>
        <div className="monthly-actions">
          <button className="button secondary" disabled={locked || dirty} onClick={onBack}>
            返回账套
          </button>
          <button className="button secondary" disabled={locked} onClick={() => void load()}>
            <RefreshCw size={15} />
            刷新余额
          </button>
        </div>
      </section>
      {(error || command.error || originalError) && (
        <p className="inline-error" role="alert">
          {error || command.error || originalError}
        </p>
      )}
      {command.notice && (
        <p className="monthly-inline-note" role="status">
          {command.notice}
        </p>
      )}
      {loading && !data ? (
        <p className="loading-block" role="status">
          <LoaderCircle className="spinner" size={16} />
          正在核对本账套的余额与交付证据…
        </p>
      ) : data ? (
        <>
          <section className="monthly-period-banner">
            <ClipboardCheck size={22} />
            <div>
              <h2>
                {data.period} · {data.period_status === 'closed' ? '期间已锁定' : '开放期间'}
              </h2>
              <p>只覆盖列出的科目与辅助项。缺失金额为待核实；本页为参考核对，不是正式财务报表。</p>
            </div>
          </section>
          <section className="monthly-panel">
            <h2>当前参考余额</h2>
            <p>
              {data.reference.difference_count} 项已知差异 · {data.reference.unknown_count}{' '}
              项金额待核实 · {data.reference.unreviewed_count} 笔未审核 ·{' '}
              {data.reference.pending_delivery_count} 笔待交付核验
            </p>
            <ReferenceTable value={data.reference} />
            <p>
              参考期末仅包含已独立确认的期初和已逐笔核验交付。三类待办净额均未计入参考期末。科目汇总与辅助明细分别核对，不可相加作为试算平衡。
            </p>
          </section>
          <section className="monthly-panel">
            <div className="monthly-record-heading">
              <div>
                <h2>外部余额与原件依据</h2>
                <p>每次修订保留新版本，由另一位会计独立确认。期初对应上月末，期末对应本月末。</p>
              </div>
              <button
                className="button secondary small"
                disabled={locked || !data.can_edit || dirty}
                onClick={() => setFormOpen(true)}
              >
                <Plus size={14} />
                录入外部余额
              </button>
            </div>
            <MonthlyBaselineImport
              session={session}
              bookId={bookId}
              period={data.period}
              canEdit={data.can_edit}
              revoked={revoked}
              externalBusy={command.busy || loading || formOpen || !!originalBusy}
              load={load}
              onBusyChange={setImportBusy}
              onDirtyChange={setImportDirty}
              onChanged={onChanged}
            />
            {formOpen && (
              <form
                aria-label="外部参考余额录入"
                onSubmit={(event) => {
                  event.preventDefault();
                  void command.run(
                    '/baselines',
                    {
                      kind,
                      external_reference: external.trim(),
                      evidence_ref: evidence.trim(),
                      reason: reason.trim(),
                      lines,
                    },
                    '已保存外部余额，等待另一位会计独立确认。',
                    resetForm,
                    true,
                  );
                }}
              >
                <div className="monthly-form">
                  <label>
                    余额类型
                    <select
                      aria-label="外部余额类型"
                      value={kind}
                      disabled={locked}
                      onChange={(event) => setKind(event.target.value as typeof kind)}
                    >
                      <option value="opening">期初（上月末）</option>
                      <option value="closing">期末（本月末）</option>
                    </select>
                  </label>
                  <label>
                    财务软件 / 导出文件编号
                    <input
                      aria-label="外部余额来源"
                      value={external}
                      maxLength={500}
                      disabled={locked}
                      onChange={(event) => setExternal(event.target.value)}
                      placeholder="例如：T+ 本月科目余额表编号"
                    />
                  </label>
                  <label>
                    原件编号或附件引用
                    <input
                      aria-label="外部余额原件依据"
                      value={evidence}
                      maxLength={500}
                      disabled={locked}
                      onChange={(event) => setEvidence(event.target.value)}
                      placeholder="可供独立会计核查的原始资料"
                    />
                  </label>
                  <label>
                    录入依据
                    <input
                      aria-label="外部余额录入依据"
                      value={reason}
                      maxLength={1000}
                      disabled={locked}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                </div>
                <div className="monthly-table-wrap">
                  <table className="monthly-table" aria-label="外部余额录入明细">
                    <thead>
                      <tr>
                        <th>科目与辅助项</th>
                        <th>净借余额（元）</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((line, index) => (
                        <tr key={index}>
                          <td>
                            <select
                              aria-label={`余额第 ${index + 1} 行科目`}
                              value={line.account_code}
                              disabled={locked}
                              onChange={(event) =>
                                updateLine(index, {
                                  account_code: event.target.value,
                                  auxiliary_values: {},
                                })
                              }
                            >
                              <option value="">选择真实科目</option>
                              {data.accounts
                                .filter((account) => account.active)
                                .map((account) => (
                                  <option value={account.code} key={account.code}>
                                    {account.code} · {account.name}
                                  </option>
                                ))}
                            </select>
                            <div className="monthly-auxiliary">
                              {(
                                data.accounts.find((account) => account.code === line.account_code)
                                  ?.auxiliary_requirements ?? []
                              ).map((key) => (
                                <label key={key}>
                                  {monthlyAuxiliaryLabels[key] ?? key}
                                  <input
                                    aria-label={`余额第 ${index + 1} 行${monthlyAuxiliaryLabels[key] ?? key}`}
                                    value={line.auxiliary_values[key] ?? ''}
                                    maxLength={200}
                                    disabled={locked}
                                    onChange={(event) =>
                                      updateLine(index, {
                                        auxiliary_values: {
                                          ...line.auxiliary_values,
                                          [key]: event.target.value,
                                        },
                                      })
                                    }
                                  />
                                </label>
                              ))}
                            </div>
                          </td>
                          <td>
                            <input
                              aria-label={`余额第 ${index + 1} 行金额`}
                              inputMode="decimal"
                              value={line.balance}
                              disabled={locked}
                              onChange={(event) =>
                                updateLine(index, { balance: event.target.value })
                              }
                            />
                            <small>借方正数，贷方负数；零余额需明确填 0.00</small>
                          </td>
                          <td>
                            <button
                              className="icon-button"
                              aria-label={`删除余额第 ${index + 1} 行`}
                              type="button"
                              disabled={locked || lines.length === 1}
                              onClick={() =>
                                setLines((old) => old.filter((_, position) => position !== index))
                              }
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="monthly-actions">
                  <button
                    className="text-button"
                    type="button"
                    disabled={locked || lines.length >= 1000}
                    onClick={() => setLines((old) => [...old, blankLine()])}
                  >
                    增加科目行
                  </button>
                  <button
                    className="button primary"
                    disabled={
                      locked ||
                      !data.can_edit ||
                      !external.trim() ||
                      !evidence.trim() ||
                      reason.trim().length < 3 ||
                      !!balanceIssue
                    }
                  >
                    保存并等待独立确认
                  </button>
                  <button
                    className="button secondary"
                    type="button"
                    disabled={locked}
                    onClick={resetForm}
                  >
                    放弃本次录入
                  </button>
                </div>
                {balanceIssue && <p className="field-help">{balanceIssue}</p>}
              </form>
            )}
            <div className="monthly-records">
              {data.baselines.map((baseline) => {
                const isLatest = latest[baseline.kind]?.id === baseline.id;
                const canConfirm =
                  data.can_review &&
                  !baseline.confirmed &&
                  isLatest &&
                  baseline.created_by !== session.user_id;
                return (
                  <article className="monthly-record" key={baseline.id}>
                    <div className="monthly-record-heading">
                      <div>
                        <h3>
                          {baseline.kind === 'opening' ? '外部期初' : '外部期末'} ·{' '}
                          {baseline.period} · v{baseline.version}
                        </h3>
                        <p>
                          {baseline.external_reference} · {baseline.evidence_ref}
                        </p>
                        <small>{baseline.reason}</small>
                      </div>
                      <span className={'monthly-chip ' + (!baseline.confirmed ? 'attention' : '')}>
                        {!isLatest
                          ? '历史版本'
                          : baseline.confirmed
                            ? `${baseline.confirmed_name ?? '独立会计'}已确认`
                            : '等待独立确认'}
                      </span>
                    </div>
                    {baseline.original && (
                      <div className="monthly-actions">
                        <button
                          className="button secondary small"
                          disabled={locked}
                          onClick={() => void downloadOriginal(baseline)}
                        >
                          <Download size={14} />
                          {baseline.original.format === 'csv' || baseline.original.format === 'json'
                            ? '下载余额原件'
                            : baseline.original.format === 'structured_entry'
                              ? '下载录入记录'
                              : '下载冻结来源记录'}
                        </button>
                        <small className="monthly-inline-note">
                          原文指纹 {baseline.original.sha256.slice(0, 12)}…
                        </small>
                      </div>
                    )}
                    <details>
                      <summary>{baseline.lines.length} 个科目 / 辅助项余额</summary>
                      <div className="monthly-table-wrap">
                        <table className="monthly-table">
                          <tbody>
                            {baseline.lines.map((line, index) => (
                              <tr key={index}>
                                <td>
                                  {line.account_code}
                                  <small>
                                    {Object.entries(line.auxiliary_values)
                                      .map(
                                        ([key, value]) =>
                                          `${monthlyAuxiliaryLabels[key] ?? key} ${value}`,
                                      )
                                      .join(' · ')}
                                  </small>
                                </td>
                                <td className="amount">{monthlyMoney(line.balance)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                    {canConfirm ? (
                      <div className="monthly-record-reason">
                        <input
                          aria-label={`确认余额 v${baseline.version} ${baseline.kind} 依据`}
                          maxLength={1000}
                          placeholder="写明核对原件的依据"
                          value={reasons[baseline.id] ?? ''}
                          disabled={locked}
                          onChange={(event) =>
                            setReasons((old) => ({ ...old, [baseline.id]: event.target.value }))
                          }
                        />
                        <button
                          className="button secondary small"
                          disabled={locked || (reasons[baseline.id] ?? '').trim().length < 3}
                          onClick={() =>
                            void command.run(
                              `/baselines/${encodeURIComponent(baseline.id)}/confirm`,
                              { reason: reasons[baseline.id].trim() },
                              '独立确认已保存。',
                              () =>
                                setReasons((old) => {
                                  const next = { ...old };
                                  delete next[baseline.id];
                                  return next;
                                }),
                            )
                          }
                        >
                          <CheckCheck size={14} />
                          独立确认
                        </button>
                      </div>
                    ) : !baseline.confirmed &&
                      isLatest &&
                      baseline.created_by === session.user_id ? (
                      <p className="field-help">由另一位有本账套复核权限的会计核查并确认。</p>
                    ) : null}
                  </article>
                );
              })}
              {!data.baselines.length && (
                <p className="monthly-empty">尚未录入外部余额。缺失余额保持待核实，不按零处理。</p>
              )}
            </div>
          </section>
          <section className="monthly-panel">
            <div className="monthly-record-heading">
              <div>
                <h2>月度差异复核快照</h2>
                <p>先独立确认最新期初和期末，再冻结比较结果。人工记录不改变金额、不自动锁期。</p>
              </div>
              <button
                className="button secondary small"
                disabled={
                  locked ||
                  !data.can_review ||
                  !latest.opening?.confirmed ||
                  !latest.closing?.confirmed
                }
                onClick={() =>
                  void command.run(
                    '/checks',
                    { opening_id: latest.opening!.id, closing_id: latest.closing!.id },
                    '已生成当前月度差异快照。',
                    undefined,
                    true,
                  )
                }
              >
                <ClipboardCheck size={14} />
                生成核对快照
              </button>
            </div>
            <div className="monthly-records">
              {data.checks.map((check) => (
                <article className="monthly-record" key={check.id}>
                  <div className="monthly-record-heading">
                    <div>
                      <h3>核对快照 v{check.version}</h3>
                      <p>
                        {check.report.difference_count} 项差异 · {check.report.unknown_count}{' '}
                        项待核实 · {new Date(check.created_at).toLocaleString('zh-CN')}
                      </p>
                    </div>
                    <span className={'monthly-chip ' + (!check.current ? 'attention' : '')}>
                      {!check.current
                        ? '事实已变化'
                        : check.closure
                          ? '已有人工核对记录'
                          : '等待人工核对'}
                    </span>
                  </div>
                  <details>
                    <summary>查看冻结的逐项比较</summary>
                    <ReferenceTable value={check.report} />
                  </details>
                  {check.closure ? (
                    <p className="monthly-inline-note">
                      {check.closure.closed_name}：{check.closure.reason}。该记录保留差异原值。
                    </p>
                  ) : check.current && data.can_review ? (
                    <div className="monthly-record-reason">
                      <input
                        aria-label={`月度快照 v${check.version} 人工核对依据`}
                        value={reasons[check.id] ?? ''}
                        maxLength={1000}
                        disabled={locked}
                        placeholder="写明差异处置或核查结果，不修改账务金额"
                        onChange={(event) =>
                          setReasons((old) => ({ ...old, [check.id]: event.target.value }))
                        }
                      />
                      <button
                        className="button secondary small"
                        disabled={locked || (reasons[check.id] ?? '').trim().length < 3}
                        onClick={() =>
                          void command.run(
                            `/checks/${encodeURIComponent(check.id)}/close`,
                            { reason: reasons[check.id].trim() },
                            '人工核对记录已保存；差异事实继续保留。',
                            () =>
                              setReasons((old) => {
                                const next = { ...old };
                                delete next[check.id];
                                return next;
                              }),
                          )
                        }
                      >
                        记录人工核对
                      </button>
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          </section>
        </>
      ) : null}
    </main>
  );
}
