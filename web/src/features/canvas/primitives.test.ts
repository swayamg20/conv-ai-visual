import { describe, expect, it } from "vitest";

import { buildSmoothCurvePath, projectFunctionPlotPoints } from "./primitives";

describe("SVG primitive geometry", () => {
  it("builds a stable smooth path through multiple points", () => {
    expect(
      buildSmoothCurvePath([
        [0, 0],
        [20, 20],
        [40, 0],
      ])
    ).toBe("M0.0,0.0 L10.0,10.0 Q20.0,20.0 40.0,0.0");
  });

  it("projects plot coordinates into the drawable viewport", () => {
    expect(
      projectFunctionPlotPoints(
        [
          [-1, -1],
          [0, 0],
          [1, 1],
        ],
        [-1, 1],
        [-1, 1],
        { width: 320, height: 220 },
        60
      )
    ).toEqual([
      [60, 160],
      [160, 110],
      [260, 60],
    ]);
  });
});
