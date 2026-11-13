import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Port 8721 is registered for the lite-eval Vite dev server in the KB ports registry (8720 is the Viewer).
// The build is one JS and one CSS file: `lite-eval report --html` inlines them into a single page
// (design §18.22), so route code splitting is off and dynamic imports are inlined
// (rolldown 1.2.19: `output.codeSplitting: false` replaces the deprecated `inlineDynamicImports`).
export default defineConfig({
  plugins: [tanstackRouter({ target: 'react', autoCodeSplitting: false }), react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 8721,
    strictPort: true,
    host: '127.0.0.1',
    // `pnpm dev` against a running `lite-eval view` on 8720
    proxy: { '/api': 'http://127.0.0.1:8720' },
  },
  build: { rolldownOptions: { output: { codeSplitting: false } } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
