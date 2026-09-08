import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { createChoreographyExecutionMetadata } from "./e2e/live-choreography-provenance";

function integerPort(value: string): number {
  if (!/^\d+$/.test(value)) {
    throw new Error(
      "CHOREOGRAPHY_E2E_PORT must be an integer between 1 and 65535",
    );
  }
  const port = Number(value);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error(
      "CHOREOGRAPHY_E2E_PORT must be an integer between 1 and 65535",
    );
  }
  return port;
}

function suiteName(value: string | undefined): "accelerated" | "capture" {
  const suite = value ?? "accelerated";
  if (suite !== "accelerated" && suite !== "capture") {
    throw new Error("CHOREOGRAPHY_E2E_SUITE must be accelerated or capture");
  }
  return suite;
}

const port = integerPort(process.env.CHOREOGRAPHY_E2E_PORT ?? "3104");
const suite = suiteName(process.env.CHOREOGRAPHY_E2E_SUITE);
const baseURL = `http://127.0.0.1:${port}`;
const artifactDir = path.resolve(
  process.env.CHOREOGRAPHY_E2E_ARTIFACT_DIR ?? "../var/live-choreography",
);
const execution = createChoreographyExecutionMetadata();

export default defineConfig({
  testDir: "./e2e",
  testMatch:
    suite === "capture"
      ? "live-choreography-capture.spec.ts"
      : "live-choreography.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: suite === "capture" ? 150_000 : 120_000,
  expect: { timeout: 20_000 },
  captureGitInfo: { commit: false, diff: false },
  metadata: { gate: "1.5", suite, ...execution },
  outputDir: path.join(artifactDir, suite, "test-results"),
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(artifactDir, suite, "report.json") }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    viewport: { width: 1_280, height: 720 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  webServer: {
    command: `MURMUR_E2E_MODE=1 MURMUR_SCENE_LAB=1 npm run dev -- --hostname 127.0.0.1 --port ${port}`,
    url: `${baseURL}/e2e/choreography`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
