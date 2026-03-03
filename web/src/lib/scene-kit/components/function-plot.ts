import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface FunctionPlotProps {
  x_range?: [number, number];
  y_range?: [number, number];
  points: { x: number; y: number }[];
  color?: string;
  grid?: boolean;
}

const PLOT_WIDTH = 300;
const PLOT_HEIGHT = 250;

export function functionPlot(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const {
    x_range = [-5, 5],
    y_range = [-5, 5],
    points = [],
    color = "#38bdf8",
    grid = true,
  } = props as unknown as FunctionPlotProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  const ox = origin.x;
  const oy = origin.y;

  const xMin = x_range[0];
  const xMax = x_range[1];
  const yMin = y_range[0];
  const yMax = y_range[1];

  const xScale = PLOT_WIDTH / (xMax - xMin);
  const yScale = PLOT_HEIGHT / (yMax - yMin);

  // Canvas position of coordinate origin (0,0)
  const zeroX = ox + (-xMin) * xScale;
  const zeroY = oy + yMax * yScale;

  // Grid lines
  if (grid) {
    for (let i = Math.ceil(xMin); i <= Math.floor(xMax); i++) {
      if (i === 0) continue;
      const gx = ox + (i - xMin) * xScale;
      commands.push({
        action: "line",
        points: [[gx, oy], [gx, oy + PLOT_HEIGHT]],
        color: "#334155",
        stroke_width: 0.5,
        animate_style: "none",
      });
    }
    for (let j = Math.ceil(yMin); j <= Math.floor(yMax); j++) {
      if (j === 0) continue;
      const gy = oy + (yMax - j) * yScale;
      commands.push({
        action: "line",
        points: [[ox, gy], [ox + PLOT_WIDTH, gy]],
        color: "#334155",
        stroke_width: 0.5,
        animate_style: "none",
      });
    }
  }

  // X axis
  commands.push({
    action: "arrow",
    points: [[ox, zeroY], [ox + PLOT_WIDTH, zeroY]],
    color: "#94a3b8",
    stroke_width: 1.5,
    target_id: "x_axis",
  });
  namedElements["x_axis"] = "x_axis";

  // Y axis
  commands.push({
    action: "arrow",
    points: [[zeroX, oy + PLOT_HEIGHT], [zeroX, oy]],
    color: "#94a3b8",
    stroke_width: 1.5,
    target_id: "y_axis",
  });
  namedElements["y_axis"] = "y_axis";

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
      animate_style: "none",
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
      animate_style: "none",
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
    animate_style: "none",
  });

  // Convert data points to canvas coordinates and sort by x
  const sorted = [...points].sort((a, b) => a.x - b.x);
  const canvasPoints: [number, number][] = sorted
    .filter((pt) => pt.x >= xMin && pt.x <= xMax && pt.y >= yMin && pt.y <= yMax)
    .map((pt) => [
      ox + (pt.x - xMin) * xScale,
      oy + (yMax - pt.y) * yScale,
    ]);

  // Curve through all points
  if (canvasPoints.length >= 2) {
    commands.push({
      action: "curve",
      points: canvasPoints,
      color,
      stroke_width: 2.5,
      target_id: "curve",
      animate_style: "draw",
    });
    namedElements["curve"] = "curve";
  }

  return {
    commands,
    bounds: { x: ox, y: oy, width: PLOT_WIDTH + 20, height: PLOT_HEIGHT + 30 },
    namedElements,
  };
}
