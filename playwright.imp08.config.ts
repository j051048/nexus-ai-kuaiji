import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({
  ...base,
  testMatch: 'e2e/imp08-analysis.spec.ts',
  reporter: [['line']],
  timeout: 90_000,
});
