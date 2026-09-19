import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { createChoreographyExecutionMetadata } from "./e2e/live-choreography-provenance";

function e2ePort(value: string): number {
  if (!/^\d+$/.test(value)) {
    throw new Error("SEMANTIC_STORYBOARD_E2E_PORT must be an integer");
  }
  const port = Number(value);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error("SEMANTIC_STORYBOARD_E2E_PORT must be between 1 and 65535");
  }
  return port;
}

type SemanticStoryboardE2ESuite = "accelerated" | "capture" | "product-smoke";

function suiteName(value: string | undefined): SemanticStoryboardE2ESuite {
  const suite = value ?? "accelerated";
  if (
    suite !== "accelerated" &&
    suite !== "capture" &&
    suite !== "product-smoke"
  ) {
    throw new Error(
      "SEMANTIC_STORYBOARD_E2E_SUITE must be accelerated, capture, or product-smoke",
    );
  }
  return suite;
}

const port = e2ePort(process.env.SEMANTIC_STORYBOARD_E2E_PORT ?? "3107");
const baseURL = `http://127.0.0.1:${port}`;
const suite = suiteName(process.env.SEMANTIC_STORYBOARD_E2E_SUITE);
const artifactDir = path.resolve(
  process.env.SEMANTIC_STORYBOARD_E2E_OUTPUT_DIR ??
    "../var/semantic-storyboard-e2e",
);
const suiteArtifactDir = path.join(artifactDir, suite);

if (
  process.env.SEMANTIC_STORYBOARD_EXPECTED_SHA &&
  process.env.CHOREOGRAPHY_EXPECTED_SHA &&
  process.env.SEMANTIC_STORYBOARD_EXPECTED_SHA !==
    process.env.CHOREOGRAPHY_EXPECTED_SHA
) {
  throw new Error(
    "SEMANTIC_STORYBOARD_EXPECTED_SHA conflicts with CHOREOGRAPHY_EXPECTED_SHA",
  );
}
if (process.env.SEMANTIC_STORYBOARD_EXPECTED_SHA) {
  process.env.CHOREOGRAPHY_EXPECTED_SHA =
    process.env.SEMANTIC_STORYBOARD_EXPECTED_SHA;
}
const execution = createChoreographyExecutionMetadata();

const firebaseE2EEnv = [
  `NEXT_PUBLIC_API_URL=${baseURL}`,
  "NEXT_PUBLIC_FIREBASE_API_KEY=test-api-key",
  "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=test.firebaseapp.com",
  "NEXT_PUBLIC_FIREBASE_PROJECT_ID=test-project",
  "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=test-project.appspot.com",
  "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=1234567890",
  "NEXT_PUBLIC_FIREBASE_APP_ID=1:1234567890:web:test",
].join(" ");

const testMatch: Readonly<Record<SemanticStoryboardE2ESuite, string>> = {
  accelerated: "semantic-storyboard.spec.ts",
  capture: "semantic-storyboard-capture.spec.ts",
  "product-smoke": "semantic-storyboard-product-auth.spec.ts",
};

export default defineConfig({
  testDir: "./e2e",
  testMatch: testMatch[suite],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: suite === "capture" ? 150_000 : 90_000,
  expect: { timeout: 20_000 },
  captureGitInfo: { commit: false, diff: false },
  metadata: {
    gate: "1.8",
    protocol: "projectile_comparison_storyboard_v1",
    suite,
    ...execution,
  },
  outputDir: path.join(suiteArtifactDir, "test-results"),
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(suiteArtifactDir, "report.json") }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    viewport: { width: 1_280, height: 720 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  webServer: {
    command: `${firebaseE2EEnv} MURMUR_E2E_MODE=1 MURMUR_SCENE_LAB=1 npm run dev -- --hostname 127.0.0.1 --port ${port}`,
    url: `${baseURL}/e2e/semantic-storyboard`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
