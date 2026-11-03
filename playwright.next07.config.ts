import { defineConfig, devices } from '@playwright/test';
import { config } from './playwright.config';

// NEXT07: weak-network recovery, revisions, mobile readability and axe on one
// fresh SQLite runtime. P02's PostgreSQL fixture stays in its own config.
export default defineConfig({
  ...config,
  testMatch: [
    '**/v07-context-recovery.spec.ts',
    '**/stage10-revisions.spec.ts',
    '**/role2-mobile-readability.spec.ts',
    '**/qa-accessibility.spec.ts',
  ],
  grep: undefined,
  timeout: 90_000,
  outputDir: 'tmp_test/next/next07-browser/results',
  reporter: [['line'], ['json', { outputFile: 'tmp_test/next/next07-browser/report.json' }]],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
    {
      name: 'mobile-webkit',
      use: { ...devices['iPhone 13'] },
      testMatch: ['**/role2-mobile-readability.spec.ts', '**/qa-accessibility.spec.ts'],
    },
  ],
});
