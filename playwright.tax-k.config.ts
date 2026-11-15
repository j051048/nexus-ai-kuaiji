import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, { testMatch: 'tax-k-oa.spec.ts', outputDir: 'tmp_test/tax-k-results' });
