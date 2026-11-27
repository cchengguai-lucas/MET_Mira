import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5180,
    proxy: {
      '/api': 'http://localhost:8800',
    },
  },
  build: {
    chunkSizeWarningLimit: 900,
    // web/dist is served live. Vite's default emptyOutDir=true creates a
    // deployment window where tabs opened on the previous build request lazy
    // chunks that have just been deleted and render a blank page. Keep hashed
    // assets across builds; the post-build pruner removes old generations
    // after a grace period.
    emptyOutDir: false,
  },
})
