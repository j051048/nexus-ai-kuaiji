import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';

const localServer = {
  host: '127.0.0.1',
  port: 8080,
  strictPort: true,
  proxy: {
    '/api': {
      target: process.env.ACCOUNTING_API_PROXY || 'http://127.0.0.1:8000',
      changeOrigin: false,
    },
  },
};

export default defineConfig({
  // E2E cannot load file-only credentials that are absent from process.env.
  envDir: process.env.E2E_LOCAL_ISOLATION === '1' ? false : './config',
  plugins: [react()],
  // Frozen E2E candidates do not hot-reload. Test artifacts and parallel local
  // worktrees must not trigger reloads or consume watcher resources mid-case.
  server: {
    ...localServer,
    ...(process.env.E2E_LOCAL_ISOLATION === '1' ? { watch: null } : {}),
  },
  preview: localServer,
  build: {
    target: 'es2020',
    sourcemap: false,
    // The real component harness is built only by the isolated local E2E runner.
    // Ordinary production builds must never contain synthetic identities or pages.
    ...(process.env.E2E_LOCAL_ISOLATION === '1'
      ? {
          rollupOptions: {
            input: {
              main: 'index.html',
              n04Signing: 'e2e/fixtures/n04-package-harness.html',
              p02Close: 'e2e/fixtures/p02-close-harness.html',
            },
          },
        }
      : {}),
  },
});
