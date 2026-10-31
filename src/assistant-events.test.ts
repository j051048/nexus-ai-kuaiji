import { describe, expect, it } from 'vitest';
import { acceptTurnEvents } from './assistant-events';

const scope = { companyId: 'a', conversationId: 'c', turnId: 't' };
const created = {
  seq: 1,
  kind: 'created',
  at: '2026-10-31T00:00:00Z',
  message: '已保存',
  accounting_approved: false,
};
const sent = { ...created, seq: 2, kind: 'send_marked', message: '供应商结果尚未确认' };
const finished = {
  ...created,
  seq: 3,
  kind: 'finished',
  status: 'interrupted',
  outcome_unknown: true,
  automatic_retry: false,
  next_action: 'inspect_execution',
};
const page = (events: unknown[], extra = {}) => ({
  schema: 'assistant-turn-events-v1',
  company_id: 'a',
  conversation_id: 'c',
  turn_id: 't',
  scope_version: 1,
  events,
  cursor: 'cursor',
  terminal: false,
  has_more: false,
  status: 'queued',
  ...extra,
});

describe('durable assistant milestones', () => {
  it('merges stable duplicates, reconnects from the same cursor, preserves unknown', () => {
    const first = acceptTurnEvents(null, page([created]), scope);
    expect(acceptTurnEvents(first, page([]), scope)).toEqual(first);
    const last = acceptTurnEvents(
      first,
      page([created, sent, finished], { terminal: true, status: 'interrupted', cursor: 'final' }),
      scope,
    );
    expect(last.events).toHaveLength(3);
    expect(last.events[2]).toMatchObject({ outcome_unknown: true, automatic_retry: false });
  });
  it('allows cancellation with no send milestone', () => {
    expect(
      acceptTurnEvents(
        null,
        page([created, { ...finished, status: 'cancelled', outcome_unknown: false }], {
          terminal: true,
          status: 'cancelled',
        }),
        scope,
      ).events,
    ).toHaveLength(2);
  });
  it.each(['company_id', 'conversation_id', 'turn_id', 'scope_version', 'schema'])(
    'rejects wrong %s atomically',
    (key) => {
      expect(() => acceptTurnEvents(null, page([created], { [key]: 'wrong' }), scope)).toThrow();
    },
  );
  it.each([
    [sent, created],
    [created, created],
    [{ ...created, seq: true }],
    [{ ...created, accounting_approved: true }],
    [{ ...created, kind: 'raw_token' }],
    [{ ...created, at: 'invalid' }],
    [{ ...created, message: 'x'.repeat(301) }],
    [created, { ...finished, automatic_retry: true }],
  ])('rejects malformed event sequence %#', (...events) => {
    expect(() => acceptTurnEvents(null, page(events), scope)).toThrow();
  });
  it('rejects changed duplicate and terminal rollback without changing previous data', () => {
    const first = acceptTurnEvents(null, page([created]), scope);
    expect(() =>
      acceptTurnEvents(first, page([{ ...created, message: 'changed' }]), scope),
    ).toThrow();
    const last = acceptTurnEvents(
      first,
      page([finished], { terminal: true, status: 'interrupted' }),
      scope,
    );
    expect(() => acceptTurnEvents(last, page([]), scope)).toThrow();
    expect(first.events).toEqual([created]);
  });
});
