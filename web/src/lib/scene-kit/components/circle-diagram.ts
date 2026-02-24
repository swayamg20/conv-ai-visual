import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface CircleDiagramProps {
  radius_label?: string;
  diameter_label?: string;
}

const DIAMETER = 160;

export function circleDiagram(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { radius_label, diameter_label } = props as unknown as CircleDiagramProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  const cx = origin.x + DIAMETER / 2;
  const cy = origin.y + DIAMETER / 2;
  const r = DIAMETER / 2;

  // Main circle
  commands.push({
    action: "circle",
    x: origin.x,
    y: origin.y,
    width: DIAMETER,
    color: "#3b82f6",
    stroke_width: 2,
    target_id: "circle_main",
  });
  namedElements["circle"] = "circle_main";

  // Center dot
  commands.push({
    action: "circle",
    x: cx - 3,
    y: cy - 3,
    width: 6,
    color: "#e2e8f0",
    fill: "#e2e8f0",
  });

  // Radius line
  if (radius_label) {
    const rId = "circle_radius";
    commands.push({
      action: "line",
      points: [
        [cx, cy],
        [cx + r, cy],
      ],
      color: "#f59e0b",
      stroke_width: 2,
      target_id: rId,
    });
    commands.push({
      action: "text",
      text: radius_label,
      x: cx + r / 2,
      y: cy - 12,
      font_size: 14,
      color: "#f59e0b",
    });
    namedElements[radius_label] = rId;
  }

  // Diameter line
  if (diameter_label) {
    const dId = "circle_diameter";
    commands.push({
      action: "line",
      points: [
        [cx - r, cy],
        [cx + r, cy],
      ],
      color: "#10b981",
      stroke_width: 2,
      target_id: dId,
    });
    commands.push({
      action: "text",
      text: diameter_label,
      x: cx,
      y: cy + 16,
      font_size: 14,
      color: "#10b981",
    });
    namedElements[diameter_label] = dId;
  }

  return {
    commands,
    bounds: { x: origin.x, y: origin.y, width: DIAMETER + 20, height: DIAMETER + 20 },
    namedElements,
  };
}
