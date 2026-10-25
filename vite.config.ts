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
  server: localServer,
  preview: localServer,
  build: { target: 'es2020', sourcemap: false },
});
