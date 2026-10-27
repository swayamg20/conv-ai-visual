"use client";

import { useEffect, useRef } from "react";

import { SVGCanvas } from "@/components/svg-canvas";
import type { SVGCanvasHandle } from "@/features/canvas/types";

import type { Thought } from "./contracts";
import { formatNumber, type QuadraticVisual } from "./quadratic";

export function QuadraticCard({ thought, visual, acknowledge }: {
  thought: Thought;
  visual: QuadraticVisual;
  acknowledge: (id: string) => void;
}) {
  const canvas = useRef<SVGCanvasHandle>(null);
  const host = useRef<HTMLElement>(null);
  useEffect(() => {
    const renderer = canvas.current;
    renderer?.clear();
    renderer?.renderFunctionPlot(visual.plot);
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        const curve = host.current?.querySelector('g[id^="plot_"] path');
        if (curve?.getAttribute("d")?.startsWith("M")) acknowledge(thought.id);
      });
    });
    return () => { cancelAnimationFrame(frame); renderer?.clear(); };
  }, [acknowledge, thought.id, visual]);
  return (
    <article ref={host} data-thought-id={thought.id} data-visual="quadratic" className="min-w-0 rounded-xl border border-lavender/25 bg-lavender/5 p-4">
      <p className="text-xs text-muted-foreground">Quadratic equation</p>
      <h3 className="mt-2 break-words text-xl font-medium">{visual.expression} = 0</h3>
      <figure className="mt-4" aria-label={`Graph of y = ${visual.expression}`}>
        <SVGCanvas ref={canvas} width={560} height={350} showGrid={false} viewportInteractionLocked reducedMotion className="[&>svg]:h-auto [&>svg]:w-full" />
        <figcaption className="mt-2 text-xs text-muted-foreground">y = {visual.expression}. Where the curve meets y = 0, x solves the equation.</figcaption>
      </figure>
      <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
        <p><span className="block text-xs text-muted-foreground">Real roots</span>{visual.roots.length ? visual.roots.map(formatNumber).join(" and ") : "None"}</p>
        <p><span className="block text-xs text-muted-foreground">Vertex</span>({visual.vertex.map(formatNumber).join(", ")})</p>
      </div>
    </article>
  );
}
