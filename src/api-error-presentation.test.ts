import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { presentApiError } from './api-error-presentation';

describe('api error presentation', () => {
  it('asks a signed-out user to sign in again without retrying a write', () => {
    const value = presentApiError(new ApiError('登录已失效，请重新登录。', 401), 'req-401');
    expect(value?.kind).toBe('auth');
    expect(value?.relogin).toBe(true);
    expect(value?.retryable).toBe(false);
    expect(value?.requestId).toBe('req-401');
  });

  it('separates a required second factor from a plain permission denial', () => {
    const mfa = presentApiError(new ApiError('需要二次验证', 403, 'admin_mfa_required'));
    const denied = presentApiError(new ApiError('当前账号没有这项操作权限', 403));
    expect(mfa?.kind).toBe('mfa');
    expect(mfa?.nextStep).toContain('验证');
    expect(denied?.kind).toBe('forbidden');
    expect(denied?.retryable).toBe(false);
  });

  it('never auto-retries a version conflict and tells the user to re-read', () => {
    const value = presentApiError(new ApiError('预览已过期，请重新预览当前凭证', 409));
    expect(value?.kind).toBe('conflict');
    expect(value?.retryable).toBe(false);
    expect(value?.nextStep).toContain('重新读取');
  });

  it('does not echo raw server text as the user-facing detail', () => {
    const value = presentApiError(
      new ApiError(
        'psycopg.errors.UniqueViolation: duplicate key value violates unique constraint',
        500,
      ),
    );
    expect(value?.detail).not.toContain('psycopg');
    expect(value?.technical).toContain('psycopg');
    expect(value?.kind).toBe('unknown');
  });

  it('explains an oversized upload with the transport limit message only', () => {
    const value = presentApiError(new ApiError('附件超过上限', 413));
    expect(value?.kind).toBe('too_large');
    expect(value?.nextStep).toContain('压缩');
  });

  it('treats a lost connection as retryable and a cancelled scope switch as silent', () => {
    const offline = presentApiError(new TypeError('Failed to fetch'));
    const cancelled = presentApiError(new DOMException('授权范围已切换', 'AbortError'));
    expect(offline?.kind).toBe('network');
    expect(offline?.retryable).toBe(true);
    expect(cancelled).toBeNull();
  });

  it('marks a temporary service failure as retryable but keeps the request id', () => {
    const value = presentApiError(
      new ApiError('暂时无法连接登录服务，请稍后重试。', 503),
      'req-503',
    );
    expect(value?.kind).toBe('unavailable');
    expect(value?.retryable).toBe(true);
    expect(value?.requestId).toBe('req-503');
  });

  it('bounds validation detail instead of printing an unbounded server payload', () => {
    const value = presentApiError(new ApiError('x'.repeat(500), 422));
    expect(value?.kind).toBe('invalid');
    expect((value?.detail ?? '').length).toBeLessThanOrEqual(240);
  });
});
