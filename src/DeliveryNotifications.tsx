import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Send } from 'lucide-react';
import { NotificationInvestigation } from './NotificationInvestigation';
import './dcp-evidence.css';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { assertHandoffDigest } from './handoff-target';
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
  focusNotification?: { id: string; attempts: number; fingerprint: string };
}

export function DeliveryNotificationList({
  items,
  session,
  bookId,
  onSaved,
}: {
  items: NotificationItem[];
  session?: Session;
  bookId?: string;
  onSaved?: () => void;
}) {
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
            {session && bookId && onSaved && (
              <NotificationInvestigation
                key={item.id + ':' + (item.investigation?.revision ?? 0)}
                item={item}
                session={session}
                bookId={bookId}
                onSaved={onSaved}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function DeliveryNotifications(props: Props) {
  return (
    <NotificationScope
      key={[props.session.scope_key, props.bookId, props.period].join(':')}
      {...props}
    />
  );
}
function NotificationScope({ session, bookId, period, focusNotification }: Props) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const revoked = useRef(false);
  useEffect(() => {
    const revoke = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      revoked.current = true;
      epoch.current++;
      setItems([]);
      setError('当前授权已失效，请重新确认权限。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      epoch.current++;
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (revoked.current) return;
      setItems([]);
      const current = ++epoch.current;
      try {
        const value = await api<{ book_id: string; period: string; items: NotificationItem[] }>(
          `/books/${encodeURIComponent(bookId)}/delivery/notifications?period=${encodeURIComponent(period ?? '')}`,
          session,
          { signal },
        );
        if (current === epoch.current && !signal?.aborted) {
          if (value.book_id !== bookId || value.period !== period || !Array.isArray(value.items))
            throw new Error('通知范围变化，请刷新后核对');
          const selected = focusNotification
            ? value.items.filter((item) => item.id === focusNotification.id)
            : value.items;
          if (
            focusNotification &&
            (selected.length !== 1 ||
              selected[0].attempts !== focusNotification.attempts ||
              selected[0].state !== 'unknown')
          )
            throw new Error('通知原记录或发送次数已变化，请返回接手队列核对。');
          if (focusNotification) {
            const item = selected[0];
            await assertHandoffDigest(focusNotification.fingerprint, [
              'notification_investigation',
              item.id,
              item.package.id,
              item.package.version,
              item.package.manifest_sha256,
              item.attempts,
              item.updated_at,
            ]);
          }
          if (current !== epoch.current || signal?.aborted || revoked.current) return;
          setItems(selected);
          setError('');
        }
      } catch (caught) {
        if (current === epoch.current && !signal?.aborted) setError(errorMessage(caught));
      } finally {
        if (current === epoch.current && !signal?.aborted) setLoading(false);
      }
    },
    [bookId, period, session, focusNotification],
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
        <DeliveryNotificationList
          items={items}
          session={session}
          bookId={bookId}
          onSaved={() => {
            void load();
          }}
        />
      )}
    </section>
  );
}
