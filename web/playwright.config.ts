import fs from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { isCanonicalPrivateIpv4 } from "./src/app/e2e/voice/rtc-diagnostics";

export type PlaywrightRtcNetworkMode = "direct" | "relay-tls";

const CERTIFICATE_BYPASS_ERROR =
  "Chromium certificate bypass configuration is invalid";
const COTURN_SPKI_ENV = "VOICE_E2E_COTURN_SPKI_SHA256_B64";
const COTURN_GATEWAY_ENV = "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4";
const GATEWAY_ERROR = "Relay gateway configuration is invalid";
const REPORTER_OVERRIDE_ERROR =
  "Relay Playwright reporter override is forbidden";
const SPKI_ARGUMENT_PREFIX = "--ignore-certificate-errors-spki-list=";
const SPKI_SHA256_B64 = /^[A-Za-z0-9+/]{43}=$/;

export function parsePlaywrightRtcNetworkMode(
  value: string | undefined
): PlaywrightRtcNetworkMode {
  if (value === undefined || value === "direct") return "direct";
  if (value === "relay-tls") return "relay-tls";
  throw new Error("VOICE_E2E_NETWORK is invalid for the isolated RTC proof");
}

export function isCanonicalSpkiSha256B64(value: unknown): value is string {
  if (typeof value !== "string" || !SPKI_SHA256_B64.test(value)) return false;
  const decoded = Buffer.from(value, "base64");
  return decoded.length === 32 && decoded.toString("base64") === value;
}

export const isCanonicalPrivateGatewayIpv4 = isCanonicalPrivateIpv4;

function isCertificateBypassArgument(argument: string): boolean {
  return (
    argument.startsWith("--ignore-certificate-errors") ||
    argument.startsWith("--ignore-ssl-errors") ||
    argument.startsWith("--allow-insecure-localhost")
  );
}

export function validateChromiumCertificateArguments(
  network: PlaywrightRtcNetworkMode,
  argumentsList: readonly string[],
  spkiPin: string | undefined
): void {
  const certificateArguments = argumentsList.filter(isCertificateBypassArgument);
  if (network === "direct") {
    if (spkiPin !== undefined || certificateArguments.length !== 0) {
      throw new Error(CERTIFICATE_BYPASS_ERROR);
    }
    return;
  }
  if (
    !isCanonicalSpkiSha256B64(spkiPin) ||
    certificateArguments.length !== 1 ||
    certificateArguments[0] !== `${SPKI_ARGUMENT_PREFIX}${spkiPin}`
  ) {
    throw new Error(CERTIFICATE_BYPASS_ERROR);
  }
}

function absoluteEnv(name: string): string {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path for the isolated RTC proof`);
  }
  return value;
}

const audioFixture = absoluteEnv("VOICE_E2E_BROWSER_AUDIO_FIXTURE");
const artifactDir = absoluteEnv("VOICE_E2E_ARTIFACT_DIR");
const network = parsePlaywrightRtcNetworkMode(process.env.VOICE_E2E_NETWORK);
const spkiPin = process.env[COTURN_SPKI_ENV];
const coturnGatewayIpv4 = process.env[COTURN_GATEWAY_ENV];

if (network === "relay-tls" && process.env.PW_TEST_REPORTER !== undefined) {
  throw new Error(REPORTER_OVERRIDE_ERROR);
}

if (!fs.statSync(audioFixture).isFile()) {
  throw new Error("VOICE_E2E_BROWSER_AUDIO_FIXTURE must reference a file");
}

if (network === "direct" && spkiPin !== undefined) {
  throw new Error(CERTIFICATE_BYPASS_ERROR);
}
if (network === "direct" && coturnGatewayIpv4 !== undefined) {
  throw new Error(GATEWAY_ERROR);
}
if (network === "relay-tls" && !isCanonicalSpkiSha256B64(spkiPin)) {
  throw new Error(CERTIFICATE_BYPASS_ERROR);
}
if (
  network === "relay-tls" &&
  !isCanonicalPrivateGatewayIpv4(coturnGatewayIpv4)
) {
  throw new Error(GATEWAY_ERROR);
}

const launchArguments = [
  "--use-fake-device-for-media-stream",
  "--use-fake-ui-for-media-stream",
  `--use-file-for-fake-audio-capture=${audioFixture}%noloop`,
  "--autoplay-policy=no-user-gesture-required",
  ...(network === "relay-tls"
    ? [`${SPKI_ARGUMENT_PREFIX}${spkiPin}`]
    : []),
];
validateChromiumCertificateArguments(network, launchArguments, spkiPin);

const directOutputDir = path.join(artifactDir, "test-results");
const relayOutputDir = path.join(
  artifactDir,
  "relay-ephemeral-output",
  "playwright-results"
);
const reportPath = path.join(artifactDir, "report.json");
const safeReporterPath = path.join(
  process.cwd(),
  "src/app/e2e/voice/playwright-safe-reporter.ts"
);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  metadata:
    network === "direct" ? { ci: { source: "runner-owned" } } : {},
  captureGitInfo: { commit: false, diff: false },
  outputDir: network === "direct" ? directOutputDir : relayOutputDir,
  reporter:
    network === "direct"
      ? [
          ["line"],
          ["json", { outputFile: reportPath }],
        ]
      : [[safeReporterPath, { outputFile: reportPath }]],
  ...(network === "relay-tls" ? { preserveOutput: "never" as const } : {}),
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.VOICE_E2E_WEB_URL ?? "http://127.0.0.1:3100",
    permissions: ["microphone"],
    trace: network === "direct" ? "retain-on-failure" : "off",
    screenshot: network === "direct" ? "only-on-failure" : "off",
    video: network === "direct" ? "retain-on-failure" : "off",
    launchOptions: {
      // Playwright otherwise adds --mute-audio in headless Chromium, which
      // makes a real remote-playback proof observe an intentionally silent sink.
      ignoreDefaultArgs: ["--mute-audio"],
      args: launchArguments,
    },
  },
});
