import { useEffect, useRef, useState } from 'react';
import { DeliveryFollowupPanel } from './DeliveryFollowupPanel';
import type { MonthlyDestination } from './monthly-workbench-types';
import type { InboxProgress } from './workspace-progress';
import { useMonthlyData } from './useMonthlyData';
import { parseFirmInbox, type FirmItem as Item, type Bucket } from './firm-inbox-contract';
import type { WorkspaceTask } from './workspace-types';
import { Bell, CheckCircle2, RefreshCw, UserRoundCheck } from 'lucide-react';
import { api, errorMessage } from './api';
import {
  followupRows,
  followupSummary,
  validateFollowupFeed,
  acceptFollowup,
  type DeliveryFollowup,
  type FollowupState,
  type DeliveryFollowupFeed,
} from './delivery-followups';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-inbox.css';

const labels: Record<Bucket, string> = {
  all: '全部',
  today: '今天',
  overdue: '逾期',
  blocked: '阻塞',
  review: '待复核',
  failed: '后台失败',
};

export function FirmInbox({
  session,
  onOpen,
  onOpenMonthly,
  locked = false,
  initialProgress,
  onProgress,
  revision: externalRevision = 0,
  onBusyChange,
  onDirtyChange,
}: {
  session: Session;
  onOpen?: (task: WorkspaceTask) => void;
  onOpenMonthly?: (target: MonthlyDestination) => void;
  locked?: boolean;
  initialProgress?: InboxProgress;
  onProgress?: (value: InboxProgress) => void;
  revision?: number;
  onBusyChange?: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [bucket, setBucket] = useState<Bucket>(initialProgress?.bucket ?? 'all');
  const [offset, setOffset] = useState(initialProgress?.offset ?? 0);
  const [deliveryPages, setDeliveryPages] = useState(initialProgress?.deliveryPages ?? [0]);
  const deliveryOffset = deliveryPages[deliveryPages.length - 1];
  const [followup, setFollowup] = useState<FollowupState | null>(null);
  const [panelBusy, setPanelBusy] = useState(false);
  const opening = useRef(false),
    epoch = useRef(0);
  useEffect(() => {
    onBusyChange?.(panelBusy);
    return () => onBusyChange?.(false);
  }, [panelBusy, onBusyChange]);
  const [selected, setSelected] = useState<string[]>([]);
  const [assignee, setAssignee] = useState('');
  const [dueOn, setDueOn] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('请在今天完成当前待办，如缺少资料请直接反馈。');
  const [busy, setBusy] = useState('');
  const [actionError, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const inbox = useMonthlyData<unknown>(
    session,
    `/firm/inbox?bucket=${bucket}&limit=20&offset=${offset}`,
    revision + externalRevision,
  );
  const data =
    inbox.data === null
      ? null
      : parseFirmInbox(inbox.data, session.firm_id, session.user_id, bucket, offset);
  const feed = useMonthlyData<DeliveryFollowupFeed>(
    session,
    `/delivery/followups?limit=8&offset=${deliveryOffset}`,
    revision + externalRevision,
  );
  const followups =
    feed.data && validateFollowupFeed(feed.data, session, { offset: deliveryOffset, limit: 8 })
      ? feed.data
      : null;
  const followupError =
    feed.error || (feed.data && !followups ? '交付待办返回格式不符，暂不展示。' : '');
  const error =
    inbox.error ||
    (inbox.data !== null && !data ? '待办范围或格式不符，请重新读取。' : '') ||
    actionError;

  const key = (item: Item) => `${item.kind}:${item.ref}`;
  useEffect(() => {
    onProgress?.({ bucket, offset, deliveryPages });
  }, [bucket, offset, deliveryPages, onProgress]);
  useEffect(() => {
    if (data && offset > 0 && offset >= data.pagination.total)
      setOffset(Math.max(0, Math.floor((data.pagination.total - 1) / 20) * 20));
  }, [data, offset]);
  useEffect(
    () => () => {
      epoch.current++;
    },
    [],
  );
  useEffect(() => {
    if (inbox.revoked || feed.revoked) {
      epoch.current++;
      setFollowup(null);
      setBusy('');
      setSelected([]);
    }
  }, [inbox.revoked, feed.revoked]);
  const restored = useRef(false);
  useEffect(() => {
    if (!inbox.loading && !feed.loading && !restored.current) {
      restored.current = true;
      window.scrollTo(0, initialProgress?.scrollY ?? 0);
    }
  }, [inbox.loading, feed.loading, initialProgress?.scrollY]);
  function back() {
    setFollowup(null);
    setRevision((n) => n + 1);
  }
  async function openDelivery(source: DeliveryFollowup) {
    if (opening.current || locked || panelBusy) return;
    opening.current = true;
    const stamp = ++epoch.current;
    setBusy('open');
    setError('');
    setFollowup(null);
    try {
      const result = await api<unknown>(
        `/books/${encodeURIComponent(source.book_id)}/delivery/monthly-followup?period=${encodeURIComponent(source.period)}`,
        session,
      );
      if (stamp !== epoch.current) return;
      const current = acceptFollowup(result, source, session);
      if (!current) throw new Error('待办范围或来源已变化，请刷新列表后重新打开。');
      setFollowup(current);
    } catch (caught) {
      if (stamp === epoch.current) {
        setError(errorMessage(caught));
        setRevision((n) => n + 1);
      }
    } finally {
      opening.current = false;
      if (stamp === epoch.current) setBusy('');
    }
  }
  async function openTask(item: Item) {
    if (opening.current || locked || panelBusy) return;
    opening.current = true;
    const stamp = ++epoch.current;
    setBusy('open');
    setError('');
    try {
      const current = await api<WorkspaceTask>(
        `/workspace/tasks/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.ref)}`,
        session,
      );
      if (stamp !== epoch.current) return;
      if (
        current.kind !== item.kind ||
        current.ref !== item.ref ||
        current.company_id !== item.company_id ||
        current.book_id !== item.book_id
      )
        throw new Error('待办范围已变化，请刷新列表后重新打开。');
      onOpen?.(current);
    } catch (caught) {
      if (stamp === epoch.current) {
        setError(errorMessage(caught));
        setRevision((n) => n + 1);
      }
    } finally {
      opening.current = false;
      if (stamp === epoch.current) setBusy('');
    }
  }
  const selectedTasks = (data?.items ?? []).filter(
    (item) => selected.includes(key(item)) && item.permissions.assign,
  );

  async function assign() {
    if (!assignee || !selectedTasks.length) return;
    setBusy('assign');
    setError('');
    try {
      await api('/firm/tasks/assign', session, {
        method: 'POST',
        body: {
          tasks: selectedTasks.map((item) => ({ kind: item.kind, ref: item.ref })),
          assignee_id: assignee,
          due_on: dueOn || null,
          note,
        },
      });
      setSelected([]);
      setNote('');
      setRevision((value) => value + 1);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  async function claim(item: Item) {
    setBusy(`claim:${key(item)}`);
    try {
      await api('/firm/tasks/claim', session, {
        method: 'POST',
        body: { kind: item.kind, ref: item.ref, note: '从异常收件箱领取' },
      });
      setRevision((value) => value + 1);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  async function remind() {
    if (!selectedTasks.length || message.trim().length < 3) return;
    setBusy('remind');
    try {
      await api('/firm/tasks/remind', session, {
        method: 'POST',
        body: {
          tasks: selectedTasks.map((item) => ({ kind: item.kind, ref: item.ref })),
          message: message.trim(),
        },
      });
      setSelected([]);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  const deliveryRows = followupRows(followups, 8);
  const delivery = followupSummary(deliveryRows);

  return (
    <main className="firm-inbox">
      <header className="firm-inbox-heading">
        <div>
          <p className="eyebrow">事务所待办</p>
          <h1>待处理与人员分工</h1>
          <p>先处理逾期、阻塞和后台失败，再进入正常复核。</p>
        </div>
        <button
          className="button secondary"
          type="button"
          disabled={!!busy}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={15} />
          刷新
        </button>
      </header>
      <div className="firm-inbox-tabs" role="tablist" aria-label="异常收件箱分类">
        {(Object.keys(labels) as Bucket[]).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={bucket === id}
            disabled={!!busy || locked || panelBusy}
            className={bucket === id ? 'active' : ''}
            onClick={() => {
              setBucket(id);
              setOffset(0);
              setSelected([]);
            }}
          >
            {labels[id]}
            {id !== 'all' && <span>{data?.counts[id] ?? '—'}</span>}
          </button>
        ))}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {followup && (
        <DeliveryFollowupPanel
          key={followup.source_fingerprint}
          session={session}
          value={followup}
          onBack={back}
          onChanged={back}
          onOpen={onOpenMonthly}
          onBusyChange={setPanelBusy}
          onDirtyChange={onDirtyChange}
        />
      )}
      {selectedTasks.length > 0 && (
        <section className="firm-inbox-bulk">
          <strong>已选 {selected.length} 项</strong>
          <label>
            指派给
            <select value={assignee} onChange={(event) => setAssignee(event.target.value)}>
              <option value="">请选择接单人</option>
              {data?.assignees.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {item.source === 'client' ? '（客户成员）' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            截止日期
            <input type="date" value={dueOn} onChange={(event) => setDueOn(event.target.value)} />
          </label>
          <label className="firm-inbox-note">
            备注
            <input value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button
            className="button secondary"
            type="button"
            disabled={!!busy || !assignee}
            onClick={() => void assign()}
          >
            <UserRoundCheck size={15} />
            批量分派
          </button>
          <label className="firm-inbox-note">
            催办消息
            <input value={message} onChange={(event) => setMessage(event.target.value)} />
          </label>
          <button
            className="button primary"
            type="button"
            disabled={!!busy || message.trim().length < 3}
            onClick={() => void remind()}
          >
            <Bell size={15} />
            发送催办
          </button>
        </section>
      )}
      <section className="firm-inbox-delivery" aria-labelledby="delivery-followup-title">
        <div className="firm-inbox-delivery-heading">
          <div>
            <h2 id="delivery-followup-title">月度交付待办</h2>
            <p>按「关账 → 生成包 → 配置签收人 → 客户签收」的顺序推进，下一步只由服务端判定。</p>
          </div>
          <span className="firm-inbox-delivery-count" aria-live="polite">
            {feed.loading
              ? '正在读取交付待办…'
              : followupError
                ? '暂时无法读取'
                : delivery.total
                  ? `当前列出 ${delivery.total} 个月份 · 其中 ${delivery.waiting} 个等待客户`
                  : '暂无待处理月份'}
          </span>
        </div>
        {feed.loading ? (
          <p role="status">正在读取交付待办…</p>
        ) : followupError ? (
          <p className="firm-inbox-delivery-error" role="status">
            交付待办读取失败：{followupError}。任务列表不受影响。
          </p>
        ) : deliveryRows.length ? (
          <ul className="firm-inbox-delivery-list">
            {deliveryRows.map((row) => (
              <li key={row.key} className={'firm-inbox-delivery-row ' + row.tone}>
                <strong>{row.bookName}</strong>
                <span className="firm-inbox-delivery-period">{row.period}</span>
                <span className="firm-inbox-delivery-reason">{row.reasonText}</span>
                <span className="firm-inbox-delivery-step">
                  下一步：{row.nextStepText} · {row.ownerText}
                </span>
                <button
                  type="button"
                  className="button secondary"
                  disabled={!!busy || locked || panelBusy}
                  onClick={() => void openDelivery(row.source)}
                >
                  {row.source.action?.label ?? '查看办理说明'}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="firm-inbox-delivery-empty">本页暂无可见交付待办。</p>
        )}
        {followups?.pagination && (
          <nav className="pagination-controls" aria-label="交付待办分页">
            <button
              type="button"
              className="button secondary"
              disabled={feed.loading || locked || !!busy || panelBusy || deliveryPages.length < 2}
              onClick={() => setDeliveryPages((pages) => pages.slice(0, -1))}
            >
              上一页交付待办
            </button>
            <span>第 {deliveryPages.length} 页</span>
            <button
              type="button"
              className="button secondary"
              disabled={
                feed.loading ||
                locked ||
                !!busy ||
                panelBusy ||
                followups.pagination.next_offset === null
              }
              onClick={() => {
                const next = followups.pagination?.next_offset;
                if (next != null) setDeliveryPages((pages) => [...pages, next]);
              }}
            >
              下一页交付待办
            </button>
          </nav>
        )}
      </section>

      <section className="firm-inbox-list" aria-busy={inbox.loading}>
        {inbox.loading ? (
          <div className="loading-block">
            <Spinner />
            正在汇总全所待办…
          </div>
        ) : !inbox.error && data?.items.length ? (
          data.items.map((item) => (
            <article key={key(item)} className={'firm-inbox-row ' + item.bucket}>
              <label className="checkbox-cell">
                <input
                  type="checkbox"
                  disabled={!item.permissions.assign || !!busy}
                  aria-label={`选择 ${item.title}`}
                  checked={selected.includes(key(item))}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, key(item)]
                        : current.filter((value) => value !== key(item)),
                    )
                  }
                />
              </label>
              <div>
                <span className={'firm-inbox-badge ' + item.bucket}>{labels[item.bucket]}</span>
                <h2>{item.title}</h2>
                {onOpen && (
                  <button
                    className="text-button"
                    type="button"
                    disabled={!!busy || locked || panelBusy}
                    onClick={() => void openTask(item)}
                  >
                    {item.action_label || '打开原事项'}
                  </button>
                )}
                <p>
                  {item.company_name} · {item.responsible}
                </p>
              </div>
              <div className="firm-inbox-meta">
                <strong>{item.assignee_name || '未分派'}</strong>
                <span>{item.due_on ? `截止 ${item.due_on}` : '未设置截止日期'}</span>
                {item.permissions.claim && (
                  <button
                    className="text-button"
                    type="button"
                    disabled={!!busy}
                    onClick={() => void claim(item)}
                  >
                    <CheckCircle2 size={14} />
                    由我领取
                  </button>
                )}
              </div>
            </article>
          ))
        ) : (
          <div className="empty-state">
            <CheckCircle2 size={28} />
            <strong>{error ? '待办暂时不可读取，请刷新或联系负责人' : '当前分类没有待办'}</strong>
            <p>{data?.note}</p>
          </div>
        )}
      </section>
      {data && (
        <nav className="cockpit-pages" aria-label="待办分页">
          <span>共 {data.pagination.total} 项</span>
          <button
            className="button secondary"
            disabled={offset === 0 || !!busy}
            onClick={() => {
              setOffset(Math.max(0, offset - 20));
              setSelected([]);
            }}
          >
            上一页
          </button>
          <button
            className="button secondary"
            disabled={!data.pagination.has_more || !!busy}
            onClick={() => {
              setOffset(offset + 20);
              setSelected([]);
            }}
          >
            下一页
          </button>
        </nav>
      )}
    </main>
  );
}
