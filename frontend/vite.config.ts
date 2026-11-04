import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Argo API (src/main.py) runs on :8000 (override with ARGO_API). Proxying
// keeps the browser on one origin, so no CORS and SSE streams straight through.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': process.env.ARGO_API ?? 'http://127.0.0.1:8000' },
  },
})
