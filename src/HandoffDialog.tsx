import { useEffect, useState } from 'react';
import { api } from './api';
import { Modal } from './AppPrimitives';
import {
  acceptAssignment,
  acceptHandoffs,
  handoffOriginalHref,
  type Handoff,
} from './batch-operations';
import type { Session } from './types';
import { useOperationRequests } from './useOperationRequests';
import './batch-operations.css';

export function HandoffDialog({
  session,
  period,
  onClose,
  canAssign = false,
  onOpenOriginal,
}: {
  session: Session;
  period: string;
  onClose: () => void;
  canAssign?: boolean;
  onOpenOriginal?: (item: Handoff) => void;
}) {
  const { run, busy, error, denied } = useOperationRequests(session);
  const [items, setItems] = useState<Handoff[]>([]),
    [selected, setSelected] = useState<Handoff | null>(null);
  const [candidates, setCandidates] = useState<{ user_id: string; name: string }[]>([]);
  const [assignee, setAssignee] = useState(''),
    [due, setDue] = useState(''),
    [reason, setReason] = useState('');
  const [notice, setNotice] = useState('');
  const load = () =>
    run(
      async (signal) => acceptHandoffs(await api<unknown>('/handoff', session, { signal })),
      (value) => {
        setItems(value.filter((row) => row.scope.period === period));
        setSelected(null);
        setCandidates([]);
      },
    );
  useEffect(() => {
    void load(); /* mounted scope is keyed by parent */
  }, []);
  async function choose(item: Handoff) {
    if (!canAssign) return;
    setSelected(null);
    setCandidates([]);
    setAssignee('');
    setDue(item.due_on ?? '');
    setReason('');
    setNotice('');
    await run(
      async (signal) => {
        const value = await api<{ items: { user_id: string; name: string }[] }>(
          `/handoff/candidates?${new URLSearchParams({ book_id: item.scope.book_id, role: item.role })}`,
          session,
          { signal },
        );
        if (
          !Array.isArray(value.items) ||
          value.items.some(
            (c) =>
              typeof c.user_id !== 'string' || !c.user_id || typeof c.name !== 'string' || !c.name,
          ) ||
          new Set(value.items.map((c) => c.user_id)).size !== value.items.length
        )
          throw new Error('候选人数据不完整');
        return value.items.filter(
          (c) => c.user_id !== item.maker_id && c.user_id !== item.assignee_id,
        );
      },
      (value) => {
        setSelected(item);
        setCandidates(value);
      },
    );
  }
  async function assign() {
    if (
      !canAssign ||
      !selected ||
      !assignee ||
      reason.trim().length < 3 ||
      !candidates.some((c) => c.user_id === assignee)
    )
      return;
    const source = selected;
    const ok = await run(
      (signal) =>
        api<unknown>(
          `/handoff/sources/${encodeURIComponent(source.source.kind)}/${encodeURIComponent(source.source.id)}/assign`,
          session,
          {
            signal,
            method: 'POST',
            body: {
              expected_version: source.source.version,
              assignee_id: assignee,
              due_on: due || null,
              reason: reason.trim(),
            },
          },
        ),
      (reply) => {
        acceptAssignment(reply, source, assignee);
        setNotice('指派已登记，请刷新队列核对；指派不会授予权限，也不代表已复核。');
      },
      true,
    );
    setSelected(null);
    setCandidates([]); // never replay a conflict or an uncertain write
    if (ok) await load();
  }
  return (
    <Modal
      title="人工接手"
      subtitle={`${period} · 税务、批量失败、通知核查与交付签收的统一接手队列`}
      onClose={onClose}
      busy={busy}
    >
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {!denied && (
        <div className="batch-operations">
          {notice && <p role="status">{notice}</p>}
          <button className="button secondary" disabled={busy} onClick={() => void load()}>
            刷新接手队列
          </button>
          {!items.length && !busy && <p>本月没有待接手的异常事项。</p>}
          <div className="batch-rows">
            {items.map((item) => (
              <article key={item.source.id} className="batch-row">
                <header>
                  <strong>{item.scope.company_name}</strong>
                  <span>{item.assignee_id ? '已指派' : '待指派'}</span>
                </header>
                <p>
                  {item.scope.book_name} ·{' '}
                  {item.source.kind === 'tax_workpaper'
                    ? '底稿'
                    : item.source.kind === 'batch_item'
                      ? '批量项'
                      : item.source.kind === 'notification_investigation'
                        ? '通知'
                        : '交付包'}
                  版本 {item.source.version}
                </p>
                <p>
                  {item.next_action}
                  {item.due_on ? ` · 截止 ${item.due_on}` : ' · 尚未设期限'}
                </p>
                {item.source.kind === 'delivery_package' && onOpenOriginal ? (
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => onOpenOriginal(item)}
                  >
                    打开原记录核对
                  </button>
                ) : (
                  <a className="button secondary" href={handoffOriginalHref(item)}>
                    打开原记录核对
                  </a>
                )}
                {canAssign && (
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => void choose(item)}
                  >
                    {item.assignee_id ? '调整负责人' : '选择负责人'}
                  </button>
                )}
              </article>
            ))}
          </div>
          {selected && (
            <fieldset disabled={busy}>
              <legend>
                {selected.scope.company_name} · 底稿版本 {selected.source.version}
              </legend>
              <label>
                接手人
                <select
                  aria-label="接手人"
                  value={assignee}
                  onChange={(e) => setAssignee(e.target.value)}
                >
                  <option value="">请选择，不默认指派</option>
                  {candidates.map((c) => (
                    <option key={c.user_id} value={c.user_id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              {!candidates.length && (
                <p>
                  没有符合独立复核条件的候选人，请管理员先核对账套授权。制单人不能作为独立复核人。
                </p>
              )}
              <label>
                办理期限
                <input
                  aria-label="办理期限"
                  type="date"
                  value={due}
                  onChange={(e) => setDue(e.target.value)}
                />
              </label>
              <label>
                指派依据
                <textarea
                  aria-label="指派依据"
                  value={reason}
                  maxLength={1000}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                className="button primary"
                disabled={!assignee || reason.trim().length < 3}
                onClick={() => void assign()}
              >
                确认指派
              </button>
            </fieldset>
          )}
        </div>
      )}
    </Modal>
  );
}
