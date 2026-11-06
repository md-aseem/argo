import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Argo lives under /argo/ everywhere (see src/argo/main.py), so the UI is built
// and served from that base. The API (src/argo/main.py) runs on :8000 (override
// with ARGO_API); proxying keeps the browser on one origin, so no CORS and SSE
// streams straight through.
export default defineConfig({
  base: '/argo/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/argo/api': process.env.ARGO_API ?? 'http://127.0.0.1:8000' },
  },
})
