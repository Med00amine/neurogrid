import { defineConfig, devices } from "@playwright/test";

// The stack (fresh database + API serving the built UI + worker + fixture company website) is started for us.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:8765", trace: "retain-on-failure", screenshot: "only-on-failure", viewport: { width: 1360, height: 900 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 900 } } }],
  webServer: {
    command: "../backend/.venv/bin/python ../backend/scripts/e2e_stack.py",
    url: "http://127.0.0.1:8765/api/health/ready",
    timeout: 90_000,
    reuseExistingServer: false,
    stdout: "pipe",
  },
});
