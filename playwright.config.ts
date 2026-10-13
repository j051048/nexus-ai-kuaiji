import { defineConfig } from '@playwright/test';

// Every run owns a fresh database. Never exercise a developer's working books.
const database = `.codex/e2e/accounting-${Date.now()}.db`;
export default defineConfig({
  testDir: './e2e',
  testIgnore: '**/stage15-assistant-model.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:18080', trace: 'retain-on-failure' },
  webServer: [
    {
      command:
        'python -m uvicorn app.main:app --app-dir nexus_backend --host 127.0.0.1 --port 18000',
      url: 'http://127.0.0.1:18000/api/session',
      env: {
        ACCOUNTING_ENV: 'local',
        ACCOUNTING_AUTH_MODE: 'local',
        ACCOUNTING_DATABASE_URL: '',
        ACCOUNTING_DB_PATH: database,
        ACCOUNTING_ATTACHMENT_ROOT: database + '.attachments',
        ACCOUNTING_SEED: '1',
        APP_ENV: 'test',
        ENV: 'test',
        ENVIRONMENT: 'test',
      },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --port 18080',
      url: 'http://127.0.0.1:18080',
      env: { ACCOUNTING_API_PROXY: 'http://127.0.0.1:18000' },
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
