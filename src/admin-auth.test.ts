import { describe, expect, it, vi } from 'vitest';
import type { SupabaseClient } from '@supabase/supabase-js';
import { adminFactorPrefix, enrollAdminTotp } from './admin-auth';

function fixture() {
  const mfa = {
    listFactors: vi.fn().mockResolvedValue({
      data: {
        all: [
          {
            id: 'active',
            factor_type: 'totp',
            status: 'verified',
            friendly_name: `${adminFactorPrefix}active`,
          },
          {
            id: 'interrupted',
            factor_type: 'totp',
            status: 'unverified',
            friendly_name: `${adminFactorPrefix}old`,
          },
          { id: 'other-app', factor_type: 'totp', status: 'unverified', friendly_name: '其他应用' },
          {
            id: 'phone',
            factor_type: 'phone',
            status: 'unverified',
            friendly_name: `${adminFactorPrefix}phone`,
          },
        ],
      },
      error: null,
    }),
    unenroll: vi.fn().mockResolvedValue({ error: null }),
    enroll: vi
      .fn()
      .mockResolvedValue({ data: { id: 'new', totp: { qr_code: '<svg />' } }, error: null }),
  };
  return { mfa, client: { auth: { mfa } } as unknown as SupabaseClient };
}

describe('administrator MFA recovery', () => {
  it('restarts only this product’s interrupted TOTP setup and preserves every verified or unrelated factor', async () => {
    const { client, mfa } = fixture();
    const result = await enrollAdminTotp(client);
    expect(mfa.unenroll.mock.calls).toEqual([[{ factorId: 'interrupted' }]]);
    expect(mfa.enroll).toHaveBeenCalledWith({
      factorType: 'totp',
      friendlyName: expect.stringMatching(/^衡账管理后台 /),
    });
    expect(result.id).toBe('new');
  });
  it('does not create more unfinished factors when cleanup fails', async () => {
    const { client, mfa } = fixture();
    mfa.unenroll.mockResolvedValueOnce({ error: new Error('temporary failure') });
    await expect(enrollAdminTotp(client)).rejects.toThrow('temporary failure');
    expect(mfa.enroll).not.toHaveBeenCalled();
  });
  it('does not mutate factors when reading the factor inventory fails', async () => {
    const { client, mfa } = fixture();
    mfa.listFactors.mockResolvedValueOnce({ data: null, error: new Error('session expired') });
    await expect(enrollAdminTotp(client)).rejects.toThrow('session expired');
    expect(mfa.unenroll).not.toHaveBeenCalled();
    expect(mfa.enroll).not.toHaveBeenCalled();
  });
});
