/** @vitest-environment happy-dom */

import { describe, expect, it } from "vitest";

import {
  buildSmoothCurvePath,
  createSvgPrimitiveRenderer,
  projectFunctionPlotPoints,
} from "./primitives";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function primitiveRenderer() {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  const renderer = createSvgPrimitiveRenderer({
    svg,
    rough: null,
    palette: {
      stroke: "#ffffff",
      grid: "#333333",
      axis: "#888888",
      error: "#ff0000",
      bg: "#000000",
    },
    generateId: () => "generated-id",
  });
  return { renderer, svg };
}

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

  it.each([
    ["start", 200],
    ["middle", 160],
    ["end", 120],
  ] as const)(
    "renders an inline measured LaTeX token at its exact %s-anchored box",
    (anchor, expectedLeft) => {
      const { renderer } = primitiveRenderer();

      const group = renderer.drawLatexToken({
        type: "latex_token",
        id: `token-${anchor}`,
        latex: "x^2",
        x: 200,
        y: 75,
        width: 80,
        height: 44,
        anchor,
        font_size: 28,
        color: "#f59e0b",
      });
      const foreignObject = group.querySelector("foreignObject");
      const container = foreignObject?.querySelector("div");

      expect(group.getAttribute("id")).toBe(`token-${anchor}`);
      expect(group.getAttribute("data-element-id")).toBe(`token-${anchor}`);
      expect(foreignObject?.getAttribute("x")).toBe(String(expectedLeft));
      expect(foreignObject?.getAttribute("y")).toBe("75");
      expect(foreignObject?.getAttribute("width")).toBe("80");
      expect(foreignObject?.getAttribute("height")).toBe("44");
      expect(container?.querySelector(".katex")).not.toBeNull();
      expect(container?.querySelector(".katex-display")).toBeNull();
      expect(container?.style.color).toBe("#f59e0b");
      expect(container?.style.fontSize).toBe("28px");
    }
  );

  it("keeps the legacy display LaTeX viewport behavior unchanged", () => {
    const { renderer } = primitiveRenderer();

    const group = renderer.drawLatex({
      type: "latex",
      id: "legacy-equation",
      latex: "a^2+b^2=c^2",
      x: 12,
      y: 34,
      font_size: 30,
      color: "#ffffff",
    });
    const foreignObject = group.querySelector("foreignObject");

    expect(foreignObject?.getAttribute("x")).toBe("12");
    expect(foreignObject?.getAttribute("y")).toBe("34");
    expect(foreignObject?.getAttribute("width")).toBe("500");
    expect(foreignObject?.getAttribute("height")).toBe("120");
    expect(foreignObject?.querySelector(".katex-display")).not.toBeNull();
  });
});
