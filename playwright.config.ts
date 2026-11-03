import { defineConfig } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolveE2ERuntime } from './scripts/e2e_runtime';

// Every run owns a fresh database. Never exercise a developer's working books.
const runtime = resolveE2ERuntime();
const core = process.env.E2E_CORE_ACCEPTANCE === '1';
const manifest: { cases: { file: string; title: string }[] } = JSON.parse(
  readFileSync(new URL('./scripts/e2e_core_cases.json', import.meta.url), 'utf8').replace(
    /^\uFEFF/,
    '',
  ),
);
const literal = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export const config = defineConfig({
  testDir: './e2e',
  // P02 runs against explicit PostgreSQL + real isolated GoTrue fixtures through
  // playwright.p02.config.ts; the standard harness owns a fresh SQLite database.
  testIgnore: '**/{stage15-assistant-model,p02-r1-business-flow,p02-close-boundary}.spec.ts',
  ...(core
    ? {
        testMatch: [...new Set(manifest.cases.map(({ file }) => `**/${file.slice(4)}`))],
        grep: manifest.cases.map(
          ({ title }) => new RegExp(`${literal(title.split(' › ').join(' '))}$`),
        ),
        reporter: [['line'], ['./scripts/e2e_reporter.mjs']] as [string][],
      }
    : {}),
  forbidOnly: true,
  failOnFlakyTests: true,
  retries: 0,
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  // Dev mode transforms modules on demand; warm the entry bundle once so a cold
  // compile does not consume the 30s a frozen case may spend on product
  // behaviour. Preview runs prebuilt assets and needs no warm-up.
  ...(runtime.mode === 'dev' ? { globalSetup: './e2e/warmup.ts' } : {}),
  use: { baseURL: runtime.baseURL, trace: core ? 'off' : 'retain-on-failure' },
  webServer: [
    {
      command: `python -m uvicorn app.main:app --app-dir nexus_backend --host 127.0.0.1 --port ${new URL(runtime.backendURL).port}`,
      url: `${runtime.backendURL}/api/session`,
      env: runtime.backendEnv,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: runtime.frontendCommand,
      url: runtime.baseURL,
      env: runtime.frontendEnv,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});

export default config;
