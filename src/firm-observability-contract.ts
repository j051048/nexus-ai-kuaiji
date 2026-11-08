import type { Snapshot } from './FirmObservability';

const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const count = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const timing = (v: unknown) =>
  v === null || (typeof v === 'number' && Number.isFinite(v) && v >= 0);
export function readFirmSnapshot(value: unknown, firm: string | null, period: string): Snapshot {
  if (
    !object(value) ||
    !firm ||
    value.firm_id !== firm ||
    value.period !== period ||
    !object(value.metrics) ||
    !object(value.cost_summary) ||
    !Array.isArray(value.signals) ||
    !object(value.queues) ||
    !object(value.projection) ||
    !object(value.alerts)
  )
    throw new Error('运行记录范围不一致，请重新读取。');
  const m = value.metrics,
    c = value.cost_summary;
  const health = value.worker_health;
  if (
    !['buckets', 'transactions', 'writes', 'rollbacks'].every((k) => count(m[k])) ||
    !['avg_connection_ms', 'avg_lock_ms', 'max_transaction_ms'].every((k) => timing(m[k])) ||
    !['sample_count', 'unknown_count', 'estimated_count'].every((k) => count(c[k])) ||
    !(c.known_micro === null || count(c.known_micro)) ||
    Number(c.unknown_count) + Number(c.estimated_count) > Number(c.sample_count) ||
    (c.sample_count === 0 && c.known_micro !== null) ||
    (health !== undefined &&
      (!object(health) ||
        typeof health.available !== 'boolean' ||
        !count(health.stale_after_seconds) ||
        !(
          health.last_heartbeat_at === null ||
          (typeof health.last_heartbeat_at === 'string' &&
            Number.isFinite(Date.parse(health.last_heartbeat_at)))
        ))) ||
    !Object.values(value.queues).every((v) => object(v) && Object.values(v).every(count)) ||
    !['total', 'dirty', 'never_built', 'failing'].every((k) =>
      count(value.projection && (value.projection as Record<string, unknown>)[k]),
    ) ||
    !Object.values(value.alerts).every((v) => object(v) && count(v.open) && count(v.critical)) ||
    value.signals.some(
      (s) =>
        !object(s) ||
        typeof s.code !== 'string' ||
        s.owner_role !== 'admin' ||
        typeof s.next_action !== 'string' ||
        !['queue', 'operations', 'runs'].includes(String(s.target)) ||
        s.automatic_action !== false,
    )
  )
    throw new Error('运行采样不完整，请管理员核对，不能按零或成功解释。');
  return value as unknown as Snapshot;
}
