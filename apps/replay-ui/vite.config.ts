import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4173,
    proxy: {
      '/intent': 'http://127.0.0.1:3001',
      '/replay': 'http://127.0.0.1:3001',
      '/scenarios': 'http://127.0.0.1:3001'
    }
  }
})
