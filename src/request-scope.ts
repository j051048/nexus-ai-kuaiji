/**
 * Context-switch epoch (Batch 02 · 1.3): switching company/period starts a new
 * epoch and aborts every request registered under the old one.
 *
 * Pages write their result only when their epoch is still current. That is the
 * second line of defence behind `AbortController`: an aborted fetch rejects, but
 * a response already on the wire can still resolve, and the epoch check makes
 * sure a late answer for the previous company/period never paints the new page.
 */

type Registration = { epoch: number; controller: AbortController };

let epoch = 0;
const live = new Set<Registration>();

export function currentEpoch(): number {
  return epoch;
}

/** Begin a new context epoch: abort everything registered before it. */
export function beginScope(): number {
  epoch += 1;
  for (const item of live) item.controller.abort();
  live.clear();
  return epoch;
}

/** Register a controller for the current epoch (aborted on the next switch). */
export function scopedController(): {
  epoch: number;
  signal: AbortSignal;
  abort: () => void;
  release: () => void;
} {
  const controller = new AbortController();
  const registration: Registration = { epoch, controller };
  live.add(registration);
  const release = () => {
    live.delete(registration);
  };
  const signal = controller.signal;
  signal.addEventListener('abort', release, { once: true });
  return { epoch, signal, abort: () => controller.abort(), release };
}

/** True when a response captured at `captured` still belongs to the live context. */
export function isCurrentScope(captured: number): boolean {
  return captured === epoch;
}

export function resetScope(): void {
  for (const item of live) item.controller.abort();
  live.clear();
  epoch = 0;
}
