import { describe, expect, it } from "vitest";

import { parseChoreographyCaptureOptions } from "./capture-options";

describe("parseChoreographyCaptureOptions", () => {
  it("defaults to the real-speed cinematic proof", () => {
    expect(parseChoreographyCaptureOptions({})).toEqual({
      layout: "cinematic",
      reducedMotion: false,
      pace: "auto",
    });
  });

  it("accepts the closed compact and reduced-motion values", () => {
    expect(
      parseChoreographyCaptureOptions({
        layout: "compact",
        motion: "reduced",
        pace: "step",
      }),
    ).toEqual({ layout: "compact", reducedMotion: true, pace: "step" });
  });

  it.each([
    [{ prompt: "author arbitrary ink" }, "unsupported capture option"],
    [{ layout: "wide" }, "layout must be cinematic or compact"],
    [{ motion: "fast" }, "motion must be real or reduced"],
    [{ pace: "manual" }, "pace must be auto or step"],
    [{ pace: ["auto", "step"] }, "pace must appear at most once"],
    [{ layout: ["compact", "cinematic"] }, "layout must appear at most once"],
  ] as const)("rejects invalid query input %#", (input, message) => {
    expect(() => parseChoreographyCaptureOptions(input)).toThrow(message);
  });
});
