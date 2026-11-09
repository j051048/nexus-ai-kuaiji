import type { BusinessEvent, BusinessScope } from './business-types';

/** A response is usable only for the requested record and authenticated scope. */
export function assertBusinessSelection(
  event: BusinessEvent,
  target: {
    id: string;
    firmId: string | null;
    companyId?: string;
    period?: string;
    version?: number;
  },
): BusinessEvent {
  if (
    !event ||
    event.id !== target.id ||
    !target.firmId ||
    event.firm_id !== target.firmId ||
    (target.companyId && event.company_id !== target.companyId) ||
    (target.period && event.payload?.occurred_on?.slice(0, 7) !== target.period) ||
    !Number.isSafeInteger(event.version) ||
    event.version < (target.version ?? 1)
  )
    throw new Error('事项范围或版本不一致，请重新读取；未显示这份响应。');
  return event;
}

/** Continue within the fresh authorized page, never infer permission from a role label. */
export function nextBusinessItem(
  events: BusinessEvent[],
  current: BusinessEvent,
  scope: BusinessScope,
) {
  return events.find(
    (row) =>
      row.id !== current.id &&
      row.company_id === current.company_id &&
      (scope === 'company'
        ? row.status === 'department_approved' && row.permissions.finance_approve
        : scope === 'department'
          ? row.status === 'submitted' && row.permissions.department_approve
          : scope === 'accounting' && row.permissions.handoff),
  );
}
