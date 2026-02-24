import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface NumberLineProps {
  min: number;
  max: number;
  marks?: number[];
  highlight?: number;
}

const LINE_WIDTH = 500;
const LINE_HEIGHT = 60;

export function numberLine(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const {
    min = 0,
    max = 10,
    marks = [],
    highlight,
  } = props as unknown as NumberLineProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  const ox = origin.x;
  const oy = origin.y + 20;
  const scale = LINE_WIDTH / (max - min);

  // Main line
  commands.push({
    action: "arrow",
    points: [
      [ox, oy],
      [ox + LINE_WIDTH, oy],
    ],
    color: "#94a3b8",
    stroke_width: 2,
    target_id: "number_line",
  });
  namedElements["number_line"] = "number_line";

  // Tick marks at integer positions
  const step = max - min <= 20 ? 1 : Math.ceil((max - min) / 10);
  for (let i = min; i <= max; i += step) {
    const tx = ox + (i - min) * scale;
    commands.push({
      action: "line",
      points: [
        [tx, oy - 6],
        [tx, oy + 6],
      ],
      color: "#94a3b8",
      stroke_width: 1,
    });
    commands.push({
      action: "text",
      text: String(i),
      x: tx,
      y: oy + 20,
      font_size: 12,
      color: "#e2e8f0",
    });
  }

  // Special marks
  for (const mark of marks) {
    const mx = ox + (mark - min) * scale;
    const markId = `mark_${mark}`;
    commands.push({
      action: "circle",
      x: mx - 5,
      y: oy - 5,
      width: 10,
      color: "#3b82f6",
      fill: "#3b82f6",
      target_id: markId,
    });
    namedElements[String(mark)] = markId;
  }

  // Highlighted point
  if (highlight !== undefined) {
    const hx = ox + (highlight - min) * scale;
    const hId = `highlight_${highlight}`;
    commands.push({
      action: "circle",
      x: hx - 8,
      y: oy - 8,
      width: 16,
      color: "#f59e0b",
      fill: "#f59e0b",
      target_id: hId,
    });
    commands.push({
      action: "text",
      text: String(highlight),
      x: hx,
      y: oy - 18,
      font_size: 14,
      color: "#f59e0b",
    });
    namedElements[String(highlight)] = hId;
  }

  return {
    commands,
    bounds: { x: ox, y: origin.y, width: LINE_WIDTH + 20, height: LINE_HEIGHT },
    namedElements,
  };
}
