import type { AssistantEvidence } from './assistant-types';

export interface AssistantDeliveryTarget {
  schema: 'assistant-month-close-v1';
  company_id: string;
  book_id: string;
  period: string;
  inputs_fingerprint: string;
}

export function assistantDeliveryTarget(
  evidence: AssistantEvidence,
): AssistantDeliveryTarget | null {
  const raw = evidence.delivery_target;
  if (evidence.kind !== 'book' || !raw || typeof raw !== 'object' || Array.isArray(raw))
    return null;
  const value = raw as Record<string, unknown>;
  if (
    Object.keys(value).sort().join(',') !== 'book_id,company_id,inputs_fingerprint,period,schema' ||
    value.schema !== 'assistant-month-close-v1' ||
    typeof value.company_id !== 'string' ||
    !value.company_id.trim() ||
    typeof value.book_id !== 'string' ||
    !value.book_id.trim() ||
    value.book_id !== evidence.book_id ||
    evidence.id !== `book:${value.book_id}` ||
    typeof value.period !== 'string' ||
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(value.period) ||
    typeof value.inputs_fingerprint !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.inputs_fingerprint)
  )
    return null;
  return value as unknown as AssistantDeliveryTarget;
}
