import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorMessage } from './api';
import { moneyToCents } from './csv';
import type { Session } from './types';
import './review-policy.css';
import { reviewModeLabel } from './review-policy';
import type { CorrectionSeed } from './voucher-correction';
import {
  manualCanReview,
  parseManualReviewList,
  type ManualReviewContent,
  type ManualReviewLine,
  type ManualReviewList,
  type ManualReviewRequest,
} from './manual-review-types';

interface Props {
  session: Session;
  bookId: string;
  companyId?: string;
  correction?: CorrectionSeed;
}
const emptyLine = (): ManualReviewLine => ({
  account_id: '',
  account_version: 1,
  debit: '0.00',
  credit: '0.00',
  auxiliary_values: {},
  source_ref: '',
});
export function ManualVoucherReviewPanel(props: Props) {
  return (
    <ManualReviewContentPanel
      key={`${props.session.scope_key}:${props.bookId}:${props.companyId ?? ''}:${props.correction?.original_voucher_id ?? ''}:${props.correction?.relation_kind ?? ''}`}
      {...props}
    />
  );
}
function ManualReviewContentPanel({ session, bookId, companyId, correction }: Props) {
  const [data, setData] = useState<ManualReviewList | null>(null);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [date, setDate] = useState(''),
    [description, setDescription] = useState(correction?.description ?? '');
  const [reason, setReason] = useState(''),
    [reviewReason, setReviewReason] = useState<Record<string, string>>({});
  const [withdrawReason, setWithdrawReason] = useState<Record<string, string>>({});
  const [lines, setLines] = useState(
    correction?.lines.map((l) => ({ ...l, auxiliary_values: { ...l.auxiliary_values } })) ?? [
      emptyLine(),
      emptyLine(),
    ],
  );
  const [impactConfirmed, setImpactConfirmed] = useState(false);
  const [submittedCorrection, setSubmittedCorrection] = useState(false);
  const [draftKey, setDraftKey] = useState(() => crypto.randomUUID());
  const [uncertain, setUncertain] = useState(false);
  const live = useRef(true),
    locked = useRef(false);
  const pendingSubmission = useRef<ManualReviewContent | null>(null);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const value = parseManualReviewList(
          await api<unknown>(
            `/books/${encodeURIComponent(bookId)}/manual-review-requests`,
            session,
            { signal },
          ),
          { bookId, companyId, firmId: session.firm_id },
        );
        if (live.current && !signal?.aborted) {
          setData(value);
          setDate((old) => old || `${value.scope.period}-01`);
          if (
            pendingSubmission.current &&
            value.requests.some(
              (item) => item.content.command_key === pendingSubmission.current?.command_key,
            )
          ) {
            if (correction) setSubmittedCorrection(true);
            pendingSubmission.current = null;
            setUncertain(false);
            setDraftKey(crypto.randomUUID());
            setLines([emptyLine(), emptyLine()]);
            setDescription('');
            setReason('');
          }
        }
      } catch (failure) {
        if (live.current && !signal?.aborted) {
          setData(null);
          setError(errorMessage(failure));
        }
      } finally {
        if (live.current && !signal?.aborted) setLoading(false);
      }
    },
    [bookId, companyId, session, correction],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  let balanced = false;
  try {
    const debit = lines.reduce((sum, line) => sum + moneyToCents(line.debit), 0n);
    const credit = lines.reduce((sum, line) => sum + moneyToCents(line.credit), 0n);
    balanced =
      debit > 0n &&
      debit === credit &&
      lines.every(
        (line) =>
          !!line.account_id && moneyToCents(line.debit) > 0n !== moneyToCents(line.credit) > 0n,
      );
  } catch {
    /* Invalid amounts remain blocked; server validates the frozen submission again. */
  }
  async function execute(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      if (live.current)
        setError(`${errorMessage(failure)} 请重新读取当前状态；不要另建相同凭证绕过失败。`);
    } finally {
      if (live.current) {
        locked.current = false;
        setBusy(false);
      }
    }
  }
  function updateLine(index: number, value: Partial<ManualReviewLine>) {
    setImpactConfirmed(false);
    setLines((old) =>
      old.map((line, position) => (position === index ? { ...line, ...value } : line)),
    );
  }
  async function approve(item: ManualReviewRequest) {
    if (!data || !manualCanReview(data, item, session.user_id)) return;
    const explanation = (reviewReason[item.id] || '').trim();
    if (item.mode === 'single_operator' && explanation.length < 3) return;
    await execute(async () => {
      await api(`/posting/manual-requests/${encodeURIComponent(item.id)}/approve`, session, {
        method: 'POST',
        body: {
          expected_version: item.version,
          expected_policy_version: item.policy_version,
          reason: explanation || '已核对原件、金额和会计处理',
        },
      });
      if (!live.current) return;
      setNotice('审核已完成；正式记账须另行确认。');
      await load();
    });
  }
  return (
    <section className="review-section manual-review-panel" aria-label="手工凭证办理">
      <h3>手工凭证：提交待复核 → 会计审核 → 正式记账</h3>
      <p>此处保留完整制单责任。未审核凭证不进入账簿；单人办理明确记为非独立复核。</p>
      {loading && <p role="status">正在读取授权、复核记录与正式科目…</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <button className="text-button" disabled={busy || loading} onClick={() => void load()}>
        重新读取手工凭证
      </button>
      {correction && data && !data.can_prepare && (
        <p role="status">
          当前授权或期间状态不允许制单。请联系本账套主办会计确认可办理期间；原凭证继续保留。
        </p>
      )}
      {data?.can_prepare && !submittedCorrection && (
        <details open={!!correction || !data.requests.length}>
          <summary>{correction ? '从原凭证准备更正' : '新增手工凭证'}</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (
                !balanced ||
                reason.trim().length < 3 ||
                !description.trim() ||
                !date ||
                uncertain ||
                (correction && !impactConfirmed)
              )
                return;
              const content: ManualReviewContent = {
                command_key: draftKey,
                book_id: bookId,
                voucher_date: date,
                voucher_type: '记',
                description: description.trim(),
                reason: reason.trim(),
                lines,
                ...(correction
                  ? {
                      relation_kind: correction.relation_kind,
                      original_voucher_id: correction.original_voucher_id,
                    }
                  : {}),
              };
              void execute(async () => {
                pendingSubmission.current = content;
                setUncertain(true);
                try {
                  await api('/posting/manual-requests', session, { method: 'POST', body: content });
                } catch (failure) {
                  if (
                    live.current &&
                    failure instanceof ApiError &&
                    [400, 401, 403, 404, 409, 422].includes(failure.status)
                  ) {
                    pendingSubmission.current = null;
                    setUncertain(false);
                  }
                  throw failure;
                }
                if (!live.current) return;
                if (correction) setSubmittedCorrection(true);
                pendingSubmission.current = null;
                setUncertain(false);
                setDraftKey(crypto.randomUUID());
                setLines([emptyLine(), emptyLine()]);
                setDescription('');
                setReason('');
                setNotice('已提交待复核，尚未正式记账。请按当前政策安排会计审核。');
                await load();
              });
            }}
          >
            <fieldset disabled={busy || uncertain} onChange={() => setImpactConfirmed(false)}>
              <legend>准备新手工凭证</legend>
              <label>
                记账日期
                <input
                  type="date"
                  value={date}
                  required
                  onChange={(event) => setDate(event.target.value)}
                />
              </label>
              <label>
                凭证摘要
                <input
                  value={description}
                  maxLength={500}
                  required
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <label>
                制单说明
                <textarea
                  value={reason}
                  maxLength={1000}
                  required
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              {lines.map((line, index) => (
                <fieldset key={index}>
                  <legend>分录 {index + 1}</legend>
                  <label>
                    正式科目
                    <select
                      aria-label={`第 ${index + 1} 行正式科目`}
                      value={line.account_id}
                      required
                      onChange={(event) => {
                        const account = data.accounts.find(
                          (item) => item.id === event.target.value,
                        );
                        updateLine(index, {
                          account_id: account?.id || '',
                          account_version: account?.version || 1,
                        });
                      }}
                    >
                      <option value="">选择已配置的正式科目</option>
                      {data.accounts.map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.code} · {account.name} · v{account.version}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    借方金额
                    <input
                      inputMode="decimal"
                      aria-label={`第 ${index + 1} 行借方金额`}
                      value={line.debit}
                      onChange={(event) => updateLine(index, { debit: event.target.value })}
                    />
                  </label>
                  <label>
                    贷方金额
                    <input
                      inputMode="decimal"
                      aria-label={`第 ${index + 1} 行贷方金额`}
                      value={line.credit}
                      onChange={(event) => updateLine(index, { credit: event.target.value })}
                    />
                  </label>
                  <label>
                    原件或依据引用
                    <input
                      value={line.source_ref}
                      maxLength={200}
                      onChange={(event) => updateLine(index, { source_ref: event.target.value })}
                    />
                  </label>
                  <details>
                    <summary>辅助核算（科目要求时填写）</summary>
                    {['department', 'counterparty', 'employee', 'project'].map((key, dimension) => (
                      <label key={key}>
                        {['部门', '往来方', '员工', '项目'][dimension]}稳定 ID
                        <input
                          value={line.auxiliary_values[key] || ''}
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
                  </details>
                  {lines.length > 2 && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        setImpactConfirmed(false);
                        setLines((old) => old.filter((_, position) => position !== index));
                      }}
                    >
                      删除本行
                    </button>
                  )}
                </fieldset>
              ))}
              <button
                type="button"
                className="button secondary"
                disabled={lines.length >= 20}
                onClick={() => {
                  setImpactConfirmed(false);
                  setLines((old) => [...old, emptyLine()]);
                }}
              >
                添加分录
              </button>
              <p role="status">
                {balanced
                  ? '借贷平衡，仍须核对业务依据与科目。'
                  : '请填写正式科目及有效、平衡的借贷金额。'}
              </p>
              {correction && (
                <section className="review-section" aria-label="更正影响预览">
                  <h4>
                    {correction.relation_kind === 'reversal' ? '全额冲销' : '差额调整'} ·
                    原凭证保持不变
                  </h4>
                  <p>
                    本次只提交以上分录的待审版本；独立复核后才能正式记账。原件、原凭证和历史审核继续保留。
                  </p>
                  <p>关联原凭证：{correction.original_voucher_id}</p>
                  <label>
                    <input
                      type="checkbox"
                      checked={impactConfirmed}
                      onChange={(e) => {
                        e.stopPropagation();
                        setImpactConfirmed(e.target.checked);
                      }}
                    />
                    已核对日期、金额、科目和以上更正影响
                  </label>
                </section>
              )}
              <button
                className="button primary"
                disabled={
                  !balanced ||
                  reason.trim().length < 3 ||
                  !description.trim() ||
                  !date ||
                  (!!correction && !impactConfirmed)
                }
              >
                提交待复核
              </button>
            </fieldset>
            {uncertain && (
              <p role="status">
                提交结果待确认，请先重新读取列表核对；本页已保留原提交内容，避免重复建立凭证。
              </p>
            )}
          </form>
        </details>
      )}
      {data && !data.requests.length && (
        <p>本账套暂无手工凭证待办。由授权制单会计提交后，在此安排复核。</p>
      )}
      {data?.requests.map((item) => (
        <article className="review-section" key={item.id}>
          <h4>{item.content.description}</h4>
          <p>
            {item.content.voucher_date} · 版本 {item.version} ·{' '}
            {item.withdrawn
              ? '已撤回，保留历史'
              : item.posted_voucher_id
                ? '已正式记账'
                : item.status === 'approved'
                  ? '已审核，待正式记账'
                  : '待会计审核'}
          </p>
          <p>
            {reviewModeLabel(item.mode)} · 政策版本 {item.policy_version} · 制单：
            {item.prepared_name || item.prepared_by}
          </p>
          <p>制单说明：{item.content.reason}</p>
          {item.content.original_voucher_id && (
            <p>
              {item.content.relation_kind === 'reversal' ? '冲销' : '调整'}原凭证：
              {item.content.original_voucher_id} · 原记录保留
            </p>
          )}
          {item.withdrawn && (
            <p>
              撤回说明：{item.withdrawal_reason || '请查看操作审计'}
              。如需更正，请重新准备，不覆盖此记录。
            </p>
          )}
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>科目</th>
                  <th>借方</th>
                  <th>贷方</th>
                  <th>依据</th>
                </tr>
              </thead>
              <tbody>
                {item.content.lines.map((line, index) => (
                  <tr key={index}>
                    <td>
                      {data.accounts.find((account) => account.id === line.account_id)?.name ||
                        line.account_id}{' '}
                      · v{line.account_version}
                    </td>
                    <td>{line.debit}</td>
                    <td>{line.credit}</td>
                    <td>
                      {line.source_ref || '未填写'}
                      {Object.entries(line.auxiliary_values).map(([key, value]) => (
                        <small key={key}>
                          {key}：{value}
                        </small>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!item.withdrawn &&
            item.status === 'pending' &&
            !manualCanReview(data, item, session.user_id) && (
              <p>
                {item.blocked_reason ||
                  '请交由符合当前政策的另一名复核会计办理；授权不足时联系事务所管理员。'}
              </p>
            )}
          {!item.withdrawn &&
            item.status === 'pending' &&
            data.can_prepare &&
            item.prepared_by === session.user_id && (
              <details>
                <summary>录错或政策变更？撤回此待审凭证</summary>
                <p>撤回只结束本次待审办理，原内容和责任仍保留；之后可重新准备新版本。</p>
                <label>
                  撤回原因
                  <textarea
                    value={withdrawReason[item.id] || ''}
                    maxLength={1000}
                    disabled={busy}
                    onChange={(event) =>
                      setWithdrawReason((old) => ({ ...old, [item.id]: event.target.value }))
                    }
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={busy || (withdrawReason[item.id] || '').trim().length < 3}
                  onClick={() =>
                    void execute(async () => {
                      const explanation = (withdrawReason[item.id] || '').trim();
                      if (explanation.length < 3) return;
                      await api(
                        `/posting/manual-requests/${encodeURIComponent(item.id)}/withdraw`,
                        session,
                        {
                          method: 'POST',
                          body: { expected_version: item.version, reason: explanation },
                        },
                      );
                      if (!live.current) return;
                      setNotice('待审凭证已撤回，原记录保留。需要更正时请重新准备。');
                      await load();
                    })
                  }
                >
                  确认撤回待审凭证
                </button>
              </details>
            )}
          {manualCanReview(data, item, session.user_id) && (
            <>
              <label>
                {item.mode === 'single_operator' ? '单人办理说明（非独立复核，必填）' : '审核说明'}
                <textarea
                  value={reviewReason[item.id] || ''}
                  maxLength={1000}
                  disabled={busy}
                  onChange={(event) =>
                    setReviewReason((old) => ({ ...old, [item.id]: event.target.value }))
                  }
                />
              </label>
              <button
                className="button secondary"
                disabled={
                  busy ||
                  (item.mode === 'single_operator' &&
                    (reviewReason[item.id] || '').trim().length < 3)
                }
                onClick={() => void approve(item)}
              >
                确认手工凭证审核
              </button>
            </>
          )}
          {!item.withdrawn && item.status === 'approved' && (
            <>
              <p>
                {item.independent === true ? '已独立复核' : '单人办理结果（非独立复核）'} · 审核：
                {item.reviewed_name || item.reviewed_by} · {item.review_reason}
              </p>
              {item.posted_voucher_id ? (
                <p>正式凭证：{item.posted_voucher_id}。请在账簿中核对；导出仍按会计授权执行。</p>
              ) : (
                data.can_review && (
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() =>
                      void execute(async () => {
                        await api(
                          `/posting/manual-requests/${encodeURIComponent(item.id)}/post`,
                          session,
                          {
                            method: 'POST',
                            body: {
                              expected_version: item.version,
                              command_key: item.content.command_key,
                            },
                          },
                        );
                        if (!live.current) return;
                        setNotice('正式记账完成，请重新读取账簿核对；此操作不代替会计导出。');
                        await load();
                      })
                    }
                  >
                    确认正式记账
                  </button>
                )
              )}
            </>
          )}
        </article>
      ))}
    </section>
  );
}
