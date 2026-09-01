export type SceneNodeKind = "line" | "path" | "rect" | "text" | "latex";

export type ScenePoint = readonly [x: number, y: number];

export type SceneEnterEffect = "draw" | "fade" | "scale" | "none";
export type SceneExitEffect = "fade" | "none";

export interface ScenePresentation {
  readonly enter: SceneEnterEffect;
  readonly exit: SceneExitEffect;
}

export interface StrokeStyle {
  readonly stroke: string;
  readonly strokeWidth: number;
  readonly opacity: number;
  readonly roughness: number;
}

export interface ShapeStyle extends StrokeStyle {
  readonly fill: string;
}

export interface TextStyle {
  readonly color: string;
  readonly fontSize: number;
  readonly fontFamily?: string;
  readonly opacity: number;
  readonly anchor: "start" | "middle" | "end";
}

export interface LatexStyle {
  readonly color: string;
  readonly fontSize: number;
  readonly opacity: number;
}

interface SceneNodeBase<Kind extends SceneNodeKind> {
  readonly id: string;
  readonly kind: Kind;
  readonly presentation: ScenePresentation;
}

export interface LineSceneNode extends SceneNodeBase<"line"> {
  readonly points: readonly [ScenePoint, ScenePoint];
  readonly style: StrokeStyle;
}

export interface PathSceneNode extends SceneNodeBase<"path"> {
  readonly points: readonly ScenePoint[];
  readonly closed: boolean;
  readonly style: ShapeStyle;
}

export interface RectSceneNode extends SceneNodeBase<"rect"> {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly style: ShapeStyle;
}

export interface TextSceneNode extends SceneNodeBase<"text"> {
  readonly x: number;
  readonly y: number;
  readonly text: string;
  readonly style: TextStyle;
}

export interface LatexSceneNode extends SceneNodeBase<"latex"> {
  readonly x: number;
  readonly y: number;
  readonly latex: string;
  readonly style: LatexStyle;
}

export type SceneNode =
  | LineSceneNode
  | PathSceneNode
  | RectSceneNode
  | TextSceneNode
  | LatexSceneNode;

export interface SceneState {
  readonly revision: number;
  readonly nodes: readonly SceneNode[];
}

export type SceneUpdateTransition = "transform" | "crossfade";

export interface RemoveMotion {
  readonly type: "remove";
  readonly id: string;
  readonly node: SceneNode;
  readonly effect: SceneExitEffect;
}

export interface UpdateMotion {
  readonly type: "update";
  readonly id: string;
  readonly previous: SceneNode;
  readonly next: SceneNode;
  readonly transition: SceneUpdateTransition;
}

export interface EnterMotion {
  readonly type: "enter";
  readonly id: string;
  readonly node: SceneNode;
  readonly effect: SceneEnterEffect;
}

export type MotionStep = RemoveMotion | UpdateMotion | EnterMotion;

export interface MotionPlan {
  readonly fromRevision: number;
  readonly toRevision: number;
  readonly steps: readonly MotionStep[];
}
