import { createSceneState } from "./state";
import type {
  MotionPlan,
  MotionStep,
  SceneNode,
  ScenePoint,
  ScenePresentation,
  SceneState,
  ShapeStyle,
  StrokeStyle,
  TextStyle,
} from "./types";

function samePoint(left: ScenePoint, right: ScenePoint): boolean {
  return left[0] === right[0] && left[1] === right[1];
}

function samePoints(left: readonly ScenePoint[], right: readonly ScenePoint[]): boolean {
  return left.length === right.length && left.every((point, index) => samePoint(point, right[index]));
}

function samePresentation(left: ScenePresentation, right: ScenePresentation): boolean {
  return left.enter === right.enter && left.exit === right.exit;
}

function sameStrokeStyle(left: StrokeStyle, right: StrokeStyle): boolean {
  return (
    left.stroke === right.stroke &&
    left.strokeWidth === right.strokeWidth &&
    left.opacity === right.opacity &&
    left.roughness === right.roughness
  );
}

function sameShapeStyle(left: ShapeStyle, right: ShapeStyle): boolean {
  return sameStrokeStyle(left, right) && left.fill === right.fill;
}

function sameTextStyle(left: TextStyle, right: TextStyle): boolean {
  return (
    left.color === right.color &&
    left.fontSize === right.fontSize &&
    left.fontFamily === right.fontFamily &&
    left.opacity === right.opacity &&
    left.anchor === right.anchor
  );
}

function sameNode(left: SceneNode, right: SceneNode): boolean {
  if (
    left.id !== right.id ||
    left.kind !== right.kind ||
    !samePresentation(left.presentation, right.presentation)
  ) {
    return false;
  }

  switch (left.kind) {
    case "line":
      return (
        right.kind === "line" &&
        samePoints(left.points, right.points) &&
        sameStrokeStyle(left.style, right.style)
      );
    case "path":
      return (
        right.kind === "path" &&
        left.closed === right.closed &&
        samePoints(left.points, right.points) &&
        sameShapeStyle(left.style, right.style)
      );
    case "rect":
      return (
        right.kind === "rect" &&
        left.x === right.x &&
        left.y === right.y &&
        left.width === right.width &&
        left.height === right.height &&
        sameShapeStyle(left.style, right.style)
      );
    case "text":
      return (
        right.kind === "text" &&
        left.x === right.x &&
        left.y === right.y &&
        left.text === right.text &&
        sameTextStyle(left.style, right.style)
      );
    case "latex":
      return (
        right.kind === "latex" &&
        left.x === right.x &&
        left.y === right.y &&
        left.latex === right.latex &&
        left.style.color === right.style.color &&
        left.style.fontSize === right.style.fontSize &&
        left.style.opacity === right.style.opacity
      );
  }
}

function updateTransition(previous: SceneNode, next: SceneNode): "transform" | "crossfade" {
  if (previous.kind !== next.kind) return "crossfade";
  if (
    previous.kind === "path" &&
    next.kind === "path" &&
    (previous.closed !== next.closed || previous.points.length !== next.points.length)
  ) {
    return "crossfade";
  }
  if (previous.kind === "text" && next.kind === "text" && previous.text !== next.text) {
    return "crossfade";
  }
  if (previous.kind === "latex" && next.kind === "latex" && previous.latex !== next.latex) {
    return "crossfade";
  }
  return "transform";
}

/** Produce a deterministic visual transition between consecutive committed revisions. */
export function planSceneTransition(previous: SceneState, next: SceneState): MotionPlan {
  const from = createSceneState(previous);
  const to = createSceneState(next);
  if (to.revision !== from.revision + 1) {
    throw new RangeError(
      `Invalid live scene transition: revision ${to.revision} must follow ${from.revision}`
    );
  }

  const previousById = new Map(from.nodes.map((node) => [node.id, node]));
  const nextIds = new Set(to.nodes.map((node) => node.id));
  const steps: MotionStep[] = [];

  for (let index = from.nodes.length - 1; index >= 0; index -= 1) {
    const node = from.nodes[index];
    if (!nextIds.has(node.id)) {
      steps.push(
        Object.freeze({
          type: "remove",
          id: node.id,
          node,
          effect: node.presentation.exit,
        })
      );
    }
  }

  for (const node of to.nodes) {
    const previousNode = previousById.get(node.id);
    if (!previousNode) {
      steps.push(
        Object.freeze({
          type: "enter",
          id: node.id,
          node,
          effect: node.presentation.enter,
        })
      );
      continue;
    }
    if (!sameNode(previousNode, node)) {
      steps.push(
        Object.freeze({
          type: "update",
          id: node.id,
          previous: previousNode,
          next: node,
          transition: updateTransition(previousNode, node),
        })
      );
    }
  }

  return Object.freeze({
    fromRevision: from.revision,
    toRevision: to.revision,
    steps: Object.freeze(steps),
  });
}

/**
 * Reconstruct the semantic snapshot represented by a partially applied plan.
 *
 * Entered and completed steps are supplied by ID. Unapplied enters stay absent,
 * while unapplied updates and removals retain their previous node.
 */
export function materializeSceneTransition(
  previous: SceneState,
  next: SceneState,
  appliedStepIds: readonly string[]
): SceneState {
  const from = createSceneState(previous);
  const to = createSceneState(next);
  const plan = planSceneTransition(from, to);
  const plannedIds = new Set(plan.steps.map((step) => step.id));
  const applied = new Set<string>();

  for (const id of appliedStepIds) {
    if (!plannedIds.has(id)) {
      throw new RangeError(`Invalid live scene materialization: ${id} is not in the plan`);
    }
    applied.add(id);
  }

  const materialized = new Map(from.nodes.map((node) => [node.id, node]));
  for (const step of plan.steps) {
    if (!applied.has(step.id)) continue;
    if (step.type === "remove") materialized.delete(step.id);
    else if (step.type === "update") materialized.set(step.id, step.next);
    else materialized.set(step.id, step.node);
  }

  const orderedIds = [
    ...to.nodes.map((node) => node.id),
    ...from.nodes.map((node) => node.id).filter((id) => !to.nodes.some((node) => node.id === id)),
  ];
  return createSceneState({
    revision: to.revision,
    nodes: orderedIds.flatMap((id) => {
      const node = materialized.get(id);
      return node ? [node] : [];
    }),
  });
}
