import { describe, expect, it } from "vitest";

import {
  computeExactCameraClipInsets,
  exactCameraClipPath,
  type RenderedSvgViewport,
} from "./exact-camera-clip";

function renderedViewport(
  clientWidth: number,
  clientHeight: number,
  borders: Readonly<{
    top?: number;
    right?: number;
    bottom?: number;
    left?: number;
  }> = {},
): RenderedSvgViewport {
  const top = borders.top ?? 0;
  const right = borders.right ?? 0;
  const bottom = borders.bottom ?? 0;
  const left = borders.left ?? 0;
  return {
    borderBoxWidth: left + clientWidth + right,
    borderBoxHeight: top + clientHeight + bottom,
    clientLeft: left,
    clientTop: top,
    clientWidth,
    clientHeight,
  };
}

describe("exact camera clip math", () => {
  it("returns no matte when rendered SVG and camera aspect ratios match", () => {
    const insets = computeExactCameraClipInsets(renderedViewport(1_200, 675), {
      width: 640,
      height: 360,
    });

    expect(insets).toEqual({ top: 0, right: 0, bottom: 0, left: 0 });
    expect(exactCameraClipPath(insets)).toBe("inset(0px 0px 0px 0px)");
  });

  it("centers a pillarbox matte for a narrower certified camera", () => {
    expect(
      computeExactCameraClipInsets(renderedViewport(1_000, 600), {
        width: 800,
        height: 600,
      }),
    ).toEqual({ top: 0, right: 100, bottom: 0, left: 100 });
  });

  it("centers a letterbox matte for a wider certified camera", () => {
    expect(
      computeExactCameraClipInsets(renderedViewport(800, 800), {
        width: 800,
        height: 400,
      }),
    ).toEqual({ top: 200, right: 0, bottom: 200, left: 0 });
  });

  it("includes the SVG border around the real cinematic trajectory matte", () => {
    expect(
      computeExactCameraClipInsets(
        renderedViewport(852, 386, {
          top: 1,
          right: 1,
          bottom: 1,
          left: 1,
        }),
        { width: 548, height: 324 },
      ),
    ).toEqual({
      top: 1,
      right: 100.567901,
      bottom: 1,
      left: 100.567901,
    });
  });

  it("preserves asymmetric and fractional border edges in the four-edge matte", () => {
    expect(
      computeExactCameraClipInsets(
        renderedViewport(1_000, 600, {
          top: 3.25,
          right: 4.5,
          bottom: 2.75,
          left: 2.5,
        }),
        { width: 800, height: 600 },
      ),
    ).toEqual({
      top: 3.25,
      right: 104.5,
      bottom: 2.75,
      left: 102.5,
    });
  });

  it("rounds sub-pixel insets deterministically without negative zero", () => {
    const insets = computeExactCameraClipInsets(renderedViewport(1_000, 563), {
      width: 16,
      height: 9,
    });

    expect(insets).toEqual({
      top: 0.25,
      right: 0,
      bottom: 0.25,
      left: 0,
    });
    expect(Object.is(insets.right, -0)).toBe(false);
  });

  it.each([
    [renderedViewport(0, 600), { width: 800, height: 600 }],
    [renderedViewport(800, Number.NaN), { width: 800, height: 600 }],
    [renderedViewport(800, 600), { width: -1, height: 600 }],
    [renderedViewport(800, 600), { width: 800, height: Infinity }],
  ])("rejects invalid rendered or camera extents", (rendered, pose) => {
    expect(() => computeExactCameraClipInsets(rendered, pose)).toThrow(
      "positive finite number",
    );
  });

  it("rejects client geometry that extends beyond its border box", () => {
    expect(() =>
      computeExactCameraClipInsets(
        {
          borderBoxWidth: 100,
          borderBoxHeight: 100,
          clientLeft: 2,
          clientTop: 3,
          clientWidth: 99,
          clientHeight: 98,
        },
        { width: 100, height: 100 },
      ),
    ).toThrow("nonnegative finite number");
  });
});
