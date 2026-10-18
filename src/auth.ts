import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { AuthConfig, Session } from './types';

let supabase: SupabaseClient | null = null;

export class AuthTokenError extends Error {
  constructor(
    message: string,
    public terminal: boolean,
  ) {
    super(message);
    this.name = 'AuthTokenError';
  }
}

function terminalAuthFailure(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const failure = error as { name?: unknown; status?: unknown };
  return (
    failure.name === 'AuthSessionMissingError' ||
    (typeof failure.status === 'number' && [400, 401, 403, 422].includes(failure.status))
  );
}

export function configureAuth(config: AuthConfig): SupabaseClient | null {
  void supabase?.auth.stopAutoRefresh();
  if (config.mode === 'local') {
    supabase = null;
    return null;
  }
  if (!config.supabase_url || !config.publishable_key)
    throw new Error('登录服务配置不完整，请联系管理员。');
  supabase = createClient(config.supabase_url, config.publishable_key, {
    auth: { persistSession: false, autoRefreshToken: true, detectSessionInUrl: false },
  });
  return supabase;
}

export function getAuthClient(): SupabaseClient | null {
  return supabase;
}

export async function currentAccessToken(session: Session): Promise<string> {
  if (session.mode === 'local') return session.token;
  if (!supabase) throw new AuthTokenError('登录已失效，请重新登录。', true);
  try {
    const result = await supabase.auth.getSession();
    if (result.error) {
      const terminal = terminalAuthFailure(result.error);
      throw new AuthTokenError(
        terminal ? '登录已失效，请重新登录。' : '暂时无法刷新登录状态，请检查网络后重试。',
        terminal,
      );
    }
    if (!result.data.session?.access_token)
      throw new AuthTokenError('登录已失效，请重新登录。', true);
    return result.data.session.access_token;
  } catch (failure) {
    if (failure instanceof AuthTokenError) throw failure;
    const terminal = terminalAuthFailure(failure);
    throw new AuthTokenError(
      terminal ? '登录已失效，请重新登录。' : '暂时无法刷新登录状态，请检查网络后重试。',
      terminal,
    );
  }
}

export function loginError(message: string): string {
  if (/invalid login credentials/i.test(message)) return '邮箱或密码不正确。';
  if (/email not confirmed/i.test(message)) return '此邮箱尚未完成验证，请联系管理员。';
  if (/too many|rate limit/i.test(message)) return '登录尝试过于频繁，请稍后重试。';
  if (/fetch|network|timeout/i.test(message)) return '暂时无法连接登录服务，请稍后重试。';
  return '登录失败，请检查账户信息或联系管理员。';
}
