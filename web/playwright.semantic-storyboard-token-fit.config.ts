import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "semantic-storyboard-token-fit.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  captureGitInfo: { commit: false, diff: false },
  metadata: { gate: "1.8", suite: "token-fit" },
  outputDir: path.resolve("../var/semantic-storyboard-token-fit"),
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "UTC",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
});
