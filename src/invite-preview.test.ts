import { describe, expect, it } from 'vitest';
import { validInvitePreview } from './invite-preview';

describe('invitation preview identity', () => {
  const items = [
    { email: 'a@example.test', company_id: 'a', company_role: 'employee', department_id: null },
  ];
  const value = {
    ready: true,
    provisioning_enabled: true,
    items: [{ ...items[0], index: 0, ready: true, book_access: 'none' }],
  };
  it('accepts an exact scoped preview', () => expect(validInvitePreview(value, items)).toBe(true));
  it('rejects wrong company, role, recipient or implicit book access', () => {
    for (const change of [
      { company_id: 'b' },
      { company_role: 'finance' },
      { email: 'b@example.test' },
      { book_access: 'reviewer' },
    ])
      expect(
        validInvitePreview({ ...value, items: [{ ...value.items[0], ...change }] }, items),
      ).toBe(false);
  });
});
