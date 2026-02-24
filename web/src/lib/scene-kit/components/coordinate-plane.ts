import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface CoordinatePlaneProps {
  x_range?: [number, number];
  y_range?: [number, number];
  points?: { x: number; y: number; label?: string }[];
  grid?: boolean;
}

const PLANE_WIDTH = 300;
const PLANE_HEIGHT = 250;

export function coordinatePlane(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const {
    x_range = [-5, 5],
    y_range = [-5, 5],
    points = [],
    grid = false,
  } = props as unknown as CoordinatePlaneProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  const ox = origin.x;
  const oy = origin.y;

  // Calculate axis positions (origin of coordinate system)
  const xMin = x_range[0];
  const xMax = x_range[1];
  const yMin = y_range[0];
  const yMax = y_range[1];

  const xScale = PLANE_WIDTH / (xMax - xMin);
  const yScale = PLANE_HEIGHT / (yMax - yMin);

  // Canvas position of coordinate origin (0,0)
  const zeroX = ox + (-xMin) * xScale;
  const zeroY = oy + yMax * yScale;

  // X axis
  commands.push({
    action: "arrow",
    points: [
      [ox, zeroY],
      [ox + PLANE_WIDTH, zeroY],
    ],
    color: "#94a3b8",
    stroke_width: 2,
    target_id: "x_axis",
  });
  namedElements["x_axis"] = "x_axis";

  // Y axis
  commands.push({
    action: "arrow",
    points: [
      [zeroX, oy + PLANE_HEIGHT],
      [zeroX, oy],
    ],
    color: "#94a3b8",
    stroke_width: 2,
    target_id: "y_axis",
  });
  namedElements["y_axis"] = "y_axis";

  // Grid lines
  if (grid) {
    for (let i = Math.ceil(xMin); i <= Math.floor(xMax); i++) {
      if (i === 0) continue;
      const gx = ox + (i - xMin) * xScale;
      commands.push({
        action: "line",
        points: [
          [gx, oy],
          [gx, oy + PLANE_HEIGHT],
        ],
        color: "#334155",
        stroke_width: 0.5,
      });
    }
    for (let j = Math.ceil(yMin); j <= Math.floor(yMax); j++) {
      if (j === 0) continue;
      const gy = oy + (yMax - j) * yScale;
      commands.push({
        action: "line",
        points: [
          [ox, gy],
          [ox + PLANE_WIDTH, gy],
        ],
        color: "#334155",
        stroke_width: 0.5,
      });
    }
  }

  // Tick labels on x-axis
  for (let i = Math.ceil(xMin); i <= Math.floor(xMax); i++) {
    if (i === 0) continue;
    const tx = ox + (i - xMin) * xScale;
    commands.push({
      action: "text",
      text: String(i),
      x: tx,
      y: zeroY + 16,
      font_size: 11,
      color: "#64748b",
    });
  }

  // Tick labels on y-axis
  for (let j = Math.ceil(yMin); j <= Math.floor(yMax); j++) {
    if (j === 0) continue;
    const ty = oy + (yMax - j) * yScale;
    commands.push({
      action: "text",
      text: String(j),
      x: zeroX - 18,
      y: ty,
      font_size: 11,
      color: "#64748b",
    });
  }

  // Origin label
  commands.push({
    action: "text",
    text: "O",
    x: zeroX - 14,
    y: zeroY + 16,
    font_size: 11,
    color: "#64748b",
  });

  // Plot points
  for (const pt of points) {
    const px = ox + (pt.x - xMin) * xScale;
    const py = oy + (yMax - pt.y) * yScale;
    const ptId = `pt_${pt.label || `${pt.x}_${pt.y}`}`;

    commands.push({
      action: "circle",
      x: px - 4,
      y: py - 4,
      width: 8,
      color: "#f59e0b",
      fill: "#f59e0b",
      target_id: ptId,
    });

    if (pt.label) {
      commands.push({
        action: "text",
        text: pt.label,
        x: px + 8,
        y: py - 8,
        font_size: 12,
        color: "#e2e8f0",
      });
      namedElements[pt.label] = ptId;
    }
  }

  return {
    commands,
    bounds: { x: ox, y: oy, width: PLANE_WIDTH + 20, height: PLANE_HEIGHT + 30 },
    namedElements,
  };
}
