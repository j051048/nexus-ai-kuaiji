/** A local bookmark is never an authorization, fact cache or approval. */
import type { BusinessScope } from './business-types';

type Identity = { user_id: string; firm_id: string | null };
export type TaskFocus = 'originals' | 'differences' | 'actions';
export type TaskMemory = {
  schema: 1;
  userId: string;
  firmId: string;
  companyId: string;
  eventId: string;
  period: string;
  version: number;
  scope: BusinessScope;
  status: string;
  offset: number;
  focus: TaskFocus;
  savedAt: number;
};
export function taskMemoryKey(identity: Identity) {
  return `nexus:business-task:v1:${encodeURIComponent(identity.user_id)}:${encodeURIComponent(identity.firm_id ?? '')}`;
}
export function taskCheckpoint(
  identity: Identity,
  event: {
    id: string;
    company_id: string;
    version: number;
    payload: { occurred_on: string; amount?: string; description?: string };
  },
  filters: { scope: BusinessScope; status: string; offset: number },
  focus: TaskFocus,
  now = Date.now(),
): TaskMemory {
  return {
    schema: 1,
    userId: identity.user_id,
    firmId: identity.firm_id ?? '',
    companyId: event.company_id,
    eventId: event.id,
    version: event.version,
    period: event.payload.occurred_on.slice(0, 7),
    scope: filters.scope,
    status: filters.status,
    offset: filters.offset,
    focus,
    savedAt: now,
  };
}
export function readTaskMemory(
  raw: string | null,
  identity: Identity,
  now = Date.now(),
): TaskMemory | null {
  try {
    if (!raw || raw.length > 4096) return null;
    const value = JSON.parse(raw) as TaskMemory;
    const keys = [
      'schema',
      'userId',
      'firmId',
      'companyId',
      'eventId',
      'period',
      'version',
      'scope',
      'status',
      'offset',
      'focus',
      'savedAt',
    ];
    if (
      !value ||
      Object.keys(value).some((key) => !keys.includes(key)) ||
      value.schema !== 1 ||
      !identity.firm_id ||
      value.userId !== identity.user_id ||
      value.firmId !== identity.firm_id ||
      ![value.companyId, value.eventId].every(
        (id) => typeof id === 'string' && /^[\w-]{1,128}$/.test(id),
      ) ||
      !/^20\d{2}-(0[1-9]|1[0-2])$/.test(value.period) ||
      !Number.isSafeInteger(value.version) ||
      value.version < 1 ||
      !['own', 'department', 'company', 'accounting'].includes(value.scope) ||
      ![
        '',
        'draft',
        'submitted',
        'department_approved',
        'finance_approved',
        'handed_off',
        'returned',
      ].includes(value.status) ||
      !Number.isSafeInteger(value.offset) ||
      value.offset < 0 ||
      value.offset > 100000 ||
      !['originals', 'differences', 'actions'].includes(value.focus) ||
      !Number.isSafeInteger(value.savedAt) ||
      value.savedAt > now ||
      now - value.savedAt > 7 * 86400000
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
