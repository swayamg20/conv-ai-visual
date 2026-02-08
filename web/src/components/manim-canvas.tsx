"use client";

import React, { useRef, useImperativeHandle, forwardRef, useCallback, useState, useEffect } from "react";

// ---- Inline types & player (from manim-react package) ----

interface CompactStyle {
  f?: string;
  fo?: number;
  s?: string;
  sw?: number;
  so?: number;
}

type ManimCommand =
  | { cmd: "init"; w: number; h: number; bg: string }
  | { cmd: "add"; id: string; d?: string; s?: CompactStyle; children?: Array<{ d: string; s: CompactStyle }> }
  | { cmd: "anim"; id: string; type: "create" | "fadeIn" | "fadeOut" | "transform" | "morph"; dur: number; d?: string; s?: CompactStyle; from?: string; to?: string }
  | { cmd: "wait"; dur: number }
  | { cmd: "remove"; id: string }
  | { cmd: "clear" };

export interface ManimCanvasHandle {
  /** Process a single Manim command (from DataChannel) */
  processCommand: (command: ManimCommand) => void;
  /** Clear the canvas */
  clear: () => void;
}

interface ManimCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  backgroundColor?: string;
}

export const ManimCanvas = forwardRef<ManimCanvasHandle, ManimCanvasProps>(
  ({ width = 800, height = 450, className, backgroundColor = "#1a1a2e" }, ref) => {
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

    const animateCreate = useCallback((el: SVGElement, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const length = path.getTotalLength?.() || 1000;
        path.style.strokeDasharray = String(length);
        path.style.strokeDashoffset = String(length);
        path.style.opacity = "1";
        const startTime = performance.now();
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          path.style.strokeDashoffset = String(length * (1 - eased));
          if (progress < 1) requestAnimationFrame(animate);
          else { path.style.strokeDasharray = ""; path.style.strokeDashoffset = ""; resolve(); }
        };
        requestAnimationFrame(animate);
      });
    }, []);

    const animateFade = useCallback((el: SVGElement, duration: number, fadeIn: boolean): Promise<void> => {
      return new Promise((resolve) => {
        const startTime = performance.now();
        const startOp = fadeIn ? 0 : 1;
        const endOp = fadeIn ? 1 : 0;
        el.style.opacity = String(startOp);
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          el.style.opacity = String(startOp + (endOp - startOp) * eased);
          if (progress < 1) requestAnimationFrame(animate);
          else resolve();
        };
        requestAnimationFrame(animate);
      });
    }, []);

    const animateTransform = useCallback((el: SVGElement, targetPath: string, targetStyle: CompactStyle | undefined, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const startTime = performance.now();
        const startPath = path.getAttribute("d") || "";
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          if (eased < 0.5) {
            path.style.opacity = String(1 - eased);
          } else {
            if (path.getAttribute("d") === startPath) {
              path.setAttribute("d", targetPath);
              if (targetStyle) applyStyle(path, targetStyle);
            }
            path.style.opacity = String(eased);
          }
          if (progress < 1) requestAnimationFrame(animate);
          else { path.style.opacity = "1"; resolve(); }
        };
        requestAnimationFrame(animate);
      });
    }, [applyStyle]);

    const processCommand = useCallback(async (cmd: ManimCommand) => {
      const svg = svgRef.current;
      if (!svg) return;
      const contentGroup = svg.querySelector(".manim-content");
      if (!contentGroup) return;

      switch (cmd.cmd) {
        case "init":
          // Config already set via props
          break;

        case "add": {
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
              if (cmd.to) await animateTransform(el, cmd.to, cmd.s, cmd.dur);
              break;
          }
          break;
        }

        case "wait":
          await new Promise((r) => setTimeout(r, cmd.dur * 1000));
          break;

        case "remove": {
          const el = elementsRef.current.get(cmd.id);
          if (el) { el.remove(); elementsRef.current.delete(cmd.id); }
          break;
        }

        case "clear":
          if (contentGroup) contentGroup.innerHTML = "";
          elementsRef.current.clear();
          break;
      }
    }, [createPathElement, animateCreate, animateFade, animateTransform]);

    const processQueue = useCallback(async () => {
      if (isProcessingRef.current) return;
      isProcessingRef.current = true;
      while (commandQueueRef.current.length > 0) {
        const cmd = commandQueueRef.current.shift()!;
        await processCommand(cmd);
      }
      isProcessingRef.current = false;
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
