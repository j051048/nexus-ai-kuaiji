import type { SupabaseClient } from '@supabase/supabase-js';

export const adminFactorPrefix = '衡账管理后台 ';

/** An interrupted setup has no recoverable QR. Restart only our own unfinished
 * factors after the user explicitly asks to bind; never remove verified MFA. */
export async function enrollAdminTotp(client: SupabaseClient) {
  const factors = await client.auth.mfa.listFactors();
  if (factors.error) throw factors.error;
  for (const factor of factors.data.all) {
    if (
      factor.factor_type === 'totp' &&
      factor.status === 'unverified' &&
      factor.friendly_name?.startsWith(adminFactorPrefix)
    ) {
      const removed = await client.auth.mfa.unenroll({ factorId: factor.id });
      if (removed.error) throw removed.error;
    }
  }
  const result = await client.auth.mfa.enroll({
    factorType: 'totp',
    friendlyName: `${adminFactorPrefix}${crypto.randomUUID().slice(0, 8)}`,
  });
  if (result.error) throw result.error;
  return result.data;
}
