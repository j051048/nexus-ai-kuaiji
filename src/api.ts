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

const MAX_READ_CACHE_ENTRIES = 40;
const readCache = new Map<string, { expiresAt: number; revision: number; data: unknown }>();
const inFlightReads = new Map<string, { revision: number; promise: Promise<unknown> }>();

/** 仅对高流量、高频复用的已鉴权只读查询启用极短内存缓存，关页即焚，绝不落盘。 */
function readCacheTtlMs(path: string): number {
  const pathname = path.split('?')[0];
  if (pathname === '/companies') return 30_000;
  if (pathname === '/workspace/profile') return 20_000;
  if (pathname === '/workbench') return 15_000;
  if (pathname === '/b04-close') return 20_000;
  if (pathname === '/catalog' || pathname.startsWith('/catalog/')) return 60_000;
  if (pathname === '/firm/observability/insights' || pathname.startsWith('/firm/observability/insights/')) {
    return 20_000;
  }
  return 0;
}

function clonePayload<T>(value: T): T {
  if (value === null || value === undefined || typeof value !== 'object') return value;
  try {
    return structuredClone(value);
  } catch {
    return JSON.parse(JSON.stringify(value)) as T;
  }
}

export function clearApiReadCache(): void {
  readCache.clear();
  inFlightReads.clear();
}

export function setApiScope(session: Session | null): void {
  scopeRevision++;
  clearApiReadCache();
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
    public requestId?: string,
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
    if (response.status === 401 && session) {
      clearApiReadCache();
      invalidSession(session);
    }
    const payload = data as { detail?: unknown; message?: string; code?: unknown } | null;
    const code =
      typeof payload?.code === 'string' && /^[a-z0-9_]{1,80}$/.test(payload.code)
        ? payload.code
        : undefined;
    const rawRequestId = response.headers.get('X-Request-Id') ?? '';
    const requestId = /^[A-Za-z0-9_.:-]{1,120}$/.test(rawRequestId) ? rawRequestId : undefined;
    if (
      response.status === 403 &&
      session &&
      activeScope === session.scope_key &&
      path !== '/me' &&
      typeof window !== 'undefined'
    ) {
      clearApiReadCache();
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
    const failureMessage =
      response.status >= 500
        ? code === 'result_unknown' ||
          (options.method !== undefined &&
            !['GET', 'HEAD', 'OPTIONS'].includes(options.method.toUpperCase()))
          ? '暂时无法确认本次操作结果，请先核对处理记录，勿重复提交。'
          : '服务暂时不可用，请稍后重新读取；若持续失败，请联系管理员。'
        : response.status === 413
          ? transportTooLargeMessage()
          : message;
    throw new ApiError(
      failureMessage + (response.status >= 500 && requestId ? ` 请求编号：${requestId}` : ''),
      response.status,
      code,
      requestId,
    );
  }
  return response;
}

export async function api<T>(
  path: string,
  credentials?: string | Session,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const session = typeof credentials === 'object' ? credentials : undefined;

  if (method !== 'GET') {
    clearApiReadCache();
  }

  const ttlMs = method === 'GET' && session ? readCacheTtlMs(path) : 0;
  if (ttlMs > 0 && session) {
    if (session.scope_key !== activeScope) {
      throw new DOMException('授权范围已切换', 'AbortError');
    }
    if (options.signal?.aborted) {
      throw new DOMException('请求已取消', 'AbortError');
    }
    const cacheKey = `${session.scope_key}\n${path}`;
    const revision = scopeRevision;
    const cached = readCache.get(cacheKey);
    if (cached && cached.revision === revision && cached.expiresAt > Date.now()) {
      return clonePayload(cached.data as T);
    }
    if (cached) readCache.delete(cacheKey);

    const existing = inFlightReads.get(cacheKey);
    if (existing && existing.revision === revision) {
      const value = (await existing.promise) as T;
      if (options.signal?.aborted || session.scope_key !== activeScope || revision !== scopeRevision) {
        throw new DOMException('授权范围已切换', 'AbortError');
      }
      return clonePayload(value);
    }

    const fetchPromise = (async () => {
      // 在途共享请求仅绑定全局 scopeController，避免单个组件卸载连带取消其他共享方
      const response = await request(path, session, { method: 'GET' });
      const data = await response.json().catch(() => null);
      if (session.scope_key !== activeScope || revision !== scopeRevision) {
        throw new DOMException('授权范围已切换', 'AbortError');
      }
      if (readCache.size >= MAX_READ_CACHE_ENTRIES) {
        const oldestKey = readCache.keys().next().value;
        if (oldestKey !== undefined) readCache.delete(oldestKey);
      }
      readCache.set(cacheKey, {
        expiresAt: Date.now() + ttlMs,
        revision,
        data: clonePayload(data),
      });
      return data;
    })().finally(() => {
      const current = inFlightReads.get(cacheKey);
      if (current?.promise === fetchPromise) inFlightReads.delete(cacheKey);
    });

    inFlightReads.set(cacheKey, { revision, promise: fetchPromise });
    const value = (await fetchPromise) as T;
    if (options.signal?.aborted || session.scope_key !== activeScope || revision !== scopeRevision) {
      throw new DOMException('授权范围已切换', 'AbortError');
    }
    return clonePayload(value);
  }

  const revision = scopeRevision;
  const response = await request(path, credentials, options);
  const data = await response.json().catch(() => null);
  if (
    options.signal?.aborted ||
    (session && (session.scope_key !== activeScope || revision !== scopeRevision))
  )
    throw new DOMException('授权范围已切换', 'AbortError');
  if (method !== 'GET') {
    clearApiReadCache();
  }
  if (
    path === '/auth/config' &&
    method === 'GET' &&
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
