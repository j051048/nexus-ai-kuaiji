import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testMatch: 'tax04-sources.spec.ts',
  outputDir: 'tmp_test/tax04-results',
});
