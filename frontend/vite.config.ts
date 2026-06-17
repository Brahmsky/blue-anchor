import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:9733';
const buildOutDir = process.env.VITE_BUILD_OUT_DIR || '../minirag/api/static';

export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/documents': proxyTarget,
      '/chat': proxyTarget,
      '/query': proxyTarget,
      '/aliases': proxyTarget,
      '/graph': proxyTarget,
      '/graphs': proxyTarget,
      '/health': proxyTarget,
      '/system': proxyTarget,
      '/api': proxyTarget,
      '^/pipeline/': {
        target: proxyTarget,
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: buildOutDir,
    emptyOutDir: false
  }
});
