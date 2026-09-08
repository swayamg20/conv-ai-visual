import type { gsap } from "gsap";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
} from "@/features/live-scene/choreography-executor";
import type {
  MotionPlan,
  PlannedCheckpointChoreography,
  SceneState,
} from "@/lib/live-scene";
import type {
  ChoreographyEasing,
  ViewportPoseV1,
} from "@/lib/live-scene/choreography";

export type MotionPlaybackStatus = "completed" | "cancelled" | "failed";

export interface MotionPlaybackOutcome {
  readonly status: MotionPlaybackStatus;
  /** Plan step IDs whose target state is materially committed to the retained SVG. */
  readonly appliedStepIds: readonly string[];
  readonly error?: string;
}

export interface MotionPlayback {
  /** Post-presentation receipt; resolves only after the terminal DOM crosses its barrier. */
  readonly finished: Promise<MotionPlaybackOutcome>;
  pause(): void;
  resume(): void;
  /** Settle synchronously; use `finished` when a post-presentation receipt is required. */
  cancel(): MotionPlaybackOutcome;
}

export interface MotionPlaybackOptions {
  /** Delay between starting adjacent plan steps. */
  staggerMs?: number;
}

export type ViewportPlaybackStatus = "completed" | "cancelled" | "failed";

export interface ViewportPlaybackOutcome {
  readonly status: ViewportPlaybackStatus;
  /** Exact pose materialized when playback settled. */
  readonly pose: ViewportPoseV1;
  readonly error?: string;
}

export interface ViewportPlayback {
  readonly finished: Promise<ViewportPlaybackOutcome>;
  /** Stop at the currently rendered pose without substituting the destination. */
  cancel(): ViewportPlaybackOutcome;
}

export interface ViewportPlaybackOptions {
  readonly durationMs: number;
  readonly easing: ChoreographyEasing;
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

export interface LatexTokenOperation {
  type: "latex_token";
  id: string;
  latex: string;
  /** Horizontal anchor coordinate in the logical canvas. */
  x: number;
  /** Top edge of the measured token box. */
  y: number;
  width: number;
  height: number;
  anchor: "start" | "middle" | "end";
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
  playMotionPlan(
    plan: MotionPlan,
    options?: MotionPlaybackOptions,
  ): MotionPlayback;
  /** Execute one verified checkpoint through the closed choreography engine. */
  playCheckpointChoreography(
    plan: PlannedCheckpointChoreography,
    observer?: ChoreographyExecutorObserver,
  ): ChoreographyPlayback;
  /** Return the exact viewBox currently rendered, including during a tween. */
  readViewport(): ViewportPoseV1;
  /** Animate only through the closed choreography camera vocabulary. */
  animateViewport(
    pose: ViewportPoseV1,
    options: ViewportPlaybackOptions,
  ): ViewportPlayback;
  /** Write one certified frame without cancelling playback or updating React state. */
  renderViewportFrame(pose: ViewportPoseV1): void;
  /** Kill camera motion and atomically apply an exact certified pose. */
  materializeViewport(pose: ViewportPoseV1): void;
  /** Reset to a supplied certified pose or the complete logical board. */
  resetViewport(pose?: ViewportPoseV1): void;
  /** Stop camera motion at its actually rendered pose. */
  cancelViewportAnimation(): ViewportPlaybackOutcome | null;
  /** Atomically reconcile the retained SVG to one canonical scene snapshot. */
  materializeScene(scene: SceneState): void;
  emphasizeElement(id: string, color?: string): void;
  /** Stop queued work and settle each active motion to its canonical terminal state. */
  cancelMotion(): void;
  clear(): void;
  saveAsImage(): void;
  zoomIn(): void;
  zoomOut(): void;
  resetZoom(): void;
  panTo(x: number, y: number, zoom?: number): void;
}

export interface SVGCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  showGrid?: boolean;
  /** Disable manual pan and zoom for a certified choreography session. */
  viewportInteractionLocked?: boolean;
  /** Preserve every checkpoint while eliminating spatial travel and tweening. */
  reducedMotion?: boolean;
}

export interface CanvasPalette {
  stroke: string;
  grid: string;
  axis: string;
  error: string;
  bg: string;
}
