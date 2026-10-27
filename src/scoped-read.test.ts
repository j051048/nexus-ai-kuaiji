import { describe, expect, it } from 'vitest';
import { createReadGuard } from './scoped-read';

describe('scoped read guard', () => {
  it('aborts the previous request as soon as a new read begins', () => {
    const guard = createReadGuard();
    const first = guard.begin();
    expect(first.signal.aborted).toBe(false);
    const second = guard.begin();
    expect(first.signal.aborted).toBe(true);
    expect(second.signal.aborted).toBe(false);
    expect(guard.accepts(second)).toBe(true);
  });

  it('drops a response whose ticket is no longer the current read', () => {
    const guard = createReadGuard();
    const stale = guard.begin();
    const current = guard.begin();
    expect(guard.accepts(stale)).toBe(false);
    expect(guard.accepts(current)).toBe(true);
  });

  it('does not accept a response that arrives after A -> B -> A', () => {
    const guard = createReadGuard();
    const firstA = guard.begin();
    const b = guard.begin();
    const secondA = guard.begin();
    expect(guard.accepts(firstA)).toBe(false);
    expect(guard.accepts(b)).toBe(false);
    expect(guard.accepts(secondA)).toBe(true);
  });

  it('invalidates the current ticket when the scope is released', () => {
    const guard = createReadGuard();
    const ticket = guard.begin();
    guard.cancel();
    expect(ticket.signal.aborted).toBe(true);
    expect(guard.accepts(ticket)).toBe(false);
  });

  it('keeps a late abort from cancelling the read that replaced it', () => {
    const guard = createReadGuard();
    const first = guard.begin();
    const second = guard.begin();
    guard.cancel(first);
    expect(second.signal.aborted).toBe(false);
    expect(guard.accepts(second)).toBe(true);
  });

  it('reports a released guard so callers can stop updating state', () => {
    const guard = createReadGuard();
    const ticket = guard.begin();
    expect(guard.released()).toBe(false);
    guard.cancel(ticket);
    expect(guard.released()).toBe(true);
    expect(guard.begin().signal.aborted).toBe(false);
  });
});
