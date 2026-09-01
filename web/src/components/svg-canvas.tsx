"use client";

import { gsap } from "gsap";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import rough from "roughjs";
import type { RoughSVG } from "roughjs/bin/svg";

import { saveCanvasImage } from "@/features/canvas/export-image";
import { normalizeOperation, normalizeTeachingSteps } from "@/features/canvas/normalization";
import { createSvgPrimitiveRenderer } from "@/features/canvas/primitives";
import { createTeachingTimeline } from "@/features/canvas/timeline";
import type {
  AnimationOperation,
  CanvasOperation,
  FunctionPlotData,
  LatexOperation,
  SVGCanvasHandle,
  SVGCanvasProps,
  SVGElementData,
  TeachingSequence,
  TeachingStep,
} from "@/features/canvas/types";
import { createSvgMotionExecutor } from "@/features/live-scene/svg-motion-executor";
import { useCanvasViewport } from "@/features/canvas/viewport";
import { getCanvasPalette, GRID_SNAP, renderGrid } from "@/lib/canvas-utils";
import {
  animateColorPulse,
  animateCrossOut,
  animateDrawOn,
  animateInkReveal,
  animateProgressivePath,
  animateSceneFadeOut,
  DURATION,
  EASING,
} from "@/lib/gsap-setup";
import "katex/dist/katex.min.css";

function sequenceFocus(steps: TeachingStep[]): { x: number; y: number } | null {
  const firstPositionedStep = steps.find(
    (step) => step.y !== undefined || step.element?.y !== undefined
  );
  if (!firstPositionedStep) return null;
  return {
    x: firstPositionedStep.x ?? firstPositionedStep.element?.x ?? 0,
    y: firstPositionedStep.y ?? firstPositionedStep.element?.y ?? 0,
  };
}

export const SVGCanvas = forwardRef<SVGCanvasHandle, SVGCanvasProps>(
  ({ width = 800, height = 600, className, showGrid = true }, ref) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const roughRef = useRef<RoughSVG | null>(null);
    const elementsRef = useRef<Map<string, SVGElementData>>(new Map());
    const timelinesRef = useRef<Map<string, gsap.core.Timeline>>(new Map());
    const sequenceQueueRef = useRef<gsap.core.Timeline[]>([]);
    const isPlayingRef = useRef(false);
    const [, forceRender] = useState(0);
    const paletteRef = useRef(getCanvasPalette());
    const {
      applyViewBox,
      handlePointerDown,
      handlePointerMove,
      handlePointerUp,
      isPanning,
      panRef,
      panTo,
      resetZoom,
      zoomIn,
      zoomLevel,
      zoomOut,
    } = useCanvasViewport({ svgRef, width, height });

    useEffect(() => {
      const refreshPalette = () => {
        paletteRef.current = getCanvasPalette();
        if (svgRef.current && showGrid) renderGrid(svgRef.current, width, height);
      };
      const observer = new MutationObserver((mutations) => {
        if (mutations.some((mutation) => mutation.attributeName === "class")) {
          refreshPalette();
        }
      });
      observer.observe(document.documentElement, { attributes: true });
      return () => observer.disconnect();
    }, [height, showGrid, width]);

    useEffect(() => {
      paletteRef.current = getCanvasPalette();
      if (!svgRef.current) return;
      roughRef.current = rough.svg(svgRef.current);
      if (showGrid) renderGrid(svgRef.current, width, height);
    }, [height, showGrid, width]);

    useEffect(() => {
      const timelines = timelinesRef.current;
      const sequenceQueue = sequenceQueueRef.current;
      return () => {
        timelines.forEach((timeline) => timeline.kill());
        timelines.clear();
        sequenceQueue.forEach((timeline) => timeline.kill());
        sequenceQueue.length = 0;
      };
    }, []);

    const generateId = useCallback(
      () => `elem_${Math.random().toString(36).substring(2, 10)}`,
      []
    );

    const primitiveRenderer = useCallback(() => {
      if (!svgRef.current) return null;
      return createSvgPrimitiveRenderer({
        svg: svgRef.current,
        rough: roughRef.current,
        palette: paletteRef.current,
        generateId,
      });
    }, [generateId]);

    const sceneMotionExecutor = useMemo(
      () =>
        createSvgMotionExecutor({
          elements: elementsRef.current,
          getSvg: () => svgRef.current,
          getRenderer: primitiveRenderer,
          getHighlightColor: () => paletteRef.current.error,
          invalidate: () => forceRender((revision) => revision + 1),
        }),
      [primitiveRenderer]
    );

    useEffect(
      () => () => {
        sceneMotionExecutor.dispose();
      },
      [sceneMotionExecutor]
    );

    const renderFunctionPlot = useCallback(
      (plot: FunctionPlotData) => {
        const svg = svgRef.current;
        const renderer = primitiveRenderer();
        if (!svg || !renderer) return;
        const result = renderer.drawFunctionPlot(plot, { width, height });
        if (!result) return;

        svg.appendChild(result.group);
        if (plot.animate) {
          gsap.set(result.group, { opacity: 1 });
          gsap.fromTo(
            result.axes,
            { opacity: 0 },
            { opacity: 1, duration: DURATION.fast, stagger: 0.02 }
          );
          animateProgressivePath(result.curve, DURATION.verySlow, EASING.draw);
        }

        elementsRef.current.set(result.id, {
          element: result.group,
          id: result.id,
          type: "function_plot",
          x: result.margin,
          y: result.margin,
          data: plot,
        });
        forceRender((revision) => revision + 1);
      },
      [height, primitiveRenderer, width]
    );

    const render = useCallback(
      (operations: CanvasOperation[]) => {
        const svg = svgRef.current;
        const renderer = primitiveRenderer();
        if (!svg || !renderer) return;

        for (const rawOperation of operations) {
          const operation = ["clear", "delete", "highlight", "crossout"].includes(
            rawOperation.action
          )
            ? rawOperation
            : normalizeOperation(rawOperation);

          if (operation.action === "clear") {
            animateSceneFadeOut(svg).then(() => {
              Array.from(svg.children)
                .filter((element) => element.id !== "canvas-grid" && element.tagName !== "defs")
                .forEach((element) => element.remove());
            });
            elementsRef.current.clear();
            continue;
          }

          if (operation.action === "delete") {
            const targetId = operation.id || operation.target_id;
            const target = targetId ? elementsRef.current.get(targetId) : undefined;
            if (target && targetId) {
              gsap.to(target.element, {
                opacity: 0,
                duration: DURATION.fast,
                ease: EASING.smooth,
                onComplete: () => target.element.remove(),
              });
              elementsRef.current.delete(targetId);
            }
            continue;
          }

          if (operation.action === "crossout") {
            const targetId = operation.id || operation.target_id;
            const target = targetId ? elementsRef.current.get(targetId) : undefined;
            if (target) {
              animateCrossOut(
                svg,
                target.element,
                operation.color ?? paletteRef.current.error,
                DURATION.fast
              );
            }
            continue;
          }

          if (operation.action === "highlight") {
            const targetId = operation.id || operation.target_id;
            const target = targetId ? elementsRef.current.get(targetId) : undefined;
            if (!target) continue;
            if (operation.highlight_color) {
              animateColorPulse(target.element, operation.highlight_color, 0.4, 2);
            } else {
              gsap.set(target.element, { transformOrigin: "center center" });
              gsap.to(target.element, {
                scale: 1.15,
                duration: 0.3,
                yoyo: true,
                repeat: 1,
                ease: EASING.teaching,
              });
            }
            continue;
          }

          const elementId = operation.id || generateId();
          const animateStyle = operation.animate_style ?? "draw";
          const element = renderer.draw({ ...operation, id: elementId });
          if (!element) continue;

          gsap.set(element, { opacity: 0 });
          svg.appendChild(element);
          const hasStrokePaths =
            element.querySelectorAll("path[stroke]:not([stroke='none'])").length > 0;
          const isInkElement =
            (operation.action === "arrow" || operation.action === "path") && !hasStrokePaths;

          if (operation.action === "curve") {
            gsap.set(element, { opacity: 1 });
            const curve = element.querySelector("path");
            if (curve) animateProgressivePath(curve, DURATION.verySlow, EASING.draw);
          } else if (animateStyle === "draw" && isInkElement) {
            animateInkReveal(element, DURATION.draw, EASING.draw);
          } else if (
            animateStyle === "draw" &&
            operation.action !== "text" &&
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

          elementsRef.current.set(elementId, {
            element,
            id: elementId,
            type: operation.action,
            x: operation.x ?? 0,
            y: operation.y ?? 0,
            data: operation,
          });
        }

        forceRender((revision) => revision + 1);
      },
      [generateId, primitiveRenderer]
    );

    const animate = useCallback((animation: AnimationOperation): gsap.core.Tween | null => {
      const target = elementsRef.current.get(animation.target_id);
      if (!target) {
        console.warn(`Animation target not found: ${animation.target_id}`);
        return null;
      }
      return gsap.to(target.element, {
        ...animation.properties,
        duration: animation.duration,
        ease: animation.ease,
        delay: animation.delay || 0,
      });
    }, []);

    const renderLatex = useCallback(
      (operation: LatexOperation) => {
        const svg = svgRef.current;
        const renderer = primitiveRenderer();
        if (!svg || !renderer) return;
        const element = renderer.drawLatex(operation);
        gsap.set(element, { opacity: 0 });
        svg.appendChild(element);
        gsap.to(element, {
          opacity: 1,
          duration: DURATION.fast,
          ease: EASING.teaching,
        });
        elementsRef.current.set(operation.id, {
          element,
          id: operation.id,
          type: "latex",
          x: operation.x,
          y: operation.y,
          data: operation,
        });
      },
      [primitiveRenderer]
    );

    const clearTimelineScene = useCallback(() => {
      const svg = svgRef.current;
      if (!svg) {
        elementsRef.current.clear();
        return;
      }
      const children = Array.from(svg.children).filter(
        (element) => element.id !== "canvas-grid" && element.tagName !== "defs"
      );
      gsap.to(children, {
        opacity: 0,
        duration: DURATION.fast,
        ease: EASING.smooth,
        stagger: 0.02,
        onComplete: () => children.forEach((element) => element.remove()),
      });
      elementsRef.current.clear();
    }, []);

    const buildTimeline = useCallback(
      (steps: TeachingStep[]) =>
        createTeachingTimeline(steps, {
          render,
          renderLatex,
          clearScene: clearTimelineScene,
          getElement: (id) => elementsRef.current.get(id)?.element ?? null,
          getSvg: () => svgRef.current,
          generateId,
          palette: paletteRef.current,
        }),
      [clearTimelineScene, generateId, render, renderLatex]
    );

    const playNextSequence = useCallback(() => {
      const next = sequenceQueueRef.current.shift();
      if (!next) {
        isPlayingRef.current = false;
        return;
      }
      isPlayingRef.current = true;
      next.eventCallback("onComplete", playNextSequence);
      next.play();
    }, []);

    const createSequence = useCallback(
      (sequence: TeachingSequence): gsap.core.Timeline => {
        const steps = normalizeTeachingSteps(sequence.steps);
        const timeline = buildTimeline(steps);
        const timelineId = `tl_${Math.random().toString(36).substring(2, 10)}`;
        timelinesRef.current.set(timelineId, timeline);
        sequenceQueueRef.current.push(timeline);

        const focus = sequenceFocus(steps);
        if (focus) {
          const pan = panRef.current;
          const outsideView =
            focus.y < pan.y ||
            focus.y > pan.y + height / zoomLevel - GRID_SNAP * 4 ||
            focus.x < pan.x ||
            focus.x > pan.x + width / zoomLevel - GRID_SNAP * 4;
          if (outsideView) panTo(focus.x, focus.y);
        }

        if (!isPlayingRef.current) playNextSequence();
        return timeline;
      },
      [buildTimeline, height, panRef, panTo, playNextSequence, width, zoomLevel]
    );

    const createPausedSequence = useCallback(
      (sequence: TeachingSequence): gsap.core.Timeline => {
        const timeline = buildTimeline(normalizeTeachingSteps(sequence.steps));
        const timelineId = `tl_${Math.random().toString(36).substring(2, 10)}`;
        timelinesRef.current.set(timelineId, timeline);
        return timeline;
      },
      [buildTimeline]
    );

    const playMotionPlan = sceneMotionExecutor.play;
    const emphasizeElement = sceneMotionExecutor.emphasize;
    const cancelMotion = useCallback(() => {
      sceneMotionExecutor.cancel();
      sequenceQueueRef.current.forEach((timeline) => timeline.kill());
      sequenceQueueRef.current.length = 0;
      isPlayingRef.current = false;
      timelinesRef.current.forEach((timeline) => timeline.kill());
      timelinesRef.current.clear();

      const svg = svgRef.current;
      if (!svg) return;
      const canvasTargets = [svg, ...Array.from(svg.querySelectorAll("*"))];
      gsap.getTweensOf(canvasTargets).forEach((animation) => animation.kill());
    }, [sceneMotionExecutor]);

    const clear = useCallback(() => {
      cancelMotion();
      panRef.current = { x: 0, y: 0 };
      applyViewBox(zoomLevel, panRef.current, true);

      const svg = svgRef.current;
      if (!svg) {
        elementsRef.current.clear();
        forceRender((revision) => revision + 1);
        return;
      }
      Array.from(svg.children)
        .filter((element) => element.id !== "canvas-grid")
        .forEach((element) => element.remove());
      elementsRef.current.clear();
      forceRender((revision) => revision + 1);
    }, [applyViewBox, cancelMotion, panRef, zoomLevel]);

    const saveAsImage = useCallback(() => {
      const svg = svgRef.current;
      if (!svg) return;
      saveCanvasImage(svg, width, height);
    }, [height, width]);

    useImperativeHandle(
      ref,
      () => ({
        render,
        animate,
        renderLatex,
        createSequence,
        createPausedSequence,
        renderFunctionPlot,
        playMotionPlan,
        emphasizeElement,
        cancelMotion,
        clear,
        saveAsImage,
        zoomIn,
        zoomOut,
        resetZoom,
        panTo,
      }),
      [
        animate,
        cancelMotion,
        clear,
        createPausedSequence,
        createSequence,
        emphasizeElement,
        panTo,
        playMotionPlan,
        render,
        renderFunctionPlot,
        renderLatex,
        resetZoom,
        saveAsImage,
        zoomIn,
        zoomOut,
      ]
    );

    return (
      <div className={`relative group ${className ?? ""}`}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={handlePointerUp}
          style={{
            border: "1px solid hsl(var(--chalk-faint) / 0.5)",
            borderRadius: "8px",
            background: "hsl(var(--void))",
            cursor: isPanning ? "grabbing" : "grab",
            touchAction: "none",
          }}
        />
        <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={zoomIn}
            className="p-1.5 rounded-md bg-void/80 hover:bg-slate text-chalk-soft hover:text-chalk transition-all text-xs font-mono"
            title="Zoom in"
          >
            +
          </button>
          <button
            onClick={resetZoom}
            className="p-1.5 rounded-md bg-void/80 hover:bg-slate text-chalk-soft hover:text-chalk transition-all text-xs font-mono min-w-[2.5rem] text-center"
            title="Reset zoom"
          >
            {Math.round(zoomLevel * 100)}%
          </button>
          <button
            onClick={zoomOut}
            className="p-1.5 rounded-md bg-void/80 hover:bg-slate text-chalk-soft hover:text-chalk transition-all text-xs font-mono"
            title="Zoom out"
          >
            -
          </button>
          <div className="w-px h-4 bg-white/20 mx-1" />
          <button
            onClick={saveAsImage}
            className="p-1.5 rounded-md bg-void/80 hover:bg-slate text-chalk-soft hover:text-chalk transition-all"
            title="Save as PNG"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
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
