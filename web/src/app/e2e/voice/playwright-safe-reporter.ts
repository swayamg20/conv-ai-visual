import fs from "node:fs";
import path from "node:path";

import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

export const PIPECAT_RELAY_SPEC_ID = "voice-pipecat-rtc-relay-tls";

export interface RelaySafeReporterOptions {
  readonly outputFile: string;
}

interface RelaySafeReport {
  readonly schema_version: 1;
  readonly status: FullResult["status"];
  readonly spec_id: typeof PIPECAT_RELAY_SPEC_ID;
  readonly pass_counts: {
    readonly tests_discovered: number;
    readonly tests_passed: number;
  };
  readonly retention_policy: {
    readonly rich_reporters_disabled: true;
    readonly media_capture_disabled: true;
    readonly reporter_stdio_disabled: true;
    readonly runner_cleanup_required: true;
  };
}

const WRITE_ERROR = "Relay safe reporter could not write its allowlisted result";
const PLAYWRIGHT_INTERNAL_OPTION_KEYS = new Set([
  "_commandHash",
  "_mode",
  "configDir",
]);

function reporterOutputPath(options: RelaySafeReporterOptions): string {
  try {
    if (typeof options !== "object" || options === null) {
      throw new Error("invalid");
    }
    const keys = Reflect.ownKeys(options);
    if (
      keys.some(
        (key) =>
          typeof key !== "string" ||
          (key !== "outputFile" && !PLAYWRIGHT_INTERNAL_OPTION_KEYS.has(key))
      )
    ) {
      throw new Error("invalid");
    }
    const outputDescriptor = Object.getOwnPropertyDescriptor(
      options,
      "outputFile"
    );
    if (
      outputDescriptor === undefined ||
      !("value" in outputDescriptor) ||
      typeof outputDescriptor.value !== "string" ||
      !path.isAbsolute(outputDescriptor.value)
    ) {
      throw new Error("invalid");
    }
    return outputDescriptor.value;
  } catch {
    throw new Error("Relay safe reporter configuration is invalid");
  }
}

function writeExclusiveJson(outputFile: string, report: RelaySafeReport): void {
  const temporary = `${outputFile}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(path.dirname(outputFile), { recursive: true, mode: 0o700 });
    if (fs.existsSync(outputFile)) throw new Error(WRITE_ERROR);
    fs.writeFileSync(temporary, `${JSON.stringify(report, null, 2)}\n`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    fs.renameSync(temporary, outputFile);
  } catch {
    try {
      fs.rmSync(temporary, { force: true });
    } catch {
      // Preserve the fixed reporter error without reflecting a filesystem path.
    }
    throw new Error(WRITE_ERROR);
  }
}

/**
 * Relay runs must not retain Playwright's rich failure objects. This reporter
 * reads only aggregate counts and terminal statuses, then writes a fixed schema.
 */
export default class RelaySafeReporter implements Reporter {
  private readonly outputFile: string;
  private testsDiscovered = 0;
  private testsPassed = 0;

  constructor(options: RelaySafeReporterOptions) {
    this.outputFile = reporterOutputPath(options);
  }

  printsToStdio(): boolean {
    // Playwright adds a verbose line/dot fallback when every reporter returns
    // false. Claim this output channel while intentionally writing no bytes.
    return true;
  }

  onBegin(_config: FullConfig, suite: Suite): void {
    this.testsDiscovered = suite.allTests().length;
  }

  onTestEnd(_test: TestCase, result: TestResult): void {
    if (result.status === "passed") this.testsPassed += 1;
  }

  onEnd(result: FullResult): void {
    writeExclusiveJson(this.outputFile, {
      schema_version: 1,
      status: result.status,
      spec_id: PIPECAT_RELAY_SPEC_ID,
      pass_counts: {
        tests_discovered: this.testsDiscovered,
        tests_passed: this.testsPassed,
      },
      retention_policy: {
        rich_reporters_disabled: true,
        media_capture_disabled: true,
        reporter_stdio_disabled: true,
        runner_cleanup_required: true,
      },
    });
  }
}
