import {
  COMPLETING_SQUARE_STAGES,
  type RoutedChoreographyRouteV2,
} from "./choreography";
import { LiveSceneProtocolError } from "./patch";
import {
  decodeCompletingSquareProblemSpecV1,
  type CompletingSquareProblemSpecV1,
} from "./parametric-problem";

export const PARAMETRIC_CHOREOGRAPHY_PROTOCOL =
  "parametric_choreography_v3" as const;
export const ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION = 3 as const;

export const PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS = [
  "problem",
  "area_model",
  "split_linear_term",
  "rearrange_halves",
  "missing_corner",
  "balance_and_complete",
  "factor_square",
  "solve_roots",
] as const;

export type ParametricCompletingSquareMainCheckpoint =
  (typeof PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS)[number];

export interface RoutedChoreographyBeatV3 {
  readonly v: typeof ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION;
  readonly beatId: string;
  readonly componentKind: "completing_square_parametric";
  readonly componentId: string;
  readonly problemSpec: CompletingSquareProblemSpecV1;
  readonly route: RoutedChoreographyRouteV2;
}

export interface ParametricCompletingSquareStateV1 {
  readonly kind: "completing_square_parametric";
  readonly id: string;
  readonly problemSpec: CompletingSquareProblemSpecV1;
  readonly lastMainCheckpoint: ParametricCompletingSquareMainCheckpoint | null;
  readonly cornerClarified: boolean;
}

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `parametric choreography ${message}`,
  );
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

function identifier(
  value: unknown,
  field: string,
  maximum: number,
): string {
  const pattern = new RegExp(`^[A-Za-z][A-Za-z0-9_-]{0,${maximum - 1}}$`);
  if (typeof value !== "string" || !pattern.test(value)) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

export function decodeParametricChoreographyRoute(
  value: unknown,
): RoutedChoreographyRouteV2 {
  const input = record(value, "route");
  if (input.intent === "advance") {
    exactKeys(input, ["intent", "targetStage"], "route");
    if (
      typeof input.targetStage !== "string" ||
      !COMPLETING_SQUARE_STAGES.includes(
        input.targetStage as (typeof COMPLETING_SQUARE_STAGES)[number],
      )
    ) {
      fail("route targetStage has an unsupported value");
    }
    return Object.freeze({
      intent: "advance",
      targetStage: input.targetStage as (typeof COMPLETING_SQUARE_STAGES)[number],
    });
  }
  if (input.intent === "clarify_corner") {
    exactKeys(input, ["intent"], "route");
    return Object.freeze({ intent: "clarify_corner" });
  }
  return fail("route intent has an unsupported value");
}

/** Decode the problem-bound V3 beat without admitting V2 compiler claims. */
export function decodeRoutedChoreographyBeatV3(
  value: unknown,
): RoutedChoreographyBeatV3 {
  const input = record(value, "routed beat");
  exactKeys(
    input,
    [
      "v",
      "beatId",
      "componentKind",
      "componentId",
      "problemSpec",
      "route",
    ],
    "routed beat",
  );
  if (input.v !== ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION) {
    fail(`routed beat v must equal ${ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION}`);
  }
  if (input.componentKind !== "completing_square_parametric") {
    fail("routed beat componentKind must equal completing_square_parametric");
  }
  return Object.freeze({
    v: ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION,
    beatId: identifier(input.beatId, "routed beat beatId", 64),
    componentKind: "completing_square_parametric",
    componentId: identifier(input.componentId, "routed beat componentId", 32),
    problemSpec: decodeCompletingSquareProblemSpecV1(input.problemSpec),
    route: decodeParametricChoreographyRoute(input.route),
  });
}

/** Decode one exact V3 semantic frontier component. */
export function decodeParametricCompletingSquareStateV1(
  value: unknown,
): ParametricCompletingSquareStateV1 {
  const input = record(value, "result component");
  exactKeys(
    input,
    ["kind", "id", "problemSpec", "lastMainCheckpoint", "cornerClarified"],
    "result component",
  );
  if (input.kind !== "completing_square_parametric") {
    fail("result component kind must equal completing_square_parametric");
  }
  const lastMainCheckpoint =
    input.lastMainCheckpoint === null
      ? null
      : PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.find(
          (checkpoint) => checkpoint === input.lastMainCheckpoint,
        );
  if (lastMainCheckpoint === undefined) {
    fail("result component lastMainCheckpoint has an unsupported value");
  }
  if (typeof input.cornerClarified !== "boolean") {
    fail("result component cornerClarified must be a boolean");
  }
  if (
    input.cornerClarified &&
    (lastMainCheckpoint === null ||
      PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
        lastMainCheckpoint,
      ) <
        PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf("missing_corner"))
  ) {
    fail("result component cornerClarified requires the missing_corner frontier");
  }
  return Object.freeze({
    kind: "completing_square_parametric",
    id: identifier(input.id, "result component id", 32),
    problemSpec: decodeCompletingSquareProblemSpecV1(input.problemSpec),
    lastMainCheckpoint,
    cornerClarified: input.cornerClarified,
  });
}
