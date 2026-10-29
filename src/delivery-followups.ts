/** Read model for the firm-level monthly delivery feed.
 *
 * The server owns "what is missing" and "who moves next"; this module only turns
 * those codes into operator language and refuses feeds that do not match the
 * contract, so a malformed response degrades to "unknown" instead of inventing a
 * step that nobody owes. */
export type DeliveryFollowup = {
  book_id: string;
  book_name: string;
  period: string;
  reasons: string[];
  next_step: string;
  owner: string;
  package_id: string | null;
  recipient_user_id: string | null;
};
export type DeliveryFollowupFeed = { items: DeliveryFollowup[]; count: number };
export type FollowupRow = {
  key: string;
  bookName: string;
  period: string;
  reasonText: string;
  nextStepText: string;
  ownerText: string;
  tone: 'waiting' | 'action';
};

const reasonText: Record<string, string> = {
  notification_unknown: '通知发送结果未知',
  period_not_closed: '期间未关账',
  package_missing: '未生成月度交付包',
  signature_missing: '客户未签收',
  recipient_missing: '未配置签收人',
  recipient_invalid: '签收人已失效',
};
const stepText: Record<string, string> = {
  reconcile_notification: '人工核对发送结果，勿重复发送',
  close_period: '先完成关账',
  publish_package: '生成并交付月度包',
  configure_recipient: '配置签收人',
  fix_recipient: '更换签收人',
  await_customer_signature: '等待客户签收',
  review: '人工复核',
};
const ownerText: Record<string, string> = {
  accountant: '会计',
  firm_manager: '事务所管理员',
  customer: '客户',
};

export function validateFollowupFeed(value: unknown): value is DeliveryFollowupFeed {
  if (!value || typeof value !== 'object') return false;
  const feed = value as Partial<DeliveryFollowupFeed>;
  if (!Array.isArray(feed.items)) return false;
  return feed.items.every((item) => {
    if (!item || typeof item !== 'object') return false;
    const row = item as Partial<DeliveryFollowup>;
    return (
      typeof row.book_id === 'string' &&
      row.book_id.length > 0 &&
      typeof row.book_name === 'string' &&
      typeof row.period === 'string' &&
      /^20\d{2}-(0[1-9]|1[0-2])$/.test(row.period) &&
      Array.isArray(row.reasons) &&
      typeof row.next_step === 'string' &&
      typeof row.owner === 'string'
    );
  });
}

export function followupRows(feed: DeliveryFollowupFeed | null, limit = 6): FollowupRow[] {
  if (!feed) return [];
  return feed.items.slice(0, Math.max(1, limit)).map((item) => {
    const reasons = item.reasons.map((reason) => reasonText[reason] ?? reason);
    return {
      key: `${item.book_id}:${item.period}`,
      bookName: item.book_name || item.book_id,
      period: item.period,
      reasonText: reasons.length ? reasons.join(' · ') : '待处理',
      nextStepText: stepText[item.next_step] ?? item.next_step,
      ownerText: ownerText[item.owner] ?? item.owner,
      tone: item.owner === 'customer' ? 'waiting' : 'action',
    };
  });
}

export function followupSummary(rows: FollowupRow[]) {
  const waiting = rows.filter((row) => row.tone === 'waiting').length;
  return { total: rows.length, waiting, needsFirm: rows.length - waiting };
}
