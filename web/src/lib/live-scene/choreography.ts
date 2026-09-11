import { LiveSceneProtocolError } from "./patch";

export const ROUTED_CHOREOGRAPHY_BEAT_VERSION = 2 as const;
export const CHOREOGRAPHY_PLAN_VERSION = 1 as const;
export const CHOREOGRAPHY_PLAN_V2_VERSION = 2 as const;
export const PRESENTATION_CHECKPOINT_VERSION = 1 as const;
export const VIEWPORT_POSE_VERSION = 1 as const;

export const MAX_CHOREOGRAPHY_CUES = 5;
export const MAX_CHOREOGRAPHY_V2_CUES = 6;
export const MAX_CHOREOGRAPHY_TARGETS_PER_CUE = 16;
export const MAX_CHOREOGRAPHY_TARGET_REFERENCES = 32;
export const MAX_CHOREOGRAPHY_V2_NODE_REFERENCES = 32;
export const MIN_CHOREOGRAPHY_PHASE_MS = 100;
export const MAX_CHOREOGRAPHY_PHASE_MS = 6_000;
export const MAX_CHOREOGRAPHY_HOLD_AFTER_MS = 9_000;
export const MAX_CHOREOGRAPHY_PLAN_MS = 12_000;

const LIVE_SCENE_BOARD_WIDTH = 800;
const LIVE_SCENE_BOARD_HEIGHT = 600;
const MAX_CHOREOGRAPHY_ID_CHARS = 64;
const MAX_CHOREOGRAPHY_COMPONENT_ID_CHARS = 32;
const MAX_CHECKPOINT_NARRATION_CHARS = 512;
const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const COMPONENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

export const COMPLETING_SQUARE_STAGES = [
  "setup",
  "split",
  "complete",
  "solve",
] as const;
export type CompletingSquareStage = (typeof COMPLETING_SQUARE_STAGES)[number];

export interface AdvanceChoreographyRouteV2 {
  readonly intent: "advance";
  readonly targetStage: CompletingSquareStage;
}

export interface ClarifyCornerRouteV2 {
  readonly intent: "clarify_corner";
}

export type RoutedChoreographyRouteV2 =
  AdvanceChoreographyRouteV2 | ClarifyCornerRouteV2;

export interface RoutedChoreographyBeatV2 {
  readonly v: typeof ROUTED_CHOREOGRAPHY_BEAT_VERSION;
  readonly beatId: string;
  readonly componentKind: "completing_square";
  readonly componentId: string;
  readonly route: RoutedChoreographyRouteV2;
}

export const CHOREOGRAPHY_CUE_ORDER = [
  "enter",
  "exit",
  "transform",
  "emphasize",
  "focus",
] as const;
export const CHOREOGRAPHY_CUE_V2_ORDER = [
  "enter",
  "exit",
  "transform",
  "trace_path",
  "emphasize",
  "focus",
] as const;
export type ChoreographyCueKind =
  (typeof CHOREOGRAPHY_CUE_ORDER)[number];
export type ChoreographyCueKindV1 = ChoreographyCueKind;
export type ChoreographyCueKindV2 =
  (typeof CHOREOGRAPHY_CUE_V2_ORDER)[number];

interface TargetCueV1<Cue extends ChoreographyCueKindV1> {
  readonly cue: Cue;
  readonly targetIds: readonly string[];
}

export type EnterCueV1 = TargetCueV1<"enter">;
export type ExitCueV1 = TargetCueV1<"exit">;
export type TransformCueV1 = TargetCueV1<"transform">;
export type EmphasizeCueV1 = TargetCueV1<"emphasize">;
export type FocusCueV1 = TargetCueV1<"focus">;

export type ChoreographyCueV1 =
  EnterCueV1 | ExitCueV1 | TransformCueV1 | EmphasizeCueV1 | FocusCueV1;

export interface TracePathCueV2 {
  readonly cue: "trace_path";
  readonly pathId: string;
  readonly markerId: string;
  /** Discriminator-only convenience; this field is forbidden on the wire. */
  readonly targetIds?: never;
}

export type ChoreographyCueV2 = ChoreographyCueV1 | TracePathCueV2;

export const CHOREOGRAPHY_EASINGS = [
  "linear",
  "ease_in",
  "ease_out_quart",
  "ease_out_quint",
  "ease_in_out",
] as const;
export type ChoreographyEasing = (typeof CHOREOGRAPHY_EASINGS)[number];

export interface ChoreographyPhaseV1 {
  readonly cues: readonly ChoreographyCueV1[];
  readonly durationMs: number;
  readonly easing: ChoreographyEasing;
  readonly holdAfterMs: number;
}

export interface ChoreographyPlanV1 {
  readonly v: typeof CHOREOGRAPHY_PLAN_VERSION;
  readonly phase: ChoreographyPhaseV1;
}

export interface ChoreographyPhaseV2 {
  readonly cues: readonly ChoreographyCueV2[];
  readonly durationMs: number;
  readonly easing: ChoreographyEasing;
  readonly holdAfterMs: number;
}

export interface ChoreographyPlanV2 {
  readonly v: typeof CHOREOGRAPHY_PLAN_V2_VERSION;
  readonly phase: ChoreographyPhaseV2;
}

export type ChoreographyPlan = ChoreographyPlanV1 | ChoreographyPlanV2;

export interface ViewportPoseV1 {
  readonly v: typeof VIEWPORT_POSE_VERSION;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface LayoutViewportMapV1 {
  readonly cinematic: ViewportPoseV1;
  readonly compact: ViewportPoseV1;
}

export interface PresentationCheckpointV1 {
  readonly v: typeof PRESENTATION_CHECKPOINT_VERSION;
  readonly checkpointId: string;
  readonly checkpointNarration: string;
  readonly baseViewports: LayoutViewportMapV1;
  readonly resultViewports: LayoutViewportMapV1;
  readonly transientFree: true;
}

type UnknownRecord = Record<string, unknown>;

function fail(
  message: string,
  code: "invalid_event" | "budget_exceeded" = "invalid_event",
): never {
  throw new LiveSceneProtocolError(code, `choreography ${message}`);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(`${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail(`${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  field: string,
): void {
  const allowed = new Set(required);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`${field} is missing field ${key}`);
  }
}

function literal<T extends string | number | boolean>(
  value: unknown,
  expected: T,
  field: string,
): T {
  if (value !== expected) fail(`${field} must equal ${String(expected)}`);
  return expected;
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  field: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    fail(`${field} has an unsupported value`);
  }
  return value as T;
}

function boundedString(
  value: unknown,
  field: string,
  maximum: number,
  trim = false,
): string {
  if (typeof value !== "string") fail(`${field} must be a string`);
  const normalized = trim ? value.trim() : value;
  if (normalized.length === 0) fail(`${field} must be non-empty`);
  if ([...normalized].length > maximum) {
    fail(`${field} exceeds ${maximum} characters`, "budget_exceeded");
  }
  return normalized;
}

function identifier(
  value: unknown,
  field: string,
  maximum: number,
  pattern: RegExp,
): string {
  const id = boundedString(value, field, maximum);
  if (!pattern.test(id)) fail(`${field} has an unsafe identifier`);
  return id;
}

function contractId(value: unknown, field: string): string {
  return identifier(
    value,
    field,
    MAX_CHOREOGRAPHY_ID_CHARS,
    CONTRACT_ID_PATTERN,
  );
}

function componentId(value: unknown, field: string): string {
  return identifier(
    value,
    field,
    MAX_CHOREOGRAPHY_COMPONENT_ID_CHARS,
    COMPONENT_ID_PATTERN,
  );
}

function safeInteger(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    fail(`${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  if ((value as number) > maximum) {
    fail(`${field} exceeds ${maximum}`, "budget_exceeded");
  }
  return value as number;
}

function finiteNumber(value: unknown, field: string, positive = false): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (positive ? value <= 0 : value < 0)
  ) {
    fail(
      `${field} must be a finite ${positive ? "positive" : "non-negative"} number`,
    );
  }
  return value;
}

function decodeRoute(value: unknown): RoutedChoreographyRouteV2 {
  const input = record(value, "routed beat route");
  if (input.intent === "advance") {
    exactKeys(input, ["intent", "targetStage"], "routed beat route");
    return Object.freeze({
      intent: "advance",
      targetStage: oneOf(
        input.targetStage,
        COMPLETING_SQUARE_STAGES,
        "routed beat route targetStage",
      ),
    });
  }
  if (input.intent === "clarify_corner") {
    exactKeys(input, ["intent"], "routed beat route");
    return Object.freeze({ intent: "clarify_corner" });
  }
  return fail("routed beat route intent has an unsupported value");
}

/** Decode the routed-only V2 input without accepting compiler-owned concerns. */
export function decodeRoutedChoreographyBeatV2(
  value: unknown,
): RoutedChoreographyBeatV2 {
  const input = record(value, "routed beat");
  exactKeys(
    input,
    ["v", "beatId", "componentKind", "componentId", "route"],
    "routed beat",
  );
  return Object.freeze({
    v: literal(input.v, ROUTED_CHOREOGRAPHY_BEAT_VERSION, "routed beat v"),
    beatId: contractId(input.beatId, "routed beat beatId"),
    componentKind: literal(
      input.componentKind,
      "completing_square",
      "routed beat componentKind",
    ),
    componentId: componentId(input.componentId, "routed beat componentId"),
    route: decodeRoute(input.route),
  });
}

/** Decode one closed cue and preserve its canonical target order. */
export function decodeChoreographyCueV1(value: unknown): ChoreographyCueV1 {
  const input = record(value, "choreography cue");
  exactKeys(input, ["cue", "targetIds"], "choreography cue");
  const cue = oneOf(input.cue, CHOREOGRAPHY_CUE_ORDER, "choreography cue kind");
  if (!Array.isArray(input.targetIds)) {
    fail("choreography cue targetIds must be an array");
  }
  if (input.targetIds.length === 0) {
    fail("choreography cue targetIds must be non-empty");
  }
  if (input.targetIds.length > MAX_CHOREOGRAPHY_TARGETS_PER_CUE) {
    fail(
      `choreography cue targetIds exceeds ${MAX_CHOREOGRAPHY_TARGETS_PER_CUE}`,
      "budget_exceeded",
    );
  }
  const targetIds = input.targetIds.map((target, index) =>
    contractId(target, `choreography cue targetIds[${index}]`),
  );
  if (new Set(targetIds).size !== targetIds.length) {
    fail("choreography cue targetIds must be unique");
  }
  if (
    targetIds.some((target, index) => target !== [...targetIds].sort()[index])
  ) {
    fail("choreography cue targetIds must use canonical lexical order");
  }
  return Object.freeze({
    cue,
    targetIds: Object.freeze(targetIds),
  }) as ChoreographyCueV1;
}

/** Decode the additive V2 cue union without widening the sealed V1 decoder. */
export function decodeChoreographyCueV2(value: unknown): ChoreographyCueV2 {
  const input = record(value, "choreography cue");
  if (input.cue !== "trace_path") return decodeChoreographyCueV1(value);
  exactKeys(input, ["cue", "pathId", "markerId"], "choreography cue");
  const pathId = contractId(input.pathId, "choreography cue pathId");
  const markerId = contractId(input.markerId, "choreography cue markerId");
  if (pathId === markerId) {
    fail("choreography trace pathId and markerId must be distinct");
  }
  return Object.freeze({ cue: "trace_path", pathId, markerId });
}

function decodePhase(value: unknown): ChoreographyPhaseV1 {
  const input = record(value, "choreography phase");
  exactKeys(
    input,
    ["cues", "durationMs", "easing", "holdAfterMs"],
    "choreography phase",
  );
  if (!Array.isArray(input.cues))
    fail("choreography phase cues must be an array");
  if (input.cues.length === 0)
    fail("choreography phase cues must be non-empty");
  if (input.cues.length > MAX_CHOREOGRAPHY_CUES) {
    fail(
      `choreography phase exceeds ${MAX_CHOREOGRAPHY_CUES} cues`,
      "budget_exceeded",
    );
  }
  const cues = input.cues.map(decodeChoreographyCueV1);
  const cueKinds = cues.map((cue) => cue.cue);
  if (new Set(cueKinds).size !== cueKinds.length) {
    fail("parallel choreography cue kinds must be unique");
  }
  const canonicalCueKinds = [...cueKinds].sort(
    (left, right) =>
      CHOREOGRAPHY_CUE_ORDER.indexOf(left) -
      CHOREOGRAPHY_CUE_ORDER.indexOf(right),
  );
  if (cueKinds.some((cue, index) => cue !== canonicalCueKinds[index])) {
    fail("parallel choreography cues must use canonical cue order");
  }
  const targetReferences = cues.reduce(
    (count, cue) => count + cue.targetIds.length,
    0,
  );
  if (targetReferences > MAX_CHOREOGRAPHY_TARGET_REFERENCES) {
    fail(
      "parallel choreography phase exceeds the target-reference budget",
      "budget_exceeded",
    );
  }
  return Object.freeze({
    cues: Object.freeze(cues),
    durationMs: safeInteger(
      input.durationMs,
      "choreography phase durationMs",
      MIN_CHOREOGRAPHY_PHASE_MS,
      MAX_CHOREOGRAPHY_PHASE_MS,
    ),
    easing: oneOf(
      input.easing,
      CHOREOGRAPHY_EASINGS,
      "choreography phase easing",
    ),
    holdAfterMs: safeInteger(
      input.holdAfterMs,
      "choreography phase holdAfterMs",
      0,
      MAX_CHOREOGRAPHY_HOLD_AFTER_MS,
    ),
  });
}

function decodePhaseV2(value: unknown): ChoreographyPhaseV2 {
  const input = record(value, "choreography phase");
  exactKeys(
    input,
    ["cues", "durationMs", "easing", "holdAfterMs"],
    "choreography phase",
  );
  if (!Array.isArray(input.cues)) {
    fail("choreography phase cues must be an array");
  }
  if (input.cues.length === 0) {
    fail("choreography phase cues must be non-empty");
  }
  if (input.cues.length > MAX_CHOREOGRAPHY_V2_CUES) {
    fail(
      `choreography phase exceeds ${MAX_CHOREOGRAPHY_V2_CUES} cues`,
      "budget_exceeded",
    );
  }
  const cues = input.cues.map(decodeChoreographyCueV2);
  const cueKinds = cues.map((cue) => cue.cue);
  if (new Set(cueKinds).size !== cueKinds.length) {
    fail("parallel choreography cue kinds must be unique");
  }
  const canonicalCueKinds = [...cueKinds].sort(
    (left, right) =>
      CHOREOGRAPHY_CUE_V2_ORDER.indexOf(left) -
      CHOREOGRAPHY_CUE_V2_ORDER.indexOf(right),
  );
  if (cueKinds.some((cue, index) => cue !== canonicalCueKinds[index])) {
    fail("parallel choreography cues must use canonical cue order");
  }
  const trace = cues.find(
    (cue): cue is TracePathCueV2 => cue.cue === "trace_path",
  );
  const transformTargets =
    cues.find((cue): cue is TransformCueV1 => cue.cue === "transform")
      ?.targetIds ?? [];
  if (
    trace &&
    transformTargets.some(
      (target) => target === trace.pathId || target === trace.markerId,
    )
  ) {
    fail("trace-owned nodes must be disjoint from transform targetIds");
  }
  const nodeReferences = cues.reduce(
    (count, cue) =>
      count + (cue.cue === "trace_path" ? 2 : cue.targetIds.length),
    0,
  );
  if (nodeReferences > MAX_CHOREOGRAPHY_V2_NODE_REFERENCES) {
    fail(
      "parallel choreography phase exceeds the node-reference budget",
      "budget_exceeded",
    );
  }
  return Object.freeze({
    cues: Object.freeze(cues),
    durationMs: safeInteger(
      input.durationMs,
      "choreography phase durationMs",
      MIN_CHOREOGRAPHY_PHASE_MS,
      MAX_CHOREOGRAPHY_PHASE_MS,
    ),
    easing: oneOf(
      input.easing,
      CHOREOGRAPHY_EASINGS,
      "choreography phase easing",
    ),
    holdAfterMs: safeInteger(
      input.holdAfterMs,
      "choreography phase holdAfterMs",
      0,
      MAX_CHOREOGRAPHY_HOLD_AFTER_MS,
    ),
  });
}

/** Decode the one-phase, server-authored choreography plan. */
export function decodeChoreographyPlanV1(value: unknown): ChoreographyPlanV1 {
  const input = record(value, "choreography plan");
  exactKeys(input, ["v", "phase"], "choreography plan");
  const phase = decodePhase(input.phase);
  if (phase.durationMs + phase.holdAfterMs > MAX_CHOREOGRAPHY_PLAN_MS) {
    fail(
      "choreography plan exceeds the total duration budget",
      "budget_exceeded",
    );
  }
  return Object.freeze({
    v: literal(input.v, CHOREOGRAPHY_PLAN_VERSION, "choreography plan v"),
    phase,
  });
}

/** Decode an additive V2 plan while retaining V1 as a separate strict shape. */
export function decodeChoreographyPlanV2(value: unknown): ChoreographyPlanV2 {
  const input = record(value, "choreography plan");
  exactKeys(input, ["v", "phase"], "choreography plan");
  const phase = decodePhaseV2(input.phase);
  if (phase.durationMs + phase.holdAfterMs > MAX_CHOREOGRAPHY_PLAN_MS) {
    fail(
      "choreography plan exceeds the total duration budget",
      "budget_exceeded",
    );
  }
  return Object.freeze({
    v: literal(
      input.v,
      CHOREOGRAPHY_PLAN_V2_VERSION,
      "choreography plan v",
    ),
    phase,
  });
}

/** Dispatch only by an exact plan version; individual decoders remain sealed. */
export function decodeChoreographyPlan(value: unknown): ChoreographyPlan {
  const input = record(value, "choreography plan");
  if (input.v === CHOREOGRAPHY_PLAN_VERSION) {
    return decodeChoreographyPlanV1(value);
  }
  if (input.v === CHOREOGRAPHY_PLAN_V2_VERSION) {
    return decodeChoreographyPlanV2(value);
  }
  return fail("choreography plan v has an unsupported value");
}

/** Decode one exact camera pose inside the canonical board. */
export function decodeViewportPoseV1(value: unknown): ViewportPoseV1 {
  const input = record(value, "viewport pose");
  exactKeys(input, ["v", "x", "y", "width", "height"], "viewport pose");
  const x = finiteNumber(input.x, "viewport pose x");
  const y = finiteNumber(input.y, "viewport pose y");
  const width = finiteNumber(input.width, "viewport pose width", true);
  const height = finiteNumber(input.height, "viewport pose height", true);
  if (x + width > LIVE_SCENE_BOARD_WIDTH) {
    fail("viewport pose must stay inside the board width");
  }
  if (y + height > LIVE_SCENE_BOARD_HEIGHT) {
    fail("viewport pose must stay inside the board height");
  }
  return Object.freeze({
    v: literal(input.v, VIEWPORT_POSE_VERSION, "viewport pose v"),
    x,
    y,
    width,
    height,
  });
}

function decodeLayoutViewportMap(
  value: unknown,
  field: string,
): LayoutViewportMapV1 {
  const input = record(value, field);
  exactKeys(input, ["cinematic", "compact"], field);
  return Object.freeze({
    cinematic: decodeViewportPoseV1(input.cinematic),
    compact: decodeViewportPoseV1(input.compact),
  });
}

/** Decode a settled presentation checkpoint and its two certified layout maps. */
export function decodePresentationCheckpointV1(
  value: unknown,
): PresentationCheckpointV1 {
  const input = record(value, "presentation checkpoint");
  exactKeys(
    input,
    [
      "v",
      "checkpointId",
      "checkpointNarration",
      "baseViewports",
      "resultViewports",
      "transientFree",
    ],
    "presentation checkpoint",
  );
  return Object.freeze({
    v: literal(
      input.v,
      PRESENTATION_CHECKPOINT_VERSION,
      "presentation checkpoint v",
    ),
    checkpointId: contractId(
      input.checkpointId,
      "presentation checkpoint checkpointId",
    ),
    checkpointNarration: boundedString(
      input.checkpointNarration,
      "presentation checkpoint checkpointNarration",
      MAX_CHECKPOINT_NARRATION_CHARS,
      true,
    ),
    baseViewports: decodeLayoutViewportMap(
      input.baseViewports,
      "presentation checkpoint baseViewports",
    ),
    resultViewports: decodeLayoutViewportMap(
      input.resultViewports,
      "presentation checkpoint resultViewports",
    ),
    transientFree: literal(
      input.transientFree,
      true,
      "presentation checkpoint transientFree",
    ),
  });
}
