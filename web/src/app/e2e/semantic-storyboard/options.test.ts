import { describe, expect, it } from "vitest";

import {
  isSemanticStoryboardE2EEnabled,
  parseSemanticStoryboardE2EOptions,
} from "./options";

describe("semantic-storyboard e2e options", () => {
  it("requires the explicit E2E flag and refuses production", () => {
    expect(
      isSemanticStoryboardE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "development",
      }),
    ).toBe(true);
    expect(
      isSemanticStoryboardE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "test",
      }),
    ).toBe(true);
    expect(
      isSemanticStoryboardE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "production",
      }),
    ).toBe(false);
    expect(isSemanticStoryboardE2EEnabled({ NODE_ENV: "development" })).toBe(
      false,
    );
    expect(isSemanticStoryboardE2EEnabled({ MURMUR_E2E_MODE: "1" })).toBe(
      false,
    );
    expect(
      isSemanticStoryboardE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "preview",
      }),
    ).toBe(false);
  });

  it("defaults to accelerated cinematic motion without keyframe pauses", () => {
    const options = parseSemanticStoryboardE2EOptions({});

    expect(options).toEqual({
      layout: "cinematic",
      reducedMotion: false,
      playbackRate: 16,
      keyframeProof: false,
    });
    expect(Object.isFrozen(options)).toBe(true);
  });

  it("accepts the compact reduced-motion normal keyframe proof", () => {
    expect(
      parseSemanticStoryboardE2EOptions({
        layout: "compact",
        motion: "reduced",
        speed: "normal",
        proof: "keyframes",
      }),
    ).toEqual({
      layout: "compact",
      reducedMotion: true,
      playbackRate: 1,
      keyframeProof: true,
    });
  });

  it.each([
    [{ layout: "wide" }, /layout/],
    [{ motion: "instant" }, /motion/],
    [{ speed: "turbo" }, /speed/],
    [{ proof: "video" }, /proof/],
    [{ provider: "azure" }, /unsupported/],
    [{ layout: ["compact", "cinematic"] }, /at most once/],
    [{ motion: ["real", "reduced"] }, /at most once/],
    [{ speed: ["normal", "accelerated"] }, /at most once/],
    [{ proof: ["none", "keyframes"] }, /at most once/],
  ] as const)(
    "rejects invalid, unknown, or repeated controls",
    (value, expected) => {
      expect(() => parseSemanticStoryboardE2EOptions(value)).toThrowError(
        expected,
      );
    },
  );
});
