import { afterEach, expect, it, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

it.each([500, 502, 503, 504])(
  'redacts upstream %i bodies and preserves a safe request ID',
  async (status) => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'SECRET upstream SQL password' }), {
          status,
          headers: { 'X-Request-Id': 'qa-request-001' },
        }),
    );
    vi.stubGlobal('fetch', fetcher);
    await expect(api('/books', 'local-fake-token')).rejects.toMatchObject({
      status,
      requestId: 'qa-request-001',
    });
    await expect(api('/books', 'local-fake-token')).rejects.not.toThrow('SECRET');
    expect(fetcher).toHaveBeenCalledTimes(2);
  },
);

it('keeps unknown writes manual and never replays them', async () => {
  const fetcher = vi.fn(
    async () =>
      new Response(JSON.stringify({ code: 'result_unknown', detail: 'untrusted' }), {
        status: 503,
        headers: { 'X-Request-Id': 'qa-unknown-1' },
      }),
  );
  vi.stubGlobal('fetch', fetcher);
  await expect(
    api('/post', 'local-fake-token', { method: 'POST', body: {} }),
  ).rejects.toMatchObject({
    code: 'result_unknown',
    requestId: 'qa-unknown-1',
    message: expect.stringContaining('勿重复提交'),
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('does not render a hostile request ID', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response('{}', { status: 500, headers: { 'X-Request-Id': '<script>bad</script>' } }),
    ),
  );
  await expect(api('/books', 'local-fake-token')).rejects.toMatchObject({ requestId: undefined });
});
