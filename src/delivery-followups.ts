/** Read model for the firm-level monthly delivery feed.
 *
 * The server owns "what is missing" and "who moves next"; this module only turns
 * those codes into operator language and refuses feeds that do not match the
 * contract, so a malformed response degrades to "unknown" instead of inventing a
 * step that nobody owes. */
export type DeliveryFollowup = {
  company_id?: string;
  source_fingerprint?: string;
  action?: FollowupAction;
  book_id: string;
  book_name: string;
  period: string;
  reasons: string[];
  next_step: string;
  owner: string;
  package_id: string | null;
  recipient_user_id: string | null;
};
export type FollowupAction = {
  kind: string;
  owner: string;
  label: string;
  can_act: boolean;
  reason: string;
};
export type DeliveryFollowupFeed = {
  items: DeliveryFollowup[];
  count: number;
  pagination?: { offset: number; limit: number; next_offset: number | null };
  scope?: { firm_id: string | null; user_id: string };
};
export type FollowupState = {
  company_name: string;
  book_name: string;
  scope: { firm_id: string | null; user_id: string };
  company_id: string;
  book_id: string;
  period: string;
  source_fingerprint: string;
  action: FollowupAction;
  event: {
    package_id: string | null;
    package_version: number | null;
    manifest_sha256: string | null;
  };
  recipient: { user_id: string | null; state: string };
  recipient_candidates: { user_id: string; name: string }[];
};
export type FollowupRow = {
  source: DeliveryFollowup;
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

const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const hash = (value: unknown): value is string =>
  typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const id = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 200;
export function validFollowupAction(value: unknown): value is FollowupAction {
  return (
    object(value) &&
    Object.prototype.hasOwnProperty.call(stepText, String(value.kind)) &&
    Object.prototype.hasOwnProperty.call(ownerText, String(value.owner)) &&
    id(value.label) &&
    typeof value.can_act === 'boolean' &&
    typeof value.reason === 'string'
  );
}
export function validateFollowupFeed(
  value: unknown,
  scope?: { firm_id: string | null; user_id: string },
  page?: { offset: number; limit: number },
): value is DeliveryFollowupFeed {
  if (!value || typeof value !== 'object') return false;
  const feed = value as Partial<DeliveryFollowupFeed>;
  if (!Array.isArray(feed.items)) return false;
  const pagination = feed.pagination;
  if (pagination) {
    if (
      !object(pagination) ||
      !Number.isSafeInteger(pagination.offset) ||
      pagination.offset < 0 ||
      pagination.offset > 1_000_000 ||
      !Number.isSafeInteger(pagination.limit) ||
      pagination.limit < 1 ||
      pagination.limit > 200 ||
      feed.items.length > pagination.limit ||
      (page && (pagination.offset !== page.offset || pagination.limit !== page.limit)) ||
      (pagination.next_offset !== null &&
        (!Number.isSafeInteger(pagination.next_offset) ||
          pagination.next_offset <= pagination.offset ||
          pagination.next_offset > Math.min(1_000_000, pagination.offset + pagination.limit * 4)))
    )
      return false;
  } else if (page && (page.offset !== 0 || feed.items.length > page.limit)) return false;
  if (
    !Number.isSafeInteger(feed.count) ||
    feed.count !== feed.items.length ||
    new Set(feed.items.map((item) => `${item?.book_id}:${item?.period}`)).size !== feed.items.length
  )
    return false;
  if (scope && (feed.scope?.firm_id !== scope.firm_id || feed.scope?.user_id !== scope.user_id))
    return false;
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
      row.reasons.every((reason) => typeof reason === 'string') &&
      (!scope ||
        (id(row.company_id) &&
          hash(row.source_fingerprint) &&
          validFollowupAction(row.action) &&
          row.action.kind === row.next_step &&
          row.action.owner === row.owner)) &&
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
      source: item,
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

/** Accept the entire new projection at once. Never silently switch company or package version. */
export function acceptFollowup(
  value: unknown,
  source: DeliveryFollowup,
  scope: { firm_id: string | null; user_id: string },
): FollowupState | null {
  if (
    !object(value) ||
    typeof value.company_name !== 'string' ||
    typeof value.book_name !== 'string' ||
    !object(value.scope) ||
    value.scope.firm_id !== scope.firm_id ||
    value.scope.user_id !== scope.user_id ||
    value.company_id !== source.company_id ||
    value.book_id !== source.book_id ||
    value.period !== source.period ||
    !hash(value.source_fingerprint) ||
    value.source_fingerprint !== source.source_fingerprint ||
    !validFollowupAction(value.action) ||
    value.action.kind !== source.next_step ||
    !object(value.event) ||
    !object(value.recipient) ||
    !Array.isArray(value.recipient_candidates)
  )
    return null;
  const event = value.event;
  if (
    event.package_id !== source.package_id ||
    (event.package_id !== null &&
      (!id(event.package_id) ||
        !Number.isSafeInteger(event.package_version) ||
        Number(event.package_version) < 1 ||
        !hash(event.manifest_sha256)))
  )
    return null;
  if (
    !value.recipient_candidates.every(
      (c) => object(c) && id(c.user_id) && typeof c.name === 'string',
    ) ||
    new Set(value.recipient_candidates.map((c) => c.user_id)).size !==
      value.recipient_candidates.length
  )
    return null;
  return value as unknown as FollowupState;
}

export function followupSummary(rows: FollowupRow[]) {
  const waiting = rows.filter((row) => row.tone === 'waiting').length;
  return { total: rows.length, waiting, needsFirm: rows.length - waiting };
}
