import { defineConfig, devices } from '@playwright/test';

// End-to-end suite for the WOMM Console against the real API on a scripted fake LLM
// (src/womm/api/e2e.py). Each `npm run e2e` gets a fresh Postgres database; see web/README.md.

const PG = process.env.WOMM_E2E_PG_URL ?? 'postgresql://womm:womm@localhost:55432';
// Evaluated in the runner and again in every worker: the runner picks the name, workers inherit it.
process.env.WOMM_E2E_DATABASE ??= `womm_e2e_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
process.env.WOMM_E2E_TOKEN ??= 'e2e-console-token-0123456789';
const PORT = Number(process.env.WOMM_E2E_PORT ?? 8765);
const LIVE = process.env.WOMM_E2E_LIVE === '1';
const BASE_URL = LIVE ? (process.env.WOMM_E2E_LIVE_URL ?? '') : `http://localhost:${PORT}`;
const DATABASE_URL = `${PG}/${process.env.WOMM_E2E_DATABASE}`;
process.env.WOMM_E2E_DATABASE_URL = DATABASE_URL;

export default defineConfig({
  testDir: './e2e',
  // WOMM_E2E_LIVE=1 runs only the live spec against a real server (see e2e/live.spec.ts).
  testMatch: LIVE ? /live\.spec\.ts$/ : /.*\.spec\.ts$/,
  // One shared server and database; the console always opens the newest run, so specs run one
  // at a time to keep "the latest run" deterministic.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  globalSetup: LIVE ? undefined : './e2e/global-setup.ts',
  globalTeardown: LIVE ? undefined : './e2e/global-teardown.ts',
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: LIVE
    ? undefined
    : {
        // Playwright starts the webServer before globalSetup, so the fresh database is created
        // here; globalTeardown drops it.
        command: [
          'npm --prefix web run build',
          'uv run python -m womm.api.e2e create-db',
          `uv run uvicorn --factory womm.api.e2e:create_e2e_app --port ${PORT} --log-level warning`,
        ].join(' && '),
        cwd: '..',
        url: `${BASE_URL}/livez`,
        reuseExistingServer: false,
        timeout: 180_000,
        stdout: 'ignore',
        stderr: 'pipe',
        env: {
          WOMM_API_TOKEN: process.env.WOMM_E2E_TOKEN,
          DATABASE_URL,
          WOMM_E2E_DELAY_S: process.env.WOMM_E2E_DELAY_S ?? '0.8',
          LANGSMITH_TRACING: 'false',
          LANGCHAIN_TRACING_V2: 'false',
        },
      },
});
