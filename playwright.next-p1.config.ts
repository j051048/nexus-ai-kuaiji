import { defineConfig } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { resolveE2ERuntime } from './scripts/e2e_runtime';

const runtime = resolveE2ERuntime();
const database = `tmp_test/next-p1/browser-${randomUUID()}.db`;
export default defineConfig({
  testDir: './e2e',
  testMatch: 'next-p1-handoff.spec.ts',
  forbidOnly: true,
  failOnFlakyTests: true,
  retries: 0,
  workers: 1,
  timeout: 90_000,
  reporter: 'line',
  globalSetup: './e2e/warmup.ts',
  outputDir: 'tmp_test/next-p1/browser-results',
  use: { baseURL: runtime.baseURL, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `python scripts/seed_next_p1_browser.py && python -m uvicorn app.main:app --app-dir nexus_backend --host 127.0.0.1 --port ${new URL(runtime.backendURL).port}`,
      url: `${runtime.backendURL}/api/session`,
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        ...runtime.backendEnv,
        ACCOUNTING_DB_PATH: database,
        ACCOUNTING_ATTACHMENT_ROOT: `${database}.attachments`,
        ACCOUNTING_QUEUE_EMBEDDED_WORKER: '0',
      },
    },
    {
      command: runtime.frontendCommand,
      url: runtime.baseURL,
      env: runtime.frontendEnv,
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
