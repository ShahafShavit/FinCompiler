import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const PY_BACKEND = 'http://127.0.0.1:8780';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': PY_BACKEND,
      '/heatmap/api': PY_BACKEND,
      '/heatmap/detail': PY_BACKEND,
      '/heatmap/heatmap_page_script.js': PY_BACKEND,
      '/categorize': PY_BACKEND,
      '/holdings': PY_BACKEND,
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
});
