export function parseInvitationStatus(raw: unknown, requestId: string) {
  const invalid = () => new Error('邀请状态无法核定，请重新读取开通记录；不会自动发送。');
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw invalid();
  const v = raw as Record<string, unknown>;
  if (
    v.request_id !== requestId ||
    typeof v.email !== 'string' ||
    !v.email ||
    typeof v.enabled !== 'boolean' ||
    typeof v.request_attempt !== 'number' ||
    !Number.isSafeInteger(v.request_attempt) ||
    v.request_attempt < 1 ||
    ![
      'pending',
      'awaiting_activation',
      'activated',
      'completed',
      'failed',
      'cancelled',
      'expired',
    ].includes(String(v.status))
  )
    throw invalid();
  let delivery: { state: string; current_link: boolean } | null = null;
  if (v.delivery !== null) {
    if (!v.delivery || typeof v.delivery !== 'object' || Array.isArray(v.delivery)) throw invalid();
    const d = v.delivery as Record<string, unknown>;
    if (
      !['sending', 'accepted', 'unknown', 'not_sent'].includes(String(d.state)) ||
      typeof d.current_link !== 'boolean'
    )
      throw invalid();
    delivery = { state: String(d.state), current_link: d.current_link };
  }
  return {
    request_id: requestId,
    email: v.email,
    request_attempt: v.request_attempt,
    status: String(v.status),
    enabled: v.enabled,
    delivery,
  };
}
