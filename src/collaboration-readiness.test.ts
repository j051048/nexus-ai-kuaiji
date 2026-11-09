import { expect, it } from 'vitest';
import { parseInvitationStatus } from './invitation-status';

const sample = () => ({
  request_id: 'r',
  email: 'fixture@example.invalid',
  request_attempt: 1,
  status: 'awaiting_activation',
  enabled: true,
  delivery: { state: 'accepted', current_link: true },
});
it('preserves channel accepted separately from activation', () => {
  expect(parseInvitationStatus(sample(), 'r').delivery?.state).toBe('accepted');
});
it.each([
  { request_attempt: 0 },
  { request_attempt: true },
  { enabled: 'true' },
  { status: 'activated?' },
  { delivery: { state: 'delivered', current_link: true } },
  { delivery: { state: 'not_sent', current_link: 'true' } },
  { request_id: 'other' },
])('rejects malformed invitation state before exposing send %j', (patch) => {
  expect(() => parseInvitationStatus({ ...sample(), ...patch }, 'r')).toThrow();
});
