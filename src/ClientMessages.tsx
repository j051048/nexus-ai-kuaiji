import { useEffect, useState } from 'react';
import { CheckCheck, MessageSquareText, RefreshCw } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
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

export function ClientMessages({ session }: { session: Session }) {
  const [items, setItems] = useState<Message[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<{ messages: Message[]; unread: number }>('/client/messages?limit=50', session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setItems(value.messages);
          setUnread(value.unread);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [session, revision]);

  async function read(message: Message) {
    if (message.read_at) return;
    await api(`/client/messages/${encodeURIComponent(message.id)}/read`, session, {
      method: 'POST',
    });
    setItems((current) =>
      current.map((item) =>
        item.id === message.id ? { ...item, read_at: new Date().toISOString() } : item,
      ),
    );
    setUnread((value) => Math.max(0, value - 1));
  }

  return (
    <main className="client-messages">
      <header>
        <div>
          <p className="eyebrow">MESSAGES / ONE NEXT STEP</p>
          <h1>消息与补件提醒</h1>
          <p>只显示需要你处理的退回原因、催办和办理进度。</p>
        </div>
        <div className="client-message-actions">
          <strong>{unread} 条未读</strong>
          <button
            className="button secondary"
            type="button"
            disabled={loading}
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
                  <small>
                    关联：{message.related_kind} / {message.related_ref}
                  </small>
                )}
              </div>
              {!message.read_at && (
                <button className="text-button" type="button" onClick={() => void read(message)}>
                  <CheckCheck size={14} />
                  标为已读
                </button>
              )}
            </article>
          ))
        ) : (
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
