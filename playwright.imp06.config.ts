import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: /imp06-readability\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'tmp_test/imp/imp-20261106-a/imp06-browser.json' }]],
  outputDir: 'tmp_test/imp/imp-20261106-a/imp06-results',
  use: {
    baseURL: process.env.IMP06_BASE_URL || 'http://127.0.0.1:8082',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
