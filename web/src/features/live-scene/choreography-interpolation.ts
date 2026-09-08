import { gsap } from "gsap";

import { resolveCssColor } from "@/lib/gsap-setup";
import type {
  MotionStep,
  SceneNode,
  ScenePoint,
  ViewportPoseV1,
} from "@/lib/live-scene";

function interpolate(from: number, to: number, progress: number): number {
  return from + (to - from) * progress;
}

export function interpolateViewport(
  from: ViewportPoseV1,
  to: ViewportPoseV1,
  progress: number,
): ViewportPoseV1 {
  return Object.freeze({
    v: 1,
    x: interpolate(from.x, to.x, progress),
    y: interpolate(from.y, to.y, progress),
    width: interpolate(from.width, to.width, progress),
    height: interpolate(from.height, to.height, progress),
  });
}

function anchorLeft(node: Extract<SceneNode, { kind: "latex_token" }>): number {
  if (node.anchor === "middle") return node.x - node.width / 2;
  if (node.anchor === "end") return node.x - node.width;
  return node.x;
}

function pointsPath(points: readonly ScenePoint[], closed: boolean): string {
  return `${points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`)
    .join(" ")}${closed ? " Z" : ""}`;
}

function interpolatePoints(
  from: readonly ScenePoint[],
  to: readonly ScenePoint[],
  progress: number,
): readonly ScenePoint[] {
  if (from.length !== to.length) {
    throw new Error("Cannot interpolate path points with different topology");
  }
  return from.map(([x, y], index) => [
    interpolate(x, to[index][0], progress),
    interpolate(y, to[index][1], progress),
  ]);
}

function isEmptyPaint(paint: string): boolean {
  return paint === "none" || paint === "transparent";
}

function transparentVersion(paint: string): string {
  const [red, green, blue] = gsap.utils.splitColor(resolveCssColor(paint));
  if (
    typeof red !== "number" ||
    typeof green !== "number" ||
    typeof blue !== "number" ||
    !Number.isFinite(red) ||
    !Number.isFinite(green) ||
    !Number.isFinite(blue)
  ) {
    return "rgba(0,0,0,0)";
  }
  return `rgba(${red},${green},${blue},0)`;
}

function paintInterpolator(
  from: string,
  to: string,
): (progress: number) => string {
  if (from === to) return () => from;
  const resolvedFrom = isEmptyPaint(from)
    ? transparentVersion(to)
    : resolveCssColor(from);
  const resolvedTo = isEmptyPaint(to)
    ? transparentVersion(from)
    : resolveCssColor(to);
  return (progress) => {
    if (progress <= 0) return from;
    if (progress >= 1) return to;
    return String(gsap.utils.interpolate(resolvedFrom, resolvedTo, progress));
  };
}

function setNumericAttribute(
  element: Element,
  name: string,
  value: number,
): void {
  element.setAttribute(name, String(value));
}

function strokeStyleRenderer(
  targets: readonly SVGElement[],
  previous: Extract<SceneNode, { kind: "line" | "path" | "rect" }>,
  next: Extract<SceneNode, { kind: "line" | "path" | "rect" }>,
): (progress: number) => void {
  const stroke = paintInterpolator(previous.style.stroke, next.style.stroke);
  return (progress) => {
    const width = interpolate(
      previous.style.strokeWidth,
      next.style.strokeWidth,
      progress,
    );
    for (const target of targets) {
      if (target.getAttribute("stroke") !== "none") {
        target.setAttribute("stroke", stroke(progress));
        setNumericAttribute(target, "stroke-width", width);
      }
    }
  };
}

function shapeStyleRenderer(
  targets: readonly SVGElement[],
  previous: Extract<SceneNode, { kind: "path" | "rect" }>,
  next: Extract<SceneNode, { kind: "path" | "rect" }>,
): (progress: number) => void {
  const renderStroke = strokeStyleRenderer(targets, previous, next);
  const fill = paintInterpolator(previous.style.fill, next.style.fill);
  return (progress) => {
    renderStroke(progress);
    for (const target of targets) {
      const currentFill = target.getAttribute("fill");
      if (currentFill !== null && currentFill !== "none") {
        target.setAttribute("fill", fill(progress));
      }
    }
  };
}

function compatibleStyleRenderer(
  element: SVGElement,
  previous: SceneNode,
  next: SceneNode,
): (progress: number) => void {
  switch (previous.kind) {
    case "path": {
      if (next.kind !== "path") {
        throw new Error("Path style target changes kind");
      }
      const path = element.querySelector<SVGElement>("path");
      if (!path) {
        throw new Error(`Path style target ${previous.id} has no path`);
      }
      return shapeStyleRenderer([path], previous, next);
    }
    case "line": {
      if (next.kind !== "line") {
        throw new Error("Line style target changes kind");
      }
      const targets = element.querySelector("line")
        ? [element.querySelector<SVGElement>("line") as SVGElement]
        : Array.from(element.querySelectorAll<SVGElement>("path"));
      if (targets.length === 0) {
        throw new Error(
          `Line style target ${previous.id} has no drawable child`,
        );
      }
      return strokeStyleRenderer(targets, previous, next);
    }
    case "rect": {
      if (next.kind !== "rect") {
        throw new Error("Rect style target changes kind");
      }
      const targets = element.querySelector("rect")
        ? [element.querySelector<SVGElement>("rect") as SVGElement]
        : Array.from(element.querySelectorAll<SVGElement>("path"));
      if (targets.length === 0) {
        throw new Error(
          `Rect style target ${previous.id} has no drawable child`,
        );
      }
      return shapeStyleRenderer(targets, previous, next);
    }
    case "text": {
      if (next.kind !== "text") {
        throw new Error("Text style target changes kind");
      }
      const text = element.querySelector<SVGElement>("text");
      if (!text) {
        throw new Error(`Text style target ${previous.id} has no text`);
      }
      const color = paintInterpolator(previous.style.color, next.style.color);
      return (progress) => {
        text.setAttribute("fill", color(progress));
        setNumericAttribute(
          text,
          "font-size",
          interpolate(previous.style.fontSize, next.style.fontSize, progress),
        );
        if (progress >= 1) text.setAttribute("text-anchor", next.style.anchor);
      };
    }
    case "latex":
    case "latex_token": {
      if (next.kind !== previous.kind) {
        throw new Error("LaTeX style target changes kind");
      }
      const container = element.querySelector<HTMLElement>("foreignObject > *");
      if (!container) {
        throw new Error(`LaTeX style target ${previous.id} has no container`);
      }
      const color = paintInterpolator(previous.style.color, next.style.color);
      return (progress) => {
        container.style.color = color(progress);
        container.style.fontSize = `${interpolate(
          previous.style.fontSize,
          next.style.fontSize,
          progress,
        )}px`;
        if (
          previous.kind === "latex_token" &&
          next.kind === "latex_token" &&
          progress >= 1
        ) {
          container.style.justifyContent =
            next.anchor === "start"
              ? "flex-start"
              : next.anchor === "end"
                ? "flex-end"
                : "center";
        }
      };
    }
  }
}

export function addCompatibleTransform(
  timeline: gsap.core.Timeline,
  element: SVGElement,
  step: Extract<MotionStep, { type: "update" }>,
  duration: number,
  ease: string,
  onError: (error: unknown) => void,
): void {
  const { previous, next } = step;
  if (previous.kind !== next.kind) {
    throw new Error(`Transform target ${step.id} changes node kind`);
  }
  const renderStyle = compatibleStyleRenderer(element, previous, next);
  const proxy = { progress: 0 };
  const render = () => {
    try {
      const progress = proxy.progress;
      element.style.opacity = String(
        interpolate(previous.style.opacity, next.style.opacity, progress),
      );
      renderStyle(progress);
      switch (previous.kind) {
        case "path": {
          if (next.kind !== "path" || previous.closed !== next.closed) {
            throw new Error(`Path transform ${step.id} changes topology`);
          }
          const path = element.querySelector("path");
          if (!path) {
            throw new Error(`Path transform ${step.id} has no SVG path`);
          }
          path.setAttribute(
            "d",
            pointsPath(
              interpolatePoints(previous.points, next.points, progress),
              previous.closed,
            ),
          );
          break;
        }
        case "line": {
          if (next.kind !== "line") {
            throw new Error(`Invalid line transform ${step.id}`);
          }
          const points = interpolatePoints(
            previous.points,
            next.points,
            progress,
          );
          const line = element.querySelector("line");
          if (line) {
            setNumericAttribute(line, "x1", points[0][0]);
            setNumericAttribute(line, "y1", points[0][1]);
            setNumericAttribute(line, "x2", points[1][0]);
            setNumericAttribute(line, "y2", points[1][1]);
          } else {
            const paths = element.querySelectorAll("path");
            if (paths.length === 0) {
              throw new Error(
                `Line transform ${step.id} has no drawable child`,
              );
            }
            paths.forEach((path) =>
              path.setAttribute("d", pointsPath(points, false)),
            );
          }
          break;
        }
        case "rect": {
          if (next.kind !== "rect") {
            throw new Error(`Invalid rect transform ${step.id}`);
          }
          const rect = element.querySelector("rect");
          if (rect) {
            setNumericAttribute(
              rect,
              "x",
              interpolate(previous.x, next.x, progress),
            );
            setNumericAttribute(
              rect,
              "y",
              interpolate(previous.y, next.y, progress),
            );
            setNumericAttribute(
              rect,
              "width",
              interpolate(previous.width, next.width, progress),
            );
            setNumericAttribute(
              rect,
              "height",
              interpolate(previous.height, next.height, progress),
            );
          } else {
            const width = interpolate(previous.width, next.width, progress);
            const height = interpolate(previous.height, next.height, progress);
            const scaleX = width / previous.width;
            const scaleY = height / previous.height;
            const x = interpolate(previous.x, next.x, progress);
            const y = interpolate(previous.y, next.y, progress);
            element.setAttribute(
              "transform",
              `matrix(${scaleX} 0 0 ${scaleY} ${x - previous.x * scaleX} ${
                y - previous.y * scaleY
              })`,
            );
          }
          break;
        }
        case "text": {
          if (next.kind !== "text") {
            throw new Error(`Invalid text transform ${step.id}`);
          }
          const text = element.querySelector("text");
          if (!text) {
            throw new Error(`Text transform ${step.id} has no SVG text`);
          }
          setNumericAttribute(
            text,
            "x",
            interpolate(previous.x, next.x, progress),
          );
          setNumericAttribute(
            text,
            "y",
            interpolate(previous.y, next.y, progress),
          );
          break;
        }
        case "latex": {
          if (next.kind !== "latex") {
            throw new Error(`Invalid LaTeX transform ${step.id}`);
          }
          const foreignObject = element.querySelector("foreignObject");
          if (!foreignObject) {
            throw new Error(`LaTeX transform ${step.id} has no foreignObject`);
          }
          setNumericAttribute(
            foreignObject,
            "x",
            interpolate(previous.x, next.x, progress),
          );
          setNumericAttribute(
            foreignObject,
            "y",
            interpolate(previous.y, next.y, progress),
          );
          break;
        }
        case "latex_token": {
          if (next.kind !== "latex_token") {
            throw new Error(`Invalid LaTeX token transform ${step.id}`);
          }
          const foreignObject = element.querySelector("foreignObject");
          if (!foreignObject) {
            throw new Error(
              `LaTeX token transform ${step.id} has no foreignObject`,
            );
          }
          setNumericAttribute(
            foreignObject,
            "x",
            interpolate(anchorLeft(previous), anchorLeft(next), progress),
          );
          setNumericAttribute(
            foreignObject,
            "y",
            interpolate(previous.y, next.y, progress),
          );
          setNumericAttribute(
            foreignObject,
            "width",
            interpolate(previous.width, next.width, progress),
          );
          setNumericAttribute(
            foreignObject,
            "height",
            interpolate(previous.height, next.height, progress),
          );
          break;
        }
      }
    } catch (error) {
      onError(error);
    }
  };
  timeline.to(proxy, { progress: 1, duration, ease, onUpdate: render }, 0);
}
