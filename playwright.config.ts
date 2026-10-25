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
  testIgnore: '**/stage15-assistant-model.spec.ts',
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
  use: { baseURL: runtime.baseURL, trace: core ? 'off' : 'retain-on-failure' },
  webServer: [
    {
      command:
        'python -m uvicorn app.main:app --app-dir nexus_backend --host 127.0.0.1 --port 18000',
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
