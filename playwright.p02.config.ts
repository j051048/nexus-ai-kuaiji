import { defineConfig } from '@playwright/test';

// The explicit fixture and real isolated PostgreSQL/auth servers are provisioned
// separately. This configuration never starts a local-persona API or deploys.
export default defineConfig({
  testDir: './e2e',
  testMatch: /(?:p02-(?:r1-business-flow|close-boundary)|next02-isolation)\.spec\.ts/,
  forbidOnly: true,
  failOnFlakyTests: true,
  retries: 0,
  workers: 1,
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  use: { baseURL: 'http://127.0.0.1:18180', trace: 'off', screenshot: 'only-on-failure' },
  outputDir: '.codex/p02-r1-browser/results',
  reporter: 'line',
});
