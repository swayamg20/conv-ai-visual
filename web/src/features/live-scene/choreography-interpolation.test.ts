import { describe, expect, it } from "vitest";

import type { PathSceneNode, ScenePoint } from "@/lib/live-scene";

import {
  createUniformTimePathSampler,
  pathBoundingBoxCenter,
  pathTranslationDelta,
  sampleUniformTimePath,
} from "./choreography-interpolation";

const points = Object.freeze([
  Object.freeze([0, 100] as const),
  Object.freeze([10, 0] as const),
  Object.freeze([20, 100] as const),
]) satisfies readonly ScenePoint[];

function marker(
  id: string,
  offsetX: number,
  offsetY: number,
  overrides: Partial<PathSceneNode> = {},
): PathSceneNode {
  return {
    id,
    kind: "path",
    points: [
      [offsetX - 4, offsetY - 4],
      [offsetX + 4, offsetY - 4],
      [offsetX + 4, offsetY + 4],
      [offsetX - 4, offsetY + 4],
    ],
    closed: true,
    presentation: { enter: "fade", exit: "fade" },
    style: {
      stroke: "#F59E0B",
      strokeWidth: 2,
      opacity: 1,
      roughness: 0,
      fill: "#F59E0B",
    },
    ...overrides,
  };
}

describe("choreography trace interpolation", () => {
  it.each([
    [0, [0, 100]],
    [0.25, [5, 50]],
    [0.5, [10, 0]],
    [0.75, [15, 50]],
    [1, [20, 100]],
  ] as const)("samples equal-time points at progress %s", (progress, point) => {
    const sample = sampleUniformTimePath(points, progress);

    expect(sample.point[0]).toBeCloseTo(point[0]);
    expect(sample.point[1]).toBeCloseTo(point[1]);
  });

  it("uses cumulative distance only for the stroke reveal", () => {
    const sampler = createUniformTimePathSampler([
      [0, 0],
      [10, 0],
      [20, 100],
    ]);
    const quarter = sampler.sample(0.25);
    const threeQuarters = sampler.sample(0.75);

    expect(quarter.point).toEqual([5, 0]);
    expect(quarter.revealedLength).toBeCloseTo(5);
    expect(threeQuarters.point).toEqual([15, 50]);
    expect(threeQuarters.revealedLength).toBeCloseTo(
      10 + Math.hypot(10, 100) / 2,
    );
    expect(threeQuarters.totalLength).toBeCloseTo(10 + Math.hypot(10, 100));
  });

  it("fails closed for invalid progress and degenerate paths", () => {
    expect(() => createUniformTimePathSampler([[0, 0]])).toThrow(
      "at least two sample points",
    );
    expect(() =>
      createUniformTimePathSampler([
        [0, 0],
        [0, 0],
      ]),
    ).toThrow("positive finite length");
    expect(() => sampleUniformTimePath(points, -0.01)).toThrow(
      "between zero and one",
    );
    expect(() => sampleUniformTimePath(points, Number.NaN)).toThrow(
      "between zero and one",
    );
  });

  it("anchors markers by bounding box and accepts only a pure translation", () => {
    const previous = marker("projectile__marker", 0, 100);
    const next = marker("projectile__marker", 20, 100);

    expect(pathBoundingBoxCenter(previous)).toEqual([0, 100]);
    expect(pathBoundingBoxCenter(next)).toEqual([20, 100]);
    expect(pathTranslationDelta(previous, next)).toEqual([20, 0]);

    const reshaped = marker("projectile__marker", 20, 100, {
      points: [
        [15, 95],
        [25, 95],
        [25, 105],
        [15, 105],
      ],
    });
    expect(pathTranslationDelta(previous, reshaped)).toBeNull();

    const recolored = marker("projectile__marker", 20, 100, {
      style: { ...next.style, fill: "#FFFFFF" },
    });
    expect(pathTranslationDelta(previous, recolored)).toBeNull();
  });
});
