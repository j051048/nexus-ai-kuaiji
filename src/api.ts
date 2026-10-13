import { currentAccessToken } from './auth';
import type { Session } from './types';
import { setTransportLimits, transportBodyError, transportTooLargeMessage } from './transport-limits';

let activeScope: string | null = null;
let scopeController = new AbortController();

export function setApiScope(session: Session | null): void {
  scopeController.abort();
  scopeController = new AbortController();
  activeScope = session?.scope_key ?? null;
}

function invalidSession(session: Session) {
  if (activeScope === session.scope_key && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('accounting:unauthorized', { detail: session.scope_key }));
  }
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request(
  path: string,
  credentials?: string | Session,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<Response> {
  const session = typeof credentials === 'object' ? credentials : undefined;
  if (session && session.scope_key !== activeScope) throw new DOMException('授权范围已切换', 'AbortError');
  let token = typeof credentials === 'string' ? credentials : undefined;
  if (session) {
    try { token = await currentAccessToken(session); }
    catch { invalidSession(session); throw new ApiError('登录已失效，请重新登录。', 401); }
    if (session.scope_key !== activeScope) throw new DOMException('授权范围已切换', 'AbortError');
  }
  const signal = session
    ? options.signal ? AbortSignal.any([options.signal, scopeController.signal]) : scopeController.signal
    : options.signal;
  const body = options.body === undefined ? undefined : options.body instanceof FormData ? options.body : JSON.stringify(options.body);
  const bodyError = transportBodyError(body);
  if (bodyError) throw new ApiError(bodyError, 413);
  const response = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers: {
      ...(options.body !== undefined && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(session?.firm_id ? { 'X-Firm-Id': session.firm_id } : {}),
    },
    ...(body !== undefined ? { body } : {}),
    signal,
  });
  if (signal?.aborted || (session && session.scope_key !== activeScope)) throw new DOMException('授权范围已切换', 'AbortError');
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    if (signal?.aborted || (session && session.scope_key !== activeScope)) throw new DOMException('授权范围已切换', 'AbortError');
    if (response.status === 401 && session) invalidSession(session);
    if (response.status === 403 && session && activeScope === session.scope_key && path !== '/me' && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: session.scope_key }));
    }
    const payload = data as { detail?: unknown; message?: string } | null;
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg ?? '输入不符合要求').join('；')
      : detail && typeof detail === 'object' && 'message' in detail ? String(detail.message)
      : payload?.message ?? `请求失败（${response.status}），请稍后重试。`;
    throw new ApiError(response.status === 413 ? transportTooLargeMessage() : message, response.status);
  }
  return response;
}

export async function api<T>(path: string, credentials?: string | Session, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const response = await request(path, credentials, options);
  const data = await response.json().catch(() => null);
  if (options.signal?.aborted || (typeof credentials === 'object' && credentials.scope_key !== activeScope)) throw new DOMException('授权范围已切换', 'AbortError');
  if (path === '/auth/config' && (options.method === undefined || options.method === 'GET') && data && typeof data === 'object') {
    setTransportLimits((data as { transport_limits?: unknown }).transport_limits);
  }
  return data as T;
}

export async function apiBlob(path: string, session: Session, options: { signal?: AbortSignal } = {}): Promise<Blob> {
  const response = await request(path, session, options);
  const blob = await response.blob();
  if (options.signal?.aborted || session.scope_key !== activeScope) throw new DOMException('授权范围已切换', 'AbortError');
  return blob;
}

export function errorMessage(error: unknown): string {
  if (error instanceof TypeError) return '无法连接服务。请确认网络与后端服务已就绪，然后重试。';
  return error instanceof Error ? error.message : '操作失败，请重试。';
}

