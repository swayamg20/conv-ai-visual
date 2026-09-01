import type { gsap } from "gsap";

import type { MotionPlan } from "@/lib/live-scene";

export type MotionPlaybackStatus = "completed" | "cancelled" | "failed";

export interface MotionPlaybackOutcome {
  readonly status: MotionPlaybackStatus;
  /** Plan step IDs that are materially represented by the retained SVG. */
  readonly appliedStepIds: readonly string[];
  readonly error?: string;
}

export interface MotionPlayback {
  readonly finished: Promise<MotionPlaybackOutcome>;
  pause(): void;
  resume(): void;
  cancel(): MotionPlaybackOutcome;
}

export interface MotionPlaybackOptions {
  /** Delay between starting adjacent plan steps. */
  staggerMs?: number;
}

export interface CanvasOperation {
  id?: string;
  action: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  color?: string;
  fill?: string;
  stroke_width?: number;
  points?: [number, number][];
  text?: string;
  font_size?: number;
  font_family?: string;
  target_id?: string;
  roughness?: number;
  animate_style?: "draw" | "fade" | "scale" | "none";
  highlight_color?: string;
  _centered?: boolean;
}

export interface AnimationOperation {
  type: "animation";
  target_id: string;
  properties: {
    x?: number;
    y?: number;
    opacity?: number;
    scale?: number;
    rotation?: number;
    [key: string]: unknown;
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
  properties?: Record<string, unknown>;
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
  animate_style?: CanvasOperation["animate_style"];
  highlight_color?: string;
  _centered?: boolean;
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

export interface SVGElementData {
  element: SVGElement;
  id: string;
  type: string;
  x: number;
  y: number;
  data: unknown;
}

export interface SVGCanvasHandle {
  render(operations: CanvasOperation[]): void;
  animate(animation: AnimationOperation): gsap.core.Tween | null;
  renderLatex(latex: LatexOperation): void;
  createSequence(sequence: TeachingSequence): gsap.core.Timeline;
  createPausedSequence(sequence: TeachingSequence): gsap.core.Timeline;
  renderFunctionPlot(plot: FunctionPlotData): void;
  playMotionPlan(plan: MotionPlan, options?: MotionPlaybackOptions): MotionPlayback;
  emphasizeElement(id: string, color?: string): void;
  /** Stop scheduled and active drawing work without removing visible SVG elements. */
  cancelMotion(): void;
  clear(): void;
  saveAsImage(): void;
  zoomIn(): void;
  zoomOut(): void;
  resetZoom(): void;
  panTo(x: number, y: number): void;
}

export interface SVGCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  showGrid?: boolean;
}

export interface CanvasPalette {
  stroke: string;
  grid: string;
  axis: string;
  error: string;
  bg: string;
}
