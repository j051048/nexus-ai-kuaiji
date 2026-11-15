import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig(base, {
  testMatch: 'tax-f.spec.ts',
  outputDir: 'tmp_test/tax-f-results',
});
