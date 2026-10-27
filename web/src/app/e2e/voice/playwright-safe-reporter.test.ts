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

import type {
  FullConfig,
  FullResult,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";
import { afterEach, describe, expect, it } from "vitest";

import RelaySafeReporter, {
  PIPECAT_RELAY_SPEC_ID,
} from "./playwright-safe-reporter";

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

function hostileObject<T extends object>(safe: Partial<T>): T {
  const target = { ...safe } as object;
  for (const key of [
    "annotations",
    "attachments",
    "config",
    "errors",
    "location",
    "metadata",
    "output",
    "parent",
    "path",
    "stderr",
    "stdout",
    "title",
  ]) {
    Object.defineProperty(target, key, {
      configurable: true,
      get() {
        throw new Error(`retained hostile ${key} secret`);
      },
    });
  }
  return target as T;
}

describe("relay-only Playwright reporter", () => {
  it("writes only aggregate policy while requiring runner-owned output cleanup", () => {
    const root = mkdtempSync(path.join(tmpdir(), "murmur-relay-reporter-"));
    temporaryRoots.push(root);
    const outputFile = path.join(root, "artifact", "report.json");
    const lastRunFile = path.join(
      root,
      "artifact",
      "relay-ephemeral-output",
      ".last-run.json"
    );
    mkdirSync(path.dirname(lastRunFile), { recursive: true });
    writeFileSync(lastRunFile, "retained hostile Playwright output secret", "utf8");
    const reporter = new RelaySafeReporter({
      outputFile,
      configDir: "retained hostile config path secret",
      _mode: "test",
      _commandHash: "retained hostile command secret",
    } as unknown as { outputFile: string });
    const testCase = hostileObject<TestCase>({});
    const suite = hostileObject<Suite>({
      allTests: () => [testCase, testCase],
    });

    reporter.onBegin?.(hostileObject<FullConfig>({}), suite);
    reporter.onTestEnd?.(
      testCase,
      hostileObject<TestResult>({ status: "passed" })
    );
    reporter.onTestEnd?.(
      testCase,
      hostileObject<TestResult>({ status: "failed" })
    );
    reporter.onEnd?.(hostileObject<FullResult>({ status: "failed" }));

    const reportText = readFileSync(outputFile, "utf8");
    expect(JSON.parse(reportText)).toEqual({
      schema_version: 1,
      status: "failed",
      spec_id: PIPECAT_RELAY_SPEC_ID,
      pass_counts: {
        tests_discovered: 2,
        tests_passed: 1,
      },
      retention_policy: {
        rich_reporters_disabled: true,
        media_capture_disabled: true,
        reporter_stdio_disabled: true,
        runner_cleanup_required: true,
      },
    });
    expect(Object.keys(JSON.parse(reportText) as object).sort()).toEqual(
      [
        "pass_counts",
        "retention_policy",
        "schema_version",
        "spec_id",
        "status",
      ].sort()
    );
    expect(reportText).not.toContain("secret");
    expect(reportText).not.toContain(root);
    expect(reportText).not.toContain("no_retained_output");
    expect(existsSync(lastRunFile)).toBe(true);
    expect(reporter.printsToStdio?.()).toBe(true);
  });

  it("rejects unsafe configuration and an existing report with fixed errors", () => {
    for (const options of [
      {},
      { outputFile: "relative/report.json" },
      { outputFile: "/tmp/report.json", extra: true },
    ]) {
      expect(() =>
        new RelaySafeReporter(options as { outputFile: string })
      ).toThrow("Relay safe reporter configuration is invalid");
    }

    const root = mkdtempSync(path.join(tmpdir(), "murmur-relay-reporter-"));
    temporaryRoots.push(root);
    const outputFile = path.join(root, "report.json");
    const reporter = new RelaySafeReporter({ outputFile });
    reporter.onBegin?.(
      {} as FullConfig,
      { allTests: () => [] } as unknown as Suite
    );
    reporter.onEnd?.({ status: "passed" } as FullResult);

    const second = new RelaySafeReporter({ outputFile });
    expect(() => second.onEnd?.({ status: "passed" } as FullResult)).toThrow(
      "Relay safe reporter could not write its allowlisted result"
    );
  });
});
