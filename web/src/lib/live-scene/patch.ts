import { planSceneTransition } from "./planner";
import { createSceneState } from "./state";
import type {
  LatexSceneNode,
  LineSceneNode,
  MotionPlan,
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

export const LIVE_SCENE_PATCH_VERSION = 1 as const;
export const LIVE_SCENE_MAX_PATCH_OPERATIONS = 16;
export const LIVE_SCENE_MAX_ACCEPTED_PATCHES = 8;
export const LIVE_SCENE_MAX_NODES = 128;
export const LIVE_SCENE_BOARD_WIDTH = 800;
export const LIVE_SCENE_BOARD_HEIGHT = 600;
export const LIVE_SCENE_MAX_PATH_POINTS = 128;
export const LIVE_SCENE_MAX_TEXT_LENGTH = 512;
export const LIVE_SCENE_MAX_LATEX_LENGTH = 512;
export const LIVE_SCENE_MAX_NARRATION_LENGTH = 512;

const MAX_STROKE_WIDTH = 32;
const MAX_ROUGHNESS = 4;
const MIN_FONT_SIZE = 8;
const MAX_FONT_SIZE = 96;
const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const HEX_PAINT_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const THEME_PAINTS = new Set([
  "hsl(var(--amber))",
  "hsl(var(--chalk))",
  "hsl(var(--chalk-soft))",
  "hsl(var(--ember))",
  "hsl(var(--lavender))",
  "hsl(var(--sage))",
]);

export type LiveSceneProtocolErrorCode =
  | "invalid_json"
  | "invalid_event"
  | "invalid_patch"
  | "invalid_operation"
  | "invalid_node"
  | "revision_mismatch"
  | "budget_exceeded";

export class LiveSceneProtocolError extends TypeError {
  readonly code: LiveSceneProtocolErrorCode;

  constructor(code: LiveSceneProtocolErrorCode, message: string) {
    super(`Invalid live scene protocol: ${message}`);
    this.name = "LiveSceneProtocolError";
    this.code = code;
  }
}

export interface ScenePatchPutOperation {
  readonly op: "put";
  readonly node: SceneNode;
}

export interface ScenePatchRemoveOperation {
  readonly op: "remove";
  readonly id: string;
}

export type ScenePatchOperation =
  | ScenePatchPutOperation
  | ScenePatchRemoveOperation;

export interface ScenePatchDraft {
  readonly v: typeof LIVE_SCENE_PATCH_VERSION;
  readonly patchId: string;
  readonly narration: string;
  readonly operations: readonly ScenePatchOperation[];
}

export interface ScenePatchEvent {
  readonly type: "scene_patch";
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
}

export type LiveScenePutOperation = ScenePatchPutOperation;
export type LiveSceneRemoveOperation = ScenePatchRemoveOperation;
export type LiveScenePatchOperation = ScenePatchOperation;
export type LiveScenePatchDraft = ScenePatchDraft;
export type LiveScenePatchEvent = ScenePatchEvent;

export interface AppliedLiveScenePatch {
  readonly scene: SceneState;
  readonly plan: MotionPlan;
}

type UnknownRecord = Record<string, unknown>;

function fail(code: LiveSceneProtocolErrorCode, message: string): never {
  throw new LiveSceneProtocolError(code, message);
}

function record(value: unknown, field: string, code: LiveSceneProtocolErrorCode): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(code, `${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail(code, `${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[],
  field: string,
  code: LiveSceneProtocolErrorCode
): void {
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(code, `${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(code, `${field} is missing field ${key}`);
  }
}

function stringValue(
  value: unknown,
  field: string,
  maxLength: number,
  code: LiveSceneProtocolErrorCode,
  stripWhitespace = false
): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    fail(code, `${field} must be a non-empty string`);
  }
  const normalized = stripWhitespace ? value.trim() : value;
  if (normalized.length > maxLength) {
    fail("budget_exceeded", `${field} exceeds ${maxLength} characters`);
  }
  return normalized;
}

function contractId(value: unknown, field: string, code: LiveSceneProtocolErrorCode): string {
  const id = stringValue(value, field, 64, code);
  if (!CONTRACT_ID_PATTERN.test(id)) fail(code, `${field} has an unsafe identifier`);
  return id;
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  code: LiveSceneProtocolErrorCode,
  maximum = Number.MAX_SAFE_INTEGER
): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    fail(code, `${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function finiteNumber(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number
): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    fail("invalid_node", `${field} must be finite and between ${minimum} and ${maximum}`);
  }
  return value;
}

function positiveNumber(value: unknown, field: string, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > maximum) {
    fail("invalid_node", `${field} must be finite, greater than zero, and at most ${maximum}`);
  }
  return value;
}

function paint(value: unknown, field: string, allowEmptyFill = false): string {
  if (allowEmptyFill && (value === "none" || value === "transparent")) return value;
  if (typeof value !== "string" || (!HEX_PAINT_PATTERN.test(value) && !THEME_PAINTS.has(value))) {
    fail("invalid_node", `${field} must be a safe theme paint or six-digit hex color`);
  }
  return value;
}

function presentation(value: unknown, nodeId: string): ScenePresentation {
  const input = record(value, `node ${nodeId} presentation`, "invalid_node");
  exactKeys(input, ["enter", "exit"], [], `node ${nodeId} presentation`, "invalid_node");
  if (!["draw", "fade", "scale", "none"].includes(input.enter as string)) {
    fail("invalid_node", `node ${nodeId} has an invalid enter effect`);
  }
  if (!["fade", "none"].includes(input.exit as string)) {
    fail("invalid_node", `node ${nodeId} has an invalid exit effect`);
  }
  return {
    enter: input.enter as ScenePresentation["enter"],
    exit: input.exit as ScenePresentation["exit"],
  };
}

function strokeStyle(value: unknown, nodeId: string): StrokeStyle {
  const input = record(value, `node ${nodeId} style`, "invalid_node");
  exactKeys(
    input,
    ["stroke", "strokeWidth", "opacity", "roughness"],
    [],
    `node ${nodeId} style`,
    "invalid_node"
  );
  return {
    stroke: paint(input.stroke, `node ${nodeId} stroke`),
    strokeWidth: positiveNumber(input.strokeWidth, `node ${nodeId} strokeWidth`, MAX_STROKE_WIDTH),
    opacity: finiteNumber(input.opacity, `node ${nodeId} opacity`, 0, 1),
    roughness: finiteNumber(input.roughness, `node ${nodeId} roughness`, 0, MAX_ROUGHNESS),
  };
}

function shapeStyle(value: unknown, nodeId: string): ShapeStyle {
  const input = record(value, `node ${nodeId} style`, "invalid_node");
  exactKeys(
    input,
    ["stroke", "strokeWidth", "opacity", "roughness", "fill"],
    [],
    `node ${nodeId} style`,
    "invalid_node"
  );
  return {
    stroke: paint(input.stroke, `node ${nodeId} stroke`),
    strokeWidth: positiveNumber(input.strokeWidth, `node ${nodeId} strokeWidth`, MAX_STROKE_WIDTH),
    opacity: finiteNumber(input.opacity, `node ${nodeId} opacity`, 0, 1),
    roughness: finiteNumber(input.roughness, `node ${nodeId} roughness`, 0, MAX_ROUGHNESS),
    fill: paint(input.fill, `node ${nodeId} fill`, true),
  };
}

function textStyle(value: unknown, nodeId: string): TextStyle {
  const input = record(value, `node ${nodeId} style`, "invalid_node");
  exactKeys(
    input,
    ["color", "fontSize", "opacity", "anchor"],
    [],
    `node ${nodeId} style`,
    "invalid_node"
  );
  if (!["start", "middle", "end"].includes(input.anchor as string)) {
    fail("invalid_node", `node ${nodeId} has an invalid text anchor`);
  }
  return {
    color: paint(input.color, `node ${nodeId} color`),
    fontSize: finiteNumber(input.fontSize, `node ${nodeId} fontSize`, MIN_FONT_SIZE, MAX_FONT_SIZE),
    opacity: finiteNumber(input.opacity, `node ${nodeId} opacity`, 0, 1),
    anchor: input.anchor as TextStyle["anchor"],
  };
}

function latexStyle(value: unknown, nodeId: string): LatexSceneNode["style"] {
  const input = record(value, `node ${nodeId} style`, "invalid_node");
  exactKeys(
    input,
    ["color", "fontSize", "opacity"],
    [],
    `node ${nodeId} style`,
    "invalid_node"
  );
  return {
    color: paint(input.color, `node ${nodeId} color`),
    fontSize: finiteNumber(input.fontSize, `node ${nodeId} fontSize`, MIN_FONT_SIZE, MAX_FONT_SIZE),
    opacity: finiteNumber(input.opacity, `node ${nodeId} opacity`, 0, 1),
  };
}

function point(value: unknown, field: string): ScenePoint {
  if (!Array.isArray(value) || value.length !== 2) {
    fail("invalid_node", `${field} must contain exactly two coordinates`);
  }
  return [
    finiteNumber(value[0], `${field}[0]`, 0, LIVE_SCENE_BOARD_WIDTH),
    finiteNumber(value[1], `${field}[1]`, 0, LIVE_SCENE_BOARD_HEIGHT),
  ];
}

function points(value: unknown, field: string, minimum: number, maximum: number): readonly ScenePoint[] {
  if (!Array.isArray(value) || value.length < minimum) {
    fail("invalid_node", `${field} must contain at least ${minimum} points`);
  }
  if (value.length > maximum) fail("budget_exceeded", `${field} exceeds ${maximum} points`);
  return value.map((candidate, index) => point(candidate, `${field}[${index}]`));
}

function decodeNode(value: unknown): SceneNode {
  const input = record(value, "put node", "invalid_node");
  const id = contractId(input.id, "put node id", "invalid_node");
  const nodePresentation = presentation(input.presentation, id);

  let candidate: SceneNode;
  switch (input.kind) {
    case "line": {
      exactKeys(input, ["id", "kind", "presentation", "points", "style"], [], `node ${id}`, "invalid_node");
      const nodePoints = points(input.points, `node ${id} points`, 2, 2);
      candidate = {
        id,
        kind: "line",
        presentation: nodePresentation,
        points: nodePoints as LineSceneNode["points"],
        style: strokeStyle(input.style, id),
      };
      break;
    }
    case "path": {
      exactKeys(
        input,
        ["id", "kind", "presentation", "points", "closed", "style"],
        [],
        `node ${id}`,
        "invalid_node"
      );
      if (typeof input.closed !== "boolean") fail("invalid_node", `node ${id} closed must be boolean`);
      candidate = {
        id,
        kind: "path",
        presentation: nodePresentation,
        points: points(input.points, `node ${id} points`, 2, LIVE_SCENE_MAX_PATH_POINTS),
        closed: input.closed,
        style: shapeStyle(input.style, id),
      } as PathSceneNode;
      break;
    }
    case "rect": {
      exactKeys(
        input,
        ["id", "kind", "presentation", "x", "y", "width", "height", "style"],
        [],
        `node ${id}`,
        "invalid_node"
      );
      const x = finiteNumber(input.x, `node ${id} x`, 0, LIVE_SCENE_BOARD_WIDTH);
      const y = finiteNumber(input.y, `node ${id} y`, 0, LIVE_SCENE_BOARD_HEIGHT);
      const width = positiveNumber(input.width, `node ${id} width`, LIVE_SCENE_BOARD_WIDTH);
      const height = positiveNumber(input.height, `node ${id} height`, LIVE_SCENE_BOARD_HEIGHT);
      if (x + width > LIVE_SCENE_BOARD_WIDTH || y + height > LIVE_SCENE_BOARD_HEIGHT) {
        fail("invalid_node", `node ${id} rectangle must stay inside the logical board`);
      }
      candidate = {
        id,
        kind: "rect",
        presentation: nodePresentation,
        x,
        y,
        width,
        height,
        style: shapeStyle(input.style, id),
      } as RectSceneNode;
      break;
    }
    case "text": {
      exactKeys(
        input,
        ["id", "kind", "presentation", "x", "y", "text", "style"],
        [],
        `node ${id}`,
        "invalid_node"
      );
      candidate = {
        id,
        kind: "text",
        presentation: nodePresentation,
        x: finiteNumber(input.x, `node ${id} x`, 0, LIVE_SCENE_BOARD_WIDTH),
        y: finiteNumber(input.y, `node ${id} y`, 0, LIVE_SCENE_BOARD_HEIGHT),
        text: stringValue(
          input.text,
          `node ${id} text`,
          LIVE_SCENE_MAX_TEXT_LENGTH,
          "invalid_node",
          true
        ),
        style: textStyle(input.style, id),
      } as TextSceneNode;
      break;
    }
    case "latex": {
      exactKeys(
        input,
        ["id", "kind", "presentation", "x", "y", "latex", "style"],
        [],
        `node ${id}`,
        "invalid_node"
      );
      candidate = {
        id,
        kind: "latex",
        presentation: nodePresentation,
        x: finiteNumber(input.x, `node ${id} x`, 0, LIVE_SCENE_BOARD_WIDTH),
        y: finiteNumber(input.y, `node ${id} y`, 0, LIVE_SCENE_BOARD_HEIGHT),
        latex: stringValue(
          input.latex,
          `node ${id} latex`,
          LIVE_SCENE_MAX_LATEX_LENGTH,
          "invalid_node",
          true
        ),
        style: latexStyle(input.style, id),
      } as LatexSceneNode;
      break;
    }
    default:
      return fail("invalid_node", `node ${id} has an unsupported kind`);
  }

  return createSceneState({ revision: 0, nodes: [candidate] }).nodes[0];
}

function decodeOperation(value: unknown, index: number): ScenePatchOperation {
  const input = record(value, `patch operation ${index}`, "invalid_operation");
  if (input.op === "put") {
    exactKeys(input, ["op", "node"], [], `patch operation ${index}`, "invalid_operation");
    return Object.freeze({ op: "put", node: decodeNode(input.node) });
  }
  if (input.op === "remove") {
    exactKeys(input, ["op", "id"], [], `patch operation ${index}`, "invalid_operation");
    return Object.freeze({
      op: "remove",
      id: contractId(input.id, `patch operation ${index} id`, "invalid_operation"),
    });
  }
  return fail("invalid_operation", `patch operation ${index} has an unsupported op`);
}

function decodePatch(value: unknown): ScenePatchDraft {
  const input = record(value, "patch", "invalid_patch");
  exactKeys(input, ["v", "patchId", "narration", "operations"], [], "patch", "invalid_patch");
  if (input.v !== LIVE_SCENE_PATCH_VERSION) {
    fail("invalid_patch", `patch v must equal ${LIVE_SCENE_PATCH_VERSION}`);
  }
  if (!Array.isArray(input.operations) || input.operations.length === 0) {
    fail("invalid_patch", "patch operations must be a non-empty array");
  }
  if (input.operations.length > LIVE_SCENE_MAX_PATCH_OPERATIONS) {
    fail("budget_exceeded", `patch exceeds ${LIVE_SCENE_MAX_PATCH_OPERATIONS} operations`);
  }

  const operations = input.operations.map(decodeOperation);
  const targets = new Set<string>();
  for (const operation of operations) {
    const target = operation.op === "put" ? operation.node.id : operation.id;
    if (targets.has(target)) fail("invalid_patch", `patch targets ${target} more than once`);
    targets.add(target);
  }

  return Object.freeze({
    v: LIVE_SCENE_PATCH_VERSION,
    patchId: contractId(input.patchId, "patch patchId", "invalid_patch"),
    narration: stringValue(
      input.narration,
      "patch narration",
      LIVE_SCENE_MAX_NARRATION_LENGTH,
      "invalid_patch",
      true
    ),
    operations: Object.freeze(operations),
  });
}

/** Strictly decode one server-authoritative `scene_patch` event. */
export function decodeScenePatchEvent(inputValue: unknown): ScenePatchEvent {
  const input = record(inputValue, "scene_patch event", "invalid_event");
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "sequence",
      "baseRevision",
      "resultRevision",
      "patch",
    ],
    [],
    "scene_patch event",
    "invalid_event"
  );
  if (input.type !== "scene_patch") fail("invalid_event", "event type must equal scene_patch");

  const baseRevision = integer(input.baseRevision, "event baseRevision", 0, "invalid_event");
  const resultRevision = integer(input.resultRevision, "event resultRevision", 1, "invalid_event");
  if (resultRevision !== baseRevision + 1) {
    fail("revision_mismatch", "event resultRevision must equal baseRevision + 1");
  }

  return Object.freeze({
    type: "scene_patch",
    generation: integer(input.generation, "event generation", 1, "invalid_event"),
    attempt: integer(input.attempt, "event attempt", 1, "invalid_event", 2),
    sequence: integer(
      input.sequence,
      "event sequence",
      1,
      "invalid_event",
      LIVE_SCENE_MAX_ACCEPTED_PATCHES
    ),
    baseRevision,
    resultRevision,
    patch: decodePatch(input.patch),
  });
}

/** Parse one JSON SSE data payload and then apply exact protocol validation. */
export function parseLiveScenePatchEvent(data: string): ScenePatchEvent {
  let input: unknown;
  try {
    input = JSON.parse(data);
  } catch {
    return fail("invalid_json", "scene_patch data must be valid JSON");
  }
  return decodeScenePatchEvent(input);
}

/**
 * Apply one validated patch to a temporary snapshot and plan its transition.
 * The caller's accepted scene is never mutated, including on failure.
 */
export function applyLiveScenePatch(
  current: SceneState,
  eventValue: ScenePatchEvent
): AppliedLiveScenePatch {
  const accepted = createSceneState(current);
  if (accepted.nodes.length > LIVE_SCENE_MAX_NODES) {
    return fail("budget_exceeded", `accepted scene exceeds ${LIVE_SCENE_MAX_NODES} nodes`);
  }
  const event = decodeScenePatchEvent(eventValue);
  if (event.baseRevision !== accepted.revision) {
    return fail(
      "revision_mismatch",
      `event baseRevision ${event.baseRevision} does not match accepted revision ${accepted.revision}`
    );
  }

  const nodes = [...accepted.nodes];
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const removed = new Set<string>();

  for (const operation of event.patch.operations) {
    if (operation.op === "remove") {
      const index = indexById.get(operation.id);
      if (index === undefined || removed.has(operation.id)) {
        return fail("invalid_patch", `patch cannot remove absent node ${operation.id}`);
      }
      removed.add(operation.id);
      continue;
    }

    const index = indexById.get(operation.node.id);
    if (index === undefined) {
      indexById.set(operation.node.id, nodes.length);
      nodes.push(operation.node);
    } else {
      nodes[index] = operation.node;
    }
  }

  const candidateNodes = nodes.filter((node) => !removed.has(node.id));
  if (candidateNodes.length > LIVE_SCENE_MAX_NODES) {
    return fail("budget_exceeded", `scene exceeds ${LIVE_SCENE_MAX_NODES} nodes`);
  }

  const scene = createSceneState({
    revision: event.resultRevision,
    nodes: candidateNodes,
  });
  const plan = planSceneTransition(accepted, scene);
  if (plan.steps.length === 0) fail("invalid_patch", "patch must change the accepted scene");

  return Object.freeze({ scene, plan });
}

/** Apply one server patch and return only its new immutable semantic snapshot. */
export function applyScenePatch(current: SceneState, event: ScenePatchEvent): SceneState {
  return applyLiveScenePatch(current, event).scene;
}

export const decodeLiveScenePatchEvent = decodeScenePatchEvent;
