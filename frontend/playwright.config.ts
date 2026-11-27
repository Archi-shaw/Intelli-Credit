import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the real stack: Next.js (3000) → FastAPI (8001) → PostgreSQL.
 * Prerequisites: database migrated and seeded (`python -m app.cli seed`). Existing servers are reused;
 * otherwise Playwright starts them.
 */
// By default the suite builds and serves a production bundle on :3100 (dev-mode on-demand compilation is too
// slow and non-deterministic for browser tests). Set E2E_BASE_URL to test an already-running deployment.
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3100";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: { baseURL: BASE, trace: "retain-on-failure", screenshot: "only-on-failure", acceptDownloads: true, viewport: { width: 1440, height: 900 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: process.env.E2E_BASE_URL ? undefined : [
    { command: "../backend/.venv/bin/uvicorn app.main:app --app-dir ../backend --port 8001", url: "http://127.0.0.1:8001/ready", reuseExistingServer: true, timeout: 60_000 },
    { command: "NEXT_DIST_DIR=.next-e2e npx next build && NEXT_DIST_DIR=.next-e2e npx next start -p 3100", url: "http://localhost:3100/login", reuseExistingServer: true, timeout: 400_000 },
  ],
});
