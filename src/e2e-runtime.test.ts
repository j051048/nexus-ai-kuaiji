import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlaywrightTestConfig } from '@playwright/test';

type WebServer = Exclude<PlaywrightTestConfig['webServer'], undefined>;

async function readPlaywrightConfig(mode?: string) {
  vi.stubEnv('E2E_FRONTEND_MODE', mode);
  vi.resetModules();
  return (await import('../playwright.config')).default;
}

function servers(config: PlaywrightTestConfig) {
  return config.webServer as Exclude<WebServer, unknown[]>[];
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('local E2E runtime contract', { timeout: 15_000 }, () => {
  it('configures identical loopback ports, strict ports and API proxies for dev and preview', async () => {
    const config = (await import('../vite.config')).default;
    expect(config.server).toMatchObject({ host: '127.0.0.1', port: 8080, strictPort: true });
    expect(config.preview).toMatchObject({ host: '127.0.0.1', port: 8080, strictPort: true });
    expect(config.preview?.proxy).toEqual(config.server?.proxy);
    expect(config.preview?.proxy?.['/api']).toMatchObject({ changeOrigin: false });
  });

  it('keeps the default dev entrypoint and refuses to reuse an existing server', async () => {
    const config = await readPlaywrightConfig();
    const [backend, frontend] = servers(config);
    expect(config.use?.baseURL).toBe('http://127.0.0.1:18080');
    expect(frontend.command).toBe('npm run dev -- --port 18080');
    expect(frontend.env?.ACCOUNTING_API_PROXY).toBe('http://127.0.0.1:18000');
    expect(backend.reuseExistingServer).toBe(false);
    expect(frontend.reuseExistingServer).toBe(false);
  });

  it('runs the same test selection against a fresh build in preview mode', async () => {
    const development = await readPlaywrightConfig('dev');
    const preview = await readPlaywrightConfig('preview');
    expect(servers(preview)[1].command).toBe('npm run build && npm run preview -- --port 18080');
    expect(preview.testDir).toBe(development.testDir);
    expect(preview.testIgnore).toBe(development.testIgnore);
    expect(preview.use?.baseURL).toBe(development.use?.baseURL);
  });

  it.each(['dev', 'preview'])('overrides inherited external services in %s mode', async (mode) => {
    const inherited = {
      ACCOUNTING_DATABASE_URL: 'synthetic-database',
      ACCOUNTING_WORKER_DATABASE_URL: 'synthetic-worker-database',
      ACCOUNTING_PROJECTION_DATABASE_URL: 'synthetic-projection-database',
      ACCOUNTING_AI_BASE_URL: 'https://synthetic-model.invalid/v1',
      ACCOUNTING_AI_API_KEY: 'synthetic-api-key',
      ACCOUNTING_AI_OCR_BASE_URL: 'https://synthetic-ocr.invalid/v1',
      ACCOUNTING_AI_OCR_API_KEY: 'synthetic-ocr-key',
      ACCOUNTING_AI_CHAT_BASE_URL: 'https://synthetic-chat.invalid/v1',
      ACCOUNTING_AI_CHAT_API_KEY: 'synthetic-chat-key',
      ACCOUNTING_AI_JOURNAL_BASE_URL: 'https://synthetic-journal.invalid/v1',
      ACCOUNTING_AI_JOURNAL_API_KEY: 'synthetic-journal-key',
      ACCOUNTING_SUPABASE_URL: 'https://synthetic-auth.invalid',
      ACCOUNTING_SUPABASE_ADMIN_KEY: 'synthetic-admin-key',
      ACCOUNTING_STORAGE_SERVER_KEY: 'synthetic-storage-key',
      ACCOUNTING_STORAGE_SUPABASE_URL: 'https://synthetic-storage.invalid',
      ACCOUNTING_WEBHOOK_URL: 'https://synthetic-webhook.invalid',
      OPENAI_API_KEY: 'synthetic-openai-key',
      DATABASE_URL: 'synthetic-generic-database',
      SMTP_HOST: 'synthetic-mail.invalid',
      VITE_SUPABASE_URL: 'https://synthetic-browser-auth.invalid',
    };
    for (const [key, value] of Object.entries(inherited)) vi.stubEnv(key, value);
    vi.stubEnv('ACCOUNTING_AUTH_MODE', 'supabase');
    vi.stubEnv('ACCOUNTING_STORAGE_BACKEND', 'supabase');
    const [backend, frontend] = servers(await readPlaywrightConfig(mode));
    // Playwright merges webServer.env into the inherited process environment.
    for (const server of [backend, frontend]) {
      const effective: Record<string, string | undefined> = { ...inherited, ...server.env };
      for (const key of Object.keys(inherited)) expect(effective[key], key).toBe('');
      expect(server.env?.ACCOUNTING_AUTH_MODE).toBe('local');
      expect(server.env?.ACCOUNTING_STORAGE_BACKEND).toBe('local');
    }
  });

  it('gives even simultaneous runs separate SQLite and attachment paths', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);
    const first = servers(await readPlaywrightConfig('dev'))[0].env;
    const second = servers(await readPlaywrightConfig('dev'))[0].env;
    const preview = servers(await readPlaywrightConfig('preview'))[0].env;
    const databases = [first, second, preview].map((env) => env?.ACCOUNTING_DB_PATH);
    expect(new Set(databases).size).toBe(3);
    for (const env of [first, second, preview]) {
      expect(env?.ACCOUNTING_DATABASE_URL).toBe('');
      expect(env?.ACCOUNTING_DB_PATH).toMatch(
        /^\.codex\/e2e\/accounting-(dev|preview)-[a-f0-9-]+\.db$/,
      );
      expect(env?.ACCOUNTING_ATTACHMENT_ROOT).toBe(`${env?.ACCOUNTING_DB_PATH}.attachments`);
    }
  });

  it('rejects an unsupported frontend mode before launching a process', async () => {
    await expect(readPlaywrightConfig('production')).rejects.toThrow('E2E_FRONTEND_MODE');
  });

  it('keeps numeric settings valid and disables credentials even when inherited values are malformed', async () => {
    const { resolveE2ERuntime } = await import('../scripts/e2e_runtime');
    const inherited = Object.freeze({
      E2E_FRONTEND_MODE: 'preview',
      ACCOUNTING_AI_TIMEOUT_SECONDS: 'malformed',
      ACCOUNTING_AI_MAX_CONCURRENCY: '100000',
      ACCOUNTING_AI_RESPONSE_FORMAT: 'malformed',
      ACCOUNTING_AI_CHAT_MODEL: 'synthetic-model',
      ACCOUNTING_AI_ALLOW_NO_AUTH: '1',
      ACCOUNTING_STORAGE_TIMEOUT_SECONDS: 'malformed',
      ACCOUNTING_STORAGE_ALLOW_HTTP: 'malformed',
      ACCOUNTING_QUEUE_LEASE_SECONDS: 'malformed',
      ACCOUNTING_PROCESSING_GLOBAL_LIMIT: 'malformed',
      ACCOUNTING_ALLOWED_ORIGINS: 'https://synthetic-frontend.invalid',
      ACCOUNTING_ALLOWED_HOSTS: 'synthetic-frontend.invalid',
      ACCOUNTING_API_PROXY: 'https://synthetic-backend.invalid',
      OPENAI_BASE_URL: 'https://synthetic-model.invalid',
      ANTHROPIC_API_KEY: 'synthetic-anthropic-key',
      SUPABASE_SERVICE_ROLE_KEY: 'synthetic-service-role',
      AWS_SECRET_ACCESS_KEY: 'synthetic-cloud-key',
      SENDGRID_API_KEY: 'synthetic-mail-key',
      HTTPS_PROXY: 'https://synthetic-proxy.invalid',
      http_proxy: 'http://synthetic-proxy.invalid',
      PATH: 'synthetic-system-path',
    });
    const runtime = resolveE2ERuntime(inherited);
    for (const env of [runtime.backendEnv, runtime.frontendEnv]) {
      expect(env).toMatchObject({
        ACCOUNTING_AI_TIMEOUT_SECONDS: '45',
        ACCOUNTING_AI_MAX_CONCURRENCY: '2',
        ACCOUNTING_AI_RESPONSE_FORMAT: 'json_schema',
        ACCOUNTING_AI_CHAT_MODEL: '',
        ACCOUNTING_AI_ALLOW_NO_AUTH: '0',
        ACCOUNTING_STORAGE_TIMEOUT_SECONDS: '20',
        ACCOUNTING_STORAGE_ALLOW_HTTP: 'false',
        ACCOUNTING_QUEUE_LEASE_SECONDS: '180',
        ACCOUNTING_PROCESSING_GLOBAL_LIMIT: '2',
        ACCOUNTING_ALLOWED_ORIGINS: 'http://127.0.0.1:18080',
        ACCOUNTING_API_PROXY: 'http://127.0.0.1:18000',
        OPENAI_BASE_URL: '',
        ANTHROPIC_API_KEY: '',
        SUPABASE_SERVICE_ROLE_KEY: '',
        AWS_SECRET_ACCESS_KEY: '',
        SENDGRID_API_KEY: '',
        HTTPS_PROXY: '',
        http_proxy: '',
      });
      expect(env.ACCOUNTING_ALLOWED_HOSTS).not.toContain('synthetic-frontend');
      expect({ ...inherited, ...env }.PATH).toBe('synthetic-system-path');
    }
    expect(inherited.ACCOUNTING_API_PROXY).toBe('https://synthetic-backend.invalid');
    expect(runtime.baseURL).toBe('http://127.0.0.1:18080');
    expect(runtime.backendURL).toBe('http://127.0.0.1:18000');
    expect(runtime.frontendEnv.NODE_ENV).toBe('production');
  });

  it('preserves the configurable API target for ordinary Vite development and preview', async () => {
    vi.stubEnv('ACCOUNTING_API_PROXY', 'http://127.0.0.1:8123');
    vi.resetModules();
    const config = (await import('../vite.config')).default;
    for (const server of [config.server, config.preview]) {
      expect(server?.proxy?.['/api']).toMatchObject({ target: 'http://127.0.0.1:8123' });
    }
  });

  it.each(['dev', 'preview'])(
    'marks the %s frontend as isolated from local env files',
    async (mode) => {
      const { resolveE2ERuntime } = await import('../scripts/e2e_runtime');
      const runtime = resolveE2ERuntime({ E2E_FRONTEND_MODE: mode, E2E_LOCAL_ISOLATION: '0' });
      expect(runtime.frontendEnv.E2E_LOCAL_ISOLATION).toBe('1');
    },
  );

  it('disables Vite env-file loading for an isolated E2E frontend', async () => {
    vi.stubEnv('E2E_LOCAL_ISOLATION', '1');
    vi.stubEnv('E2E_FRONTEND_MODE', undefined);
    vi.resetModules();
    const config = (await import('../vite.config')).default;
    expect(config.envDir).toBe(false);
  });

  it('keeps ordinary Vite env loading independent from the frontend mode selector', async () => {
    vi.stubEnv('E2E_LOCAL_ISOLATION', undefined);
    vi.stubEnv('E2E_FRONTEND_MODE', 'preview');
    vi.resetModules();
    const config = (await import('../vite.config')).default;
    expect(config.envDir).toBe('./config');
  });
});
