import { useEffect, useState } from 'react';
import { Bell, CheckCircle2, RefreshCw, UserRoundCheck } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-inbox.css';

type Bucket = 'all' | 'today' | 'overdue' | 'blocked' | 'review' | 'failed';
type Item = {
  kind: string;
  ref: string;
  company_id: string;
  company_name: string;
  book_id: string | null;
  title: string;
  state: string;
  due_on: string;
  assignee_id: string | null;
  assignee_name: string;
  assignment_version: number;
  assignment_note: string;
  bucket: Exclude<Bucket, 'all'>;
  updated_at: string;
};
type Inbox = {
  counts: Record<Exclude<Bucket, 'all'>, number>;
  items: Item[];
  assignees: { id: string; name: string; source: 'firm' | 'client' }[];
  note: string;
};

const labels: Record<Bucket, string> = {
  all: '全部',
  today: '今天',
  overdue: '逾期',
  blocked: '阻塞',
  review: '待复核',
  failed: '后台失败',
};

export function FirmInbox({ session }: { session: Session }) {
  const [bucket, setBucket] = useState<Bucket>('all');
  const [data, setData] = useState<Inbox | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [assignee, setAssignee] = useState('');
  const [dueOn, setDueOn] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('请在今天完成当前待办，如缺少资料请直接反馈。');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setBusy('load');
    setError('');
    void api<Inbox>(`/firm/inbox?bucket=${bucket}`, session, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setAssignee((current) => current || value.assignees[0]?.id || '');
          setSelected([]);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy('');
      });
    return () => controller.abort();
  }, [session, bucket, revision]);

  const key = (item: Item) => `${item.kind}:${item.ref}`;
  const selectedTasks = (data?.items ?? []).filter((item) => selected.includes(key(item)));

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

  return (
    <main className="firm-inbox">
      <header className="firm-inbox-heading">
        <div>
          <p className="eyebrow">EXCEPTION INBOX / SLA</p>
          <h1>异常收件箱与任务调度</h1>
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
            className={bucket === id ? 'active' : ''}
            onClick={() => setBucket(id)}
          >
            {labels[id]}
            {id !== 'all' && <span>{data?.counts[id] ?? 0}</span>}
          </button>
        ))}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {selected.length > 0 && (
        <section className="firm-inbox-bulk">
          <strong>已选 {selected.length} 项</strong>
          <label>
            指派给
            <select value={assignee} onChange={(event) => setAssignee(event.target.value)}>
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
      <section className="firm-inbox-list" aria-busy={busy === 'load'}>
        {busy === 'load' && !data ? (
          <div className="loading-block">
            <Spinner />
            正在汇总全所待办…
          </div>
        ) : data?.items.length ? (
          data.items.map((item) => (
            <article key={key(item)} className={'firm-inbox-row ' + item.bucket}>
              <label className="checkbox-cell">
                <input
                  type="checkbox"
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
                <p>
                  {item.company_name} · {item.kind} · {item.state}
                </p>
              </div>
              <div className="firm-inbox-meta">
                <strong>{item.assignee_name || '未分派'}</strong>
                <span>{item.due_on ? `截止 ${item.due_on}` : '未设置截止日期'}</span>
                {!item.assignee_id && (
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
            <strong>当前分类没有待办</strong>
            <p>{data?.note}</p>
          </div>
        )}
      </section>
    </main>
  );
}
