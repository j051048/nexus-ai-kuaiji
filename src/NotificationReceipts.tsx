import { useEffect, useState } from 'react';
import { api } from './api';
import { useScopedQuery } from './useScopedQuery';
import type { Session } from './types';
import type { NotificationItem } from './delivery-notification-state';

interface Receipt {
  notification_id: string;
  book_id: string;
  company_id: string;
  package_id: string;
  package_version: number;
  manifest_sha256: string;
  attempts: number;
  recipient_user_id: string;
  accepted: boolean;
  delivered: boolean;
  viewed: boolean;
  bounced: boolean;
  signed: boolean;
  customer_opened: boolean;
  receipt_conflict: boolean;
  can_retry: false;
}
export function parseNotificationReceipt(
  value: unknown,
  bookId: string,
  item: NotificationItem,
): Receipt {
  if (!value || typeof value !== 'object') throw new Error('回执格式无效');
  const r = value as Receipt;
  if (
    r.notification_id !== item.id ||
    r.book_id !== bookId ||
    !r.company_id ||
    r.package_id !== item.package.id ||
    r.package_version !== item.package.version ||
    !item.package.manifest_sha256 ||
    r.manifest_sha256 !== item.package.manifest_sha256 ||
    r.attempts !== item.attempts ||
    r.recipient_user_id !== item.recipient.user_id ||
    r.can_retry !== false ||
    ![
      'accepted',
      'delivered',
      'viewed',
      'bounced',
      'signed',
      'customer_opened',
      'receipt_conflict',
    ].every((k) => typeof (r as unknown as Record<string, unknown>)[k] === 'boolean') ||
    r.receipt_conflict !== (r.bounced && (r.delivered || r.viewed))
  )
    throw new Error('回执范围、版本或收件人已变化，请重新读取通知记录。');
  return r;
}
export function NotificationReceipts({
  session,
  bookId,
  item,
}: {
  session: Session;
  bookId: string;
  item: NotificationItem;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? '收起送达证据' : '查看送达证据'}
      </button>
      {open && (
        <ReceiptContent
          key={`${session.scope_key}:${bookId}:${item.id}:${item.attempts}:${item.package.manifest_sha256}`}
          session={session}
          bookId={bookId}
          item={item}
        />
      )}
    </div>
  );
}
function ReceiptContent({
  session,
  bookId,
  item,
}: {
  session: Session;
  bookId: string;
  item: NotificationItem;
}) {
  const [revoked, setRevoked] = useState(false);
  const query = useScopedQuery(
    async (signal) =>
      parseNotificationReceipt(
        await api<unknown>(
          `/books/${encodeURIComponent(bookId)}/delivery/notifications/${encodeURIComponent(item.id)}/receipts`,
          session,
          { signal },
        ),
        bookId,
        item,
      ),
    `${session.scope_key}:${bookId}:${item.id}`,
  );
  useEffect(() => {
    const revoke = (e: Event) => {
      if ((e as CustomEvent<string>).detail === session.scope_key) setRevoked(true);
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);
  if (revoked) return <p role="alert">当前授权已失效，送达证据已清空。</p>;
  const d = query.data;
  return (
    <section aria-label="通知送达证据">
      {query.loading && <p role="status">正在核对该次通知…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {d && (
        <>
          <ul>
            <li>通道接受：{d.accepted ? '有回执' : '未取得回执'}</li>
            <li>通道报告送达：{d.delivered ? '有回执' : '未确认'}</li>
            <li>通道报告查看：{d.viewed ? '有回执（不代表本人登录）' : '未确认'}</li>
            <li>客户登录后打开：{d.customer_opened ? '已记录' : '暂无记录'}</li>
            <li>客户本人签收：{d.signed ? '已签收' : '尚未签收'}</li>
          </ul>
          {d.bounced && (
            <p role="alert">
              {d.receipt_conflict
                ? '送达与退信证据冲突，请核查通道回执及收件人。'
                : '通道报告退信，请核对收件地址和通道记录。'}{' '}
              不会据此自动重发。
            </p>
          )}
          <p>以上分别记录通道回执与客户操作，不能互相替代。</p>
        </>
      )}
      <button className="text-button" disabled={query.loading} onClick={query.reload}>
        重新读取送达证据
      </button>
    </section>
  );
}
