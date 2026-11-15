import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testMatch: 'tax05-large-voucher.spec.ts',
  outputDir: 'tmp_test/tax05-results',
});
