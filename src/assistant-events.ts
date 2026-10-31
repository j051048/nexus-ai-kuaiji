/** This feed contains persisted milestones, never unverified model tokens. */
export interface TurnEvent {
  seq: number;
  kind: 'created' | 'send_marked' | 'finished';
  at: string | null;
  message: string;
  accounting_approved: false;
  status?: string;
  outcome_unknown?: boolean;
  automatic_retry?: false;
  next_action?: string;
}
export interface TurnEventFeed {
  events: TurnEvent[];
  cursor: string;
  terminal: boolean;
  has_more: boolean;
  status: string;
}
export interface TurnEventScope {
  companyId: string;
  conversationId: string;
  turnId: string;
}
const terminal = ['succeeded', 'failed', 'interrupted', 'stale', 'cancelled'];
const actions = ['inspect_execution', 'refresh_scope', 'contact_admin', 'review_facts'];
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('执行记录格式已变化，请重新读取。');
  return value as Record<string, unknown>;
}
function reject(): never {
  throw new Error('执行记录范围或顺序已变化，请重新读取。');
}
export function acceptTurnEvents(
  previous: TurnEventFeed | null,
  input: unknown,
  scope: TurnEventScope,
): TurnEventFeed {
  const value = object(input);
  if (
    value.schema !== 'assistant-turn-events-v1' ||
    value.company_id !== scope.companyId ||
    value.conversation_id !== scope.conversationId ||
    value.turn_id !== scope.turnId ||
    value.scope_version !== 1 ||
    typeof value.cursor !== 'string' ||
    !value.cursor.length ||
    value.cursor.length > 2048 ||
    typeof value.terminal !== 'boolean' ||
    typeof value.has_more !== 'boolean' ||
    typeof value.status !== 'string' ||
    ![...terminal, 'queued', 'running'].includes(value.status) ||
    value.terminal !== terminal.includes(value.status) ||
    (previous?.terminal && (!value.terminal || previous.status !== value.status)) ||
    !Array.isArray(value.events) ||
    value.events.length > 3
  )
    reject();
  let seq = 0;
  const incoming = value.events.map((raw) => {
    const e = object(raw);
    if (
      typeof e.seq !== 'number' ||
      !Number.isInteger(e.seq) ||
      e.seq <= seq ||
      e.seq > 3 ||
      e.kind !== ['created', 'send_marked', 'finished'][e.seq - 1] ||
      e.accounting_approved !== false ||
      typeof e.message !== 'string' ||
      !e.message ||
      e.message.length > 300 ||
      (!(e.at === null && e.kind === 'finished') &&
        !(typeof e.at === 'string' && e.at.length <= 50 && Number.isFinite(Date.parse(e.at))))
    )
      reject();
    seq = e.seq;
    if (
      e.kind === 'finished' &&
      (!value.terminal ||
        e.status !== value.status ||
        typeof e.outcome_unknown !== 'boolean' ||
        e.automatic_retry !== false ||
        !actions.includes(String(e.next_action)))
    )
      reject();
    const event: TurnEvent = {
      seq: e.seq,
      kind: e.kind as TurnEvent['kind'],
      at: e.at as string | null,
      message: e.message,
      accounting_approved: false,
    };
    if (event.kind === 'finished')
      Object.assign(event, {
        status: e.status,
        outcome_unknown: e.outcome_unknown,
        automatic_retry: false,
        next_action: e.next_action,
      });
    return event;
  });
  const events = [...(previous?.events ?? [])];
  for (const e of incoming) {
    const duplicate = events.find((prior) => prior.seq === e.seq);
    if (duplicate) {
      if (JSON.stringify(duplicate) !== JSON.stringify(e)) reject();
    } else {
      if (events.length && e.seq < events[events.length - 1].seq) reject();
      events.push(e);
    }
  }
  if (events[0]?.seq !== 1 || (!value.has_more && value.terminal && events.at(-1)?.seq !== 3))
    reject();
  return {
    events,
    cursor: value.cursor,
    terminal: value.terminal,
    has_more: value.has_more,
    status: value.status,
  };
}
