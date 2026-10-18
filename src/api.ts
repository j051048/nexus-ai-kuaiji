import { AuthTokenError, currentAccessToken } from './auth';
import type { Session } from './types';
import {
  setTransportLimits,
  transportBodyError,
  transportTooLargeMessage,
} from './transport-limits';

let activeScope: string | null = null;
let scopeController = new AbortController();
let scopeRevision = 0;

export function setApiScope(session: Session | null): void {
  scopeRevision++;
  scopeController.abort();
  scopeController = new AbortController();
  activeScope = session?.scope_key ?? null;
}

// A permission recheck cancels old reads without turning a valid login into a logout.
export function invalidateApiScope(session: Session): void {
  if (session.scope_key === activeScope) setApiScope(session);
}

function invalidSession(session: Session) {
  if (activeScope === session.scope_key && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('accounting:unauthorized', { detail: session.scope_key }));
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}

async function request(
  path: string,
  credentials?: string | Session,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<Response> {
  const session = typeof credentials === 'object' ? credentials : undefined;
  const revision = scopeRevision;
  if (session && session.scope_key !== activeScope)
    throw new DOMException('授权范围已切换', 'AbortError');
  let token = typeof credentials === 'string' ? credentials : undefined;
  if (session) {
    try {
      token = await currentAccessToken(session);
    } catch (failure) {
      if (session.scope_key !== activeScope || revision !== scopeRevision)
        throw new DOMException('授权范围已切换', 'AbortError');
      if (failure instanceof AuthTokenError && failure.terminal) {
        invalidSession(session);
        throw new ApiError('登录已失效，请重新登录。', 401);
      }
      throw new ApiError('暂时无法连接登录服务，请稍后重试。', 503);
    }
    if (session.scope_key !== activeScope || revision !== scopeRevision)
      throw new DOMException('授权范围已切换', 'AbortError');
  }
  const signal = session
    ? options.signal
      ? AbortSignal.any([options.signal, scopeController.signal])
      : scopeController.signal
    : options.signal;
  const body =
    options.body === undefined
      ? undefined
      : options.body instanceof FormData
        ? options.body
        : JSON.stringify(options.body);
  const bodyError = transportBodyError(body);
  if (bodyError) throw new ApiError(bodyError, 413);
  const response = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers: {
      ...(options.body !== undefined && !(options.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(session?.firm_id ? { 'X-Firm-Id': session.firm_id } : {}),
    },
    ...(body !== undefined ? { body } : {}),
    signal,
  });
  if (signal?.aborted || (session && session.scope_key !== activeScope))
    throw new DOMException('授权范围已切换', 'AbortError');
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    if (signal?.aborted || (session && session.scope_key !== activeScope))
      throw new DOMException('授权范围已切换', 'AbortError');
    if (response.status === 401 && session) invalidSession(session);
    const payload = data as { detail?: unknown; message?: string; code?: unknown } | null;
    const code = typeof payload?.code === 'string' ? payload.code : undefined;
    if (
      response.status === 403 &&
      session &&
      activeScope === session.scope_key &&
      path !== '/me' &&
      typeof window !== 'undefined'
    ) {
      window.dispatchEvent(
        new CustomEvent(
          code === 'admin_mfa_required' ? 'accounting:mfa-required' : 'accounting:forbidden',
          { detail: session.scope_key },
        ),
      );
    }
    const detail = payload?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item.msg ?? '输入不符合要求').join('；')
          : detail && typeof detail === 'object' && 'message' in detail
            ? String(detail.message)
            : (payload?.message ?? `请求失败（${response.status}），请稍后重试。`);
    throw new ApiError(
      response.status === 413 ? transportTooLargeMessage() : message,
      response.status,
      code,
    );
  }
  return response;
}

export async function api<T>(
  path: string,
  credentials?: string | Session,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const revision = scopeRevision;
  const response = await request(path, credentials, options);
  const data = await response.json().catch(() => null);
  if (
    options.signal?.aborted ||
    (typeof credentials === 'object' &&
      (credentials.scope_key !== activeScope || revision !== scopeRevision))
  )
    throw new DOMException('授权范围已切换', 'AbortError');
  if (
    path === '/auth/config' &&
    (options.method === undefined || options.method === 'GET') &&
    data &&
    typeof data === 'object'
  ) {
    setTransportLimits((data as { transport_limits?: unknown }).transport_limits);
  }
  return data as T;
}

export async function apiBlob(
  path: string,
  session: Session,
  options: { signal?: AbortSignal } = {},
): Promise<Blob> {
  const revision = scopeRevision;
  const response = await request(path, session, options);
  const blob = await response.blob();
  if (options.signal?.aborted || session.scope_key !== activeScope || revision !== scopeRevision)
    throw new DOMException('授权范围已切换', 'AbortError');
  return blob;
}

export function errorMessage(error: unknown): string {
  if (error instanceof TypeError) return '无法连接服务。请确认网络与后端服务已就绪，然后重试。';
  return error instanceof Error ? error.message : '操作失败，请重试。';
}
