import { describe, expect, it } from "vitest";

import { operationForTeachingStep } from "./timeline";

describe("teaching timeline planning", () => {
  it("converts a shape step into one stable render operation", () => {
    expect(
      operationForTeachingStep(
        {
          action: "rect",
          label: "diagram",
          x: 100,
          y: 80,
          width: 200,
          height: 100,
          color: "#fff",
          animate_style: "scale",
        },
        () => "generated"
      )
    ).toEqual({
      action: "rect",
      id: "diagram",
      x: 100,
      y: 80,
      width: 200,
      height: 100,
      color: "#fff",
      fill: undefined,
      stroke_width: undefined,
      points: undefined,
      text: undefined,
      font_size: undefined,
      font_family: undefined,
      roughness: undefined,
      animate_style: "scale",
      _centered: undefined,
    });
  });

  it("generates an id only when the sequence did not provide one", () => {
    expect(operationForTeachingStep({ action: "circle" }, () => "abc").id).toBe(
      "circle_abc"
    );
  });
});
