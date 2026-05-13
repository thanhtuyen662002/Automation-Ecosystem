import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // changeOrigin: true  → strips the browser Origin header, BE sees 127.0.0.1
      // ws: true            → also proxy WebSocket upgrade requests (used by /api/v1/ws/brain)
      '/api':       { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
      '/pipelines': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/analytics': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/jobs':      { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/tasks':     { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/system':    { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
