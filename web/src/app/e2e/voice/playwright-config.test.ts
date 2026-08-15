import { execFileSync, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

const configPath = fileURLToPath(
  new URL("../../../../playwright.config.ts", import.meta.url)
);
const webRoot = path.dirname(configPath);
const playwrightCliPath = fileURLToPath(
  new URL("../../../../node_modules/@playwright/test/cli.js", import.meta.url)
);
const playwrightModulePath = fileURLToPath(
  new URL(
    "../../../../node_modules/@playwright/test/index.js",
    import.meta.url
  )
);
const audioFixturePath = fileURLToPath(
  new URL(
    "../../../../../tests/fixtures/voice/audio/browser-barge-in.wav",
    import.meta.url
  )
);
const safeReporterPath = fileURLToPath(
  new URL("./playwright-safe-reporter.ts", import.meta.url)
);

function stubRequiredConfigEnvironment(artifactDir: string): void {
  vi.stubEnv("VOICE_E2E_BROWSER_AUDIO_FIXTURE", audioFixturePath);
  vi.stubEnv("VOICE_E2E_ARTIFACT_DIR", artifactDir);
  vi.stubEnv("VOICE_E2E_WEB_URL", "http://127.0.0.1:3100");
  vi.stubEnv("VOICE_E2E_NETWORK", undefined);
  vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", undefined);
  vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", undefined);
  vi.stubEnv("PW_TEST_REPORTER", undefined);
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("Playwright report metadata", () => {
  it("stays allowlisted when GitHub Actions exposes its full CI environment", async () => {
    const temporaryRoot = mkdtempSync(
      path.join(tmpdir(), "murmur-playwright-config-")
    );
    const artifactDir = path.join(temporaryRoot, "artifacts");
    const smokeTestDir = path.join(temporaryRoot, "tests");
    const reportPath = path.join(artifactDir, "report.json");
    const smokeTestPath = path.join(smokeTestDir, "metadata.spec.ts");
    const smokeConfigPath = path.join(temporaryRoot, "playwright.config.ts");
    mkdirSync(artifactDir);
    mkdirSync(smokeTestDir);

    const githubServerUrl = "https://github.example.invalid";
    const githubRepository = "private-owner/private-repository";
    const githubRunId = "2468013579";
    const githubSha = "0123456789abcdef0123456789abcdef01234567";
    const authorizationSecret = "Bearer gha-private-token";
    const candidateSecret = "candidate:private-ice-candidate";
    const sdpSecret = "v=0 private-session-description";
    const gitDiffSecret = "private-git-diff-content";

    stubRequiredConfigEnvironment(artifactDir);
    vi.stubEnv("GITHUB_ACTIONS", "true");
    vi.stubEnv("GITHUB_SERVER_URL", githubServerUrl);
    vi.stubEnv("GITHUB_REPOSITORY", githubRepository);
    vi.stubEnv("GITHUB_RUN_ID", githubRunId);
    vi.stubEnv("GITHUB_SHA", githubSha);
    vi.stubEnv("GITHUB_EVENT_PATH", path.join(temporaryRoot, "event.json"));
    vi.stubEnv("AUTHORIZATION", authorizationSecret);
    vi.stubEnv("ICE_CANDIDATE", candidateSecret);
    vi.stubEnv("RTC_SDP", sdpSecret);
    vi.stubEnv("GIT_DIFF", gitDiffSecret);

    try {
      const { default: config } = await import("../../../../playwright.config");

      expect(config.metadata).toEqual({
        ci: { source: "runner-owned" },
      });
      expect(config.captureGitInfo).toEqual({ commit: false, diff: false });
      expect({
        testDir: config.testDir,
        fullyParallel: config.fullyParallel,
        workers: config.workers,
        retries: config.retries,
        timeout: config.timeout,
        expectTimeout: config.expect?.timeout,
        outputDir: config.outputDir,
        reporter: config.reporter,
        baseURL: config.use?.baseURL,
        permissions: config.use?.permissions,
        trace: config.use?.trace,
        screenshot: config.use?.screenshot,
        video: config.use?.video,
        launchOptions: config.use?.launchOptions,
      }).toEqual({
        testDir: "./e2e",
        fullyParallel: false,
        workers: 1,
        retries: 0,
        timeout: 60_000,
        expectTimeout: 20_000,
        outputDir: path.join(artifactDir, "test-results"),
        reporter: [
          ["line"],
          ["json", { outputFile: reportPath }],
        ],
        baseURL: "http://127.0.0.1:3100",
        permissions: ["microphone"],
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
        launchOptions: {
          ignoreDefaultArgs: ["--mute-audio"],
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            `--use-file-for-fake-audio-capture=${audioFixturePath}%noloop`,
            "--autoplay-policy=no-user-gesture-required",
          ],
        },
      });

      writeFileSync(
        smokeTestPath,
        `import { test } from ${JSON.stringify(playwrightModulePath)};\n` +
          `test("safe report metadata", () => {});\n`,
        "utf8"
      );
      writeFileSync(
        smokeConfigPath,
        `import baseConfig from ${JSON.stringify(configPath)};\n` +
          `export default {\n` +
          `  ...baseConfig,\n` +
          `  testDir: ${JSON.stringify(smokeTestDir)},\n` +
          `  reporter: [["json", { outputFile: ${JSON.stringify(reportPath)} }]],\n` +
          `};\n`,
        "utf8"
      );

      execFileSync(
        process.execPath,
        [
          playwrightCliPath,
          "test",
          smokeTestPath,
          `--config=${smokeConfigPath}`,
        ],
        {
          cwd: webRoot,
          env: process.env,
          encoding: "utf8",
        }
      );

      const reportText = readFileSync(reportPath, "utf8");
      const report = JSON.parse(reportText) as {
        config: { metadata: Record<string, unknown> };
      };
      expect(report.config.metadata).toEqual({
        actualWorkers: 1,
        ci: { source: "runner-owned" },
      });
      expect(Object.keys(report.config.metadata).sort()).toEqual([
        "actualWorkers",
        "ci",
      ]);
      expect(reportText).not.toMatch(/https?:\/\//i);
      expect(reportText).not.toContain(githubRepository);
      expect(reportText).not.toContain(`/actions/runs/${githubRunId}`);
      expect(reportText).not.toContain(githubRunId);
      expect(reportText).not.toContain(githubSha);
      for (const forbiddenMetadataKey of [
        "commitHref",
        "buildHref",
        "prHref",
        "gitCommit",
        "gitDiff",
      ]) {
        expect(reportText).not.toContain(`"${forbiddenMetadataKey}"`);
      }
      expect(reportText).not.toContain(gitDiffSecret);
      expect(reportText).not.toContain(authorizationSecret);
      expect(reportText).not.toContain(candidateSecret);
      expect(reportText).not.toContain(sdpSecret);
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });

  it("uses only the pinned SPKI bypass and ephemeral output in relay mode", async () => {
    const temporaryRoot = mkdtempSync(
      path.join(tmpdir(), "murmur-playwright-relay-config-")
    );
    const artifactDir = path.join(temporaryRoot, "artifacts");
    const spkiPin = Buffer.alloc(32, 0xa5).toString("base64");
    const gatewayIpv4 = "172.28.0.1";
    mkdirSync(artifactDir);
    stubRequiredConfigEnvironment(artifactDir);
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", spkiPin);
    vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", gatewayIpv4);

    try {
      const configModule = await import("../../../../playwright.config");
      const config = configModule.default;
      const spkiArgument = `--ignore-certificate-errors-spki-list=${spkiPin}`;

      expect(config.metadata).toEqual({});
      expect(config.captureGitInfo).toEqual({ commit: false, diff: false });
      expect(config.outputDir).toBe(
        path.join(artifactDir, "relay-ephemeral-output")
      );
      expect(config.preserveOutput).toBe("never");
      expect(config.reporter).toEqual([
        [safeReporterPath, { outputFile: path.join(artifactDir, "report.json") }],
      ]);
      expect(config.use?.trace).toBe("off");
      expect(config.use?.screenshot).toBe("off");
      expect(config.use?.video).toBe("off");
      expect(config.use?.launchOptions).toEqual({
        ignoreDefaultArgs: ["--mute-audio"],
        args: [
          "--use-fake-device-for-media-stream",
          "--use-fake-ui-for-media-stream",
          `--use-file-for-fake-audio-capture=${audioFixturePath}%noloop`,
          "--autoplay-policy=no-user-gesture-required",
          spkiArgument,
        ],
      });
      expect(
        config.use?.launchOptions?.args?.filter((argument) =>
          argument.startsWith("--ignore-certificate-errors")
        )
      ).toEqual([spkiArgument]);

      expect(configModule.isCanonicalSpkiSha256B64(spkiPin)).toBe(true);
      expect(configModule.isCanonicalPrivateGatewayIpv4(gatewayIpv4)).toBe(true);
      expect(JSON.stringify(config)).not.toContain(gatewayIpv4);
      for (const argumentsList of [
        [spkiArgument, "--ignore-certificate-errors"],
        [spkiArgument, "--allow-insecure-localhost"],
        [spkiArgument, "--ignore-ssl-errors=yes"],
        [spkiArgument, spkiArgument],
      ]) {
        expect(() =>
          configModule.validateChromiumCertificateArguments(
            "relay-tls",
            argumentsList,
            spkiPin
          )
        ).toThrow("Chromium certificate bypass configuration is invalid");
      }
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });

  it("leaves the existing direct reporter override behavior unchanged", async () => {
    const temporaryRoot = mkdtempSync(
      path.join(tmpdir(), "murmur-playwright-direct-reporter-")
    );
    const artifactDir = path.join(temporaryRoot, "artifacts");
    mkdirSync(artifactDir);
    stubRequiredConfigEnvironment(artifactDir);
    vi.stubEnv("VOICE_E2E_NETWORK", "direct");
    vi.stubEnv("PW_TEST_REPORTER", "json");

    try {
      const { default: config } = await import("../../../../playwright.config");
      expect(config.reporter).toEqual([
        ["line"],
        ["json", { outputFile: path.join(artifactDir, "report.json") }],
      ]);
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });

  it("accepts only exact network and SPKI environment contracts", async () => {
    const temporaryRoot = mkdtempSync(
      path.join(tmpdir(), "murmur-playwright-env-contract-")
    );
    const artifactDir = path.join(temporaryRoot, "artifacts");
    mkdirSync(artifactDir);
    stubRequiredConfigEnvironment(artifactDir);

    try {
      for (const invalidNetwork of ["", "relay", "DIRECT", "relay-tls "]) {
        vi.stubEnv("VOICE_E2E_NETWORK", invalidNetwork);
        vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", undefined);
        vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", undefined);
        vi.resetModules();
        await expect(import("../../../../playwright.config")).rejects.toThrow(
          "VOICE_E2E_NETWORK is invalid for the isolated RTC proof"
        );
      }

      const secretPin = `${"A".repeat(42)}A=`;
      vi.stubEnv("VOICE_E2E_NETWORK", "direct");
      vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", secretPin);
      vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", undefined);
      vi.resetModules();
      let directError = "";
      try {
        await import("../../../../playwright.config");
      } catch (error) {
        directError = String(error);
      }
      expect(directError).toContain(
        "Chromium certificate bypass configuration is invalid"
      );
      expect(directError).not.toContain(secretPin);

      for (const invalidPin of [
        "",
        "not-base64",
        `${"A".repeat(42)}B=`,
      ]) {
        vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
        vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", invalidPin);
        vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", "172.28.0.1");
        vi.resetModules();
        let relayError = "";
        try {
          await import("../../../../playwright.config");
        } catch (error) {
          relayError = String(error);
        }
        expect(relayError).toContain(
          "Chromium certificate bypass configuration is invalid"
        );
        if (invalidPin) expect(relayError).not.toContain(invalidPin);
      }

      const secretGateway = "172.28.0.1";
      vi.stubEnv("VOICE_E2E_NETWORK", "direct");
      vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", undefined);
      vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", secretGateway);
      vi.resetModules();
      let directGatewayError = "";
      try {
        await import("../../../../playwright.config");
      } catch (error) {
        directGatewayError = String(error);
      }
      expect(directGatewayError).toContain(
        "Relay gateway configuration is invalid"
      );
      expect(directGatewayError).not.toContain(secretGateway);

      for (const invalidGateway of [
        "",
        "172.28.00.1",
        "172.15.0.1",
        "203.0.113.1",
        "172.28.0.0",
        "172.28.0.255",
      ]) {
        vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
        vi.stubEnv("VOICE_E2E_COTURN_SPKI_SHA256_B64", secretPin);
        vi.stubEnv("VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4", invalidGateway);
        vi.resetModules();
        let gatewayError = "";
        try {
          await import("../../../../playwright.config");
        } catch (error) {
          gatewayError = String(error);
        }
        expect(gatewayError).toContain(
          "Relay gateway configuration is invalid"
        );
        if (invalidGateway) expect(gatewayError).not.toContain(invalidGateway);
      }
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });

  it("rejects ambient rich reporters before real relay discovery", () => {
    const temporaryRoot = mkdtempSync(
      path.join(tmpdir(), "murmur-playwright-reporter-override-")
    );
    const customReporterPath = path.join(temporaryRoot, "hostile-reporter.cjs");
    const customReporterMarker = path.join(temporaryRoot, "hostile-loaded.txt");
    const customReporterSource =
      `const fs = require("node:fs");\n` +
      `module.exports = class HostileReporter {\n` +
      `  constructor() { fs.writeFileSync(${JSON.stringify(customReporterMarker)}, "rich output"); }\n` +
      `  printsToStdio() { return true; }\n` +
      `};\n`;
    writeFileSync(customReporterPath, customReporterSource, "utf8");

    try {
      for (const [index, reporterOverride] of [
        "",
        "json",
        customReporterPath,
      ].entries()) {
        const artifactDir = path.join(temporaryRoot, `artifacts-${index}`);
        mkdirSync(artifactDir);
        const result = spawnSync(
          process.execPath,
          [
            playwrightCliPath,
            "test",
            "e2e/voice-pipecat-rtc.spec.ts",
            `--config=${configPath}`,
            "--list",
          ],
          {
            cwd: webRoot,
            env: {
              ...process.env,
              PW_TEST_REPORTER: reporterOverride,
              VOICE_E2E_NETWORK: "relay-tls",
              VOICE_E2E_COTURN_SPKI_SHA256_B64: Buffer.alloc(32).toString(
                "base64"
              ),
              VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4: "172.28.0.1",
              VOICE_E2E_BROWSER_AUDIO_FIXTURE: audioFixturePath,
              VOICE_E2E_ARTIFACT_DIR: artifactDir,
              VOICE_E2E_WEB_URL: "http://127.0.0.1:3100",
            },
            encoding: "utf8",
          }
        );
        const output = `${result.stdout}${result.stderr}`;

        expect(result.status).not.toBe(0);
        expect(output).toContain(
          "Relay Playwright reporter override is forbidden"
        );
        expect(output).not.toContain(`PW_TEST_REPORTER=${reporterOverride}`);
        if (reporterOverride) expect(output).not.toContain(reporterOverride);
        expect(output).not.toContain('"suites"');
        expect(output).not.toContain(
          "real browser media crosses Pipecat SmallWebRTC"
        );
        expect(existsSync(path.join(artifactDir, "report.json"))).toBe(false);
        expect(existsSync(customReporterMarker)).toBe(false);
      }
    } finally {
      rmSync(temporaryRoot, { recursive: true, force: true });
    }
  });
});
