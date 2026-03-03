// ============= SDL Types (what the LLM generates) =============

export interface SDLScene {
  steps: SDLStep[];
}

export interface SDLStep {
  say: string;
  show?: SDLShowDirective;
  highlight?: string | string[];
  clear?: boolean;
}

export interface SDLShowDirective {
  component: ComponentName;
  props: Record<string, unknown>;
  position?: RelativePosition;
  id?: string;
}

export type ComponentName =
  | "right_triangle"
  | "equation"
  | "coordinate_plane"
  | "function_plot"
  | "number_line"
  | "bar_chart"
  | "flowchart"
  | "tree"
  | "venn_diagram"
  | "circle_diagram"
  | "label";

export type RelativePosition =
  | "center"
  | "top"
  | "bottom"
  | "left"
  | "right"
  | { below: string; gap?: number }
  | { rightOf: string; gap?: number };

// ============= Render Plan (what scene-kit produces) =============

export interface RenderPlan {
  steps: RenderStep[];
}

export interface RenderStep {
  say: string;
  commands: RenderCommand[];
}

/**
 * Matches the TeachingStep interface in svg-canvas.tsx so output feeds
 * directly into SVGCanvas.createSequence().
 */
export interface RenderCommand {
  action: string;
  id?: string;
  target_id?: string;
  label?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  color?: string;
  fill?: string;
  stroke_width?: number;
  points?: [number, number][];
  text?: string;
  latex?: string;
  font_size?: number;
  font_family?: string;
  roughness?: number;
  animate_style?: "draw" | "fade" | "scale" | "none";
  highlight_color?: string;
  duration?: number;
  speech_cue?: string;
  properties?: Record<string, unknown>;
}

// ============= Component Interface =============

export interface Viewport {
  width: number;
  height: number;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ComponentResult {
  commands: RenderCommand[];
  bounds: BoundingBox;
  /** Maps user-facing names (e.g. "c") to internal element target_ids */
  namedElements: Record<string, string>;
}

export type ComponentRenderer = (
  props: Record<string, unknown>,
  origin: { x: number; y: number },
  viewport: Viewport
) => ComponentResult;
