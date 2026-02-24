import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface LabelProps {
  text: string;
  fontSize?: number;
  color?: string;
}

export function label(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const {
    text,
    fontSize = 18,
    color = "#e2e8f0",
  } = props as unknown as LabelProps;

  const id = `label_${Date.now().toString(36)}`;
  const estimatedWidth = text.length * fontSize * 0.6;
  const estimatedHeight = fontSize + 10;

  const commands: RenderCommand[] = [
    {
      action: "text",
      text,
      x: origin.x,
      y: origin.y,
      font_size: fontSize,
      color,
      target_id: id,
    },
  ];

  return {
    commands,
    bounds: { x: origin.x, y: origin.y, width: estimatedWidth, height: estimatedHeight },
    namedElements: { [id]: id },
  };
}
