import type { ComponentResult, RenderCommand, Viewport } from "../types";

interface TreeProps {
  root: string;
  children: Record<string, string[]>;
}

const NODE_RADIUS = 25;
const LEVEL_GAP = 80;
const SIBLING_GAP = 70;

export function tree(
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  _viewport: Viewport
): ComponentResult {
  const { root, children = {} } = props as unknown as TreeProps;

  const commands: RenderCommand[] = [];
  const namedElements: Record<string, string> = {};

  // Build level arrays via BFS
  const levels: string[][] = [];
  const queue: string[] = [root];
  const visited = new Set<string>();

  while (queue.length > 0) {
    const levelSize = queue.length;
    const level: string[] = [];
    for (let i = 0; i < levelSize; i++) {
      const node = queue.shift()!;
      if (visited.has(node)) continue;
      visited.add(node);
      level.push(node);
      const kids = children[node] || [];
      for (const kid of kids) {
        if (!visited.has(kid)) queue.push(kid);
      }
    }
    if (level.length > 0) levels.push(level);
  }

  // Calculate positions
  const positions: Record<string, { x: number; y: number }> = {};
  const maxWidth = Math.max(...levels.map((l) => l.length));
  const totalWidth = maxWidth * SIBLING_GAP;

  levels.forEach((level, depth) => {
    const levelWidth = level.length * SIBLING_GAP;
    const startX = origin.x + (totalWidth - levelWidth) / 2 + SIBLING_GAP / 2;

    level.forEach((node, i) => {
      positions[node] = {
        x: startX + i * SIBLING_GAP,
        y: origin.y + depth * LEVEL_GAP + NODE_RADIUS,
      };
    });
  });

  // Draw edges first (behind nodes)
  for (const [parent, kids] of Object.entries(children)) {
    const parentPos = positions[parent];
    if (!parentPos) continue;
    for (const kid of kids) {
      const kidPos = positions[kid];
      if (!kidPos) continue;
      commands.push({
        action: "line",
        points: [
          [parentPos.x, parentPos.y + NODE_RADIUS],
          [kidPos.x, kidPos.y - NODE_RADIUS],
        ],
        color: "#475569",
        stroke_width: 1.5,
      });
    }
  }

  // Draw nodes
  for (const node of Array.from(visited)) {
    const pos = positions[node];
    if (!pos) continue;
    const nodeId = `tree_${node}`;

    commands.push({
      action: "circle",
      x: pos.x - NODE_RADIUS,
      y: pos.y - NODE_RADIUS,
      width: NODE_RADIUS * 2,
      color: "#3b82f6",
      stroke_width: 2,
      target_id: nodeId,
    });

    commands.push({
      action: "text",
      text: node,
      x: pos.x,
      y: pos.y,
      font_size: 12,
      color: "#e2e8f0",
    });

    namedElements[node] = nodeId;
  }

  const totalHeight = levels.length * LEVEL_GAP;

  return {
    commands,
    bounds: { x: origin.x, y: origin.y, width: totalWidth + SIBLING_GAP, height: totalHeight },
    namedElements,
  };
}
