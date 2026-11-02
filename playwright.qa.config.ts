import { defineConfig, devices } from '@playwright/test';
import { config } from './playwright.config';

// Independent, explicit cross-engine gate; standard local runs remain small.
export default defineConfig({
  ...config,
  testMatch: [
    '**/qa-accessibility.spec.ts',
    '**/v03-entry-review.spec.ts',
    '**/dcp-r4-operations.spec.ts',
    '**/dcp-r567-evidence.spec.ts',
    '**/n04-client-signing.spec.ts',
  ],
  grep: undefined,
  timeout: 90_000,
  outputDir: 'tmp_test/qa-browser/results',
  reporter: [['line'], ['json', { outputFile: 'tmp_test/qa-browser/report.json' }]],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
    {
      name: 'mobile-webkit',
      use: { ...devices['iPhone 13'] },
      testMatch: '**/qa-accessibility.spec.ts',
    },
  ],
});
