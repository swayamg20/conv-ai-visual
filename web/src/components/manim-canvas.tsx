"use client";

import React, { useRef, useImperativeHandle, forwardRef, useCallback } from "react";

// ---- Easing functions ----

const easeInOutCubic = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

const easeInCubic = (t: number) => t * t * t;

// ---- Color utilities ----

function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const m = hex.replace("#", "").match(/^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) return null;
  return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) };
}

function rgbToHex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("")}`;
}

function interpolateColor(from: string, to: string, t: number): string {
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  if (!a || !b) return t < 0.5 ? from : to;
  return rgbToHex(
    a.r + (b.r - a.r) * t,
    a.g + (b.g - a.g) * t,
    a.b + (b.b - a.b) * t
  );
}

// ---- SVG path interpolation ----

function interpolatePath(from: string, to: string, t: number): string {
  const fromNums = from.match(/-?\d+\.?\d*/g)?.map(Number) || [];
  const toNums = to.match(/-?\d+\.?\d*/g)?.map(Number) || [];
  const fromLetters = from.match(/[A-Za-z]/g) || [];
  const toLetters = to.match(/[A-Za-z]/g) || [];

  // If same structure, interpolate all numeric values
  if (
    fromNums.length === toNums.length &&
    fromLetters.length === toLetters.length &&
    fromLetters.join("") === toLetters.join("")
  ) {
    let numIdx = 0;
    return to.replace(/-?\d+\.?\d*/g, () => {
      const val = fromNums[numIdx] + (toNums[numIdx] - fromNums[numIdx]) * t;
      numIdx++;
      return val.toFixed(2);
    });
  }

  // Fallback: crossfade at midpoint
  return t < 0.5 ? from : to;
}

// ---- Style utilities ----

interface CompactStyle {
  f?: string;
  fo?: number;
  s?: string;
  sw?: number;
  so?: number;
}

function captureStyle(el: SVGElement): CompactStyle {
  return {
    f: el.getAttribute("fill") || undefined,
    fo: el.hasAttribute("fill-opacity") ? parseFloat(el.getAttribute("fill-opacity")!) : undefined,
    s: el.getAttribute("stroke") || undefined,
    sw: el.hasAttribute("stroke-width") ? parseFloat(el.getAttribute("stroke-width")!) : undefined,
    so: el.hasAttribute("stroke-opacity") ? parseFloat(el.getAttribute("stroke-opacity")!) : undefined,
  };
}

function interpolateStyleProps(
  el: SVGElement,
  from: CompactStyle,
  to: CompactStyle,
  t: number
) {
  if (from.f && to.f && from.f !== "none" && to.f !== "none") {
    el.setAttribute("fill", interpolateColor(from.f, to.f, t));
  } else if (to.f) {
    el.setAttribute("fill", to.f);
  }
  if (from.s && to.s && from.s !== "none" && to.s !== "none") {
    el.setAttribute("stroke", interpolateColor(from.s, to.s, t));
  } else if (to.s) {
    el.setAttribute("stroke", to.s);
  }
  if (from.fo !== undefined && to.fo !== undefined) {
    el.setAttribute("fill-opacity", String(from.fo + (to.fo - from.fo) * t));
  }
  if (from.sw !== undefined && to.sw !== undefined) {
    el.setAttribute("stroke-width", String(from.sw + (to.sw - from.sw) * t));
  }
  if (from.so !== undefined && to.so !== undefined) {
    el.setAttribute("stroke-opacity", String(from.so + (to.so - from.so) * t));
  }
}

// ---- Types ----

type ManimCommand =
  | { cmd: "init"; w: number; h: number; bg: string }
  | { cmd: "add"; id: string; d?: string; s?: CompactStyle; children?: Array<{ d: string; s: CompactStyle }> }
  | { cmd: "anim"; id: string; type: "create" | "write" | "fadeIn" | "fadeOut" | "transform" | "morph"; dur: number; d?: string; s?: CompactStyle; from?: string; to?: string }
  | { cmd: "wait"; dur: number }
  | { cmd: "remove"; id: string }
  | { cmd: "clear" };

export interface ManimCanvasHandle {
  processCommand: (command: ManimCommand) => void;
  clear: () => void;
}

interface ManimCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  backgroundColor?: string;
}

// ---- Component ----

export const ManimCanvas = forwardRef<ManimCanvasHandle, ManimCanvasProps>(
  ({ width = 1920, height = 1080, className, backgroundColor = "#1a1a2e" }, ref) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const elementsRef = useRef<Map<string, SVGElement>>(new Map());
    const commandQueueRef = useRef<ManimCommand[]>([]);
    const isProcessingRef = useRef(false);

    const applyStyle = useCallback((el: SVGElement, s: CompactStyle) => {
      if (s.f) el.setAttribute("fill", s.f);
      else el.setAttribute("fill", "none");
      if (s.fo !== undefined) el.setAttribute("fill-opacity", String(s.fo));
      if (s.s) el.setAttribute("stroke", s.s);
      if (s.sw !== undefined) el.setAttribute("stroke-width", String(s.sw));
      if (s.so !== undefined) el.setAttribute("stroke-opacity", String(s.so));
      el.setAttribute("stroke-linecap", "round");
      el.setAttribute("stroke-linejoin", "round");
    }, []);

    const createPathElement = useCallback((d: string, s: CompactStyle): SVGPathElement => {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      applyStyle(path, s);
      return path;
    }, [applyStyle]);

    // ---- Animation: stroke-draw (ShowCreation) ----
    const animateCreate = useCallback((el: SVGElement, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const length = path.getTotalLength?.() || 1000;
        path.style.strokeDasharray = String(length);
        path.style.strokeDashoffset = String(length);
        path.style.opacity = "1";
        const startTime = performance.now();
        const step = (timestamp: number) => {
          const progress = Math.min((timestamp - startTime) / (duration * 1000), 1);
          const eased = easeInOutCubic(progress);
          path.style.strokeDashoffset = String(length * (1 - eased));
          if (progress < 1) requestAnimationFrame(step);
          else {
            path.style.strokeDasharray = "";
            path.style.strokeDashoffset = "";
            resolve();
          }
        };
        requestAnimationFrame(step);
      });
    }, []);

    // ---- Animation: write (sequential left-to-right for text/tex) ----
    const animateWrite = useCallback((el: SVGElement, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        if (el.tagName === "g") {
          const paths = Array.from(el.querySelectorAll("path")) as SVGPathElement[];
          // Sort by x-position for left-to-right reveal
          paths.sort((a, b) => {
            const aBox = (a as SVGGraphicsElement).getBBox?.();
            const bBox = (b as SVGGraphicsElement).getBBox?.();
            return (aBox?.x || 0) - (bBox?.x || 0);
          });
          if (paths.length === 0) { resolve(); return; }
          const perPath = duration / paths.length;
          let chain = Promise.resolve();
          for (const p of paths) {
            chain = chain.then(() => animateCreate(p, Math.max(perPath, 0.05)));
          }
          chain.then(resolve);
        } else {
          animateCreate(el, duration).then(resolve);
        }
      });
    }, [animateCreate]);

    // ---- Animation: fade ----
    const animateFade = useCallback((el: SVGElement, duration: number, fadeIn: boolean): Promise<void> => {
      return new Promise((resolve) => {
        const startOp = fadeIn ? 0 : 1;
        const endOp = fadeIn ? 1 : 0;
        el.style.opacity = String(startOp);
        const startTime = performance.now();
        const step = (timestamp: number) => {
          const progress = Math.min((timestamp - startTime) / (duration * 1000), 1);
          const eased = fadeIn ? easeOutCubic(progress) : easeInCubic(progress);
          el.style.opacity = String(startOp + (endOp - startOp) * eased);
          if (progress < 1) requestAnimationFrame(step);
          else resolve();
        };
        requestAnimationFrame(step);
      });
    }, []);

    // ---- Animation: real path morph + color interpolation ----
    const animateTransform = useCallback((
      el: SVGElement,
      targetPath: string,
      targetStyle: CompactStyle | undefined,
      duration: number
    ): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const startPath = path.getAttribute("d") || "";
        const startStyle = captureStyle(path);
        const startTime = performance.now();

        const step = (timestamp: number) => {
          const progress = Math.min((timestamp - startTime) / (duration * 1000), 1);
          const eased = easeInOutCubic(progress);

          // Interpolate path shape
          const interpolated = interpolatePath(startPath, targetPath, eased);
          path.setAttribute("d", interpolated);

          // Interpolate style (colors, stroke width, etc.)
          if (targetStyle) {
            interpolateStyleProps(path, startStyle, targetStyle, eased);
          }

          if (progress < 1) requestAnimationFrame(step);
          else {
            // Ensure final state is exact
            path.setAttribute("d", targetPath);
            if (targetStyle) applyStyle(path, targetStyle);
            resolve();
          }
        };
        requestAnimationFrame(step);
      });
    }, [applyStyle]);

    // ---- Command processor ----
    const processCommand = useCallback(async (cmd: ManimCommand) => {
      const svg = svgRef.current;
      if (!svg) return;
      const contentGroup = svg.querySelector(".manim-content");
      if (!contentGroup) return;

      switch (cmd.cmd) {
        case "init":
          break;

        case "add": {
          // Remove existing element with same ID (prevents duplicates on 2nd question)
          const existing = elementsRef.current.get(cmd.id);
          if (existing) {
            existing.remove();
            elementsRef.current.delete(cmd.id);
          }
          if (cmd.d && cmd.s) {
            const path = createPathElement(cmd.d, cmd.s);
            path.id = cmd.id;
            path.style.opacity = "0";
            contentGroup.appendChild(path);
            elementsRef.current.set(cmd.id, path);
          } else if (cmd.children) {
            const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
            group.id = cmd.id;
            group.style.opacity = "0";
            for (const child of cmd.children) {
              group.appendChild(createPathElement(child.d, child.s));
            }
            contentGroup.appendChild(group);
            elementsRef.current.set(cmd.id, group);
          }
          break;
        }

        case "anim": {
          const el = elementsRef.current.get(cmd.id);
          if (!el) break;
          if (el.style.opacity === "0" && cmd.type !== "fadeOut") el.style.opacity = "1";

          switch (cmd.type) {
            case "create":
              if (el.tagName === "g") {
                const paths = el.querySelectorAll("path");
                await Promise.all(Array.from(paths).map((p) => animateCreate(p, cmd.dur)));
              } else {
                await animateCreate(el, cmd.dur);
              }
              break;
            case "write":
              await animateWrite(el, cmd.dur);
              break;
            case "fadeIn":
              await animateFade(el, cmd.dur, true);
              break;
            case "fadeOut":
              await animateFade(el, cmd.dur, false);
              break;
            case "transform":
              if (cmd.d) await animateTransform(el, cmd.d, cmd.s, cmd.dur);
              break;
            case "morph":
              if (cmd.from && cmd.to) {
                const path = el as SVGPathElement;
                path.setAttribute("d", cmd.from);
                await animateTransform(el, cmd.to, cmd.s, cmd.dur);
              } else if (cmd.to) {
                await animateTransform(el, cmd.to, cmd.s, cmd.dur);
              }
              break;
          }
          break;
        }

        case "wait":
          await new Promise((r) => setTimeout(r, cmd.dur * 1000));
          break;

        case "remove": {
          const el = elementsRef.current.get(cmd.id);
          if (el) {
            el.remove();
            elementsRef.current.delete(cmd.id);
          }
          break;
        }

        case "clear":
          if (contentGroup) contentGroup.innerHTML = "";
          elementsRef.current.clear();
          break;
      }
    }, [createPathElement, animateCreate, animateWrite, animateFade, animateTransform]);

    const processQueue = useCallback(async () => {
      if (isProcessingRef.current) return;
      isProcessingRef.current = true;
      try {
        while (commandQueueRef.current.length > 0) {
          const cmd = commandQueueRef.current.shift()!;
          try {
            await processCommand(cmd);
          } catch (e) {
            console.warn("[ManimCanvas] Command failed:", cmd.cmd, e);
          }
        }
      } finally {
        isProcessingRef.current = false;
      }
    }, [processCommand]);

    useImperativeHandle(ref, () => ({
      processCommand: (command: ManimCommand) => {
        commandQueueRef.current.push(command);
        processQueue();
      },
      clear: () => {
        commandQueueRef.current = [];
        isProcessingRef.current = false;
        const svg = svgRef.current;
        if (svg) {
          const content = svg.querySelector(".manim-content");
          if (content) content.innerHTML = "";
        }
        elementsRef.current.clear();
      },
    }));

    return (
      <div className={className}>
        <svg
          ref={svgRef}
          xmlns="http://www.w3.org/2000/svg"
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: "100%", height: "100%", borderRadius: "12px" }}
        >
          <rect width="100%" height="100%" fill={backgroundColor} />
          <g className="manim-content" />
        </svg>
      </div>
    );
  }
);

ManimCanvas.displayName = "ManimCanvas";
