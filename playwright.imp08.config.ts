import { defineConfig } from '@playwright/test';
import base from './playwright.config';

const webServer = Array.isArray(base.webServer)
  ? base.webServer.map((server, index) =>
      index === 0
        ? { ...server, env: { ...server.env, ACCOUNTING_ASSISTANT_INVESTIGATION: '1' } }
        : server,
    )
  : base.webServer;

export default defineConfig({
  ...base,
  testMatch: ['e2e/imp08-analysis.spec.ts', 'e2e/imp08-analysis-real.spec.ts'],
  webServer,
  reporter: [['line']],
  timeout: 90_000,
});
