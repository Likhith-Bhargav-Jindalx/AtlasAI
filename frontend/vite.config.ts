/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Fixed, dedicated port — this machine runs other projects' dev servers
  // on Vite's default 5173, and the backend's CORS allowlist is keyed to
  // this exact origin (see engine/src/atlas_engine/api.py), so letting
  // Vite silently fall back to a different port would break API calls.
  server: { port: 5183, strictPort: true },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    globals: true,
  },
})
