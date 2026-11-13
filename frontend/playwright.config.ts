import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

// The e2e Viewer serves real run records of the fake target (backend tests/e2e/viewer_fixture.py)
// on port 8722 (registered in the KB ports registry), built fresh each run so annotations start
// empty; the same script writes the two HTML exports into .e2e/exports/ (gitignored).
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url))
export const E2E = here('./.e2e')
const PORT = 8722

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1, // one Viewer, one annotations file: tests that write must not race
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    // `pnpm e2e` builds first; CI runs Playwright right after its own build step
    command: [
      `uv run --directory ${here('../backend')} python -m tests.e2e.viewer_fixture`,
      `--root ${E2E}/root --exports ${E2E}/exports --static ${here('./dist')} --port ${PORT}`,
    ].join(' '),
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
