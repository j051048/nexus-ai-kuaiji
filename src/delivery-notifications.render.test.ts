import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DeliveryNotificationList } from './DeliveryNotifications';
import {
  notificationKindLabel,
  notificationStateView,
  type NotificationItem,
} from './delivery-notification-state';

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: 'n1',
    kind: 'signature_reminder',
    state: 'sink_written',
    semantic: 'accepted_unconfirmed',
    attempts: 1,
    recipient: { user_id: 'u1', name: '客户财务' },
    package: { id: 'p1', version: 3 },
    channel: 'smtp',
    last_error: '',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:01:00Z',
    dispatched_at: '2026-11-01T10:01:00Z',
    opened_at: '',
    acknowledged_at: '',
    ...overrides,
  };
}

describe('notification states', () => {
  it('never claims receipt for a server acceptance', () => {
    const view = notificationStateView(item());
    expect(view.label).toBe('通道已接受，送达未确认');
    expect(view.hint).toContain('不代表客户已收到');
  });

  it('keeps unknown ambiguous and refuses automatic resend wording', () => {
    const view = notificationStateView(item({ semantic: 'unknown', state: 'unknown' }));
    expect(view.label).toBe('结果未知');
    expect(view.hint).toContain('不会自动重发');
  });

  it('ties explicit failures to bounded attempts and the channel error', () => {
    const view = notificationStateView(
      item({ semantic: 'failed', state: 'failed', attempts: 2, last_error: 'DeliveryNotSent' }),
    );
    expect(view.hint).toContain('已尝试 2 次');
    expect(view.hint).toContain('DeliveryNotSent');
  });

  it('promotes customer actions above transport states', () => {
    expect(notificationStateView(item({ semantic: 'viewed' })).label).toBe('客户已查看');
    expect(notificationStateView(item({ semantic: 'signed' })).label).toBe('客户已签收');
    expect(notificationStateView(item({ semantic: 'queued' })).label).toBe('待发送');
    expect(notificationStateView(item({ semantic: 'cancelled' })).label).toBe('已取消');
  });
});

describe('DeliveryNotificationList', () => {
  it('renders the hint for an unknown attempt and no delivered wording', () => {
    const html = renderToStaticMarkup(
      createElement(DeliveryNotificationList, {
        items: [item({ semantic: 'unknown', state: 'unknown' })],
      }),
    );
    expect(html).toContain('结果未知');
    expect(html).toContain('不会自动重发');
    expect(html).toContain('签收提醒');
    expect(html).toContain('第 3 版');
    expect(html).not.toContain('已送达');
  });

  it('explains an empty month instead of showing a blank panel', () => {
    const html = renderToStaticMarkup(createElement(DeliveryNotificationList, { items: [] }));
    expect(html).toContain('本月还没有外发提醒记录');
  });
});

describe('notification labels', () => {
  it('falls back to a generic reminder kind', () => {
    expect(notificationKindLabel('signature_reminder')).toBe('签收提醒');
    expect(notificationKindLabel('something_new')).toBe('提醒');
  });
});
