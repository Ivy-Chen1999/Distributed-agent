/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

declare const process: { env: Record<string, string | undefined> };

const API = process.env.WOMM_API_URL ?? 'http://localhost:8000';
const PROXIED = ['/runs', '/scenarios', '/system', '/health', '/livez'];
const proxy = Object.fromEntries(PROXIED.map((p) => [p, { target: API, changeOrigin: true }]));

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  build: { outDir: 'dist', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
  },
});
