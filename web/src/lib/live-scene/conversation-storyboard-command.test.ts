import { describe, expect, it } from "vitest";

import { PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL } from "./semantic-storyboard";
import {
  CONVERSATION_STORYBOARD_COMMAND_VERSION,
  decodeConversationStoryboardCommandV1,
} from "./conversation-storyboard-command";

const COMMAND_ID = "7ed2205c-24af-4f6f-8c44-367f5ed832a0";

function command(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    v: CONVERSATION_STORYBOARD_COMMAND_VERSION,
    commandId: COMMAND_ID,
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
    prompt: "Compare both trajectories visually.",
    ...overrides,
  };
}

describe("conversation storyboard command", () => {
  it.each([
    [20, [30, 45]],
    [20, [30, 60]],
    [20, [45, 60]],
    [25, [30, 45]],
    [25, [30, 60]],
    [25, [45, 60]],
    [30, [30, 45]],
    [30, [30, 60]],
    [30, [45, 60]],
  ])("accepts the certified %i m/s %j problem", (speedMps, anglesDeg) => {
    const decoded = decodeConversationStoryboardCommandV1(
      command({
        prompt: "  Compare both trajectories visually.  ",
        problemSpec: { v: 1, speedMps, anglesDeg },
      }),
    );

    expect(decoded).toEqual({
      v: 1,
      commandId: COMMAND_ID,
      protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
      problemSpec: { v: 1, speedMps, anglesDeg },
      prompt: "Compare both trajectories visually.",
    });
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.problemSpec)).toBe(true);
    expect(Object.isFrozen(decoded.problemSpec.anglesDeg)).toBe(true);
  });

  it.each([
    ["non-object", null],
    ["wrong version", command({ v: 2 })],
    ["wrong protocol", command({ protocol: "semantic_storyboard_v1" })],
    ["non-v4 UUID", command({ commandId: "7ed2205c-24af-1f6f-8c44-367f5ed832a0" })],
    ["invalid UUID variant", command({ commandId: "7ed2205c-24af-4f6f-7c44-367f5ed832a0" })],
    ["unsupported speed", command({ problemSpec: { v: 1, speedMps: 40, anglesDeg: [30, 60] } })],
    ["equal angles", command({ problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 30] } })],
    ["descending angles", command({ problemSpec: { v: 1, speedMps: 20, anglesDeg: [60, 30] } })],
    ["blank prompt", command({ prompt: " \n\t " })],
    ["oversized prompt", command({ prompt: "x".repeat(2_001) })],
    ["unknown field", { ...command(), extra: true }],
  ])("rejects %s", (_name, value) => {
    expect(() => decodeConversationStoryboardCommandV1(value)).toThrow();
  });

  it.each(["v", "commandId", "protocol", "problemSpec", "prompt"])(
    "rejects a missing %s field",
    (key) => {
      const value = command();
      delete value[key];
      expect(() => decodeConversationStoryboardCommandV1(value)).toThrow();
    },
  );

  it("counts prompt Unicode code points instead of UTF-16 units", () => {
    const decoded = decodeConversationStoryboardCommandV1(
      command({ prompt: ` ${"😀".repeat(2_000)} ` }),
    );
    expect([...decoded.prompt]).toHaveLength(2_000);

    expect(() =>
      decodeConversationStoryboardCommandV1(
        command({ prompt: "😀".repeat(2_001) }),
      ),
    ).toThrow();
  });
});
