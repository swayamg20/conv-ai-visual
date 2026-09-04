import type {
  LatexStyle,
  LineSceneNode,
  PathSceneNode,
  RectSceneNode,
  SceneNode,
  ScenePoint,
  ScenePresentation,
  SceneState,
  ShapeStyle,
  StrokeStyle,
  TextSceneNode,
  TextStyle,
} from "./types";

const SCENE_NODE_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;

function validationError(message: string): never {
  throw new TypeError(`Invalid live scene: ${message}`);
}

function assertFinite(value: number, field: string): void {
  if (!Number.isFinite(value)) validationError(`${field} must be finite`);
}

function assertPositive(value: number, field: string): void {
  assertFinite(value, field);
  if (value <= 0) validationError(`${field} must be greater than zero`);
}

function assertNonNegative(value: number, field: string): void {
  assertFinite(value, field);
  if (value < 0) validationError(`${field} must not be negative`);
}

function assertOpacity(value: number, field: string): void {
  assertFinite(value, field);
  if (value < 0 || value > 1) validationError(`${field} must be between zero and one`);
}

function assertNonEmpty(value: string, field: string): void {
  if (typeof value !== "string" || value.trim().length === 0) {
    validationError(`${field} must be a non-empty string`);
  }
}

function clonePresentation(
  presentation: ScenePresentation,
  nodeId: string
): ScenePresentation {
  if (!presentation || !["draw", "fade", "scale", "none"].includes(presentation.enter)) {
    validationError(`node ${nodeId} has an invalid enter presentation`);
  }
  if (!["fade", "none"].includes(presentation.exit)) {
    validationError(`node ${nodeId} has an invalid exit presentation`);
  }
  return Object.freeze({ enter: presentation.enter, exit: presentation.exit });
}

function clonePoint(point: ScenePoint, field: string): ScenePoint {
  if (!Array.isArray(point) || point.length !== 2) {
    validationError(`${field} must contain exactly two coordinates`);
  }
  assertFinite(point[0], `${field}[0]`);
  assertFinite(point[1], `${field}[1]`);
  return Object.freeze([point[0], point[1]]) as ScenePoint;
}

function cloneStrokeStyle(style: StrokeStyle, nodeId: string): StrokeStyle {
  if (!style) validationError(`node ${nodeId} is missing its stroke style`);
  assertNonEmpty(style.stroke, `node ${nodeId} stroke`);
  assertPositive(style.strokeWidth, `node ${nodeId} strokeWidth`);
  assertOpacity(style.opacity, `node ${nodeId} opacity`);
  assertNonNegative(style.roughness, `node ${nodeId} roughness`);
  return Object.freeze({
    stroke: style.stroke,
    strokeWidth: style.strokeWidth,
    opacity: style.opacity,
    roughness: style.roughness,
  });
}

function cloneShapeStyle(style: ShapeStyle, nodeId: string): ShapeStyle {
  const stroke = cloneStrokeStyle(style, nodeId);
  assertNonEmpty(style.fill, `node ${nodeId} fill`);
  return Object.freeze({ ...stroke, fill: style.fill });
}

function cloneTextStyle(style: TextStyle, nodeId: string): TextStyle {
  if (!style) validationError(`node ${nodeId} is missing its text style`);
  assertNonEmpty(style.color, `node ${nodeId} color`);
  assertPositive(style.fontSize, `node ${nodeId} fontSize`);
  assertOpacity(style.opacity, `node ${nodeId} opacity`);
  if (style.fontFamily !== undefined) {
    assertNonEmpty(style.fontFamily, `node ${nodeId} fontFamily`);
  }
  if (!["start", "middle", "end"].includes(style.anchor)) {
    validationError(`node ${nodeId} has an invalid text anchor`);
  }
  return Object.freeze({
    color: style.color,
    fontSize: style.fontSize,
    ...(style.fontFamily === undefined ? {} : { fontFamily: style.fontFamily }),
    opacity: style.opacity,
    anchor: style.anchor,
  });
}

function cloneLatexStyle(style: LatexStyle, nodeId: string): LatexStyle {
  if (!style) validationError(`node ${nodeId} is missing its LaTeX style`);
  assertNonEmpty(style.color, `node ${nodeId} color`);
  assertPositive(style.fontSize, `node ${nodeId} fontSize`);
  assertOpacity(style.opacity, `node ${nodeId} opacity`);
  return Object.freeze({
    color: style.color,
    fontSize: style.fontSize,
    opacity: style.opacity,
  });
}

function cloneLineNode(
  node: LineSceneNode,
  presentation: ScenePresentation
): LineSceneNode {
  if (!Array.isArray(node.points) || node.points.length !== 2) {
    validationError(`line node ${node.id} must contain exactly two points`);
  }
  const points = Object.freeze([
    clonePoint(node.points[0], `node ${node.id} points[0]`),
    clonePoint(node.points[1], `node ${node.id} points[1]`),
  ]) as unknown as LineSceneNode["points"];
  return Object.freeze({
    id: node.id,
    kind: "line",
    presentation,
    points,
    style: cloneStrokeStyle(node.style, node.id),
  });
}

function clonePathNode(
  node: PathSceneNode,
  presentation: ScenePresentation
): PathSceneNode {
  if (!Array.isArray(node.points) || node.points.length < 2) {
    validationError(`path node ${node.id} must contain at least two points`);
  }
  if (typeof node.closed !== "boolean") {
    validationError(`path node ${node.id} closed must be a boolean`);
  }
  const points = Object.freeze(
    node.points.map((point, index) => clonePoint(point, `node ${node.id} points[${index}]`))
  ) as unknown as PathSceneNode["points"];
  return Object.freeze({
    id: node.id,
    kind: "path",
    presentation,
    points,
    closed: node.closed,
    style: cloneShapeStyle(node.style, node.id),
  });
}

function cloneRectNode(
  node: RectSceneNode,
  presentation: ScenePresentation
): RectSceneNode {
  assertFinite(node.x, `node ${node.id} x`);
  assertFinite(node.y, `node ${node.id} y`);
  assertPositive(node.width, `node ${node.id} width`);
  assertPositive(node.height, `node ${node.id} height`);
  return Object.freeze({
    id: node.id,
    kind: "rect",
    presentation,
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    style: cloneShapeStyle(node.style, node.id),
  });
}

function cloneTextNode(
  node: TextSceneNode,
  presentation: ScenePresentation
): TextSceneNode {
  assertFinite(node.x, `node ${node.id} x`);
  assertFinite(node.y, `node ${node.id} y`);
  assertNonEmpty(node.text, `node ${node.id} text`);
  return Object.freeze({
    id: node.id,
    kind: "text",
    presentation,
    x: node.x,
    y: node.y,
    text: node.text,
    style: cloneTextStyle(node.style, node.id),
  });
}

function cloneSceneNode(node: SceneNode): SceneNode {
  if (!node || typeof node !== "object") validationError("each node must be an object");
  if (typeof node.id !== "string" || !SCENE_NODE_ID_PATTERN.test(node.id)) {
    validationError(
      `node id ${JSON.stringify(node.id)} must match ${SCENE_NODE_ID_PATTERN.source}`
    );
  }
  const presentation = clonePresentation(node.presentation, node.id);

  switch (node.kind) {
    case "line":
      return cloneLineNode(node, presentation);
    case "path":
      return clonePathNode(node, presentation);
    case "rect":
      return cloneRectNode(node, presentation);
    case "text":
      return cloneTextNode(node, presentation);
    case "latex": {
      assertFinite(node.x, `node ${node.id} x`);
      assertFinite(node.y, `node ${node.id} y`);
      assertNonEmpty(node.latex, `node ${node.id} latex`);
      return Object.freeze({
        id: node.id,
        kind: "latex",
        presentation,
        x: node.x,
        y: node.y,
        latex: node.latex,
        style: cloneLatexStyle(node.style, node.id),
      });
    }
    default:
      return validationError("node has an unsupported kind");
  }
}

/** Validate, deep-clone, and freeze one committed scene snapshot. */
export function createSceneState(input: SceneState): SceneState {
  if (!input || typeof input !== "object") validationError("state must be an object");
  if (!Number.isSafeInteger(input.revision) || input.revision < 0) {
    validationError("revision must be a non-negative safe integer");
  }
  if (!Array.isArray(input.nodes)) validationError("nodes must be an array");

  const ids = new Set<string>();
  const nodes = input.nodes.map((node) => {
    const snapshot = cloneSceneNode(node);
    if (ids.has(snapshot.id)) validationError(`node id ${snapshot.id} is duplicated`);
    ids.add(snapshot.id);
    return snapshot;
  });

  return Object.freeze({
    revision: input.revision,
    nodes: Object.freeze(nodes),
  });
}
