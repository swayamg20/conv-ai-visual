import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const configuredPort = process.env.SCENE_E2E_PORT ?? "3102";
const port = Number(configuredPort);
if (!/^\d+$/.test(configuredPort) || !Number.isSafeInteger(port) || port < 1 || port > 65_535) {
  throw new Error("SCENE_E2E_PORT must be an integer between 1 and 65535");
}
const baseURL = `http://127.0.0.1:${port}`;
const artifactDir = path.resolve(
  process.env.SCENE_E2E_ARTIFACT_DIR ?? "../var/scene-e2e"
);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "live-scene.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  captureGitInfo: { commit: false, diff: false },
  outputDir: path.join(artifactDir, "test-results"),
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(artifactDir, "report.json") }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  webServer: {
    command: `MURMUR_SCENE_LAB=1 npm run dev -- --hostname 127.0.0.1 --port ${port}`,
    url: `${baseURL}/labs/live-scene`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
