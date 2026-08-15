import { execFileSync } from "node:child_process";
import {
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

    vi.stubEnv("VOICE_E2E_BROWSER_AUDIO_FIXTURE", audioFixturePath);
    vi.stubEnv("VOICE_E2E_ARTIFACT_DIR", artifactDir);
    vi.stubEnv("VOICE_E2E_WEB_URL", "http://127.0.0.1:3100");
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
});
