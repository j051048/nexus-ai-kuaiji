import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, apiBlob, ApiError, setApiScope } from './api';
import { currentAccessToken } from './auth';
import type { Session } from './types';
import { getTransportLimits, maxUploadBytes, setTransportLimits } from './transport-limits';

vi.mock('./auth', () => ({ currentAccessToken: vi.fn() }));
const credentials: Session = { mode: 'supabase', token: 'initial-token', firm_id: 'firm-one', firm_name: '第一事务所', user_id: 'user-one', user_name: '会计', role: 'reviewer', scope_key: 'scope-one' };
const tokenGetter = vi.mocked(currentAccessToken);

afterEach(() => { setApiScope(null); setTransportLimits(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe('authenticated accounting requests', () => {
  it('resolves the current token for every request and sends the selected firm', async () => {
    setApiScope(credentials);
    tokenGetter.mockResolvedValueOnce('first-token').mockResolvedValueOnce('refreshed-token');
    const fetcher = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    await api('/workbench', credentials);
    await api('/books/example', credentials);
    expect(fetcher.mock.calls[0][1].headers).toMatchObject({ Authorization: 'Bearer first-token', 'X-Firm-Id': 'firm-one' });
    expect(fetcher.mock.calls[1][1].headers).toMatchObject({ Authorization: 'Bearer refreshed-token', 'X-Firm-Id': 'firm-one' });
  });

  it('rejects a stale workspace before obtaining credentials or making a request', async () => {
    setApiScope({ ...credentials, scope_key: 'new-scope', firm_id: 'firm-two' });
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    await expect(api('/workbench', credentials)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).not.toHaveBeenCalled(); expect(tokenGetter).not.toHaveBeenCalled();
  });

  it('aborts pending work and prevents a late response from leaking the previous firm data', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('token');
    let deliver: (response: Response) => void = () => {};
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal('fetch', vi.fn((_path, options: { signal: AbortSignal }) => {
      requestSignal = options.signal;
      return new Promise<Response>(resolve => { deliver = resolve; });
    }));
    const pending = api('/workbench', credentials);
    await vi.waitFor(() => expect(requestSignal).toBeDefined());
    setApiScope({ ...credentials, scope_key: 'next-scope', firm_id: 'firm-two' });
    expect(requestSignal?.aborted).toBe(true);
    deliver(new Response('{"private":"previous-firm-data"}', { status: 200 }));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('signals login invalidation on 401', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('expired-token');
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"登录已过期"}', { status: 401 })));
    await expect(api('/workbench', credentials)).rejects.toMatchObject({ message: '登录已过期', status: 401 });
    expect(dispatchEvent.mock.calls[0][0].type).toBe('accounting:unauthorized');
  });

  it('sends authenticated multipart attachments without overriding the browser boundary', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('attachment-token');
    const fetcher = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 })); vi.stubGlobal('fetch', fetcher);
    const body = new FormData(); body.append('file', new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'invoice.pdf');
    await api('/business/events/example/attachments?expected_version=2', credentials, { method: 'POST', body });
    const options = fetcher.mock.calls[0][1] as RequestInit;
    expect(options.body).toBe(body);
    expect(options.headers).toMatchObject({ Authorization: 'Bearer attachment-token', 'X-Firm-Id': 'firm-one' });
    expect(options.headers).not.toHaveProperty('Content-Type');
  });

  it('downloads original attachments through the same authenticated scope', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('download-token');
    const fetcher = vi.fn().mockImplementation(async () => new Response('private-file', { status: 200 })); vi.stubGlobal('fetch', fetcher);
    const result = await apiBlob('/business/attachments/example/content', credentials);
    expect(await result.text()).toBe('private-file');
    expect(fetcher.mock.calls[0][0]).toBe('/api/business/attachments/example/content');
    expect(fetcher.mock.calls[0][1].headers).toMatchObject({ Authorization: 'Bearer download-token', 'X-Firm-Id': 'firm-one' });
  });

  it('rejects a late attachment body after the authorized workspace changes', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('download-token');
    let deliver: (response: Response) => void = () => {};
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { deliver = resolve; })));
    const pending = apiBlob('/business/attachments/example/content', credentials);
    await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    setApiScope({ ...credentials, scope_key: 'another-scope' });
    deliver(new Response('previous-client-file', { status: 200 }));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('requests a fresh authorization context on 403 but avoids identity retry loops', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('token');
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"权限不足"}', { status: 403 })));
    await expect(api('/books/private', credentials)).rejects.toBeInstanceOf(ApiError);
    expect(dispatchEvent.mock.calls[0][0].type).toBe('accounting:forbidden');
    dispatchEvent.mockClear();
    await expect(api('/me', credentials)).rejects.toMatchObject({ status: 403 });
    expect(dispatchEvent).not.toHaveBeenCalled();
  });

  it('does not invalidate the new identity when the old scope receives a delayed forbidden error body', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('old-token');
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent });
    let deliver: (body: unknown) => void = () => {};
    let reading = false;
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403, json: () => { reading = true; return new Promise(resolve => { deliver = resolve; }); } })));
    const pending = api('/business/events?scope=company', credentials);
    await vi.waitFor(() => expect(reading).toBe(true));
    setApiScope({ ...credentials, scope_key: 'new-firm-scope', firm_id: 'new-firm' });
    deliver({ detail: '旧企业已撤权' });
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(dispatchEvent).not.toHaveBeenCalled();
  });
});

describe('deployment transport limits', () => {
  const limits = { max_file_bytes: 3145728, max_request_bytes: 4194304, max_response_bytes: 4194304 };

  it('reads runtime limits from auth config and resets for a direct backend without metadata', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ mode: 'supabase', transport_limits: limits })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ mode: 'local', personas: [] }))));
    await api('/auth/config');
    expect(getTransportLimits()).toEqual(limits);
    expect(maxUploadBytes()).toBe(3145728);
    await api('/auth/config');
    expect(getTransportLimits()).toBeNull();
    expect(maxUploadBytes()).toBe(10 * 1024 * 1024);
  });

  it('does not accept metadata from another endpoint or an unsuccessful config request', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ transport_limits: limits })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ transport_limits: limits }), { status: 503 })));
    await api('/workbench');
    expect(getTransportLimits()).toBeNull();
    await expect(api('/auth/config')).rejects.toMatchObject({ status: 503 });
    expect(getTransportLimits()).toBeNull();
  });

  it('turns a platform HTML 413 into a Chinese actionable error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>FUNCTION_PAYLOAD_TOO_LARGE</html>', { status: 413 })));
    await expect(api('/import')).rejects.toMatchObject({ status: 413, message: expect.stringContaining('按期间拆分') });
  });

  it('checks encoded JSON and multipart requests before fetch when runtime limits are present', async () => {
    setTransportLimits(limits);
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    await expect(api('/import', undefined, { method: 'POST', body: { content: '账'.repeat(1_500_000) } })).rejects.toMatchObject({ status: 413 });
    const body = new FormData(); body.append('file', new Blob([new Uint8Array(3145729)]), 'invoice.pdf');
    await expect(api('/business/attachments', undefined, { method: 'POST', body })).rejects.toMatchObject({ status: 413, message: expect.stringContaining('3 MB') });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('keeps the existing authenticated multipart transport at the 3 MB boundary', async () => {
    setApiScope(credentials); tokenGetter.mockResolvedValue('fresh-token'); setTransportLimits(limits);
    const fetcher = vi.fn().mockImplementation(async () => new Response('{}')); vi.stubGlobal('fetch', fetcher);
    const body = new FormData(); body.append('file', new Blob([new Uint8Array(3145728)]), 'invoice.pdf');
    await api('/business/attachments', credentials, { method: 'POST', body });
    expect(fetcher.mock.calls[0][1]).toMatchObject({ body, headers: { Authorization: 'Bearer fresh-token', 'X-Firm-Id': 'firm-one' } });
    expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty('Content-Type');
  });
});
