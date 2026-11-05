import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { CheckCheck, MessageSquareText, RefreshCw } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { createReadGuard, type ReadTicket } from './scoped-read';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import type { WorkspaceTask } from './workspace-types';
import { parseMessageTarget, type MessageTarget } from './message-target';
const MonthlyPackagePanel = lazy(() =>
  import('./MonthlyPackagePanel').then((m) => ({ default: m.MonthlyPackagePanel })),
);
import './client-messages.css';

type Message = {
  id: string;
  company_id: string;
  kind: 'reminder' | 'return' | 'system' | 'approval';
  subject: string;
  body: string;
  related_kind: string;
  related_ref: string;
  created_at: string;
  read_at: string | null;
};

type Props = { session: Session; onOpen?: (task: WorkspaceTask) => void };
export function ClientMessages(props: Props) {
  return <MessageList key={props.session.scope_key} {...props} />;
}

function MessageList({ session, onOpen }: Props) {
  const [items, setItems] = useState<Message[]>([]);
  const [unread, setUnread] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [packageTarget, setPackageTarget] = useState<Extract<
    MessageTarget,
    { kind: 'monthly_package' }
  > | null>(null);
  const [packageBusy, setPackageBusy] = useState(false);
  const sending = useRef(false);
  const guard = useRef(createReadGuard());
  const current = useRef<ReadTicket | null>(null);

  useEffect(() => {
    const gate = guard.current;
    const ticket = gate.begin();
    current.current = ticket;
    setLoading(true);
    setError('');
    setItems([]);
    setUnread(null);
    void api<{ messages: Message[]; unread: number }>('/client/messages?limit=50', session, {
      signal: ticket.signal,
    })
      .then((value) => {
        if (!gate.accepts(ticket)) return;
        if (
          !value ||
          !Array.isArray(value.messages) ||
          !Number.isSafeInteger(value.unread) ||
          value.unread < 0 ||
          value.unread > value.messages.length ||
          new Set(value.messages.map((item) => item?.id)).size !== value.messages.length ||
          value.messages.some(
            (item) =>
              !item ||
              !item.id ||
              !item.company_id ||
              typeof item.subject !== 'string' ||
              typeof item.body !== 'string' ||
              typeof item.created_at !== 'string' ||
              !Number.isFinite(Date.parse(item.created_at)) ||
              !(item.read_at === null || typeof item.read_at === 'string'),
          )
        ) {
          throw new Error('消息内容暂时无法核验，请重新读取。');
        }
        setItems(value.messages);
        setUnread(value.unread);
        setUncertain(false);
      })
      .catch((failure) => {
        if (gate.accepts(ticket) || (failure instanceof ApiError && failure.status === 403)) {
          setItems([]);
          setUnread(null);
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (current.current === ticket) setLoading(false);
      });
    return () => {
      gate.cancel(ticket);
      current.current = null;
    };
  }, [session, revision]);

  useEffect(() => {
    function invalidate(event: Event) {
      if ((event as CustomEvent).detail !== session.scope_key) return;
      guard.current.cancel();
      setItems([]);
      setUnread(null);
      setPackageTarget(null);
      setLoading(false);
      setError('访问权限已变化，请重新读取；如仍无权访问，请联系企业管理员。');
    }
    window.addEventListener('accounting:forbidden', invalidate);
    window.addEventListener('accounting:unauthorized', invalidate);
    return () => {
      window.removeEventListener('accounting:forbidden', invalidate);
      window.removeEventListener('accounting:unauthorized', invalidate);
    };
  }, [session.scope_key]);

  async function open(message: Message) {
    const ticket = current.current;
    if (!ticket || !guard.current.accepts(ticket) || sending.current) return;
    sending.current = true;
    setPending(message.id);
    setError('');
    try {
      const response = await api<unknown>(
        `/client/messages/${encodeURIComponent(message.id)}/target`,
        session,
        { signal: ticket.signal },
      );
      if (!guard.current.accepts(ticket)) return;
      const target = parseMessageTarget(response, message);
      if (target.kind === 'monthly_package') setPackageTarget(target);
      else
        onOpen?.({
          key: message.id,
          kind: target.kind,
          ref: message.related_ref,
          company_id: target.company_id,
          company_name: '',
          book_id: target.kind === 'material' ? target.book_id : null,
          event_id: target.kind === 'business' ? target.event_id : null,
          draft_id: null,
          batch_id: null,
          title: message.subject,
          state: 'needs_attention',
          priority: 0,
          due_on: null,
          updated_at: message.created_at,
          bucket: 'action',
          action_label: '打开原事项',
          responsible: '',
          overdue: false,
        });
    } catch (failure) {
      if (guard.current.accepts(ticket)) setError(errorMessage(failure));
    } finally {
      sending.current = false;
      if (guard.current.accepts(ticket)) setPending(null);
    }
  }

  async function read(message: Message) {
    const ticket = current.current;
    if (
      !ticket ||
      !guard.current.accepts(ticket) ||
      message.read_at ||
      sending.current ||
      uncertain
    )
      return;
    sending.current = true;
    setPending(message.id);
    setError('');
    try {
      const result = await api<{ id: string; read: boolean }>(
        `/client/messages/${encodeURIComponent(message.id)}/read`,
        session,
        {
          method: 'POST',
          signal: ticket.signal,
        },
      );
      if (!guard.current.accepts(ticket)) return;
      if (result?.id !== message.id || result.read !== true)
        throw new Error('无法确认标记结果，请刷新核对，勿重复提交。');
      // Read the authoritative receipt and unread count. Never optimistically
      // mark an unknown write as successful or automatically repeat its POST.
      setRevision((value) => value + 1);
    } catch (failure) {
      if (
        guard.current.accepts(ticket) ||
        (failure instanceof ApiError && failure.status === 403)
      ) {
        setError(errorMessage(failure));
        setUncertain(true);
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          setItems([]);
          setUnread(null);
        }
      }
    } finally {
      sending.current = false;
      setPending(null);
    }
  }

  return (
    <main className="client-messages">
      {packageTarget && (
        <section aria-label="消息对应的资料包">
          <button
            className="button secondary"
            disabled={packageBusy}
            onClick={() => setPackageTarget(null)}
          >
            返回消息列表
          </button>
          <Suspense fallback={<p role="status">正在读取资料包…</p>}>
            <MonthlyPackagePanel
              session={session}
              bookId={packageTarget.book_id}
              clientOnly
              focusPackageId={packageTarget.package_id}
              expectedSource={{
                id: packageTarget.package_id,
                version: packageTarget.version,
                fingerprint: packageTarget.manifest_sha256,
              }}
              onBusyChange={setPackageBusy}
            />
          </Suspense>
        </section>
      )}
      <header>
        <div>
          <p className="eyebrow">MESSAGES / ONE NEXT STEP</p>
          <h1>消息与补件提醒</h1>
          <p>只显示需要你处理的退回原因、催办和办理进度。</p>
        </div>
        <div className="client-message-actions">
          <strong>{unread === null ? '' : `当前页 ${unread} 条未读`}</strong>
          <button
            className="button secondary"
            type="button"
            disabled={loading || pending !== null}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <section aria-live="polite">
        {loading && !items.length ? (
          <div className="loading-block">
            <Spinner />
            正在读取消息…
          </div>
        ) : items.length ? (
          items.map((message) => (
            <article key={message.id} className={message.read_at ? '' : 'unread'}>
              <span className="client-message-icon">
                <MessageSquareText size={17} />
              </span>
              <div>
                <header>
                  <strong>{message.subject}</strong>
                  <time>{new Date(message.created_at).toLocaleString('zh-CN')}</time>
                </header>
                <p>{message.body}</p>
                {message.related_kind && (
                  <button
                    className="button secondary small"
                    type="button"
                    disabled={loading || pending !== null || packageBusy}
                    onClick={() => void open(message)}
                  >
                    打开原事项
                  </button>
                )}
              </div>
              {!message.read_at && (
                <button
                  className="text-button"
                  type="button"
                  disabled={pending !== null || uncertain || loading}
                  onClick={() => void read(message)}
                >
                  <CheckCheck size={14} />
                  标为已读
                </button>
              )}
            </article>
          ))
        ) : error ? null : (
          <div className="empty-state">
            <MessageSquareText size={28} />
            <strong>当前没有新消息</strong>
            <p>会计催办、资料退回和审批结果会在这里集中出现。</p>
          </div>
        )}
      </section>
    </main>
  );
}
