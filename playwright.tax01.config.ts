import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testMatch: 'tax01-pilot.spec.ts',
  outputDir: 'tmp_test/taxi-pilot-audit-20261110/results',
});
