import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests (`e2e/*.spec.ts`, IMPLEMENTATION_PLAN.md "No browser E2E"). Runs the real
 * Next.js app; each spec stubs `/api/v1/**` in the browser (`e2e/support/api-stub.ts`), so no Postgres
 * is needed. The healthcare specs route generate/validate to the real worker (`worker_bridge.py`) and,
 * for the medallion spec, Onboarding/STTM calls to the real backend on SQLite (`backend_bridge.py`); those
 * need Python with the backend/worker requirements installed (`PYTHON` overrides the interpreter).
 * `npm run test:e2e`.
 *
 * Browser: Playwright's bundled Chromium by default (`npx playwright install chromium`). Set
 * `PW_CHANNEL=chrome` (or `msedge`) to drive a locally installed browser instead — e.g. where the
 * Playwright CDN download is blocked.
 */
const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  // First `next dev` compile of the page is slow; the wizard itself is fast once loaded.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}) },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
