import { describe, expect, it } from "vitest";

import { normalizeOperation, normalizeTeachingSteps } from "./normalization";

describe("canvas normalization", () => {
  it("snaps operations and enforces minimum dimensions", () => {
    expect(
      normalizeOperation({
        action: "rect",
        x: 13,
        y: 31,
        width: 9,
        height: 21,
        points: [
          [9, 11],
          [29, 41],
        ],
      })
    ).toEqual({
      action: "rect",
      x: 20,
      y: 40,
      width: 40,
      height: 40,
      points: [
        [0, 20],
        [20, 40],
      ],
    });
  });

  it("centers short labels against the preceding shape", () => {
    const [shape, label] = normalizeTeachingSteps([
      { action: "rect", x: 100, y: 80, width: 200, height: 80 },
      { action: "text", x: 180, y: 120, text: "Area", font_size: 20 },
    ]);

    expect(shape).toMatchObject({ x: 100, y: 80, width: 200, height: 80 });
    expect(label).toMatchObject({ x: 180, y: 120, text: "Area", _centered: true });
  });

  it("does not associate a label across an unrelated action", () => {
    const steps = normalizeTeachingSteps([
      { action: "rect", x: 100, y: 80, width: 200, height: 80 },
      { action: "pause", duration: 1 },
      { action: "text", x: 180, y: 120, text: "Standalone" },
    ]);

    expect(steps[2]._centered).toBeUndefined();
  });
});
