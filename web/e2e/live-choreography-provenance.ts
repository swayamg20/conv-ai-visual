import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import { chromium, type Browser, type FullConfig } from "@playwright/test";
import playwrightPackage from "@playwright/test/package.json";

const E2E_ROOT = __dirname;
const REPOSITORY_ROOT = path.resolve(E2E_ROOT, "..", "..");
const SHA1_PATTERN = /^[a-f0-9]{40}$/;
const BROWSER_VERSION_PATTERN = /(?:^|\s)(\d+(?:\.\d+){3})$/;
const NEXT_ENV_PATH = "web/next-env.d.ts";
const RELEVANT_SOURCE_PATHS = [
  "backend/murmur",
  "scripts/generate_live_choreography_fixture.py",
  "web",
  ".github/workflows/ci.yml",
] as const;

export interface ChoreographySourceObservation {
  readonly gitCommit: string;
  readonly gitTree: string;
}

export interface ChoreographyEnvironmentObservation {
  readonly nodeVersion: string;
  readonly platform: NodeJS.Platform;
  readonly arch: string;
  readonly playwrightVersion: string;
  readonly browserName: "chromium";
  readonly browserVersion: string;
}

export interface ChoreographyExecutionObservation {
  readonly source: ChoreographySourceObservation;
  readonly environment: ChoreographyEnvironmentObservation;
}

function commandRaw(executable: string, arguments_: readonly string[]): string {
  try {
    return execFileSync(executable, arguments_, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const stderr =
      error && typeof error === "object" && "stderr" in error
        ? String(error.stderr).trim()
        : "";
    throw new Error(
      `${executable} ${arguments_.join(" ")} failed${stderr ? `: ${stderr}` : ""}`,
    );
  }
}

function command(executable: string, arguments_: readonly string[]): string {
  return commandRaw(executable, arguments_).trim();
}

function expectedNextDevContents(tracked: string): string | null {
  const replacements = [
    [
      'import "./.next/types/routes.d.ts";',
      'import "./.next/dev/types/routes.d.ts";',
    ],
    [
      'import "./.next/types/root-params.d.ts";',
      'import "./.next/dev/types/root-params.d.ts";',
    ],
  ] as const;
  let expected = tracked;
  for (const [source, generated] of replacements) {
    if (expected.split(source).length !== 2) return null;
    expected = expected.replace(source, generated);
  }
  return expected;
}

function assertRelevantSourcesCommitted(): void {
  const status = commandRaw("git", [
    "-C",
    REPOSITORY_ROOT,
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
    "--",
    ...RELEVANT_SOURCE_PATHS,
  ]).replace(/\n$/, "");
  if (!status) return;

  const nextEnvStatus = ` M ${NEXT_ENV_PATH}`;
  let dirtyEntries = status.split("\n");
  if (dirtyEntries.includes(nextEnvStatus)) {
    const tracked = commandRaw("git", [
      "-C",
      REPOSITORY_ROOT,
      "show",
      `HEAD:${NEXT_ENV_PATH}`,
    ]);
    const working = readFileSync(
      path.join(REPOSITORY_ROOT, NEXT_ENV_PATH),
      "utf8",
    );
    if (working === expectedNextDevContents(tracked)) {
      dirtyEntries = dirtyEntries.filter((entry) => entry !== nextEnvStatus);
    }
  }
  if (dirtyEntries.length > 0) {
    throw new Error(
      `Live choreography evidence requires committed sources:\n${dirtyEntries.join("\n")}`,
    );
  }
}

function fullSha(value: string, label: string): string {
  if (!SHA1_PATTERN.test(value)) {
    throw new Error(`${label} must be a full lowercase 40-character SHA-1`);
  }
  return value;
}

function gitObject(revision: "HEAD^{commit}" | "HEAD^{tree}"): string {
  return fullSha(
    command("git", ["-C", REPOSITORY_ROOT, "rev-parse", "--verify", revision]),
    `git ${revision}`,
  );
}

function installedChromiumVersion(): string {
  const output = command(chromium.executablePath(), ["--version"]);
  const version = output.match(BROWSER_VERSION_PATTERN)?.[1];
  if (!version) {
    throw new Error(
      `Could not read Chromium version from ${JSON.stringify(output)}`,
    );
  }
  return version;
}

export function createChoreographyExecutionMetadata(): ChoreographyExecutionObservation {
  assertRelevantSourcesCommitted();
  const gitCommit = gitObject("HEAD^{commit}");
  const gitTree = gitObject("HEAD^{tree}");
  const expectedSha =
    process.env.CHOREOGRAPHY_EXPECTED_SHA ?? process.env.GITHUB_SHA;
  const expectedShaLabel = process.env.CHOREOGRAPHY_EXPECTED_SHA !== undefined
    ? "CHOREOGRAPHY_EXPECTED_SHA"
    : "GITHUB_SHA";
  if (expectedSha !== undefined) {
    fullSha(expectedSha, expectedShaLabel);
    if (expectedSha !== gitCommit) {
      throw new Error(
        `${expectedShaLabel} ${expectedSha} does not match checked-out HEAD ${gitCommit}`,
      );
    }
  }

  return Object.freeze({
    source: Object.freeze({ gitCommit, gitTree }),
    environment: Object.freeze({
      nodeVersion: process.version,
      platform: process.platform,
      arch: process.arch,
      playwrightVersion: playwrightPackage.version,
      browserName: "chromium" as const,
      browserVersion: installedChromiumVersion(),
    }),
  });
}

function exactObject(
  value: unknown,
  keys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} metadata must be an object`);
  }
  const record = value as Record<string, unknown>;
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw new Error(`${label} metadata has an unexpected shape`);
  }
  return record;
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} metadata must be a non-empty string`);
  }
  return value;
}

/** Bind report metadata to the exact browser binary launched for this worker. */
export function observeChoreographyExecution(
  config: FullConfig,
  browser: Browser,
): ChoreographyExecutionObservation {
  const source = exactObject(
    config.metadata.source,
    ["gitCommit", "gitTree"],
    "source",
  );
  const environment = exactObject(
    config.metadata.environment,
    [
      "nodeVersion",
      "platform",
      "arch",
      "playwrightVersion",
      "browserName",
      "browserVersion",
    ],
    "environment",
  );
  const gitCommit = fullSha(
    nonEmptyString(source.gitCommit, "source.gitCommit"),
    "source.gitCommit",
  );
  const gitTree = fullSha(
    nonEmptyString(source.gitTree, "source.gitTree"),
    "source.gitTree",
  );
  const browserName = browser.browserType().name();
  const browserVersion = browser.version();
  if (browserName !== "chromium") {
    throw new Error(`Expected Chromium, received ${browserName}`);
  }
  if (environment.browserName !== browserName) {
    throw new Error("Launched browser name does not match report metadata");
  }
  if (environment.browserVersion !== browserVersion) {
    throw new Error("Launched Chromium version does not match report metadata");
  }

  return Object.freeze({
    source: Object.freeze({ gitCommit, gitTree }),
    environment: Object.freeze({
      nodeVersion: nonEmptyString(
        environment.nodeVersion,
        "environment.nodeVersion",
      ),
      platform: nonEmptyString(
        environment.platform,
        "environment.platform",
      ) as NodeJS.Platform,
      arch: nonEmptyString(environment.arch, "environment.arch"),
      playwrightVersion: nonEmptyString(
        environment.playwrightVersion,
        "environment.playwrightVersion",
      ),
      browserName,
      browserVersion,
    }),
  });
}
