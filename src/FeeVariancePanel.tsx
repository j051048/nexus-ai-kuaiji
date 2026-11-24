import { useEffect, useState } from 'react';
import { api, ApiError, errorMessage } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import { formatCents, parseYuanToCents } from './money';
import type { Session } from './types';
import './fee-variance.css';

type Variance = {
  id: string;
  kind: string;
  status: string;
  state: string;
  expected_cents: number;
  actual_cents: number;
  difference_cents: number;
  reason_code: string;
  evidence_ref: string;
  processing_started_by: string | null;
  assignee_name?: string | null;
  draft_id?: string | null;
  bank_source?: {
    date: string;
    description: string;
    counterparty: string;
    amount_cents: number;
  } | null;
  candidates?: { id: string; amount_cents: number; counterparty: string }[];
};

type Reason = { code: string; label: string; blocks_close: boolean };
type Assignee = { id: string; name: string };

const KIND_LABELS: Record<string, string> = {
  underpaid: '少收',
  overpaid: '多收',
  unmatched: '无对应应收',
  cross_period: '跨期到账',
  many_to_one: '多流水对一应收',
  one_to_many: '一流水对多应收',
};

const STATE_LABELS: Record<string, string> = {
  pending: '待处理',
  processing: '处理中',
  handled: '已处理',
  reversed: '已撤销',
};

function failureText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录已失效，请重新登录后返回。';
    if (error.status === 403 || error.status === 404)
      return '当前账号没有处理这笔差异的权限，列表已按服务端授权刷新。';
    if (error.status === 409 || error.status === 422) return error.message;
  }
  return errorMessage(error);
}

export function FeeVariancePanel({
  session,
  bookId,
  period,
  onOpenDraft,
  initialVarianceId,
  canManage = false,
}: {
  session: Session;
  bookId: string;
  period: string;
  onOpenDraft?: (bookId: string, draftId: string) => void;
  initialVarianceId?: string;
  canManage?: boolean;
}) {
  const [items, setItems] = useState<Variance[] | null>(null);
  const [reasons, setReasons] = useState<Reason[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [reasonBy, setReasonBy] = useState<Record<string, string>>({});
  const [evidenceBy, setEvidenceBy] = useState<Record<string, string>>({});
  const [reverseBy, setReverseBy] = useState<Record<string, string>>({});
  const [assignees, setAssignees] = useState<Assignee[]>([]);
  const [assignTo, setAssignTo] = useState<Record<string, string>>({});
  const [assignReason, setAssignReason] = useState<Record<string, string>>({});
  const [assignError, setAssignError] = useState('');
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState('');
  const [registered, setRegistered] = useState('');
  const [targetNotice, setTargetNotice] = useState('');
  useEffect(() => {
    if (!initialVarianceId || !items) return;
    const node = document.getElementById(`fee-variance-${initialVarianceId}`);
    if (!node) {
      setTargetNotice('这笔差异不在当前期间，可能已处理、已撤销或权限已变更，请刷新待办。');
      return;
    }
    setTargetNotice('已定位到待办中的这笔差异。');
    const frame = requestAnimationFrame(() => {
      node.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
      node.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [initialVarianceId, items]);

  const [draft, setDraft] = useState({
    kind: 'unmatched',
    expected: '0.00',
    actual: '',
    evidence: '',
    receivable: '',
    bankSource: '',
  });

  async function load(signal?: AbortSignal) {
    setError('');
    const priority = initialVarianceId ? 'high' : 'background';
    try {
      const [rows, catalog] = await Promise.all([
        scheduleSubpanel(
          () =>
            api<{ items: Variance[] }>(
              `/tax/books/${bookId}/fee-variances?period=${encodeURIComponent(period)}`,
              session,
              { signal },
            ),
          signal,
          priority,
        ),
        scheduleSubpanel(
          () =>
            api<{ items: Reason[] }>(`/tax/books/${bookId}/fee-variance-reasons`, session, {
              signal,
            }),
          signal,
          priority,
        ),
      ]);
      if (signal?.aborted) return;
      setItems(rows.items);
      setReasons(catalog.items);
    } catch (failure) {
      if (signal?.aborted || (failure instanceof DOMException && failure.name === 'AbortError'))
        return;
      setError(failureText(failure));
      setItems([]);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [bookId, period, session.scope_key]);

  useEffect(() => {
    if (!canManage) {
      setAssignees([]);
      return;
    }
    const controller = new AbortController();
    let active = true;
    void scheduleSubpanel(
      () =>
        api<{ items: Assignee[] }>(`/tax/books/${bookId}/fee-variance-assignees`, session, {
          signal: controller.signal,
        }),
      controller.signal,
    )
      .then((people) => {
        if (active) setAssignees(people.items);
      })
      .catch(() => {
        if (active) setAssignees([]);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [bookId, canManage, session.scope_key]);

  async function register() {
    setRegisterError('');
    setRegistered('');
    const expected = parseYuanToCents(draft.expected);
    const actual = parseYuanToCents(draft.actual);
    if (expected === null || actual === null) {
      setRegisterError('请填写有效的应收和实际到账金额。');
      return;
    }
    if (expected < 0n || actual < 0n || actual === 0n) {
      setRegisterError('金额不能为负，实际到账必须大于 0。');
      return;
    }
    setRegistering(true);
    try {
      await api(`/tax/books/${bookId}/fee-variances`, session, {
        method: 'POST',
        body: {
          kind: draft.kind,
          expected_cents: Number(expected),
          actual_cents: Number(actual),
          receivable_id: draft.receivable.trim() || null,
          bank_source_id: draft.bankSource.trim() || null,
          evidence_ref: draft.evidence.trim() || null,
          idempotency_key: `ui-${crypto.randomUUID()}`,
        },
      });
      setDraft((current) => ({
        ...current,
        actual: '',
        evidence: '',
        receivable: '',
        bankSource: '',
      }));
      setRegistered('差异已登记，请继续认领并按政策原因处理。');
      await load();
    } catch (failure) {
      setRegisterError(failureText(failure));
    } finally {
      setRegistering(false);
    }
  }

  async function act(id: string, action: 'start' | 'handle' | 'reverse') {
    setBusy(id + action);
    setError('');
    try {
      if (action === 'start') {
        await api(`/tax/fee-variances/${id}/start`, session, { method: 'POST', body: {} });
      } else if (action === 'handle') {
        await api(`/tax/fee-variances/${id}/handle`, session, {
          method: 'POST',
          body: { reason_code: reasonBy[id] || 'unresolved', evidence_ref: evidenceBy[id] || '' },
        });
      } else {
        await api(`/tax/fee-variances/${id}/reverse`, session, {
          method: 'POST',
          body: { reason: reverseBy[id] || '' },
        });
      }
      await load();
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy('');
    }
  }

  return (
    <section className="fee-variance" aria-label="规费差异">
      <header>
        <div>
          <h3>规费差异</h3>
          <p>差异原因只能来自当前生效政策；未配置时只能记待查，并阻断月结。</p>
        </div>
        <button type="button" className="secondary" onClick={() => void load()} disabled={!!busy}>
          刷新
        </button>
      </header>
      {error && (
        <p role="alert" className="fee-variance-error">
          {error}
        </p>
      )}
      <details className="fee-variance-register" open={!!registerError}>
        <summary>登记差异</summary>
        <form
          aria-label="登记规费差异"
          onSubmit={(event) => {
            event.preventDefault();
            void register();
          }}
        >
          <label>
            差异类型
            <select
              value={draft.kind}
              onChange={(event) =>
                setDraft((current) => ({ ...current, kind: event.target.value }))
              }
            >
              {Object.entries(KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            应收金额（元）
            <input
              inputMode="decimal"
              value={draft.expected}
              onChange={(event) =>
                setDraft((current) => ({ ...current, expected: event.target.value }))
              }
            />
          </label>
          <label>
            实际到账（元）
            <input
              inputMode="decimal"
              value={draft.actual}
              placeholder="例如 500.00"
              onChange={(event) =>
                setDraft((current) => ({ ...current, actual: event.target.value }))
              }
            />
          </label>
          <label>
            核查依据
            <input
              value={draft.evidence}
              placeholder="流水号、回单编号或核查说明"
              onChange={(event) =>
                setDraft((current) => ({ ...current, evidence: event.target.value }))
              }
            />
          </label>
          <label>
            应收标识（可选）
            <input
              value={draft.receivable}
              onChange={(event) =>
                setDraft((current) => ({ ...current, receivable: event.target.value }))
              }
            />
          </label>
          <label>
            银行来源标识（可选）
            <input
              value={draft.bankSource}
              onChange={(event) =>
                setDraft((current) => ({ ...current, bankSource: event.target.value }))
              }
            />
          </label>
          {registerError && (
            <p role="alert" className="fee-variance-error">
              {registerError}
            </p>
          )}
          {registered && (
            <p role="status" className="fee-variance-ok">
              {registered}
            </p>
          )}
          <button type="submit" disabled={registering}>
            {registering ? '正在登记…' : '登记差异'}
          </button>
        </form>
      </details>
      {items === null ? (
        <p className="fee-variance-empty">正在读取规费差异…</p>
      ) : items.length === 0 ? (
        <p className="fee-variance-empty">当前期间没有规费差异。</p>
      ) : (
        <>
          {targetNotice && (
            <p role="status" className="fee-variance-target">
              {targetNotice}
            </p>
          )}
          <ul className="fee-variance-list">
            {items.map((item) => {
              const state = item.state || item.status;
              const reason = reasons.find((entry) => entry.code === item.reason_code);
              return (
                <li
                  key={item.id}
                  id={`fee-variance-${item.id}`}
                  data-state={state}
                  tabIndex={initialVarianceId === item.id ? -1 : undefined}
                  className={initialVarianceId === item.id ? 'is-target' : undefined}
                >
                  <div className="fee-variance-head">
                    <strong>{KIND_LABELS[item.kind] || item.kind}</strong>
                    <span className={`fee-variance-state is-${state}`}>
                      {STATE_LABELS[state] || state}
                    </span>
                    <span className="fee-variance-diff">
                      差额 {formatCents(BigInt(item.difference_cents))}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>应收</dt>
                      <dd>{formatCents(BigInt(item.expected_cents))}</dd>
                    </div>
                    <div>
                      <dt>实际到账</dt>
                      <dd>{formatCents(BigInt(item.actual_cents))}</dd>
                    </div>
                    <div>
                      <dt>原因</dt>
                      <dd>
                        {reason?.label ||
                          (item.reason_code === 'unresolved' ? '待查' : item.reason_code)}
                      </dd>
                    </div>
                    <div>
                      <dt>证据</dt>
                      <dd>{item.evidence_ref || '—'}</dd>
                    </div>
                    {item.assignee_name ? (
                      <div>
                        <dt>当前处理人</dt>
                        <dd>{item.assignee_name}</dd>
                      </div>
                    ) : null}
                    {item.bank_source ? (
                      <>
                        <div>
                          <dt>流水日期</dt>
                          <dd>{item.bank_source.date}</dd>
                        </div>
                        <div>
                          <dt>流水摘要</dt>
                          <dd>{item.bank_source.description}</dd>
                        </div>
                        <div>
                          <dt>流水对方</dt>
                          <dd>{item.bank_source.counterparty}</dd>
                        </div>
                        <div>
                          <dt>流水金额</dt>
                          <dd>{formatCents(BigInt(item.bank_source.amount_cents))}</dd>
                        </div>
                      </>
                    ) : null}
                  </dl>
                  {item.candidates?.length ? (
                    <div className="fee-variance-candidates">
                      <span>候选应收（只读）</span>
                      <ul>
                        {item.candidates.map((candidate) => (
                          <li key={candidate.id}>
                            {candidate.counterparty} · {formatCents(BigInt(candidate.amount_cents))}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {item.draft_id && onOpenDraft ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => onOpenDraft(bookId, item.draft_id as string)}
                    >
                      打开差异草稿
                    </button>
                  ) : null}
                  {state === 'pending' && !item.processing_started_by && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void act(item.id, 'start')}
                      disabled={!!busy}
                    >
                      开始处理
                    </button>
                  )}
                  {(state === 'pending' || state === 'processing') && (
                    <div className="fee-variance-form">
                      <label>
                        原因
                        <select
                          value={reasonBy[item.id] || 'unresolved'}
                          onChange={(event) =>
                            setReasonBy((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                        >
                          <option value="unresolved">待查</option>
                          {reasons.map((entry) => (
                            <option key={entry.code} value={entry.code}>
                              {entry.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        核查依据
                        <input
                          value={evidenceBy[item.id] || ''}
                          onChange={(event) =>
                            setEvidenceBy((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                          placeholder="原件编号、流水号或核查说明"
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => void act(item.id, 'handle')}
                        disabled={!!busy}
                      >
                        保存处理
                      </button>
                    </div>
                  )}
                  {(state === 'pending' || state === 'processing') && assignees.length ? (
                    <details className="fee-variance-reverse">
                      <summary>转交处理</summary>
                      <label>
                        接手人
                        <select
                          value={assignTo[item.id] || ''}
                          onChange={(event) =>
                            setAssignTo((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                        >
                          <option value="">选择有编辑权限的人员</option>
                          {assignees.map((person) => (
                            <option key={person.id} value={person.id}>
                              {person.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        转交原因
                        <input
                          value={assignReason[item.id] || ''}
                          onChange={(event) =>
                            setAssignReason((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                          placeholder="至少 3 个字"
                        />
                      </label>
                      {assignError ? <p role="alert">{assignError}</p> : null}
                      <button
                        type="button"
                        className="secondary"
                        disabled={!!busy}
                        onClick={async () => {
                          setAssignError('');
                          const target = assignTo[item.id];
                          const reason = (assignReason[item.id] || '').trim();
                          if (!target || reason.length < 3) {
                            setAssignError('请选择接手人并填写至少 3 个字的转交原因。');
                            return;
                          }
                          setBusy(item.id);
                          try {
                            await api(`/tax/fee-variances/${item.id}/assign`, session, {
                              method: 'POST',
                              body: { assignee_id: target, reason },
                            });
                            setAssignTo((current) => ({ ...current, [item.id]: '' }));
                            setAssignReason((current) => ({ ...current, [item.id]: '' }));
                            await load();
                          } catch (failure) {
                            setAssignError(failureText(failure));
                          } finally {
                            setBusy('');
                          }
                        }}
                      >
                        确认转交
                      </button>
                    </details>
                  ) : null}
                  {state !== 'reversed' && (
                    <details className="fee-variance-reverse">
                      <summary>撤销这笔差异</summary>
                      <label>
                        撤销原因
                        <input
                          value={reverseBy[item.id] || ''}
                          onChange={(event) =>
                            setReverseBy((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                          placeholder="至少 3 个字"
                        />
                      </label>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => void act(item.id, 'reverse')}
                        disabled={!!busy}
                      >
                        确认撤销
                      </button>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
