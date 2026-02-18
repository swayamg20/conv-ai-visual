"use client";

import {
  useRef,
  useEffect,
  useImperativeHandle,
  forwardRef,
  useCallback,
  useState
} from "react";
import rough from "roughjs";
import type { RoughSVG } from "roughjs/bin/svg";
import { gsap } from "gsap";
import { createTimeline, EASING, DURATION } from "@/lib/gsap-setup";
import type { CanvasOperation } from "@/hooks/use-webrtc";
import katex from "katex";
import "katex/dist/katex.min.css";

/**
 * Animation operation from backend
 */
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

/**
 * LaTeX element operation
 */
export interface LatexOperation {
  type: "latex";
  id: string;
  latex: string;
  x: number;
  y: number;
  font_size: number;
  color: string;
}

/**
 * Teaching sequence step
 */
export interface TeachingStep {
  action: string; // "clear"|"draw"|"animate"|"latex"|"highlight"|"morph"|"pause"|"text" + shape primitives (rect, circle, ellipse, line, arrow, path)
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
}

/**
 * Teaching sequence message
 */
export interface TeachingSequence {
  steps: TeachingStep[];
}

/**
 * SVG element with metadata
 */
interface SVGElementData {
  element: SVGElement;
  id: string;
  type: string;
  x: number;
  y: number;
  data: any; // Original operation data
}

/**
 * SVGCanvas component handle (exposed methods)
 */
export interface SVGCanvasHandle {
  render(operations: CanvasOperation[]): void;
  animate(animation: AnimationOperation): gsap.core.Tween | null;
  renderLatex(latex: LatexOperation): void;
  createSequence(sequence: TeachingSequence): gsap.core.Timeline;
  clear(): void;
  saveAsImage(): void;
}

interface SVGCanvasProps {
  width?: number;
  height?: number;
  className?: string;
}

export const SVGCanvas = forwardRef<SVGCanvasHandle, SVGCanvasProps>(
  ({ width = 800, height = 600, className }, ref) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const roughRef = useRef<RoughSVG | null>(null);
    const elementsRef = useRef<Map<string, SVGElementData>>(new Map());
    const timelinesRef = useRef<Map<string, gsap.core.Timeline>>(new Map());
    const sequenceQueueRef = useRef<gsap.core.Timeline[]>([]);
    const isPlayingRef = useRef<boolean>(false);
    const [, setForceUpdate] = useState(0);

    // Initialize Rough.js on mount
    useEffect(() => {
      if (svgRef.current) {
        roughRef.current = rough.svg(svgRef.current);
      }
    }, []);

    /**
     * Helper: Generate unique ID
     */
    const generateId = useCallback(() => {
      return `elem_${Math.random().toString(36).substring(2, 10)}`;
    }, []);

    /**
     * Helper: Create SVG group for element
     */
    const createGroup = useCallback(
      (id: string): SVGGElement => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.setAttribute("id", id);
        g.setAttribute("data-element-id", id);
        return g;
      },
      []
    );

    /**
     * Helper: Create arrowhead marker
     */
    const createArrowhead = useCallback((id: string, color: string) => {
      if (!svgRef.current) return;

      const defs = svgRef.current.querySelector("defs") || document.createElementNS("http://www.w3.org/2000/svg", "defs");
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

    /**
     * Draw rectangle with Rough.js
     */
    const drawRect = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;

        const g = createGroup(op.id || generateId());
        const node = roughRef.current.rectangle(
          op.x ?? 0,
          op.y ?? 0,
          op.width ?? 100,
          op.height ?? 100,
          {
            stroke: op.color ?? "#3b82f6",
            strokeWidth: op.stroke_width ?? 2,
            fill: op.fill || undefined,
            roughness: 1.5, // Hand-drawn feel
            fillStyle: op.fill ? "solid" : undefined
          }
        );

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Draw circle with Rough.js
     */
    const drawCircle = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;

        const g = createGroup(op.id || generateId());
        const centerX = (op.x ?? 0) + (op.width ?? 50) / 2;
        const centerY = (op.y ?? 0) + (op.width ?? 50) / 2;
        const diameter = op.width ?? 50;

        const node = roughRef.current.circle(centerX, centerY, diameter, {
          stroke: op.color ?? "#3b82f6",
          strokeWidth: op.stroke_width ?? 2,
          fill: op.fill || undefined,
          roughness: 1.5,
          fillStyle: op.fill ? "solid" : undefined
        });

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Draw ellipse with Rough.js
     */
    const drawEllipse = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current) return null;

        const g = createGroup(op.id || generateId());
        const centerX = (op.x ?? 0) + (op.width ?? 100) / 2;
        const centerY = (op.y ?? 0) + (op.height ?? 80) / 2;

        const node = roughRef.current.ellipse(
          centerX,
          centerY,
          op.width ?? 100,
          op.height ?? 80,
          {
            stroke: op.color ?? "#3b82f6",
            strokeWidth: op.stroke_width ?? 2,
            fill: op.fill || undefined,
            roughness: 1.5,
            fillStyle: op.fill ? "solid" : undefined
          }
        );

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Draw line with Rough.js
     */
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
          {
            stroke: op.color ?? "#3b82f6",
            strokeWidth: op.stroke_width ?? 2,
            roughness: 1.5
          }
        );

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Draw arrow with Rough.js + arrowhead
     */
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
          {
            stroke: op.color ?? "#3b82f6",
            strokeWidth: op.stroke_width ?? 2,
            roughness: 1.5
          }
        );

        // Add arrowhead
        const markerId = createArrowhead(op.id || generateId(), op.color ?? "#3b82f6");
        const path = node.querySelector("path");
        if (path && markerId) {
          path.setAttribute("marker-end", `url(#${markerId})`);
        }

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId, createArrowhead]
    );

    /**
     * Draw text (SVG text element)
     */
    const drawText = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!svgRef.current) return null;

        const g = createGroup(op.id || generateId());
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");

        text.setAttribute("x", String(op.x ?? 0));
        text.setAttribute("y", String(op.y ?? 0));
        text.setAttribute("fill", op.color ?? "#000000");
        text.setAttribute("font-size", String(op.font_size ?? 16));
        text.setAttribute("font-family", op.font_family ?? "Arial, sans-serif");
        text.textContent = op.text ?? "";

        g.appendChild(text);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Draw path (freehand) with Rough.js
     */
    const drawPath = useCallback(
      (op: CanvasOperation): SVGElement | null => {
        if (!roughRef.current || !svgRef.current || !op.points || op.points.length < 2) return null;

        const g = createGroup(op.id || generateId());

        // Convert points to path string
        const pathString = op.points
          .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
          .join(" ");

        const node = roughRef.current.path(pathString, {
          stroke: op.color ?? "#3b82f6",
          strokeWidth: op.stroke_width ?? 2,
          roughness: 0.5 // Smoother for freehand
        });

        g.appendChild(node);
        return g;
      },
      [createGroup, generateId]
    );

    /**
     * Render LaTeX equation
     */
    const drawLatex = useCallback(
      (latexOp: LatexOperation): SVGElement | null => {
        if (!svgRef.current) return null;

        const g = createGroup(latexOp.id);

        // Render LaTeX to HTML string
        const html = katex.renderToString(latexOp.latex, {
          throwOnError: false,
          displayMode: true
        });

        // Create foreignObject to embed HTML in SVG
        const foreignObject = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
        foreignObject.setAttribute("x", String(latexOp.x));
        foreignObject.setAttribute("y", String(latexOp.y));
        foreignObject.setAttribute("width", "400"); // Adjust as needed
        foreignObject.setAttribute("height", "100"); // Adjust as needed

        const div = document.createElement("div");
        div.innerHTML = html;
        div.style.color = latexOp.color;
        div.style.fontSize = `${latexOp.font_size}px`;

        foreignObject.appendChild(div);
        g.appendChild(foreignObject);

        return g;
      },
      [createGroup]
    );

    /**
     * Render canvas operations
     */
    const render = useCallback(
      (operations: CanvasOperation[]) => {
        if (!svgRef.current) return;

        for (const op of operations) {
          // Handle control actions
          if (op.action === "clear") {
            elementsRef.current.clear();
            if (svgRef.current) {
              svgRef.current.innerHTML = "";
            }
            continue;
          }

          if (op.action === "delete") {
            const targetId = op.id || op.target_id;
            if (targetId && elementsRef.current.has(targetId)) {
              const data = elementsRef.current.get(targetId);
              data?.element.remove();
              elementsRef.current.delete(targetId);
            }
            continue;
          }

          // Draw element
          let element: SVGElement | null = null;
          const elemId = op.id || generateId();

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
            case "highlight":
              // Highlight existing element
              const targetId = op.id || op.target_id;
              if (targetId && elementsRef.current.has(targetId)) {
                const data = elementsRef.current.get(targetId);
                if (data) {
                  gsap.set(data.element, { transformOrigin: "center center" });
                  gsap.to(data.element, {
                    scale: 1.15,
                    duration: 0.3,
                    yoyo: true,
                    repeat: 1,
                    ease: EASING.teaching
                  });
                }
              }
              continue;
          }

          if (element) {
            // Set initial opacity to 0 for fade-in animation
            gsap.set(element, { opacity: 0 });
            svgRef.current.appendChild(element);

            // Fade in
            gsap.to(element, {
              opacity: 1,
              duration: DURATION.fast,
              ease: EASING.teaching
            });

            // Store element
            elementsRef.current.set(elemId, {
              element,
              id: elemId,
              type: op.action,
              x: op.x ?? 0,
              y: op.y ?? 0,
              data: op
            });
          }
        }

        setForceUpdate((n) => n + 1);
      },
      [drawRect, drawCircle, drawEllipse, drawLine, drawArrow, drawText, drawPath, generateId]
    );

    /**
     * Animate element
     */
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
          delay: animation.delay || 0
        });
      },
      []
    );

    /**
     * Render LaTeX
     */
    const renderLatex = useCallback(
      (latexOp: LatexOperation) => {
        if (!svgRef.current) return;

        const element = drawLatex(latexOp);
        if (element) {
          // Set initial opacity to 0 for fade-in animation
          gsap.set(element, { opacity: 0 });
          svgRef.current.appendChild(element);

          // Fade in
          gsap.to(element, {
            opacity: 1,
            duration: DURATION.fast,
            ease: EASING.teaching
          });

          // Store element
          elementsRef.current.set(latexOp.id, {
            element,
            id: latexOp.id,
            type: "latex",
            x: latexOp.x,
            y: latexOp.y,
            data: latexOp
          });
        }
      },
      [drawLatex]
    );

    /**
     * Play next queued sequence timeline
     */
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

    /**
     * Create teaching sequence (GSAP timeline) — auto-plays and queues
     */
    const createSequence = useCallback(
      (sequence: TeachingSequence): gsap.core.Timeline => {
        const tl = createTimeline({ paused: true });
        const timelineId = `tl_${Math.random().toString(36).substring(2, 10)}`;

        for (const step of sequence.steps) {
          switch (step.action) {
            case "clear":
              tl.add(() => {
                elementsRef.current.clear();
                if (svgRef.current) {
                  svgRef.current.innerHTML = "";
                }
              });
              break;

            case "draw":
              if (step.element) {
                tl.add(() => render([step.element!]), "+=0.2");
              }
              break;

            case "animate":
              if (step.target_id && step.properties) {
                // Defer element lookup to playback time
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
                  id: `latex_${generateId()}`,
                  latex: step.latex,
                  x: step.x,
                  y: step.y,
                  font_size: step.font_size ?? 20,
                  color: step.color ?? "#000000"
                };
                tl.add(() => renderLatex(latexOp), "+=0.2");
              }
              break;

            case "text": {
              // LLM sends text steps directly (not wrapped in element)
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
              tl.add(() => render([textOp]), "+=0.2");
              break;
            }

            case "highlight":
              if (step.target_id) {
                const hTargetId = step.target_id;
                const hDuration = step.duration ?? 0.3;
                tl.add(() => {
                  const targetData = elementsRef.current.get(hTargetId);
                  if (targetData) {
                    // Set transformOrigin to center for proper scaling
                    gsap.set(targetData.element, { transformOrigin: "center center" });
                    gsap.to(targetData.element, {
                      scale: 1.15,
                      duration: hDuration,
                      yoyo: true,
                      repeat: 1,
                      ease: EASING.teaching
                    });
                  } else {
                    console.warn(`Highlight target not found: ${hTargetId}`);
                  }
                }, ">");
              }
              break;

            case "pause":
              tl.to({}, { duration: step.duration ?? 0.5 });
              break;

            default: {
              // Treat as a canvas drawing primitive (rect, circle, ellipse, line, arrow, path)
              const shapeOp: CanvasOperation = {
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
              };
              tl.add(() => render([shapeOp]), "+=0.2");
              break;
            }
          }
        }

        // Store and queue
        timelinesRef.current.set(timelineId, tl);
        sequenceQueueRef.current.push(tl);

        // Start playing if not already
        if (!isPlayingRef.current) {
          playNextSequence();
        }

        return tl;
      },
      [render, renderLatex, generateId, playNextSequence]
    );

    /**
     * Clear canvas
     */
    const clear = useCallback(() => {
      // Kill all queued and active timelines
      sequenceQueueRef.current.forEach(tl => tl.kill());
      sequenceQueueRef.current = [];
      isPlayingRef.current = false;
      timelinesRef.current.forEach(tl => tl.kill());
      timelinesRef.current.clear();
      elementsRef.current.clear();
      if (svgRef.current) {
        svgRef.current.innerHTML = "";
      }
      setForceUpdate((n) => n + 1);
    }, []);

    /**
     * Save canvas as PNG image
     */
    const saveAsImage = useCallback(() => {
      const svg = svgRef.current;
      if (!svg) return;

      // Clone SVG so we can modify it for export
      const clone = svg.cloneNode(true) as SVGSVGElement;
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");

      // Inline KaTeX styles for foreignObject rendering
      const katexStyles = document.querySelector('style[data-katex]')
        || Array.from(document.styleSheets).find(s => s.href?.includes('katex'));

      // Add a white background rect as first child
      const bgRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bgRect.setAttribute("width", String(width));
      bgRect.setAttribute("height", String(height));
      bgRect.setAttribute("fill", "#ffffff");
      clone.insertBefore(bgRect, clone.firstChild);

      // Collect KaTeX CSS rules for inline embedding
      let katexCss = "";
      try {
        for (const sheet of Array.from(document.styleSheets)) {
          if (sheet.href?.includes("katex")) {
            for (const rule of Array.from(sheet.cssRules)) {
              katexCss += rule.cssText + "\n";
            }
          }
        }
      } catch { /* CORS may block access — ignore */ }

      if (katexCss) {
        const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
        styleEl.textContent = katexCss;
        clone.insertBefore(styleEl, clone.firstChild);
      }

      const serializer = new XMLSerializer();
      const svgString = serializer.serializeToString(clone);
      const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(svgBlob);

      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        const scale = 2; // Retina quality
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
          a.download = `canvas_${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.png`;
          a.href = pngUrl;
          a.click();
          URL.revokeObjectURL(pngUrl);
        }, "image/png");

        URL.revokeObjectURL(url);
      };
      img.src = url;
    }, [width, height]);

    // Expose methods via ref
    useImperativeHandle(
      ref,
      () => ({
        render,
        animate,
        renderLatex,
        createSequence,
        clear,
        saveAsImage
      }),
      [render, animate, renderLatex, createSequence, clear, saveAsImage]
    );

    // Track whether canvas has content for showing the save button
    const hasContent = elementsRef.current.size > 0;

    return (
      <div className={`relative ${className ?? ""}`}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          style={{
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            background: "#ffffff"
          }}
        />
        {/* Save as Image button */}
        <button
          onClick={saveAsImage}
          className="absolute top-3 right-3 p-2 rounded-lg bg-black/50 hover:bg-black/70 text-white/70 hover:text-white transition-all opacity-0 hover:opacity-100 focus:opacity-100 group-hover:opacity-100"
          style={{ opacity: undefined }} // Let CSS handle it
          title="Save as PNG"
          onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}
          onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.3")}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </button>
      </div>
    );
  }
);

SVGCanvas.displayName = "SVGCanvas";
