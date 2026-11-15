import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig(base, {
  testMatch: 'tax10-costs.spec.ts',
  outputDir: 'tmp_test/tax10-results',
});
