import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Port 8721 is registered for the lite-eval Vite dev server in the KB ports registry (8720 is the Viewer).
export default defineConfig({
  plugins: [react()],
  server: { port: 8721, strictPort: true, host: '127.0.0.1' },
  test: {
    environment: 'jsdom',
  },
})
