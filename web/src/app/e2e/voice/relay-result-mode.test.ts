import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const relaySpecPath = fileURLToPath(
  new URL("../../../../e2e/voice-pipecat-rtc.spec.ts", import.meta.url)
);

describe("relay rich-result ownership", () => {
  it("creates the exclusive temporary result with owner-only mode", () => {
    const source = readFileSync(relaySpecPath, "utf8");
    const writer = source.slice(
      source.indexOf("function writeResultAtomically"),
      source.indexOf("function observeSanitizedRequests")
    );

    expect(writer).toContain('flag: "wx"');
    expect(writer).toContain("mode: 0o600");
    expect(writer).toContain("fs.renameSync(temporary, resultPath)");
  });
});
