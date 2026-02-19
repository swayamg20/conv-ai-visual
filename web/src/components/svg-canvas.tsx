"use client";

import {
  useRef,
  useEffect,
  useImperativeHandle,
  forwardRef,
  useCallback,
  useState,
} from "react";
import rough from "roughjs";
import type { RoughSVG } from "roughjs/bin/svg";
import { gsap } from "gsap";
import {
  createTimeline,
  animateDrawOn,
  animateProgressivePath,
  animateColorPulse,
  animateCrossOut,
  EASING,
  DURATION,
} from "@/lib/gsap-setup";
import type { CanvasOperation } from "@/hooks/use-webrtc";
import katex from "katex";
import "katex/dist/katex.min.css";

// ============= Types =============

export interface AnimationOperation {
  type: "animation";
  target_id: string;
  properties: {
    x?: number;
    y?: number;
    opacity?: number;
    scale?: number;
    rotation?: number;
    [key: string]: any;
  };
  duration: number;
  ease: string;
  delay?: number;
}

export interface LatexOperation {
  type: "latex";
  id: string;
  latex: string;
  x: number;
  y: number;
  font_size: number;
  color: string;
}

export interface TeachingStep {
  action: string;
  element?: CanvasOperation;
  target_id?: string;
  properties?: Record<string, any>;
  duration?: number;
  speech_cue?: string;
  latex?: string;
  text?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  font_size?: number;
  color?: string;
  fill?: string;
  stroke_width?: number;
  points?: [number, number][];
  font_family?: string;
  label?: string;
  roughness?: number;
  animate_style?: "draw" | "fade" | "scale" | "none";
  highlight_color?: string;
}

export interface TeachingSequence {
  steps: TeachingStep[];
}

export interface FunctionPlotData {
  type: "function_plot";
  function: string;
  points: number[][];
  x_range: number[];
  y_range: number[];
  color: string;
  animate: boolean;
  show_axes: boolean;
}

interface SVGElementData {
  element: SVGElement;
  id: string;
  type: string;
  x: number;
  y: number;
  data: any;
}

export interface SVGCanvasHandle {
  render(operations: CanvasOperation[]): void;
  animate(animation: AnimationOperation): gsap.core.Tween | null;
  renderLatex(latex: LatexOperation): void;
  createSequence(sequence: TeachingSequence): gsap.core.Timeline;
  renderFunctionPlot(plot: FunctionPlotData): void;
  clear(): void;
  saveAsImage(): void;
  zoomIn(): void;
  zoomOut(): void;
  resetZoom(): void;
}

interface SVGCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  showGrid?: boolean;
}

// ============= Layout Normalization =============

const GRID_SNAP = 20; // snap coordinates to 20px grid

function snap(v: number): number {
  return Math.round(v / GRID_SNAP) * GRID_SNAP;
}

function snapPoints(pts: [number, number][]): [number, number][] {
  return pts.map(([x, y]) => [snap(x), snap(y)]);
}

type ShapeBounds = { x: number; y: number; w: number; h: number };

/**
 * Pre-process a list of teaching steps to improve layout:
 * 1. Snap all coordinates to a 20px grid
 * 2. Auto-center text labels inside the most recent preceding shape
 * 3. Ensure minimum shape sizes
 */
function normalizeSteps(steps: TeachingStep[]): TeachingStep[] {
  let lastShapeBounds: ShapeBounds | null = null;
  const SHAPE_ACTIONS = new Set(["rect", "circle", "ellipse"]);

  return steps.map((step) => {
    const s = { ...step };

    // Snap coordinates
    if (s.x != null) s.x = snap(s.x);
    if (s.y != null) s.y = snap(s.y);
    if (s.width != null) s.width = Math.max(snap(s.width), 40);
    if (s.height != null) s.height = Math.max(snap(s.height), 40);
    if (s.points) s.points = snapPoints(s.points);

    // Track shape bounds
    if (SHAPE_ACTIONS.has(s.action) && s.x != null && s.y != null) {
      const w = s.width ?? (s.action === "circle" ? s.width ?? 60 : 100);
      const h = s.height ?? (s.action === "circle" ? w : 60);
      lastShapeBounds = { x: s.x, y: s.y, w, h };
    }

    // Auto-center text inside preceding shape if it looks like a label
    if (s.action === "text" && lastShapeBounds && s.text) {
      const isShortLabel = (s.text.length <= 20);
      const b = lastShapeBounds;
      // Check if the text position is roughly near the shape (within 40px)
      const textX = s.x ?? 0;
      const textY = s.y ?? 0;
      const nearShape =
        Math.abs(textX - b.x) < b.w + 40 &&
        Math.abs(textY - b.y) < b.h + 40;

      if (isShortLabel && nearShape) {
        const fontSize = s.font_size ?? 16;
        const estTextWidth = s.text.length * fontSize * 0.55;
        s.x = snap(b.x + b.w / 2 - estTextWidth / 2);
        s.y = snap(b.y + b.h / 2 + fontSize / 3);
        s.font_size = Math.min(fontSize, b.h * 0.4);
        // Also set text-anchor to middle for precise centering
        (s as any)._centered = true;
      }

      // If text is a standalone label below a shape, center it under the shape
      if (isShortLabel && nearShape && textY > b.y + b.h - 10) {
        s.x = snap(b.x + b.w / 2 - (s.text.length * (s.font_size ?? 14) * 0.55) / 2);
        s.y = snap(b.y + b.h + 20);
      }
    }

    // For non-shape/non-text actions, don't update lastShapeBounds
    if (s.action === "text" || s.action === "latex") {
      // don't overwrite lastShapeBounds
    } else if (!SHAPE_ACTIONS.has(s.action)) {
      lastShapeBounds = null;
    }

    return s;
  });
}

/**
 * Normalize a single CanvasOperation (snap to grid, minimum sizes).
 */
function normalizeOp(op: CanvasOperation): CanvasOperation {
  const o = { ...op };
  if (o.x != null) o.x = snap(o.x);
  if (o.y != null) o.y = snap(o.y);
  if (o.width != null) o.width = Math.max(snap(o.width), 40);
  if (o.height != null) o.height = Math.max(snap(o.height), 40);
  if (o.points) o.points = snapPoints(o.points);
  return o;
}

// ============= Grid Background =============

function renderGrid(
  svg: SVGSVGElement,
  width: number,
  height: number,
  gridSize: number = 40
) {
  const existing = svg.querySelector("#canvas-grid");
  if (existing) existing.remove();

  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("id", "canvas-grid");
  g.setAttribute("pointer-events", "none");

  // Minor gridlines
  for (let x = gridSize; x < width; x += gridSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("x2", String(x));
    line.setAttribute("y2", String(height));
    line.setAttribute("stroke", "#e5e7eb");
    line.setAttribute("stroke-width", "0.5");
    line.setAttribute("opacity", "0.5");
    g.appendChild(line);
  }
  for (let y = gridSize; y < height; y += gridSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "0");
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(width));
    line.setAttribute("y2", String(y));
    line.setAttribute("stroke", "#e5e7eb");
    line.setAttribute("stroke-width", "0.5");
    line.setAttribute("opacity", "0.5");
    g.appendChild(line);
  }

  // Major gridlines (every 4th)
  const majorSize = gridSize * 4;
  for (let x = majorSize; x < width; x += majorSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("x2", String(x));
    line.setAttribute("y2", String(height));
    line.setAttribute("stroke", "#d1d5db");
    line.setAttribute("stroke-width", "0.8");
    line.setAttribute("opacity", "0.6");
    g.appendChild(line);
  }
  for (let y = majorSize; y < height; y += majorSize) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "0");
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(width));
    line.setAttribute("y2", String(y));
    line.setAttribute("stroke", "#d1d5db");
    line.setAttribute("stroke-width", "0.8");
    line.setAttribute("opacity", "0.6");
    g.appendChild(line);
  }

  svg.insertBefore(g, svg.firstChild);
}

// ============= Component =============

export const SVGCanvas = forwardRef<SVGCanvasHandle, SVGCanvasProps>(
  ({ width = 800, height = 600, className, showGrid = true }, ref) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const roughRef = useRef<RoughSVG | null>(null);
    const elementsRef = useRef<Map<string, SVGElementData>>(new Map());
    const timelinesRef = useRef<Map<string, gsap.core.Timeline>>(new Map());
    const sequenceQueueRef = useRef<gsap.core.Timeline[]>([]);
    const isPlayingRef = useRef(false);
    const [, setForceUpdate] = useState(0);
    const [zoomLevel, setZoomLevel] = useState(1);

    useEffect(() => {
      if (svgRef.current) {
        roughRef.current = rough.svg(svgRef.current);
        if (showGrid) renderGrid(svgRef.current, width, height);
      }
    }, [width, height, showGrid]);

    // ============= Helpers =============

    const generateId = useCallback(
      () => `elem_${Math.random().toString(36).substring(2, 10)}`,
      []
    );

    const createGroup = useCallback((id: string): SVGGElement => {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("id", id);
      g.setAttribute("data-element-id", id);
      return g;
    }, []);

    const createArrowhead = useCallback((id: string, color: string) => {
      if (!svgRef.current) return;
      const defs =
        svgRef.current.querySelector("defs") ||
        document.createElementNS("http://www.w3.org/2000/svg", "defs");
      if (!svgRef.current.querySelector("defs")) {
        svgRef.current.appendChild(defs);
      }
      const markerId = `arrowhead-${id}`;
      if (defs.querySelector(`#${markerId}`)) return markerId;

      const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
      marker.setAttribute("id", markerId);
      marker.setAttribute("markerWidth", "10");
      marker.setAttribute("markerHeight", "10");
      marker.setAttribute("refX", "8");
      marker.setAttribute("refY", "3");
      marker.setAttribute("orient", "auto");
      marker.setAttribute("markerUnits", "strokeWidth");

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M0,0 L0,6 L9,3 z");
      path.setAttribute("fill", color);
      marker.appendChild(path);
      defs.appendChild(marker);
      return markerId;
    }, []);

    const getRoughOptions = useCallback(
      (op: CanvasOperation & { roughness?: number }) => ({
        stroke: op.color ?? "#3b82f6",
        strokeWidth: op.stroke_width ?? 2,
        fill: op.fill || undefined,
        roughness: op.roughness ?? 1.5,
        fillStyle: op.fill ? "solid" : undefined,
      }),
      []
    );

    // ============= Drawing Primitives =============

    const drawRect = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;
        const g = createGroup(op.id || generateId());
        const node = roughRef.current.rectangle(
          op.x ?? 0,
          op.y ?? 0,
          op.width ?? 100,
          op.height ?? 100,
          getRoughOptions(op)
        );
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions]
    );

    const drawCircle = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;
        const g = createGroup(op.id || generateId());
        const cx = (op.x ?? 0) + (op.width ?? 50) / 2;
        const cy = (op.y ?? 0) + (op.width ?? 50) / 2;
        const diameter = op.width ?? 50;
        const node = roughRef.current.circle(cx, cy, diameter, getRoughOptions(op));
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions]
    );

    const drawEllipse = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;
        const g = createGroup(op.id || generateId());
        const cx = (op.x ?? 0) + (op.width ?? 100) / 2;
        const cy = (op.y ?? 0) + (op.height ?? 80) / 2;
        const node = roughRef.current.ellipse(
          cx,
          cy,
          op.width ?? 100,
          op.height ?? 80,
          getRoughOptions(op)
        );
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions]
    );

    const drawLine = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current || !op.points || op.points.length < 2) return null;
        const g = createGroup(op.id || generateId());
        const start = op.points[0];
        const end = op.points[op.points.length - 1];
        const node = roughRef.current.line(
          start[0],
          start[1],
          end[0],
          end[1],
          getRoughOptions(op)
        );
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions]
    );

    const drawArrow = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current || !op.points || op.points.length < 2) return null;
        const g = createGroup(op.id || generateId());
        const start = op.points[0];
        const end = op.points[op.points.length - 1];
        const node = roughRef.current.line(
          start[0],
          start[1],
          end[0],
          end[1],
          getRoughOptions(op)
        );
        const markerId = createArrowhead(op.id || generateId(), op.color ?? "#3b82f6");
        const p = node.querySelector("path");
        if (p && markerId) p.setAttribute("marker-end", `url(#${markerId})`);
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions, createArrowhead]
    );

    const drawText = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!svgRef.current) return null;
        const g = createGroup(op.id || generateId());
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", String(op.x ?? 0));
        text.setAttribute("y", String(op.y ?? 0));
        text.setAttribute("fill", op.color ?? "#000000");
        text.setAttribute("font-size", String(op.font_size ?? 16));
        text.setAttribute("font-family", op.font_family ?? "var(--font-handwriting), var(--font-handwriting-alt), cursive, sans-serif");
        if ((op as any)._centered) {
          text.setAttribute("text-anchor", "middle");
          text.setAttribute("dominant-baseline", "central");
        }
        text.textContent = op.text ?? "";
        g.appendChild(text);
        return g;
      },
      [createGroup, generateId]
    );

    const drawPath = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current || !op.points || op.points.length < 2) return null;
        const g = createGroup(op.id || generateId());
        const pathString = op.points
          .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
          .join(" ");
        const node = roughRef.current.path(pathString, {
          ...getRoughOptions(op),
          roughness: (op as any).roughness ?? 0.5,
        });
        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, getRoughOptions]
    );

    const drawLatex = useCallback(
      (latexOp: LatexOperation): SVGElement | null => {
        if (!svgRef.current) return null;
        const g = createGroup(latexOp.id);
        const html = katex.renderToString(latexOp.latex, {
          throwOnError: false,
          displayMode: true,
        });
        const fo = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
        fo.setAttribute("x", String(latexOp.x));
        fo.setAttribute("y", String(latexOp.y));
        fo.setAttribute("width", "500");
        fo.setAttribute("height", "120");
        const div = document.createElement("div");
        div.innerHTML = html;
        div.style.color = latexOp.color;
        div.style.fontSize = `${latexOp.font_size}px`;
        fo.appendChild(div);
        g.appendChild(fo);
        return g;
      },
      [createGroup]
    );

    // ============= Function Plot =============

    const renderFunctionPlot = useCallback(
      (plot: FunctionPlotData) => {
        if (!svgRef.current || !plot.points || plot.points.length < 2) return;

        const plotId = `plot_${generateId()}`;
        const g = createGroup(plotId);

        const canvasW = width;
        const canvasH = height;
        const margin = 60;
        const plotW = canvasW - margin * 2;
        const plotH = canvasH - margin * 2;

        const [minX, maxX] = plot.x_range;
        const [minY, maxY] = plot.y_range;
        const scaleX = (x: number) => margin + ((x - minX) / (maxX - minX)) * plotW;
        const scaleY = (y: number) => margin + ((maxY - y) / (maxY - minY)) * plotH;

        if (plot.show_axes) {
          // X-axis
          const xAxisY = scaleY(0);
          if (xAxisY >= margin && xAxisY <= canvasH - margin) {
            const xLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
            xLine.setAttribute("x1", String(margin));
            xLine.setAttribute("y1", String(xAxisY));
            xLine.setAttribute("x2", String(canvasW - margin));
            xLine.setAttribute("y2", String(xAxisY));
            xLine.setAttribute("stroke", "#9ca3af");
            xLine.setAttribute("stroke-width", "1.5");
            g.appendChild(xLine);

            // X tick labels
            const xStep = (maxX - minX) / 8;
            for (let v = Math.ceil(minX / xStep) * xStep; v <= maxX; v += xStep) {
              if (Math.abs(v) < 0.001) continue;
              const tx = scaleX(v);
              const tick = document.createElementNS("http://www.w3.org/2000/svg", "line");
              tick.setAttribute("x1", String(tx));
              tick.setAttribute("y1", String(xAxisY - 4));
              tick.setAttribute("x2", String(tx));
              tick.setAttribute("y2", String(xAxisY + 4));
              tick.setAttribute("stroke", "#9ca3af");
              tick.setAttribute("stroke-width", "1");
              g.appendChild(tick);

              const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
              label.setAttribute("x", String(tx));
              label.setAttribute("y", String(xAxisY + 18));
              label.setAttribute("text-anchor", "middle");
              label.setAttribute("fill", "#6b7280");
              label.setAttribute("font-size", "11");
              label.textContent = Number(v.toFixed(1)).toString();
              g.appendChild(label);
            }
          }

          // Y-axis
          const yAxisX = scaleX(0);
          if (yAxisX >= margin && yAxisX <= canvasW - margin) {
            const yLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
            yLine.setAttribute("x1", String(yAxisX));
            yLine.setAttribute("y1", String(margin));
            yLine.setAttribute("x2", String(yAxisX));
            yLine.setAttribute("y2", String(canvasH - margin));
            yLine.setAttribute("stroke", "#9ca3af");
            yLine.setAttribute("stroke-width", "1.5");
            g.appendChild(yLine);

            const yStep = (maxY - minY) / 6;
            for (let v = Math.ceil(minY / yStep) * yStep; v <= maxY; v += yStep) {
              if (Math.abs(v) < 0.001) continue;
              const ty = scaleY(v);
              const tick = document.createElementNS("http://www.w3.org/2000/svg", "line");
              tick.setAttribute("x1", String(yAxisX - 4));
              tick.setAttribute("y1", String(ty));
              tick.setAttribute("x2", String(yAxisX + 4));
              tick.setAttribute("y2", String(ty));
              tick.setAttribute("stroke", "#9ca3af");
              tick.setAttribute("stroke-width", "1");
              g.appendChild(tick);

              const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
              label.setAttribute("x", String(yAxisX - 10));
              label.setAttribute("y", String(ty + 4));
              label.setAttribute("text-anchor", "end");
              label.setAttribute("fill", "#6b7280");
              label.setAttribute("font-size", "11");
              label.textContent = Number(v.toFixed(1)).toString();
              g.appendChild(label);
            }
          }
        }

        // Build the curve path
        const pathD = plot.points
          .map(([px, py], i) => {
            const sx = scaleX(px);
            const sy = scaleY(py);
            return `${i === 0 ? "M" : "L"}${sx.toFixed(1)},${sy.toFixed(1)}`;
          })
          .join(" ");

        const curvePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        curvePath.setAttribute("d", pathD);
        curvePath.setAttribute("fill", "none");
        curvePath.setAttribute("stroke", plot.color);
        curvePath.setAttribute("stroke-width", "2.5");
        curvePath.setAttribute("stroke-linecap", "round");
        curvePath.setAttribute("stroke-linejoin", "round");
        g.appendChild(curvePath);

        svgRef.current.appendChild(g);

        // Animate: progressive draw or instant
        if (plot.animate) {
          gsap.set(g, { opacity: 1 });
          // Fade in axes
          const axes = g.querySelectorAll("line, text");
          gsap.fromTo(axes, { opacity: 0 }, { opacity: 1, duration: DURATION.fast, stagger: 0.02 });
          // Progressive draw of the curve
          animateProgressivePath(curvePath, DURATION.verySlow, EASING.draw);
        }

        elementsRef.current.set(plotId, {
          element: g,
          id: plotId,
          type: "function_plot",
          x: margin,
          y: margin,
          data: plot,
        });

        setForceUpdate((n) => n + 1);
      },
      [width, height, generateId, createGroup]
    );

    // ============= Render Operations =============

    const render = useCallback(
      (operations: CanvasOperation[]) => {
        if (!svgRef.current) return;

        for (const rawOp of operations) {
          const op = (rawOp.action === "clear" || rawOp.action === "delete" || rawOp.action === "highlight" || rawOp.action === "crossout")
            ? rawOp
            : normalizeOp(rawOp);
          if (op.action === "clear") {
            elementsRef.current.clear();
            if (svgRef.current) {
              svgRef.current.innerHTML = "";
              if (showGrid) renderGrid(svgRef.current, width, height);
            }
            continue;
          }

          if (op.action === "delete") {
            const targetId = op.id || op.target_id;
            if (targetId && elementsRef.current.has(targetId)) {
              const data = elementsRef.current.get(targetId);
              if (data) {
                gsap.to(data.element, {
                  opacity: 0,
                  duration: DURATION.fast,
                  ease: EASING.smooth,
                  onComplete: () => data.element.remove(),
                });
              }
              elementsRef.current.delete(targetId);
            }
            continue;
          }

          if (op.action === "crossout") {
            const targetId = op.id || op.target_id;
            if (targetId && elementsRef.current.has(targetId) && svgRef.current) {
              const data = elementsRef.current.get(targetId);
              if (data) {
                animateCrossOut(
                  svgRef.current,
                  data.element,
                  op.color ?? "#ef4444",
                  DURATION.fast
                );
              }
            }
            continue;
          }

          let element: SVGElement | null = null;
          const elemId = op.id || generateId();
          const animateStyle = (op as any).animate_style ?? "draw";

          switch (op.action) {
            case "rect":
              element = drawRect({ ...op, id: elemId });
              break;
            case "circle":
              element = drawCircle({ ...op, id: elemId });
              break;
            case "ellipse":
              element = drawEllipse({ ...op, id: elemId });
              break;
            case "line":
              element = drawLine({ ...op, id: elemId });
              break;
            case "arrow":
              element = drawArrow({ ...op, id: elemId });
              break;
            case "text":
              element = drawText({ ...op, id: elemId });
              break;
            case "path":
              element = drawPath({ ...op, id: elemId });
              break;
            case "highlight": {
              const hTargetId = op.id || op.target_id;
              if (hTargetId && elementsRef.current.has(hTargetId)) {
                const data = elementsRef.current.get(hTargetId);
                if (data) {
                  const hColor = (op as any).highlight_color;
                  if (hColor) {
                    animateColorPulse(data.element, hColor, 0.4, 2);
                  } else {
                    gsap.set(data.element, { transformOrigin: "center center" });
                    gsap.to(data.element, {
                      scale: 1.15,
                      duration: 0.3,
                      yoyo: true,
                      repeat: 1,
                      ease: EASING.teaching,
                    });
                  }
                }
              }
              continue;
            }
          }

          if (element) {
            gsap.set(element, { opacity: 0 });
            svgRef.current.appendChild(element);

            // Use draw-on animation for shapes, fade for text/latex
            if (
              animateStyle === "draw" &&
              op.action !== "text" &&
              element.querySelectorAll("path").length > 0
            ) {
              animateDrawOn(element, DURATION.draw, EASING.draw);
            } else if (animateStyle === "scale") {
              gsap.fromTo(
                element,
                { opacity: 0, scale: 0, transformOrigin: "center center" },
                { opacity: 1, scale: 1, duration: DURATION.normal, ease: EASING.back }
              );
            } else {
              gsap.to(element, {
                opacity: 1,
                duration: DURATION.fast,
                ease: EASING.teaching,
              });
            }

            elementsRef.current.set(elemId, {
              element,
              id: elemId,
              type: op.action,
              x: op.x ?? 0,
              y: op.y ?? 0,
              data: op,
            });
          }
        }

        setForceUpdate((n) => n + 1);
      },
      [
        drawRect, drawCircle, drawEllipse, drawLine, drawArrow, drawText, drawPath,
        generateId, showGrid, width, height,
      ]
    );

    // ============= Animate Element =============

    const animate = useCallback(
      (animation: AnimationOperation): gsap.core.Tween | null => {
        const targetData = elementsRef.current.get(animation.target_id);
        if (!targetData) {
          console.warn(`Animation target not found: ${animation.target_id}`);
          return null;
        }
        return gsap.to(targetData.element, {
          ...animation.properties,
          duration: animation.duration,
          ease: animation.ease,
          delay: animation.delay || 0,
        });
      },
      []
    );

    // ============= Render LaTeX =============

    const renderLatex = useCallback(
      (latexOp: LatexOperation) => {
        if (!svgRef.current) return;
        const element = drawLatex(latexOp);
        if (element) {
          gsap.set(element, { opacity: 0 });
          svgRef.current.appendChild(element);
          gsap.to(element, {
            opacity: 1,
            duration: DURATION.fast,
            ease: EASING.teaching,
          });
          elementsRef.current.set(latexOp.id, {
            element,
            id: latexOp.id,
            type: "latex",
            x: latexOp.x,
            y: latexOp.y,
            data: latexOp,
          });
        }
      },
      [drawLatex]
    );

    // ============= Sequence Queue =============

    const playNextSequence = useCallback(() => {
      if (sequenceQueueRef.current.length === 0) {
        isPlayingRef.current = false;
        return;
      }
      isPlayingRef.current = true;
      const next = sequenceQueueRef.current.shift()!;
      next.eventCallback("onComplete", () => playNextSequence());
      next.play();
    }, []);

    // ============= Teaching Sequence =============

    const createSequence = useCallback(
      (sequence: TeachingSequence): gsap.core.Timeline => {
        const tl = createTimeline({ paused: true });
        const timelineId = `tl_${Math.random().toString(36).substring(2, 10)}`;
        const normalizedSteps = normalizeSteps(sequence.steps);

        for (const step of normalizedSteps) {
          const entryStyle = step.animate_style ?? "draw";

          switch (step.action) {
            case "clear":
              tl.add(() => {
                elementsRef.current.clear();
                if (svgRef.current) {
                  svgRef.current.innerHTML = "";
                  if (showGrid) renderGrid(svgRef.current, width, height);
                }
              });
              break;

            case "draw":
              if (step.element) {
                tl.add(() => {
                  render([{ ...step.element!, animate_style: entryStyle } as any]);
                }, "+=0.15");
              }
              break;

            case "animate":
              if (step.target_id && step.properties) {
                const targetId = step.target_id;
                const props = step.properties;
                const dur = step.duration ?? DURATION.normal;
                tl.add(() => {
                  const targetData = elementsRef.current.get(targetId);
                  if (targetData) {
                    gsap.to(targetData.element, { ...props, duration: dur });
                  }
                }, ">");
              }
              break;

            case "latex":
              if (step.latex && step.x !== undefined && step.y !== undefined) {
                const latexOp: LatexOperation = {
                  type: "latex",
                  id: step.target_id || `latex_${generateId()}`,
                  latex: step.latex,
                  x: step.x,
                  y: step.y,
                  font_size: step.font_size ?? 20,
                  color: step.color ?? "#000000",
                };
                tl.add(() => renderLatex(latexOp), "+=0.15");
              }
              break;

            case "text": {
              const textId = step.target_id || `text_${generateId()}`;
              const textOp: CanvasOperation = {
                action: "text",
                id: textId,
                text: step.text ?? step.speech_cue ?? "",
                x: step.x ?? 0,
                y: step.y ?? 0,
                color: step.color ?? "#000000",
                font_size: step.font_size ?? 16,
                font_family: step.font_family,
              };
              tl.add(() => render([textOp]), "+=0.15");
              break;
            }

            case "highlight":
              if (step.target_id) {
                const hTargetId = step.target_id;
                const hDuration = step.duration ?? 0.3;
                const hColor = step.highlight_color;
                tl.add(() => {
                  const targetData = elementsRef.current.get(hTargetId);
                  if (targetData) {
                    if (hColor) {
                      animateColorPulse(targetData.element, hColor, hDuration, 2);
                    } else {
                      gsap.set(targetData.element, { transformOrigin: "center center" });
                      gsap.to(targetData.element, {
                        scale: 1.15,
                        duration: hDuration,
                        yoyo: true,
                        repeat: 1,
                        ease: EASING.teaching,
                      });
                    }
                  }
                }, ">");
              }
              break;

            case "crossout":
              if (step.target_id) {
                const coTargetId = step.target_id;
                const coColor = step.color ?? "#ef4444";
                tl.add(() => {
                  const targetData = elementsRef.current.get(coTargetId);
                  if (targetData && svgRef.current) {
                    animateCrossOut(svgRef.current, targetData.element, coColor, DURATION.fast);
                  }
                }, ">");
              }
              break;

            case "pause":
              tl.to({}, { duration: step.duration ?? 0.5 });
              break;

            default: {
              // Shape primitives (rect, circle, ellipse, line, arrow, path)
              const shapeOp: CanvasOperation & { roughness?: number; animate_style?: string } = {
                action: step.action,
                id: step.target_id || step.label || `${step.action}_${generateId()}`,
                x: step.x,
                y: step.y,
                width: step.width,
                height: step.height,
                color: step.color,
                fill: step.fill,
                stroke_width: step.stroke_width,
                points: step.points,
                text: step.text,
                font_size: step.font_size,
                font_family: step.font_family,
                roughness: step.roughness,
                animate_style: entryStyle,
              };
              tl.add(() => render([shapeOp as CanvasOperation]), "+=0.15");
              break;
            }
          }
        }

        timelinesRef.current.set(timelineId, tl);
        sequenceQueueRef.current.push(tl);

        if (!isPlayingRef.current) {
          playNextSequence();
        }

        return tl;
      },
      [render, renderLatex, generateId, playNextSequence, showGrid, width, height]
    );

    // ============= Clear =============

    const clear = useCallback(() => {
      sequenceQueueRef.current.forEach((tl) => tl.kill());
      sequenceQueueRef.current = [];
      isPlayingRef.current = false;
      timelinesRef.current.forEach((tl) => tl.kill());
      timelinesRef.current.clear();
      elementsRef.current.clear();
      if (svgRef.current) {
        svgRef.current.innerHTML = "";
        if (showGrid) renderGrid(svgRef.current, width, height);
      }
      setForceUpdate((n) => n + 1);
    }, [showGrid, width, height]);

    // ============= Zoom =============

    const zoomIn = useCallback(() => {
      setZoomLevel((z) => {
        const next = Math.min(z + 0.25, 3);
        if (svgRef.current) {
          const vbW = width / next;
          const vbH = height / next;
          const ox = (width - vbW) / 2;
          const oy = (height - vbH) / 2;
          gsap.to(svgRef.current, {
            attr: { viewBox: `${ox} ${oy} ${vbW} ${vbH}` },
            duration: DURATION.fast,
            ease: EASING.smooth,
          });
        }
        return next;
      });
    }, [width, height]);

    const zoomOut = useCallback(() => {
      setZoomLevel((z) => {
        const next = Math.max(z - 0.25, 0.5);
        if (svgRef.current) {
          const vbW = width / next;
          const vbH = height / next;
          const ox = (width - vbW) / 2;
          const oy = (height - vbH) / 2;
          gsap.to(svgRef.current, {
            attr: { viewBox: `${ox} ${oy} ${vbW} ${vbH}` },
            duration: DURATION.fast,
            ease: EASING.smooth,
          });
        }
        return next;
      });
    }, [width, height]);

    const resetZoom = useCallback(() => {
      setZoomLevel(1);
      if (svgRef.current) {
        gsap.to(svgRef.current, {
          attr: { viewBox: `0 0 ${width} ${height}` },
          duration: DURATION.fast,
          ease: EASING.smooth,
        });
      }
    }, [width, height]);

    // ============= Save As Image =============

    const saveAsImage = useCallback(() => {
      const svg = svgRef.current;
      if (!svg) return;

      const clone = svg.cloneNode(true) as SVGSVGElement;
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
      // Reset viewBox for export
      clone.setAttribute("viewBox", `0 0 ${width} ${height}`);

      const bgRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bgRect.setAttribute("width", String(width));
      bgRect.setAttribute("height", String(height));
      bgRect.setAttribute("fill", "#ffffff");
      clone.insertBefore(bgRect, clone.firstChild);

      let katexCss = "";
      try {
        for (const sheet of Array.from(document.styleSheets)) {
          if (sheet.href?.includes("katex")) {
            for (const rule of Array.from(sheet.cssRules)) {
              katexCss += rule.cssText + "\n";
            }
          }
        }
      } catch {
        /* CORS */
      }
      if (katexCss) {
        const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
        styleEl.textContent = katexCss;
        clone.insertBefore(styleEl, clone.firstChild);
      }

      const serializer = new XMLSerializer();
      const svgString = serializer.serializeToString(clone);
      const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(svgBlob);

      const hasForeignObject = clone.querySelector("foreignObject") !== null;
      const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, "-");

      if (hasForeignObject) {
        const a = document.createElement("a");
        a.download = `canvas_${timestamp}.svg`;
        a.href = url;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 100);
      } else {
        const img = new Image();
        img.onload = () => {
          const canvas = document.createElement("canvas");
          const scale = 2;
          canvas.width = width * scale;
          canvas.height = height * scale;
          const ctx = canvas.getContext("2d")!;
          ctx.scale(scale, scale);
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, width, height);
          ctx.drawImage(img, 0, 0, width, height);

          canvas.toBlob((blob) => {
            if (!blob) return;
            const pngUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.download = `canvas_${timestamp}.png`;
            a.href = pngUrl;
            a.click();
            URL.revokeObjectURL(pngUrl);
          }, "image/png");
          URL.revokeObjectURL(url);
        };
        img.src = url;
      }
    }, [width, height]);

    // ============= Expose Handle =============

    useImperativeHandle(
      ref,
      () => ({
        render,
        animate,
        renderLatex,
        createSequence,
        renderFunctionPlot,
        clear,
        saveAsImage,
        zoomIn,
        zoomOut,
        resetZoom,
      }),
      [render, animate, renderLatex, createSequence, renderFunctionPlot, clear, saveAsImage, zoomIn, zoomOut, resetZoom]
    );

    return (
      <div className={`relative group ${className ?? ""}`}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          style={{
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            background: "#ffffff",
          }}
        />
        {/* Toolbar */}
        <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={zoomIn}
            className="p-1.5 rounded-md bg-black/50 hover:bg-black/70 text-white/70 hover:text-white transition-all text-xs font-mono"
            title="Zoom in"
          >
            +
          </button>
          <button
            onClick={resetZoom}
            className="p-1.5 rounded-md bg-black/50 hover:bg-black/70 text-white/70 hover:text-white transition-all text-xs font-mono min-w-[2.5rem] text-center"
            title="Reset zoom"
          >
            {Math.round(zoomLevel * 100)}%
          </button>
          <button
            onClick={zoomOut}
            className="p-1.5 rounded-md bg-black/50 hover:bg-black/70 text-white/70 hover:text-white transition-all text-xs font-mono"
            title="Zoom out"
          >
            -
          </button>
          <div className="w-px h-4 bg-white/20 mx-1" />
          <button
            onClick={saveAsImage}
            className="p-1.5 rounded-md bg-black/50 hover:bg-black/70 text-white/70 hover:text-white transition-all"
            title="Save as PNG"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </button>
        </div>
      </div>
    );
  }
);

SVGCanvas.displayName = "SVGCanvas";
