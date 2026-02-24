import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface RightTriangleProps {
  sides?: [string, string, string]; // [bottom, right, hypotenuse]
  angle_mark?: boolean;
}

const DEFAULT_WIDTH = 200;
const DEFAULT_HEIGHT = 160;

export function rightTriangle(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { sides = ["a", "b", "c"], angle_mark = true } = props as unknown as RightTriangleProps;
  const [sideA, sideB, sideC] = sides;

  const x = origin.x;
  const y = origin.y;

  // Triangle vertices: bottom-left, bottom-right, top-right (right angle at bottom-right)
  const bl: [number, number] = [x, y + DEFAULT_HEIGHT];
  const br: [number, number] = [x + DEFAULT_WIDTH, y + DEFAULT_HEIGHT];
  const tr: [number, number] = [x + DEFAULT_WIDTH, y];

  const prefix = `rt_${Date.now().toString(36)}`;
  const sideAId = `${prefix}_${sideA}`;
  const sideBId = `${prefix}_${sideB}`;
  const sideCId = `${prefix}_${sideC}`;

  const commands: RenderCommand[] = [
    // Bottom side (a)
    {
      action: "line",
      points: [bl, br],
      color: "#3b82f6",
      stroke_width: 2,
      target_id: sideAId,
      label: sideA,
    },
    // Right side (b)
    {
      action: "line",
      points: [br, tr],
      color: "#3b82f6",
      stroke_width: 2,
      target_id: sideBId,
      label: sideB,
    },
    // Hypotenuse (c)
    {
      action: "line",
      points: [tr, bl],
      color: "#3b82f6",
      stroke_width: 2,
      target_id: sideCId,
      label: sideC,
    },
    // Label for side a (bottom, centered)
    {
      action: "text",
      text: sideA,
      x: x + DEFAULT_WIDTH / 2,
      y: y + DEFAULT_HEIGHT + 20,
      font_size: 16,
      color: "#e2e8f0",
    },
    // Label for side b (right)
    {
      action: "text",
      text: sideB,
      x: x + DEFAULT_WIDTH + 15,
      y: y + DEFAULT_HEIGHT / 2,
      font_size: 16,
      color: "#e2e8f0",
    },
    // Label for hypotenuse c (middle of diagonal)
    {
      action: "text",
      text: sideC,
      x: x + DEFAULT_WIDTH / 2 - 20,
      y: y + DEFAULT_HEIGHT / 2 - 10,
      font_size: 16,
      color: "#e2e8f0",
    },
  ];

  // Right angle marker at bottom-right
  if (angle_mark) {
    const markSize = 15;
    commands.push({
      action: "line",
      points: [
        [br[0] - markSize, br[1]],
        [br[0] - markSize, br[1] - markSize],
      ],
      color: "#94a3b8",
      stroke_width: 1,
    });
    commands.push({
      action: "line",
      points: [
        [br[0] - markSize, br[1] - markSize],
        [br[0], br[1] - markSize],
      ],
      color: "#94a3b8",
      stroke_width: 1,
    });
  }

  const namedElements: Record<string, string> = {};
  namedElements[sideA] = sideAId;
  namedElements[sideB] = sideBId;
  namedElements[sideC] = sideCId;

  return {
    commands,
    bounds: { x, y, width: DEFAULT_WIDTH + 40, height: DEFAULT_HEIGHT + 30 },
    namedElements,
  };
}
