import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: /imp02-shell\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'tmp_test/imp/imp-20261106-a/imp02-browser.json' }]],
  outputDir: 'tmp_test/imp/imp-20261106-a/imp02-results',
  use: {
    baseURL: process.env.IMP02_BASE_URL || 'http://127.0.0.1:8082',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
