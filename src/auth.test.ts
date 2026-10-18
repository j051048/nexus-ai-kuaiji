import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from './types';

const sdk = vi.hoisted(() => ({ getSession: vi.fn(), stopAutoRefresh: vi.fn() }));
vi.mock('@supabase/supabase-js', () => ({
  createClient: vi.fn(() => ({ auth: sdk })),
}));

import { AuthTokenError, configureAuth, currentAccessToken } from './auth';

const session: Session = {
  mode: 'supabase',
  token: 'previous-access-token',
  user_id: 'test-user',
  user_name: '虚构用户',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  role: 'owner',
  scope_key: 'test-scope',
};

beforeEach(() => {
  vi.clearAllMocks();
  configureAuth({
    mode: 'supabase',
    supabase_url: 'https://auth.example.invalid',
    publishable_key: 'fictional-public-key',
  });
});

describe('access token failure classification', () => {
  it('uses the latest SDK token instead of replaying the login token', async () => {
    sdk.getSession.mockResolvedValue({
      data: { session: { access_token: 'refreshed-access-token' } },
      error: null,
    });
    await expect(currentAccessToken(session)).resolves.toBe('refreshed-access-token');
  });

  it('keeps a temporary provider failure recoverable', async () => {
    sdk.getSession.mockResolvedValue({
      data: { session: null },
      error: { name: 'AuthRetryableFetchError', status: 503 },
    });
    await expect(currentAccessToken(session)).rejects.toMatchObject({
      name: 'AuthTokenError',
      terminal: false,
    });
  });

  it('does not turn a thrown network failure into a signed-out identity', async () => {
    sdk.getSession.mockRejectedValue(new TypeError('fictional network failure'));
    await expect(currentAccessToken(session)).rejects.toMatchObject({ terminal: false });
  });

  it.each([400, 401, 403, 422])(
    'marks rejected refresh credentials (%s) terminal',
    async (status) => {
      sdk.getSession.mockResolvedValue({
        data: { session: null },
        error: { name: 'AuthApiError', status },
      });
      await expect(currentAccessToken(session)).rejects.toMatchObject({ terminal: true });
    },
  );

  it('requires a real active SDK session', async () => {
    sdk.getSession.mockResolvedValue({ data: { session: null }, error: null });
    await expect(currentAccessToken(session)).rejects.toBeInstanceOf(AuthTokenError);
    await expect(currentAccessToken(session)).rejects.toMatchObject({ terminal: true });
  });

  it('uses local sessions without contacting the SDK', async () => {
    await expect(currentAccessToken({ ...session, mode: 'local' })).resolves.toBe(session.token);
    expect(sdk.getSession).not.toHaveBeenCalled();
  });
});
