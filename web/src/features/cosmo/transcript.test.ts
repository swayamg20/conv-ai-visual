import type { TranscriptDeltaEvent } from "cosmo-ai";
import { describe, expect, it } from "vitest";

import type { Transcript } from "./contracts";
import { foldTranscript } from "./transcript";

function replay(events: Partial<TranscriptDeltaEvent>[]) {
  return events.reduce<readonly Transcript[]>((items, event, index) => foldTranscript(items, {
    id: `delta-${index}`, turnId: "shared", role: "user", text: "", isFinal: false, append: true, ...event,
  }), []);
}

describe("Cosmo interleaved transcripts", () => {
  it("keeps a user utterance through another speaker's turn completion and a delayed final", () => {
    const items = replay([
      { text: "Tell me a story." },
      { role: "assistant", text: "Once upon a time.", isFinal: true },
      { turnId: "after-assistant-complete", text: "Tell me a story.", isFinal: true, append: false },
      { turnId: "after-assistant-complete", text: "Wait, stop. Say banana." },
      { role: "assistant", turnId: "after-assistant-complete", text: "banana", isFinal: true },
    ]);
    expect(items.map(item => item.text)).toEqual(["Tell me a story.", "Once upon a time.", "Wait, stop. Say banana.", "banana"]);
    expect(items[0].final).toBe(true);
  });

  it("preserves the user's prefix when another speaker resets append", () => {
    const items = replay([
      { text: "Please show me " },
      { role: "assistant", text: "Sure." },
      { text: "a quadratic.", append: false },
    ]);
    expect(items[0].text).toBe("Please show me a quadratic.");
  });

  it("starts a new utterance after a final even when the SDK turn ID is reused", () => {
    const items = replay([
      { text: "First request.", isFinal: true },
      { text: "Second " },
      { text: "Second request.", isFinal: true },
    ]);
    expect(items.map(item => item.text)).toEqual(["First request.", "Second request."]);
    expect(new Set(items.map(item => item.id)).size).toBe(2);
  });

  it("clears suppressed speech on an empty final without creating empty bubbles", () => {
    const items = replay([{ text: "" }, { text: "Unusable fragment" }, { text: "", isFinal: true }]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ text: "", final: true });
  });
});
