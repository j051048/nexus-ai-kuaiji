import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Send } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import {
  notificationKindLabel,
  notificationStateView,
  notificationTime,
  type NotificationItem,
} from './delivery-notification-state';

interface Props {
  session: Session;
  bookId: string;
  period?: string;
}

export function DeliveryNotificationList({ items }: { items: NotificationItem[] }) {
  if (!items.length) return <p className="delivery-notification-empty">本月还没有外发提醒记录。</p>;
  return (
    <ul className="delivery-notification-list">
      {items.map((item) => {
        const view = notificationStateView(item);
        return (
          <li key={item.id} data-tone={view.tone}>
            <div className="delivery-notification-head">
              <strong>{notificationKindLabel(item.kind)}</strong>
              <span className="delivery-notification-badge">{view.label}</span>
            </div>
            <p className="delivery-notification-meta">
              收件人 {item.recipient.name || '未命名'} · 冻结包第 {item.package.version} 版 ·{' '}
              {notificationTime(item)}
            </p>
            {view.hint && <p className="delivery-notification-hint">{view.hint}</p>}
          </li>
        );
      })}
    </ul>
  );
}

export function DeliveryNotifications({ session, bookId, period }: Props) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const epoch = useRef(0);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      const current = ++epoch.current;
      try {
        const value = await api<{ items: NotificationItem[] }>(
          `/books/${encodeURIComponent(bookId)}/delivery/notifications?period=${encodeURIComponent(period ?? '')}`,
          session,
          { signal },
        );
        if (current === epoch.current && !signal?.aborted) {
          setItems(value.items ?? []);
          setError('');
        }
      } catch (caught) {
        if (current === epoch.current && !signal?.aborted) setError(errorMessage(caught));
      } finally {
        if (current === epoch.current && !signal?.aborted) setLoading(false);
      }
    },
    [bookId, period, session],
  );

  useEffect(() => {
    if (!period) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, period]);

  if (!period) return null;

  return (
    <section className="delivery-notifications" aria-label="提醒发送状态">
      <header>
        <div>
          <h3>
            <Send size={18} /> 提醒发送状态
          </h3>
          <p>服务器接受不等于客户收到；结果未知的提醒不会自动重发。</p>
        </div>
        <button
          className="button secondary"
          onClick={() => {
            setLoading(true);
            void load();
          }}
          disabled={loading}
        >
          <RefreshCw size={16} /> 刷新
        </button>
      </header>
      {error ? (
        <p role="alert">{error}</p>
      ) : loading ? (
        <p role="status">正在读取发送记录…</p>
      ) : (
        <DeliveryNotificationList items={items} />
      )}
    </section>
  );
}
