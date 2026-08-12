import fs from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

function absoluteEnv(name: string): string {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path for the isolated RTC proof`);
  }
  return value;
}

const audioFixture = absoluteEnv("VOICE_E2E_BROWSER_AUDIO_FIXTURE");
const artifactDir = absoluteEnv("VOICE_E2E_ARTIFACT_DIR");

if (!fs.statSync(audioFixture).isFile()) {
  throw new Error("VOICE_E2E_BROWSER_AUDIO_FIXTURE must reference a file");
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  outputDir: path.join(artifactDir, "test-results"),
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(artifactDir, "report.json") }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.VOICE_E2E_WEB_URL ?? "http://127.0.0.1:3100",
    permissions: ["microphone"],
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    launchOptions: {
      // Playwright otherwise adds --mute-audio in headless Chromium, which
      // makes a real remote-playback proof observe an intentionally silent sink.
      ignoreDefaultArgs: ["--mute-audio"],
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        `--use-file-for-fake-audio-capture=${audioFixture}%noloop`,
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },
});
