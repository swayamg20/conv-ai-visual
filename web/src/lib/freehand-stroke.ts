import { getStroke } from "perfect-freehand";

/**
 * Convert point arrays into variable-width "ink stroke" SVG path strings.
 * Uses perfect-freehand to simulate pressure-sensitive pen strokes.
 * Returns a filled path (no stroke) — gives a natural hand-drawn pen look.
 */
export function getInkStrokePath(
  points: [number, number][],
  options?: {
    size?: number;
    thinning?: number;
    smoothing?: number;
    streamline?: number;
  }
): string {
  const strokePoints = getStroke(points, {
    size: options?.size ?? 3,
    thinning: options?.thinning ?? 0.5,
    smoothing: options?.smoothing ?? 0.5,
    streamline: options?.streamline ?? 0.5,
    simulatePressure: true,
  });

  return svgPathFromStroke(strokePoints);
}

/**
 * Convert stroke outline points into a smooth SVG path "d" attribute.
 * Uses quadratic bezier curves (standard algorithm from perfect-freehand docs).
 */
function svgPathFromStroke(stroke: number[][]): string {
  if (stroke.length === 0) return "";

  const d: string[] = [];
  const [first, ...rest] = stroke;

  d.push(`M ${first[0].toFixed(2)} ${first[1].toFixed(2)}`);

  for (let i = 0; i < rest.length; i++) {
    const [x0, y0] = rest[i];
    if (i < rest.length - 1) {
      const [x1, y1] = rest[i + 1];
      d.push(`Q ${x0.toFixed(2)} ${y0.toFixed(2)} ${((x0 + x1) / 2).toFixed(2)} ${((y0 + y1) / 2).toFixed(2)}`);
    } else {
      d.push(`L ${x0.toFixed(2)} ${y0.toFixed(2)}`);
    }
  }

  d.push("Z");
  return d.join(" ");
}
