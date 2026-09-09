import { describe, expect, it } from "vitest";

import {
  isParametricChoreographyE2EEnabled,
  parseParametricChoreographyE2EOptions,
} from "./options";

describe("parametric choreography e2e options", () => {
  it("enables the proof route only outside production with the explicit flag", () => {
    expect(
      isParametricChoreographyE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "development",
      }),
    ).toBe(true);
    expect(
      isParametricChoreographyE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "test",
      }),
    ).toBe(true);
    expect(
      isParametricChoreographyE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "production",
      }),
    ).toBe(false);
    expect(
      isParametricChoreographyE2EEnabled({ NODE_ENV: "development" }),
    ).toBe(false);
    expect(
      isParametricChoreographyE2EEnabled({ MURMUR_E2E_MODE: "1" }),
    ).toBe(false);
    expect(
      isParametricChoreographyE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "preview",
      }),
    ).toBe(false);
  });

  it("defaults to the accelerated cinematic main proof", () => {
    expect(parseParametricChoreographyE2EOptions({})).toEqual({
      layout: "cinematic",
      reducedMotion: false,
      flow: "main",
      playbackRate: 16,
      keyframeProof: false,
    });
  });

  it("accepts only the compact reduced-motion adaptive variant", () => {
    expect(
      parseParametricChoreographyE2EOptions({
        layout: "compact",
        motion: "reduced",
        flow: "adaptive",
        proof: "keyframes",
      }),
    ).toEqual({
      layout: "compact",
      reducedMotion: true,
      flow: "adaptive",
      playbackRate: 16,
      keyframeProof: true,
    });
  });

  it("selects normal speed only through the closed speed option", () => {
    expect(
      parseParametricChoreographyE2EOptions({ speed: "normal" }),
    ).toEqual({
      layout: "cinematic",
      reducedMotion: false,
      flow: "main",
      playbackRate: 1,
      keyframeProof: false,
    });
  });

  it.each([
    [{ layout: "wide" }, /layout/],
    [{ motion: "instant" }, /motion/],
    [{ flow: "paid" }, /flow/],
    [{ proof: "video" }, /proof/],
    [{ speed: "turbo" }, /speed/],
    [{ unknown: "1" }, /unsupported/],
    [{ layout: ["compact", "cinematic"] }, /at most once/],
  ] as const)("rejects invalid or repeated controls", (value, expected) => {
    expect(() => parseParametricChoreographyE2EOptions(value)).toThrowError(
      expected,
    );
  });
});
