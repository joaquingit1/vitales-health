import { defineConfig, devices } from "@playwright/test";

const hub = "http://127.0.0.1:8765";
const app = "http://127.0.0.1:5175";

export default defineConfig({
  testDir: "./src",
  testMatch: "features/**/*.spec.ts",
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 60_000,
  outputDir: "node_modules/.playwright/results",
  reporter: "list",
  use: { baseURL: app, trace: "retain-on-failure" },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { ignoreDefaultArgs: ["--disable-back-forward-cache"] },
      },
    },
  ],
  webServer: [
    {
      command: "uv run --project ../../hub python ../../hub/tests/privacy/e02_station.py --port 8765",
      url: `${hub}/v1/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: "pnpm exec vite --host 127.0.0.1 --port 5175 --strictPort",
      url: app,
      env: { CREW_HUB_ORIGIN: hub },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
