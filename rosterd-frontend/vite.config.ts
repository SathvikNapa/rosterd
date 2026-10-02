import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // 5432 is reserved for Postgres (see docker-compose.yml), 8000/8100/8300
    // for ingestion/kernel/coordinator. 5173 is Vite's default and free.
    port: 5173,
    host: true,
  },
  build: { outDir: 'dist', sourcemap: true },
});
