import { randomUUID } from 'node:crypto';

type RuntimeEnvironment = Readonly<Record<string, string | undefined>>;

export interface E2ERuntime {
  mode: 'dev' | 'preview';
  baseURL: string;
  backendURL: string;
  database: string;
  attachmentRoot: string;
  backendEnv: Record<string, string>;
  frontendEnv: Record<string, string>;
  frontendCommand: string;
}

// Playwright merges this map over process.env. Omitting an inherited key would
// preserve it, so clear application/integration namespaces before safe defaults.
const externalConfiguration =
  /^(ACCOUNTING_|VITE_|NEXT_PUBLIC_|REACT_APP_|OPENAI_|ANTHROPIC_|AZURE_OPENAI_|GOOGLE_API_|GEMINI_|SUPABASE_|AWS_|S3_|R2_|CLOUDFLARE_|SMTP_|MAIL_|RESEND_|SENDGRID_|POSTMARK_|DATABASE_|POSTGRES_|PG(?:HOST|PORT|USER|PASSWORD|DATABASE|SERVICE|SSL)|REDIS_|SENTRY_|OTEL_|LANGSMITH_|LANGCHAIN_|HELICONE_)/i;

const emptySettings = [
  'ACCOUNTING_DATABASE_URL',
  'ACCOUNTING_WORKER_DATABASE_URL',
  'ACCOUNTING_PROJECTION_DATABASE_URL',
  'ACCOUNTING_MAINTENANCE_DATABASE_URL',
  'ACCOUNTING_SYSTEM_DATABASE_URL',
  'ACCOUNTING_ADMIN_DATABASE_URL',
  'ACCOUNTING_TEST_DATABASE_URL',
  'ACCOUNTING_DATABASE_PATH',
  'ACCOUNTING_STORAGE_ROOT',
  'ACCOUNTING_RESTORE_HOLD_FILE',
  'ACCOUNTING_SUPABASE_URL',
  'ACCOUNTING_SUPABASE_PUBLISHABLE_KEY',
  'ACCOUNTING_SUPABASE_ADMIN_KEY',
  'ACCOUNTING_PUBLIC_APP_URL',
  'ACCOUNTING_STORAGE_SUPABASE_URL',
  'ACCOUNTING_STORAGE_BUCKET',
  'ACCOUNTING_STORAGE_SERVER_KEY',
  'ACCOUNTING_BACKEND_ORIGIN',
  'ACCOUNTING_AI_BASE_URL',
  'ACCOUNTING_AI_API_KEY',
  'ACCOUNTING_AI_VISION_MODEL',
  'ACCOUNTING_AI_OCR_BASE_URL',
  'ACCOUNTING_AI_OCR_API_KEY',
  'ACCOUNTING_AI_OCR_MODEL',
  'ACCOUNTING_AI_JOURNAL_BASE_URL',
  'ACCOUNTING_AI_JOURNAL_API_KEY',
  'ACCOUNTING_AI_JOURNAL_MODEL',
  'ACCOUNTING_AI_CHAT_BASE_URL',
  'ACCOUNTING_AI_CHAT_API_KEY',
  'ACCOUNTING_AI_CHAT_MODEL',
  'DATABASE_URL',
  'DIRECT_URL',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
] as const;

function isolatedEnvironment(environ: RuntimeEnvironment): Record<string, string> {
  const result: Record<string, string> = {};
  for (const key of Object.keys(environ)) {
    if (
      externalConfiguration.test(key) ||
      /^(DIRECT_URL|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)$/i.test(key)
    ) {
      result[key] = '';
    }
  }
  for (const key of emptySettings) result[key] = '';
  return {
    ...result,
    ACCOUNTING_ENV: 'local',
    ACCOUNTING_AUTH_MODE: 'local',
    ACCOUNTING_AUTH_AUDIENCE: 'authenticated',
    ACCOUNTING_ALLOW_FIRM_SIGNUP: '1',
    ACCOUNTING_ALLOWED_HOSTS: '127.0.0.1,localhost,[::1],::1',
    ACCOUNTING_STORAGE_BACKEND: 'local',
    ACCOUNTING_STORAGE_TIMEOUT_SECONDS: '20',
    ACCOUNTING_STORAGE_ALLOW_HTTP: 'false',
    ACCOUNTING_AI_ALLOW_HTTP: '0',
    ACCOUNTING_AI_ALLOW_NO_AUTH: '0',
    ACCOUNTING_AI_ALLOW_SINGLE_JSON_FENCE: '0',
    ACCOUNTING_AI_TIMEOUT_SECONDS: '45',
    ACCOUNTING_AI_MAX_CONCURRENCY: '2',
    ACCOUNTING_AI_RESPONSE_FORMAT: 'json_schema',
    ACCOUNTING_AI_OCR_RESPONSE_FORMAT: 'json_schema',
    ACCOUNTING_AI_JOURNAL_RESPONSE_FORMAT: 'json_schema',
    ACCOUNTING_AI_CHAT_RESPONSE_FORMAT: 'json_schema',
    ACCOUNTING_PROCESSING_GLOBAL_LIMIT: '2',
    ACCOUNTING_DB_POOL_MIN: '0',
    ACCOUNTING_DB_POOL_MAX: '4',
    ACCOUNTING_DB_POOL_TIMEOUT_SECONDS: '5',
    ACCOUNTING_DB_POOL_MAX_WAITING: '32',
    ACCOUNTING_QUEUE_EMBEDDED_WORKER: '1',
    ACCOUNTING_QUEUE_LEASE_SECONDS: '180',
    ACCOUNTING_QUEUE_CONCURRENCY: '2',
    ACCOUNTING_QUEUE_POLL_SECONDS: '1',
    ACCOUNTING_MIGRATION_CONCURRENCY: '1',
    ACCOUNTING_PROJECTION_POLL_SECONDS: '5',
    ACCOUNTING_SEED: '1',
    // The isolated suite exercises deterministic read-only analysis. Provider
    // endpoints and credentials remain empty; production defaults are untouched.
    ACCOUNTING_ASSISTANT_INVESTIGATION: '1',
    APP_ENV: 'test',
    ENV: 'test',
    ENVIRONMENT: 'test',
    NO_PROXY: '127.0.0.1,localhost,::1',
    no_proxy: '127.0.0.1,localhost,::1',
  };
}

function localPort(environ: RuntimeEnvironment, key: string, fallback: number): number {
  const raw = environ[key];
  if (raw === undefined || raw === '') return fallback;
  if (!/^\d{4,5}$/.test(raw) || Number(raw) < 1024 || Number(raw) > 65535) {
    throw new Error(`${key} must be an integer port from 1024 to 65535`);
  }
  return Number(raw);
}

export function resolveE2ERuntime(environ: RuntimeEnvironment = process.env): E2ERuntime {
  const mode = environ.E2E_FRONTEND_MODE || 'dev';
  if (mode !== 'dev' && mode !== 'preview') {
    throw new Error('E2E_FRONTEND_MODE must be dev or preview');
  }
  const webPort = localPort(environ, 'E2E_WEB_PORT', 18080);
  const apiPort = localPort(environ, 'E2E_API_PORT', 18000);
  if (webPort === apiPort) throw new Error('E2E backend and frontend ports must be distinct');
  const baseURL = `http://127.0.0.1:${webPort}`;
  const backendURL = `http://127.0.0.1:${apiPort}`;
  const database = `.codex/e2e/accounting-${mode}-${randomUUID()}.db`;
  const attachmentRoot = `${database}.attachments`;
  const environment = {
    ...isolatedEnvironment(environ),
    ACCOUNTING_DB_PATH: database,
    ACCOUNTING_ATTACHMENT_ROOT: attachmentRoot,
    ACCOUNTING_ALLOWED_ORIGINS: baseURL,
    ACCOUNTING_API_PROXY: backendURL,
  };
  return {
    mode,
    baseURL,
    backendURL,
    database,
    attachmentRoot,
    backendEnv: { ...environment },
    frontendEnv: {
      ...environment,
      E2E_LOCAL_ISOLATION: '1',
      NODE_ENV: mode === 'preview' ? 'production' : 'development',
    },
    frontendCommand:
      mode === 'preview'
        ? `npm run build && npm run preview -- --port ${webPort}`
        : `npm run dev -- --port ${webPort}`,
  };
}
