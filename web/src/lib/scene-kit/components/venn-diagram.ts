import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface VennDiagramProps {
  sets: { label: string }[];
  intersectionLabel?: string;
}

const CIRCLE_RADIUS = 90;
const OVERLAP = 50;

const COLORS = ["#3b82f6", "#10b981", "#8b5cf6"];

export function vennDiagram(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { sets = [], intersectionLabel } = props as unknown as VennDiagramProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};
  const count = Math.min(sets.length, 3);

  const cx = origin.x + CIRCLE_RADIUS + 10;
  const cy = origin.y + CIRCLE_RADIUS + 10;

  if (count >= 2) {
    // Circle 1 (left)
    const c1Id = `venn_${sets[0].label}`;
    commands.push({
      action: "circle",
      x: cx - OVERLAP / 2 - CIRCLE_RADIUS,
      y: cy - CIRCLE_RADIUS,
      width: CIRCLE_RADIUS * 2,
      color: COLORS[0],
      stroke_width: 2,
      target_id: c1Id,
    });
    commands.push({
      action: "text",
      text: sets[0].label,
      x: cx - OVERLAP / 2 - CIRCLE_RADIUS / 2,
      y: cy - CIRCLE_RADIUS - 10,
      font_size: 14,
      color: COLORS[0],
    });
    namedElements[sets[0].label] = c1Id;

    // Circle 2 (right)
    const c2Id = `venn_${sets[1].label}`;
    commands.push({
      action: "circle",
      x: cx + OVERLAP / 2 - CIRCLE_RADIUS,
      y: cy - CIRCLE_RADIUS,
      width: CIRCLE_RADIUS * 2,
      color: COLORS[1],
      stroke_width: 2,
      target_id: c2Id,
    });
    commands.push({
      action: "text",
      text: sets[1].label,
      x: cx + OVERLAP / 2 + CIRCLE_RADIUS / 2,
      y: cy - CIRCLE_RADIUS - 10,
      font_size: 14,
      color: COLORS[1],
    });
    namedElements[sets[1].label] = c2Id;

    // Intersection label
    if (intersectionLabel) {
      commands.push({
        action: "text",
        text: intersectionLabel,
        x: cx,
        y: cy,
        font_size: 13,
        color: "#f59e0b",
        target_id: "venn_intersection",
      });
      namedElements["intersection"] = "venn_intersection";
    }
  }

  if (count >= 3) {
    // Circle 3 (bottom center)
    const c3Id = `venn_${sets[2].label}`;
    commands.push({
      action: "circle",
      x: cx - CIRCLE_RADIUS,
      y: cy + OVERLAP / 2 - CIRCLE_RADIUS,
      width: CIRCLE_RADIUS * 2,
      color: COLORS[2],
      stroke_width: 2,
      target_id: c3Id,
    });
    commands.push({
      action: "text",
      text: sets[2].label,
      x: cx,
      y: cy + OVERLAP / 2 + CIRCLE_RADIUS + 16,
      font_size: 14,
      color: COLORS[2],
    });
    namedElements[sets[2].label] = c3Id;
  }

  const totalWidth = CIRCLE_RADIUS * 2 + OVERLAP + 20;
  const totalHeight = count >= 3
    ? CIRCLE_RADIUS * 2 + OVERLAP + 40
    : CIRCLE_RADIUS * 2 + 30;

  return {
    commands,
    bounds: { x: origin.x, y: origin.y, width: totalWidth, height: totalHeight },
    namedElements,
  };
}
