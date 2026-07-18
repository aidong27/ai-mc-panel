import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const port = 18173;
const baseURL = `http://${host}:${port}`;
const fixturePassword = ["browser", "fixture", "only", "2026"].join("-");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  timeout: 30_000,
  expect: { timeout: 6_000 },
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "uv run --project ../api python ../../tests/e2e/run_panel.py",
    env: {
      MC_PANEL_E2E_HOST: host,
      MC_PANEL_E2E_PORT: String(port),
      MC_PANEL_E2E_PASSWORD: fixturePassword,
    },
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
