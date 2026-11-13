import { defineConfig } from '@hey-api/openapi-ts'

// The backend's committed spec is the only contract (frontend AGENTS rule 1); regenerate with
// `pnpm generate-client` after `uv run python scripts/dump_openapi.py` in backend/.
export default defineConfig({
  input: '../backend/openapi.json',
  output: { path: 'src/client' },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
})
