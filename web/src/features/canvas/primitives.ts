import katex from "katex";
import type { RoughSVG } from "roughjs/bin/svg";

import { getInkStrokePath } from "@/lib/freehand-stroke";

import type {
  CanvasOperation,
  CanvasPalette,
  FunctionPlotData,
  LatexOperation,
} from "./types";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

export interface PrimitiveRendererContext {
  svg: SVGSVGElement;
  rough: RoughSVG | null;
  palette: CanvasPalette;
  generateId: () => string;
}

export interface FunctionPlotElements {
  id: string;
  group: SVGGElement;
  curve: SVGPathElement;
  axes: NodeListOf<SVGLineElement | SVGTextElement>;
  margin: number;
}

export interface SVGPrimitiveRenderer {
  draw(operation: CanvasOperation): SVGElement | null;
  drawLatex(operation: LatexOperation): SVGElement;
  drawFunctionPlot(
    plot: FunctionPlotData,
    dimensions: { width: number; height: number }
  ): FunctionPlotElements | null;
}

function createSvgElement<K extends keyof SVGElementTagNameMap>(
  name: K
): SVGElementTagNameMap[K] {
  return document.createElementNS(SVG_NAMESPACE, name);
}

/** Build the smooth path used by free-form curves and function annotations. */
export function buildSmoothCurvePath(points: [number, number][]): string {
  if (points.length === 0) return "";

  let path = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
  if (points.length === 1) return path;

  if (points.length === 2) {
    return `${path} L${points[1][0].toFixed(1)},${points[1][1].toFixed(1)}`;
  }

  for (let index = 0; index < points.length - 1; index += 1) {
    const [x0, y0] = points[index];
    const [x1, y1] = points[index + 1];
    if (index === 0) {
      const midpointX = (x0 + x1) / 2;
      const midpointY = (y0 + y1) / 2;
      path += ` L${midpointX.toFixed(1)},${midpointY.toFixed(1)}`;
    } else if (index === points.length - 2) {
      path += ` Q${x0.toFixed(1)},${y0.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    } else {
      const midpointX = (x0 + x1) / 2;
      const midpointY = (y0 + y1) / 2;
      path += ` Q${x0.toFixed(1)},${y0.toFixed(1)} ${midpointX.toFixed(1)},${midpointY.toFixed(1)}`;
    }
  }

  return path;
}

/** Project mathematical coordinates into the drawable function-plot rectangle. */
export function projectFunctionPlotPoints(
  points: number[][],
  xRange: number[],
  yRange: number[],
  dimensions: { width: number; height: number },
  margin: number
): [number, number][] {
  const [minimumX, maximumX] = xRange;
  const [minimumY, maximumY] = yRange;
  const plotWidth = dimensions.width - margin * 2;
  const plotHeight = dimensions.height - margin * 2;

  return points.map(([x, y]) => [
    margin + ((x - minimumX) / (maximumX - minimumX)) * plotWidth,
    margin + ((maximumY - y) / (maximumY - minimumY)) * plotHeight,
  ]);
}

export function createSvgPrimitiveRenderer(
  context: PrimitiveRendererContext
): SVGPrimitiveRenderer {
  const { svg, rough, palette, generateId } = context;

  const createGroup = (id: string): SVGGElement => {
    const group = createSvgElement("g");
    group.setAttribute("id", id);
    group.setAttribute("data-element-id", id);
    return group;
  };

  const createArrowhead = (id: string, color: string): string => {
    const existingDefinitions = svg.querySelector("defs");
    const definitions = existingDefinitions ?? createSvgElement("defs");
    if (!existingDefinitions) svg.appendChild(definitions);

    const markerId = `arrowhead-${id}`;
    if (definitions.querySelector(`#${markerId}`)) return markerId;

    const marker = createSvgElement("marker");
    marker.setAttribute("id", markerId);
    marker.setAttribute("markerWidth", "10");
    marker.setAttribute("markerHeight", "10");
    marker.setAttribute("refX", "8");
    marker.setAttribute("refY", "3");
    marker.setAttribute("orient", "auto");
    marker.setAttribute("markerUnits", "strokeWidth");

    const arrowhead = createSvgElement("path");
    arrowhead.setAttribute("d", "M0,0 L0,6 L9,3 z");
    arrowhead.setAttribute("fill", color);
    marker.appendChild(arrowhead);
    definitions.appendChild(marker);
    return markerId;
  };

  const roughOptions = (operation: CanvasOperation) => ({
    stroke: operation.color ?? palette.stroke,
    strokeWidth: operation.stroke_width ?? 1.5,
    fill: operation.fill || undefined,
    roughness: operation.roughness ?? 1.2,
    fillStyle: operation.fill ? "solid" : undefined,
  });

  const drawRect = (operation: CanvasOperation): SVGElement | null => {
    if (!rough) return null;
    const group = createGroup(operation.id || generateId());
    group.appendChild(
      rough.rectangle(
        operation.x ?? 0,
        operation.y ?? 0,
        operation.width ?? 100,
        operation.height ?? 100,
        roughOptions(operation)
      )
    );
    return group;
  };

  const drawCircle = (operation: CanvasOperation): SVGElement | null => {
    if (!rough) return null;
    const group = createGroup(operation.id || generateId());
    const diameter = operation.width ?? 50;
    const centerX = (operation.x ?? 0) + diameter / 2;
    const centerY = (operation.y ?? 0) + diameter / 2;
    group.appendChild(rough.circle(centerX, centerY, diameter, roughOptions(operation)));
    return group;
  };

  const drawEllipse = (operation: CanvasOperation): SVGElement | null => {
    if (!rough) return null;
    const group = createGroup(operation.id || generateId());
    const width = operation.width ?? 100;
    const height = operation.height ?? 80;
    group.appendChild(
      rough.ellipse(
        (operation.x ?? 0) + width / 2,
        (operation.y ?? 0) + height / 2,
        width,
        height,
        roughOptions(operation)
      )
    );
    return group;
  };

  const drawLine = (operation: CanvasOperation): SVGElement | null => {
    if (!rough || !operation.points || operation.points.length < 2) return null;
    const group = createGroup(operation.id || generateId());
    const start = operation.points[0];
    const end = operation.points[operation.points.length - 1];
    group.appendChild(rough.line(start[0], start[1], end[0], end[1], roughOptions(operation)));
    return group;
  };

  const drawArrow = (operation: CanvasOperation): SVGElement | null => {
    if (!operation.points || operation.points.length < 2) return null;
    const id = operation.id || generateId();
    const group = createGroup(id);
    const start = operation.points[0];
    const end = operation.points[operation.points.length - 1];
    const color = operation.color ?? palette.stroke;
    const strokeWidth = operation.stroke_width ?? 1.5;

    const inkPath = createSvgElement("path");
    inkPath.setAttribute(
      "d",
      getInkStrokePath(operation.points, { size: strokeWidth * 2 })
    );
    inkPath.setAttribute("fill", color);
    inkPath.setAttribute("stroke", "none");
    group.appendChild(inkPath);

    const markerLine = createSvgElement("line");
    markerLine.setAttribute("x1", String(start[0]));
    markerLine.setAttribute("y1", String(start[1]));
    markerLine.setAttribute("x2", String(end[0]));
    markerLine.setAttribute("y2", String(end[1]));
    markerLine.setAttribute("stroke", "transparent");
    markerLine.setAttribute("stroke-width", "1");
    markerLine.setAttribute("marker-end", `url(#${createArrowhead(id, color)})`);
    group.appendChild(markerLine);
    return group;
  };

  const drawText = (operation: CanvasOperation): SVGElement => {
    const group = createGroup(operation.id || generateId());
    const text = createSvgElement("text");
    text.setAttribute("x", String(operation.x ?? 0));
    text.setAttribute("y", String(operation.y ?? 0));
    text.setAttribute("fill", operation.color ?? palette.stroke);
    text.setAttribute("font-size", String(operation.font_size ?? 16));
    text.setAttribute(
      "font-family",
      operation.font_family ??
        "var(--font-handwriting), var(--font-handwriting-alt), cursive, sans-serif"
    );
    if (operation._centered) {
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dominant-baseline", "central");
    }
    text.textContent = operation.text ?? "";
    group.appendChild(text);
    return group;
  };

  const drawPath = (operation: CanvasOperation): SVGElement | null => {
    if (!operation.points || operation.points.length < 2) return null;
    const group = createGroup(operation.id || generateId());
    const inkPath = createSvgElement("path");
    inkPath.setAttribute(
      "d",
      getInkStrokePath(operation.points, { size: (operation.stroke_width ?? 1.5) * 2 })
    );
    inkPath.setAttribute("fill", operation.color ?? palette.stroke);
    inkPath.setAttribute("stroke", "none");
    group.appendChild(inkPath);
    return group;
  };

  const drawCurve = (operation: CanvasOperation): SVGElement | null => {
    if (!operation.points || operation.points.length < 2) return null;
    const group = createGroup(operation.id || generateId());
    const curve = createSvgElement("path");
    curve.setAttribute("d", buildSmoothCurvePath(operation.points));
    curve.setAttribute("fill", "none");
    curve.setAttribute("stroke", operation.color ?? palette.stroke);
    curve.setAttribute("stroke-width", String(operation.stroke_width ?? 2.5));
    curve.setAttribute("stroke-linecap", "round");
    curve.setAttribute("stroke-linejoin", "round");
    group.appendChild(curve);
    return group;
  };

  const draw = (operation: CanvasOperation): SVGElement | null => {
    switch (operation.action) {
      case "rect":
        return drawRect(operation);
      case "circle":
        return drawCircle(operation);
      case "ellipse":
        return drawEllipse(operation);
      case "line":
        return drawLine(operation);
      case "arrow":
        return drawArrow(operation);
      case "text":
        return drawText(operation);
      case "path":
        return drawPath(operation);
      case "curve":
        return drawCurve(operation);
      default:
        return null;
    }
  };

  const drawLatex = (operation: LatexOperation): SVGElement => {
    const group = createGroup(operation.id);
    const foreignObject = createSvgElement("foreignObject");
    foreignObject.setAttribute("x", String(operation.x));
    foreignObject.setAttribute("y", String(operation.y));
    foreignObject.setAttribute("width", "500");
    foreignObject.setAttribute("height", "120");

    const container = document.createElement("div");
    container.innerHTML = katex.renderToString(operation.latex, {
      throwOnError: false,
      displayMode: true,
    });
    container.style.color = operation.color;
    container.style.fontSize = `${operation.font_size}px`;
    foreignObject.appendChild(container);
    group.appendChild(foreignObject);
    return group;
  };

  const drawFunctionPlot = (
    plot: FunctionPlotData,
    dimensions: { width: number; height: number }
  ): FunctionPlotElements | null => {
    if (!plot.points || plot.points.length < 2) return null;

    const plotId = `plot_${generateId()}`;
    const group = createGroup(plotId);
    const margin = 60;
    const projectedPoints = projectFunctionPlotPoints(
      plot.points,
      plot.x_range,
      plot.y_range,
      dimensions,
      margin
    );
    const [minimumX, maximumX] = plot.x_range;
    const [minimumY, maximumY] = plot.y_range;
    const scaleX = (value: number) =>
      margin + ((value - minimumX) / (maximumX - minimumX)) * (dimensions.width - margin * 2);
    const scaleY = (value: number) =>
      margin + ((maximumY - value) / (maximumY - minimumY)) * (dimensions.height - margin * 2);

    if (plot.show_axes) {
      const xAxisY = scaleY(0);
      if (xAxisY >= margin && xAxisY <= dimensions.height - margin) {
        const axis = createSvgElement("line");
        axis.setAttribute("x1", String(margin));
        axis.setAttribute("y1", String(xAxisY));
        axis.setAttribute("x2", String(dimensions.width - margin));
        axis.setAttribute("y2", String(xAxisY));
        axis.setAttribute("stroke", palette.axis);
        axis.setAttribute("stroke-width", "1.5");
        group.appendChild(axis);

        const step = (maximumX - minimumX) / 8;
        for (let value = Math.ceil(minimumX / step) * step; value <= maximumX; value += step) {
          if (Math.abs(value) < 0.001) continue;
          const x = scaleX(value);
          const tick = createSvgElement("line");
          tick.setAttribute("x1", String(x));
          tick.setAttribute("y1", String(xAxisY - 4));
          tick.setAttribute("x2", String(x));
          tick.setAttribute("y2", String(xAxisY + 4));
          tick.setAttribute("stroke", palette.axis);
          tick.setAttribute("stroke-width", "1");
          group.appendChild(tick);

          const label = createSvgElement("text");
          label.setAttribute("x", String(x));
          label.setAttribute("y", String(xAxisY + 18));
          label.setAttribute("text-anchor", "middle");
          label.setAttribute("fill", palette.axis);
          label.setAttribute("font-size", "11");
          label.textContent = Number(value.toFixed(1)).toString();
          group.appendChild(label);
        }
      }

      const yAxisX = scaleX(0);
      if (yAxisX >= margin && yAxisX <= dimensions.width - margin) {
        const axis = createSvgElement("line");
        axis.setAttribute("x1", String(yAxisX));
        axis.setAttribute("y1", String(margin));
        axis.setAttribute("x2", String(yAxisX));
        axis.setAttribute("y2", String(dimensions.height - margin));
        axis.setAttribute("stroke", palette.axis);
        axis.setAttribute("stroke-width", "1.5");
        group.appendChild(axis);

        const step = (maximumY - minimumY) / 6;
        for (let value = Math.ceil(minimumY / step) * step; value <= maximumY; value += step) {
          if (Math.abs(value) < 0.001) continue;
          const y = scaleY(value);
          const tick = createSvgElement("line");
          tick.setAttribute("x1", String(yAxisX - 4));
          tick.setAttribute("y1", String(y));
          tick.setAttribute("x2", String(yAxisX + 4));
          tick.setAttribute("y2", String(y));
          tick.setAttribute("stroke", palette.axis);
          tick.setAttribute("stroke-width", "1");
          group.appendChild(tick);

          const label = createSvgElement("text");
          label.setAttribute("x", String(yAxisX - 10));
          label.setAttribute("y", String(y + 4));
          label.setAttribute("text-anchor", "end");
          label.setAttribute("fill", palette.axis);
          label.setAttribute("font-size", "11");
          label.textContent = Number(value.toFixed(1)).toString();
          group.appendChild(label);
        }
      }
    }

    const curve = createSvgElement("path");
    curve.setAttribute(
      "d",
      projectedPoints
        .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
        .join(" ")
    );
    curve.setAttribute("fill", "none");
    curve.setAttribute("stroke", plot.color);
    curve.setAttribute("stroke-width", "2.5");
    curve.setAttribute("stroke-linecap", "round");
    curve.setAttribute("stroke-linejoin", "round");
    group.appendChild(curve);

    return {
      id: plotId,
      group,
      curve,
      axes: group.querySelectorAll("line, text"),
      margin,
    };
  };

  return { draw, drawLatex, drawFunctionPlot };
}
