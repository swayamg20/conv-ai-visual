import {
  PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
  PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  decodeParametricChoreographyRoute,
  type ParametricCompletingSquareMainCheckpoint,
} from "@/lib/live-scene/parametric-choreography";
import {
  decodeParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneCheckpointEventV3,
  type ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";
import { CHECKPOINT_COMPILER_V3_VERSION } from "@/lib/live-scene/parametric-checkpoint";
import {
  decodeCompletingSquareProblemSpecV1,
  type CompletingSquareProblemSpecV1,
} from "@/lib/live-scene/parametric-problem";

export type ParametricChoreographyFixtureErrorCode =
  | "invalid_fixture"
  | "request_mismatch";

export class ParametricChoreographyFixtureError extends Error {
  readonly code: ParametricChoreographyFixtureErrorCode;

  constructor(code: ParametricChoreographyFixtureErrorCode, message: string) {
    super(message);
    this.name = "ParametricChoreographyFixtureError";
    this.code = code;
  }
}

export interface ParametricChoreographyFixtureLaneBase {
  readonly revision: number;
  readonly checkpointId: ParametricCompletingSquareMainCheckpoint | null;
  readonly cornerClarified: boolean;
  readonly certificateHeadSha256: string | null;
}

export interface DecodedParametricChoreographyFixtureLane {
  readonly generation: number;
  readonly base: ParametricChoreographyFixtureLaneBase;
  readonly route: ReturnType<typeof decodeParametricChoreographyRoute>;
  readonly checkpointIds: readonly (
    | ParametricCompletingSquareMainCheckpoint
    | "corner_detail"
  )[];
  readonly events: readonly ParametricChoreographySceneStreamEventV3[];
  readonly checkpoints: readonly ParametricChoreographySceneCheckpointEventV3[];
}

export interface DecodedParametricChoreographyFixtureEnvelope {
  readonly fixtureId: string;
  readonly problemText: string;
  readonly problemSpec: CompletingSquareProblemSpecV1;
  readonly main: DecodedParametricChoreographyFixtureLane;
  readonly clarifyCorner?: DecodedParametricChoreographyFixtureLane;
  readonly continueAfterClarification?: DecodedParametricChoreographyFixtureLane;
}

type UnknownRecord = Record<string, unknown>;

const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const FIXTURE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function failParametricChoreographyFixture(
  code: ParametricChoreographyFixtureErrorCode,
  message: string,
): never {
  throw new ParametricChoreographyFixtureError(code, message);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be an object`,
    );
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be a plain object`,
    );
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  field: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...required].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must contain exactly ${expected.join(", ")}`,
    );
  }
}

function safeInteger(value: unknown, field: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be a safe integer at least ${minimum}`,
    );
  }
  return value as number;
}

function boundedText(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value.trim()].length > 2_000
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be a non-empty string of at most 2000 characters`,
    );
  }
  return value.trim();
}

function fixtureId(value: unknown): string {
  if (typeof value !== "string" || !FIXTURE_ID_PATTERN.test(value)) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      "fixtureId must be an SSE-safe identifier",
    );
  }
  return value;
}

function digest(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be null or a lowercase SHA-256 digest`,
    );
  }
  return value;
}

export function sameParametricChoreographyFixtureValue(
  left: unknown,
  right: unknown,
): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) =>
        sameParametricChoreographyFixtureValue(value, right[index]),
      )
    );
  }
  if (
    typeof left !== "object" ||
    left === null ||
    typeof right !== "object" ||
    right === null
  ) {
    return false;
  }
  const leftRecord = left as UnknownRecord;
  const rightRecord = right as UnknownRecord;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        sameParametricChoreographyFixtureValue(
          leftRecord[key],
          rightRecord[key],
        ),
    )
  );
}

function decodeCheckpointIds(
  value: unknown,
  field: string,
): DecodedParametricChoreographyFixtureLane["checkpointIds"] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 8) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must contain between one and eight checkpoints`,
    );
  }
  return Object.freeze(
    value.map((candidate, index) => {
      if (
        candidate !== "corner_detail" &&
        !PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.some(
          (checkpoint) => checkpoint === candidate,
        )
      ) {
        return failParametricChoreographyFixture(
          "invalid_fixture",
          `${field}[${index}] is not a completing-square checkpoint`,
        );
      }
      return candidate as
        | ParametricCompletingSquareMainCheckpoint
        | "corner_detail";
    }),
  );
}

function decodeLaneBase(
  value: unknown,
  field: string,
): ParametricChoreographyFixtureLaneBase {
  const input = record(value, field);
  exactKeys(
    input,
    [
      "revision",
      "checkpointId",
      "cornerClarified",
      "certificateHeadSha256",
    ],
    field,
  );
  const checkpointId =
    input.checkpointId === null
      ? null
      : PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.find(
          (checkpoint) => checkpoint === input.checkpointId,
        );
  if (checkpointId === undefined) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} checkpointId must be null or a main checkpoint`,
    );
  }
  if (typeof input.cornerClarified !== "boolean") {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} cornerClarified must be a boolean`,
    );
  }
  return Object.freeze({
    revision: safeInteger(input.revision, `${field} revision`),
    checkpointId,
    cornerClarified: input.cornerClarified,
    certificateHeadSha256: digest(
      input.certificateHeadSha256,
      `${field} certificateHeadSha256`,
    ),
  });
}

function decodeLane(
  value: unknown,
  field: string,
): DecodedParametricChoreographyFixtureLane {
  const input = record(value, field);
  exactKeys(
    input,
    [
      "generation",
      "base",
      "route",
      "checkpointIds",
      "checkpointCount",
      "events",
    ],
    field,
  );
  const generation = safeInteger(input.generation, `${field} generation`, 1);
  const base = decodeLaneBase(input.base, `${field} base`);
  let route: ReturnType<typeof decodeParametricChoreographyRoute>;
  try {
    route = decodeParametricChoreographyRoute(input.route);
  } catch (error) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} route failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  const checkpointIds = decodeCheckpointIds(
    input.checkpointIds,
    `${field} checkpointIds`,
  );
  if (
    safeInteger(input.checkpointCount, `${field} checkpointCount`, 1) !==
    checkpointIds.length
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} checkpointCount does not match checkpointIds`,
    );
  }
  if (!Array.isArray(input.events)) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} events must be an array`,
    );
  }
  let events: readonly ParametricChoreographySceneStreamEventV3[];
  try {
    events = Object.freeze(
      input.events.map(decodeParametricChoreographySceneStreamEventV3),
    );
  } catch (error) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} events failed strict V3 decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  const checkpoints = Object.freeze(
    events.filter(
      (
        event,
      ): event is ParametricChoreographySceneCheckpointEventV3 =>
        event.type === "parametric_choreography_scene_checkpoint",
    ),
  );
  const types = events.map((event) => event.type);
  if (
    events.length !== checkpointIds.length + 2 ||
    types[0] !== "scene_stream_started" ||
    types.at(-1) !== "scene_stream_completed" ||
    checkpoints.length !== checkpointIds.length ||
    types
      .slice(1, -1)
      .some((type) => type !== "parametric_choreography_scene_checkpoint")
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} must be one started event, its checkpoints, and one completed event`,
    );
  }
  const started = events[0];
  const completed = events.at(-1);
  if (
    started.type !== "scene_stream_started" ||
    completed?.type !== "scene_stream_completed" ||
    started.generation !== generation ||
    started.attempt !== 1 ||
    started.baseRevision !== base.revision ||
    completed.generation !== generation ||
    completed.patchCount !== checkpoints.length ||
    completed.repaired
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} lifecycle does not match its generation, base, or checkpoint count`,
    );
  }
  const patchIds = new Set<string>();
  for (const [index, checkpoint] of checkpoints.entries()) {
    if (
      checkpoint.generation !== generation ||
      checkpoint.attempt !== 1 ||
      checkpoint.sequence !== index + 1 ||
      checkpoint.baseRevision !== base.revision + index ||
      checkpoint.semantic.checkpointId !== checkpointIds[index] ||
      !sameParametricChoreographyFixtureValue(
        checkpoint.semantic.beat.route,
        route,
      ) ||
      patchIds.has(checkpoint.patch.patchId)
    ) {
      return failParametricChoreographyFixture(
        "invalid_fixture",
        `${field} checkpoint ${index + 1} does not join its exact lane`,
      );
    }
    patchIds.add(checkpoint.patch.patchId);
  }
  if (completed.finalRevision !== base.revision + checkpoints.length) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} completion revision does not match its checkpoints`,
    );
  }
  return Object.freeze({
    generation,
    base,
    route,
    checkpointIds,
    events,
    checkpoints,
  });
}

function checkpointListEquals(
  actual: DecodedParametricChoreographyFixtureLane["checkpointIds"],
  expected: readonly string[],
): boolean {
  return (
    actual.length === expected.length &&
    actual.every((checkpoint, index) => checkpoint === expected[index])
  );
}

/** Strictly decode fixture-owned metadata before any lane can reach transport. */
export function decodeParametricChoreographyFixtureEnvelope(
  value: unknown,
): DecodedParametricChoreographyFixtureEnvelope {
  const input = record(value, "parametric choreography fixture");
  exactKeys(
    input,
    [
      "v",
      "fixtureId",
      "protocol",
      "compilerVersion",
      "problemText",
      "problemSpec",
      "providerRequestCount",
      "lanes",
    ],
    "parametric choreography fixture",
  );
  if (
    input.v !== 1 ||
    input.protocol !== PARAMETRIC_CHOREOGRAPHY_PROTOCOL ||
    input.compilerVersion !== CHECKPOINT_COMPILER_V3_VERSION ||
    input.providerRequestCount !== 0
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      "fixture version, protocol, compiler, or provider count is invalid",
    );
  }
  const decodedFixtureId = fixtureId(input.fixtureId);
  const problemText = boundedText(input.problemText, "problemText");
  let problemSpec: CompletingSquareProblemSpecV1;
  try {
    problemSpec = decodeCompletingSquareProblemSpecV1(input.problemSpec);
  } catch (error) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `problemSpec failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  const canonicalProblemText = `x² + ${problemSpec.linearCoefficient}x = ${problemSpec.rightHandSide}`;
  if (problemText !== canonicalProblemText) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      "problemText does not match problemSpec",
    );
  }
  const lanes = record(input.lanes, "fixture lanes");
  const laneNames = Object.keys(lanes).sort();
  const mainOnly = laneNames.join(",") === "main";
  const adaptive =
    laneNames.join(",") ===
    "clarifyCorner,continueAfterClarification,main";
  if (!mainOnly && !adaptive) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      "fixture lanes must contain main alone or the exact adaptive trio",
    );
  }

  const main = decodeLane(lanes.main, `${decodedFixtureId} main lane`);
  if (
    main.generation !== 1 ||
    !checkpointListEquals(
      main.checkpointIds,
      PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
    )
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${decodedFixtureId} main lane must contain the exact eight main checkpoints`,
    );
  }
  if (!adaptive) {
    return Object.freeze({
      fixtureId: decodedFixtureId,
      problemText,
      problemSpec,
      main,
    });
  }

  const clarifyCorner = decodeLane(
    lanes.clarifyCorner,
    `${decodedFixtureId} clarifyCorner lane`,
  );
  if (
    clarifyCorner.generation !== 2 ||
    !checkpointListEquals(clarifyCorner.checkpointIds, ["corner_detail"])
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${decodedFixtureId} clarifyCorner lane must contain only corner_detail at generation 2`,
    );
  }
  const continueAfterClarification = decodeLane(
    lanes.continueAfterClarification,
    `${decodedFixtureId} continueAfterClarification lane`,
  );
  if (
    continueAfterClarification.generation !== 3 ||
    !checkpointListEquals(continueAfterClarification.checkpointIds, [
      "balance_and_complete",
      "factor_square",
      "solve_roots",
    ])
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${decodedFixtureId} continuation lane has the wrong generation or suffix`,
    );
  }
  return Object.freeze({
    fixtureId: decodedFixtureId,
    problemText,
    problemSpec,
    main,
    clarifyCorner,
    continueAfterClarification,
  });
}
