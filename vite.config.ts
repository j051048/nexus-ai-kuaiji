import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';

export default defineConfig({
  // Never load the enterprise platform's inherited environment or credentials.
  envDir: './config',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 8080,
    strictPort: true,
    proxy: { '/api': { target: process.env.ACCOUNTING_API_PROXY || 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  build: { target: 'es2020', sourcemap: false },
});
