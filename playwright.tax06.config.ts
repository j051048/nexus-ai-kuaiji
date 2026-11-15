import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testMatch: 'tax06-fees.spec.ts',
  outputDir: 'tmp_test/tax06-results',
});
