import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface FlowchartProps {
  nodes: { id: string; text: string }[];
  edges: [string, string][];
}

const NODE_WIDTH = 120;
const NODE_HEIGHT = 50;
const NODE_GAP = 60;

export function flowchart(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { nodes = [], edges = [] } = props as unknown as FlowchartProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  // Layout: left to right
  const nodePositions: Record<string, { x: number; y: number }> = {};
  const ox = origin.x;
  const oy = origin.y;

  nodes.forEach((node, i) => {
    const nx = ox + i * (NODE_WIDTH + NODE_GAP);
    const ny = oy;
    nodePositions[node.id] = { x: nx, y: ny };

    const nodeId = `fc_${node.id}`;

    commands.push({
      action: "rect",
      x: nx,
      y: ny,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      color: "#3b82f6",
      stroke_width: 2,
      target_id: nodeId,
    });

    commands.push({
      action: "text",
      text: node.text,
      x: nx + NODE_WIDTH / 2,
      y: ny + NODE_HEIGHT / 2,
      font_size: 13,
      color: "#e2e8f0",
    });

    namedElements[node.id] = nodeId;
  });

  // Edges (arrows)
  for (const [from, to] of edges) {
    const fromPos = nodePositions[from];
    const toPos = nodePositions[to];
    if (!fromPos || !toPos) continue;

    const fromX = fromPos.x + NODE_WIDTH;
    const fromY = fromPos.y + NODE_HEIGHT / 2;
    const toX = toPos.x;
    const toY = toPos.y + NODE_HEIGHT / 2;

    commands.push({
      action: "arrow",
      points: [
        [fromX, fromY],
        [toX, toY],
      ],
      color: "#64748b",
      stroke_width: 2,
    });
  }

  const totalWidth = nodes.length * (NODE_WIDTH + NODE_GAP) - NODE_GAP;

  return {
    commands,
    bounds: { x: ox, y: oy, width: Math.max(totalWidth, NODE_WIDTH), height: NODE_HEIGHT },
    namedElements,
  };
}
