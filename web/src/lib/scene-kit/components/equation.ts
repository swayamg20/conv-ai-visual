import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface EquationProps {
  latex: string;
  color?: string;
  font_size?: number;
}

export function equation(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const {
    latex: latexStr,
    color = "#e2e8f0",
    font_size = 22,
  } = props as unknown as EquationProps;

  const id = `eq_${Date.now().toString(36)}`;

  // Estimate width based on latex length (rough heuristic)
  const estimatedWidth = Math.max(100, latexStr.length * 10);
  const estimatedHeight = 50;

  const commands: RenderCommand[] = [
    {
      action: "latex",
      latex: latexStr,
      x: origin.x,
      y: origin.y,
      font_size,
      color,
      target_id: id,
      label: id,
    },
  ];

  return {
    commands,
    bounds: {
      x: origin.x,
      y: origin.y,
      width: estimatedWidth,
      height: estimatedHeight,
    },
    namedElements: { [id]: id },
  };
}
