export type NotificationSemantic =
  | 'queued'
  | 'sending'
  | 'accepted_unconfirmed'
  | 'failed'
  | 'unknown'
  | 'cancelled'
  | 'viewed'
  | 'signed';

export interface NotificationItem {
  id: string;
  kind: string;
  state: string;
  semantic: NotificationSemantic;
  attempts: number;
  recipient: { user_id: string; name: string | null };
  package: { id: string; version: number };
  channel: string;
  last_error: string;
  created_at: string;
  updated_at: string;
  dispatched_at: string;
  opened_at: string;
  acknowledged_at: string;
}

interface Copy {
  label: string;
  tone: 'ok' | 'warn' | 'danger' | 'muted';
  hint: string;
}

const COPY: Record<NotificationSemantic, Copy> = {
  queued: { label: '待发送', tone: 'muted', hint: '排队中；发送前会重新核对收件人与包版本。' },
  sending: { label: '发送中', tone: 'warn', hint: '已登记发送尝试，尚未等到通道结论。' },
  accepted_unconfirmed: {
    label: '通道已接受，送达未确认',
    tone: 'warn',
    hint: '服务器接受了这封提醒，但这不代表客户已收到；请以客户查看与签收为准。',
  },
  failed: { label: '明确失败', tone: 'danger', hint: '' },
  unknown: {
    label: '结果未知',
    tone: 'danger',
    hint: '可能已经发出。系统不会自动重发；请人工核对是否送达，再决定是否需要新的提醒。',
  },
  cancelled: {
    label: '已取消',
    tone: 'muted',
    hint: '发送前复核未通过（授权、版本或收件人已变化）。',
  },
  viewed: { label: '客户已查看', tone: 'ok', hint: '' },
  signed: { label: '客户已签收', tone: 'ok', hint: '' },
};

const KINDS: Record<string, string> = {
  signature_reminder: '签收提醒',
  delivery_reminder: '交付提醒',
  missing_contact_followup: '缺联系人跟进',
};

export function notificationStateView(item: NotificationItem): Copy {
  const base = COPY[item.semantic] ?? COPY.unknown;
  if (item.semantic === 'failed') {
    const tries = Math.max(0, item.attempts);
    return {
      ...base,
      hint: `已尝试 ${tries} 次，通道确认未发出${item.last_error ? `（${item.last_error}）` : ''}。`,
    };
  }
  return base;
}

export function notificationKindLabel(kind: string): string {
  return KINDS[kind] ?? '提醒';
}

export function notificationTime(item: NotificationItem): string {
  const stamp = item.dispatched_at || item.updated_at || item.created_at;
  return stamp ? stamp.replace('T', ' ').slice(0, 16) : '';
}
