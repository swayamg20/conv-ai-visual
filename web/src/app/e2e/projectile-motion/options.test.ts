import { describe, expect, it } from "vitest";

import {
  isProjectileMotionE2EEnabled,
  parseProjectileMotionE2EOptions,
} from "./options";

describe("projectile-motion e2e options", () => {
  it("requires the explicit proof flag and still refuses production", () => {
    expect(
      isProjectileMotionE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "development",
      }),
    ).toBe(true);
    expect(
      isProjectileMotionE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "test",
      }),
    ).toBe(true);
    expect(
      isProjectileMotionE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "production",
      }),
    ).toBe(false);
    expect(isProjectileMotionE2EEnabled({ NODE_ENV: "development" })).toBe(
      false,
    );
    expect(isProjectileMotionE2EEnabled({ MURMUR_E2E_MODE: "1" })).toBe(false);
    expect(
      isProjectileMotionE2EEnabled({
        MURMUR_E2E_MODE: "1",
        NODE_ENV: "preview",
      }),
    ).toBe(false);
  });

  it("defaults to the accelerated cinematic main proof", () => {
    expect(parseProjectileMotionE2EOptions({})).toEqual({
      layout: "cinematic",
      reducedMotion: false,
      flow: "main",
      playbackRate: 16,
      keyframeProof: false,
    });
  });

  it("accepts the compact reduced-motion adaptive keyframe proof", () => {
    expect(
      parseProjectileMotionE2EOptions({
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

  it("selects normal timing only through the closed speed option", () => {
    expect(parseProjectileMotionE2EOptions({ speed: "normal" })).toEqual({
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
  ] as const)(
    "rejects invalid, unknown, or repeated controls",
    (value, expected) => {
      expect(() => parseProjectileMotionE2EOptions(value)).toThrowError(
        expected,
      );
    },
  );
});
