export type Readiness = 'ready' | 'unreviewed' | 'stale' | 'missing' | 'blocked';

export interface ReadinessKind {
  kind: string;
  label: string;
  readiness: Readiness;
  reason: string;
  next_action: string;
  reviewed: boolean;
  current: boolean;
  workpaper_version: number | null;
  variance_cents?: number;
}

export interface FilingReadiness {
  period: string;
  supported: boolean;
  blocked_reason: string;
  kinds: ReadinessKind[];
  external_result: 'none' | 'registered';
}

const states: Readiness[] = ['ready', 'unreviewed', 'stale', 'missing', 'blocked'];

function text(value: unknown, limit = 500): string {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

function kind(raw: unknown): ReadinessKind | null {
  if (!raw || typeof raw !== 'object') return null;
  const row = raw as Record<string, unknown>;
  const state = text(row.readiness) as Readiness;
  if (!states.includes(state)) return null;
  const label = text(row.label, 40);
  if (!label) return null;
  const version = row.workpaper_version;
  return {
    kind: text(row.kind, 32),
    label,
    readiness: state,
    reason: text(row.reason),
    next_action: text(row.next_action),
    reviewed: row.reviewed === true,
    current: row.current === true,
    workpaper_version: typeof version === 'number' && Number.isInteger(version) ? version : null,
    variance_cents: typeof row.variance_cents === 'number' ? row.variance_cents : undefined,
  };
}

/** Unknown shapes are dropped rather than guessed; the page then shows nothing. */
export function parseReadiness(raw: unknown): FilingReadiness | null {
  if (!raw || typeof raw !== 'object') return null;
  const body = raw as Record<string, unknown>;
  const period = text(body.period, 7);
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(period)) return null;
  const list = Array.isArray(body.kinds) ? body.kinds.map(kind).filter(Boolean) : [];
  return {
    period,
    supported: body.supported === true,
    blocked_reason: text(body.blocked_reason),
    kinds: list as ReadinessKind[],
    external_result: body.external_result === 'registered' ? 'registered' : 'none',
  };
}

export const readinessLabels: Record<Readiness, string> = {
  ready: '可办理申报',
  unreviewed: '待独立复核',
  stale: '来源已变化',
  missing: '待生成底稿',
  blocked: '已阻断',
};

export function readinessTone(state: Readiness): string {
  if (state === 'ready') return 'ok';
  if (state === 'blocked' || state === 'stale') return 'danger';
  return 'warn';
}
