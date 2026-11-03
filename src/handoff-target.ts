import type { Handoff } from './batch-operations';

export type HandoffTarget = Handoff['source'];
export function readHandoffTarget(search: string): HandoffTarget | null {
  const query = new URLSearchParams(search);
  const keys = ['kind', 'id', 'version', 'fingerprint'];
  if (!keys.some((key) => query.has(`handoff_${key}`))) return null;
  if (keys.some((key) => query.getAll(`handoff_${key}`).length !== 1))
    throw new Error('接手链接不完整，请返回队列重新打开。');
  const kind = query.get('handoff_kind')!,
    id = query.get('handoff_id')!,
    version = query.get('handoff_version')!,
    fingerprint = query.get('handoff_fingerprint')!;
  if (
    !['batch_item', 'notification_investigation', 'tax_workpaper', 'delivery_package'].includes(
      kind,
    ) ||
    !id ||
    id.length > 200 ||
    /[\u0000-\u001f\u007f]/.test(id) ||
    !/^[1-9]\d*$/.test(version) ||
    !Number.isSafeInteger(Number(version)) ||
    !/^[a-f0-9]{64}$/.test(fingerprint)
  )
    throw new Error('接手链接的对象或版本无效，请返回队列重新打开。');
  return { kind: kind as HandoffTarget['kind'], id, version: Number(version), fingerprint };
}
/** Mirrors the server's explicit source fingerprint, never hashes arbitrary UI text. */
export async function assertHandoffDigest(expected: string, parts: unknown[]): Promise<void> {
  if (
    parts.some(
      (part) =>
        part !== null &&
        typeof part !== 'string' &&
        !(typeof part === 'number' && Number.isSafeInteger(part)),
    )
  )
    throw new Error('来源依据不完整，请返回接手队列核对。');
  const bytes = new TextEncoder().encode(
    parts.map((part) => (part === null ? 'None' : String(part))).join('|'),
  );
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
  if (digest !== expected) throw new Error('来源依据已变化，请返回接手队列核对；不会自动重试。');
}
