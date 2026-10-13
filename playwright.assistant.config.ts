import { defineConfig } from '@playwright/test';

// Dedicated synthetic gateway verifies the real HTTP transport + queue + UI.
// No real supplier key, customer's database or browser response substitution.
const database = `.codex/e2e/assistant-model-${Date.now()}.db`;
export default defineConfig({
  testDir: './e2e',
  testMatch: 'stage15-assistant-model.spec.ts',
  workers: 1,
  fullyParallel: false,
  timeout: 45_000,
  use: { baseURL: 'http://127.0.0.1:18180', trace: 'retain-on-failure' },
  webServer: [
    {
      command: 'python e2e/assistant_model_fixture.py',
      url: 'http://127.0.0.1:18101/health',
      reuseExistingServer: false,
    },
    {
      command:
        'python -m uvicorn app.main:app --app-dir nexus_backend --host 127.0.0.1 --port 18100',
      url: 'http://127.0.0.1:18100/api/session',
      reuseExistingServer: false,
      env: {
        ACCOUNTING_ENV: 'local',
        ACCOUNTING_AUTH_MODE: 'local',
        ACCOUNTING_DATABASE_URL: '',
        ACCOUNTING_DB_PATH: database,
        ACCOUNTING_ATTACHMENT_ROOT: database + '.attachments',
        ACCOUNTING_ALLOWED_ORIGINS: 'http://127.0.0.1:18180',
        ACCOUNTING_SEED: '1',
        APP_ENV: 'test',
        ENV: 'test',
        ENVIRONMENT: 'test',
        ACCOUNTING_AI_BASE_URL: 'http://127.0.0.1:18101/v1',
        ACCOUNTING_AI_ALLOW_HTTP: '1',
        ACCOUNTING_AI_API_KEY: 'synthetic-assistant-fixture',
        ACCOUNTING_AI_CHAT_MODEL: 'synthetic-assistant-fixture',
        ACCOUNTING_AI_VISION_MODEL: '',
        ACCOUNTING_AI_JOURNAL_MODEL: '',
        ACCOUNTING_AI_CHAT_INPUT_CNY_PER_MILLION: '2.000000',
        ACCOUNTING_AI_CHAT_OUTPUT_CNY_PER_MILLION: '4.000000',
      },
    },
    {
      command: 'npm run dev -- --port 18180',
      url: 'http://127.0.0.1:18180',
      reuseExistingServer: false,
      env: { ACCOUNTING_API_PROXY: 'http://127.0.0.1:18100' },
    },
  ],
});
