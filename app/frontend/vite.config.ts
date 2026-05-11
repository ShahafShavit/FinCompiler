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
      // Only the queue JSON — do not proxy `/categorize/` or Vite would never serve the SPA
      // and `/assets/*` would resolve to dev 404/HTML (wrong MIME for JS/CSS).
      '/categorize/api': PY_BACKEND,
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
});
