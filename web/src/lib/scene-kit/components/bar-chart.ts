import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface BarChartProps {
  data: { label: string; value: number }[];
  title?: string;
}

const CHART_WIDTH = 400;
const CHART_HEIGHT = 200;
const BAR_GAP = 12;

const BAR_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

export function barChart(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { data = [], title } = props as unknown as BarChartProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  const ox = origin.x;
  let oy = origin.y;

  // Title
  if (title) {
    commands.push({
      action: "text",
      text: title,
      x: ox + CHART_WIDTH / 2,
      y: oy,
      font_size: 18,
      color: "#e2e8f0",
    });
    oy += 30;
  }

  if (data.length === 0) {
    return {
      commands,
      bounds: { x: ox, y: origin.y, width: CHART_WIDTH, height: 40 },
      namedElements,
    };
  }

  const maxValue = Math.max(...data.map((d) => d.value), 1);
  const barWidth = Math.max(20, (CHART_WIDTH - BAR_GAP * (data.length + 1)) / data.length);

  // Bars
  data.forEach((item, i) => {
    const barHeight = (item.value / maxValue) * CHART_HEIGHT;
    const bx = ox + BAR_GAP + i * (barWidth + BAR_GAP);
    const by = oy + CHART_HEIGHT - barHeight;
    const barId = `bar_${item.label}`;
    const color = BAR_COLORS[i % BAR_COLORS.length];

    commands.push({
      action: "rect",
      x: bx,
      y: by,
      width: barWidth,
      height: barHeight,
      color,
      fill: color,
      target_id: barId,
    });

    // Value label above bar
    commands.push({
      action: "text",
      text: String(item.value),
      x: bx + barWidth / 2,
      y: by - 8,
      font_size: 12,
      color: "#e2e8f0",
    });

    // Category label below bar
    commands.push({
      action: "text",
      text: item.label,
      x: bx + barWidth / 2,
      y: oy + CHART_HEIGHT + 16,
      font_size: 12,
      color: "#94a3b8",
    });

    namedElements[item.label] = barId;
  });

  const totalHeight = (title ? 30 : 0) + CHART_HEIGHT + 30;

  return {
    commands,
    bounds: { x: ox, y: origin.y, width: CHART_WIDTH, height: totalHeight },
    namedElements,
  };
}
