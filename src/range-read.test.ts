import { describe, expect, it } from 'vitest';

import {
  canExport,
  emptyRangeRead,
  rangeKey,
  reduceRead,
  REVOKED_TEXT,
  type RangeReadState,
} from './range-read';

const A = rangeKey('ledger_1', '2026-10', '2026-11');
const B = rangeKey('ledger_1', '2026-10', '2026-12');

function ready(state: RangeReadState, key: string, data: unknown): RangeReadState {
  return reduceRead(state, { type: 'ok', key, ticket: state.ticket, data });
}

function loaded(key: string, data: unknown): RangeReadState {
  return ready(reduceRead(emptyRangeRead, { type: 'switch', key, ticket: 1 }), key, data);
}

describe('range read lifecycle', () => {
  it('clears the previous balance the moment the range changes', () => {
    const shown = loaded(A, { start: '2026-10', end: '2026-11', rows: [] });
    expect(canExport(shown, A)).toBe(true);

    const switched = reduceRead(shown, { type: 'switch', key: B, ticket: 2 });

    expect(switched.data).toBeNull();
    expect(switched.status).toBe('loading');
    expect(switched.error).toBe('');
    expect(canExport(switched, B)).toBe(false);
    expect(canExport(switched, A)).toBe(false);
  });

  it('drops a late answer for the range that is no longer on screen', () => {
    const switched = reduceRead(loaded(A, { rows: [] }), { type: 'switch', key: B, ticket: 2 });

    const stale = reduceRead(switched, {
      type: 'ok',
      key: A,
      ticket: 1,
      data: { rows: ['stale'] },
    });

    expect(stale).toBe(switched);
    expect(stale.data).toBeNull();
  });

  it('does not let an old A answer win when the user went A → B → A', () => {
    let state = loaded(A, { rows: ['first'] });
    state = reduceRead(state, { type: 'switch', key: B, ticket: 2 });
    state = reduceRead(state, { type: 'switch', key: A, ticket: 3 });

    const late = reduceRead(state, { type: 'ok', key: A, ticket: 1, data: { rows: ['first'] } });
    expect(late.data).toBeNull();

    const current = reduceRead(late, {
      type: 'ok',
      key: A,
      ticket: state.ticket,
      data: { rows: ['second'] },
    });
    expect(current.status).toBe('ready');
    expect(current.data).toEqual({ rows: ['second'] });
  });

  it('treats a lost grant as a reason to wipe the numbers, not to keep showing them', () => {
    const state = loaded(A, { rows: [{ account: '1002', closing_cents: 100 }] });

    const revoked = reduceRead(state, {
      type: 'fail',
      key: A,
      ticket: state.ticket,
      status: 403,
      message: '无权访问',
    });

    expect(revoked.status).toBe('failed');
    expect(revoked.data).toBeNull();
    expect(revoked.error).toBe(REVOKED_TEXT);
    expect(canExport(revoked, A)).toBe(false);
  });

  it('keeps an ordinary failure message but still refuses to export stale numbers', () => {
    const state = loaded(A, { rows: [{ account: '1002', closing_cents: 100 }] });

    const failed = reduceRead(state, {
      type: 'fail',
      key: A,
      ticket: state.ticket,
      status: 500,
      message: '服务暂时不可用，请稍后重试。',
    });

    expect(failed.error).toBe('服务暂时不可用，请稍后重试。');
    expect(failed.data).toBeNull();
  });

  it('drops the drilled ledger when the range changes, so old entries cannot sit under new dates', () => {
    const detail = loaded(A, { entries: [{ voucher_id: 'v1' }] });
    const reset = reduceRead(detail, { type: 'reset' });

    expect(reset.status).toBe('idle');
    expect(reset.data).toBeNull();
    expect(canExport(reset, A)).toBe(false);
  });

  it('revokes loudly: late answers are dropped and the panel cannot export', () => {
    const state = loaded(A, { rows: [{ account: '1002' }] });

    const revoked = reduceRead(state, { type: 'revoke' });
    const late = reduceRead(revoked, {
      type: 'ok',
      key: A,
      ticket: state.ticket,
      data: { rows: [] },
    });

    expect(late).toBe(revoked);
    expect(late.data).toBeNull();
    expect(canExport(late, A)).toBe(false);
    expect(late.error).toContain('权限已变更');
  });
});
