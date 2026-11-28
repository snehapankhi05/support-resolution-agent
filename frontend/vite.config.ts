import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy keeps the SPA same-origin with FastAPI — no CORS change required.
const apiTarget = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '^/(auth|orders|tickets|runs|proposals|me|health|escalations)': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
