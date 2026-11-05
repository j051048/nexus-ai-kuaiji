export type MessageTarget = { message_id: string; company_id: string; period: string } & (
  | { kind: 'business'; event_id: string }
  | { kind: 'material'; material_id: string; book_id: string }
  | {
      kind: 'monthly_package';
      package_id: string;
      book_id: string;
      version: number;
      manifest_sha256: string;
    }
);
export function parseMessageTarget(
  value: unknown,
  message: { id: string; company_id: string; related_ref: string },
): MessageTarget {
  const row = value as Record<string, unknown> | null;
  const invalid = () => new Error('提醒与原事项范围不一致，请重新读取消息。');
  if (
    !row ||
    row.message_id !== message.id ||
    row.company_id !== message.company_id ||
    typeof row.period !== 'string' ||
    !/^20\d{2}-(0[1-9]|1[0-2])$/.test(row.period)
  )
    throw invalid();
  if (
    row.kind === 'business' &&
    typeof row.event_id === 'string' &&
    row.event_id === message.related_ref
  )
    return row as MessageTarget;
  if (typeof row.book_id !== 'string' || !row.book_id) throw invalid();
  if (row.kind === 'material' && row.material_id === message.related_ref)
    return row as MessageTarget;
  if (
    row.kind === 'monthly_package' &&
    row.package_id === message.related_ref &&
    typeof row.version === 'number' &&
    Number.isSafeInteger(row.version) &&
    row.version > 0 &&
    typeof row.manifest_sha256 === 'string' &&
    /^[a-f0-9]{64}$/.test(row.manifest_sha256)
  )
    return row as MessageTarget;
  throw invalid();
}
