/**
 * Pure utility functions for the SVG canvas — theme palette, coordinate snapping, grid rendering.
 * No React state or component dependencies.
 */

// ============= Theme-Aware Canvas Palette =============

export function getCanvasPalette() {
  if (typeof window === "undefined") {
    return { stroke: "#E8E4DC", grid: "#4A4843", axis: "#9B9790", error: "#E87B7B", bg: "#08080C" };
  }
  const style = getComputedStyle(document.documentElement);
  const hsl = (v: string) => {
    const val = style.getPropertyValue(v).trim();
    return val ? `hsl(${val})` : "";
  };
  return {
    stroke: hsl("--chalk") || "#E8E4DC",
    grid: hsl("--chalk-faint") || "#4A4843",
    axis: hsl("--chalk-soft") || "#9B9790",
    error: hsl("--ember") || "#E87B7B",
    bg: hsl("--void") || "#08080C",
  };
}

// ============= Coordinate Snapping =============

export const GRID_SNAP = 20;

export function snap(v: number): number {
  return Math.round(v / GRID_SNAP) * GRID_SNAP;
}

export function snapPoints(pts: [number, number][]): [number, number][] {
  return pts.map(([x, y]) => [snap(x), snap(y)]);
}

// ============= Grid Background =============

// Grid covers a large area so it's visible when panning
export const GRID_EXTENT = 4000;

export function renderGrid(
  svg: SVGSVGElement,
  _width: number,
  _height: number,
  gridSize = 40
): void {
  const existing = svg.querySelector("#canvas-grid");
  if (existing) existing.remove();

  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("id", "canvas-grid");
  g.setAttribute("pointer-events", "none");

  const palette = getCanvasPalette();
  const extent = GRID_EXTENT;

  // Minor gridlines
  for (let x = gridSize; x < extent; x += gridSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("x2", String(x));
    line.setAttribute("y2", String(extent));
    line.setAttribute("stroke", palette.grid);
    line.setAttribute("stroke-width", "0.5");
    line.setAttribute("opacity", "0.3");
    g.appendChild(line);
  }
  for (let y = gridSize; y < extent; y += gridSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "0");
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(extent));
    line.setAttribute("y2", String(y));
    line.setAttribute("stroke", palette.grid);
    line.setAttribute("stroke-width", "0.5");
    line.setAttribute("opacity", "0.3");
    g.appendChild(line);
  }

  // Major gridlines (every 4th)
  const majorSize = gridSize * 4;
  for (let x = majorSize; x < extent; x += majorSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("x2", String(x));
    line.setAttribute("y2", String(extent));
    line.setAttribute("stroke", palette.grid);
    line.setAttribute("stroke-width", "0.8");
    line.setAttribute("opacity", "0.5");
    g.appendChild(line);
  }
  for (let y = majorSize; y < extent; y += majorSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "0");
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(extent));
    line.setAttribute("y2", String(y));
    line.setAttribute("stroke", palette.grid);
    line.setAttribute("stroke-width", "0.8");
    line.setAttribute("opacity", "0.5");
    g.appendChild(line);
  }

  svg.insertBefore(g, svg.firstChild);
}
