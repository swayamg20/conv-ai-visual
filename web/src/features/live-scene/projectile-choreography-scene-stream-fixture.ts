import primaryFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json";
import complementaryLowFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v20-a30.v1.json";
import complementaryHighFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v20-a60.v1.json";
import maximumRangeFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v30-a45.v1.json";
import maximumHeightFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v30-a60.v1.json";

import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import {
  MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  PROJECTILE_MOTION_CHECKPOINT_IDS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  decodeProjectileMotionProblemSpecV1,
  decodeProjectileMotionRouteV1,
  sameProjectileMotionProblem,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
} from "@/lib/live-scene/projectile-motion";

import { createFixtureSseResponse } from "./fixture-sse";
import {
  consumeProjectileChoreographySceneStreamResponse,
  type ProjectileChoreographySceneStreamRunner,
} from "./projectile-choreography-model-stream";
import {
  EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
  prepareProjectileChoreographyCheckpoint,
  type ProjectileChoreographyFrontier,
} from "./projectile-choreography-playback";

export const PROJECTILE_CHOREOGRAPHY_FIXTURE_MODES = [
  "main",
  "adaptive",
] as const;
export type ProjectileChoreographyFixtureMode =
  (typeof PROJECTILE_CHOREOGRAPHY_FIXTURE_MODES)[number];

export type ProjectileChoreographyFixtureLaneName =
  | "main"
  | "continueMain"
  | "clarifyHorizontal"
  | "clarifyApex"
  | "clarifySymmetry"
  | "continueAfterClarification"
  | "retargetAtApex"
  | "retargetAfterSummary";

export interface ProjectileChoreographyFixtureBatch {
  readonly fixtureId: string;
  readonly lane: ProjectileChoreographyFixtureLaneName;
  readonly events: readonly ProjectileChoreographySceneStreamEventV1[];
  readonly holdOpenUntilAbort: boolean;
}

export interface ProjectileChoreographyFixtureRunnerOptions {
  readonly mode?: ProjectileChoreographyFixtureMode;
  readonly eventDelayMs?: number;
  readonly chunkDelayMs?: number;
  /** Injectable only for strict malformed-envelope tests. */
  readonly fixtureValues?: readonly unknown[];
}

export type ProjectileChoreographyFixtureErrorCode =
  "invalid_fixture" | "request_mismatch";

export class ProjectileChoreographyFixtureError extends Error {
  readonly code: ProjectileChoreographyFixtureErrorCode;

  constructor(code: ProjectileChoreographyFixtureErrorCode, message: string) {
    super(message);
    this.name = "ProjectileChoreographyFixtureError";
    this.code = code;
  }
}

type UnknownRecord = Record<string, unknown>;

interface DecodedFixtureLane {
  readonly scenarioId: string;
  readonly generation: number;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly route: ProjectileMotionRouteV1;
  readonly checkpointIds: readonly ProjectileMotionCheckpointId[];
  readonly events: readonly ProjectileChoreographySceneStreamEventV1[];
  readonly checkpoints: readonly ProjectileChoreographySceneCheckpointEventV1[];
  readonly baseRequest: ProjectileMotionRequestV1;
  readonly frontierValue: unknown;
  readonly expectedTerminalValue: unknown;
}

interface MaterializedFixtureLane extends DecodedFixtureLane {
  readonly baseFrontier: ProjectileChoreographyFrontier;
  readonly resultFrontier: ProjectileChoreographyFrontier;
  readonly prefixes: readonly ProjectileChoreographyFrontier[];
}

interface DecodedFixtureEnvelope {
  readonly fixtureId: string;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly lanesValue: UnknownRecord;
}

interface MaterializedFixture {
  readonly fixtureId: string;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly main: MaterializedFixtureLane;
  readonly clarifyHorizontal?: MaterializedFixtureLane;
  readonly clarifyApex?: MaterializedFixtureLane;
  readonly clarifySymmetry?: MaterializedFixtureLane;
  readonly continueAfterClarification?: MaterializedFixtureLane;
  readonly retargetAtApex?: MaterializedFixtureLane;
  readonly retargetAfterSummary?: MaterializedFixtureLane;
}

const FIXTURE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const PRIMARY_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 45,
} as const satisfies ProjectileMotionProblemSpecV1);
const RETARGET_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 60,
} as const satisfies ProjectileMotionProblemSpecV1);
const ADVANCE_TO_SOLVE = Object.freeze({
  intent: "advance",
  targetStage: "solve",
} as const satisfies ProjectileMotionRouteV1);
const DEFAULT_FIXTURE_VALUES = Object.freeze([
  complementaryLowFixtureValue,
  primaryFixtureValue,
  complementaryHighFixtureValue,
  maximumRangeFixtureValue,
  maximumHeightFixtureValue,
]);

function fail(
  code: ProjectileChoreographyFixtureErrorCode,
  message: string,
): never {
  throw new ProjectileChoreographyFixtureError(code, message);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return fail("invalid_fixture", `${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    return fail("invalid_fixture", `${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  expectedKeys: readonly string[],
  field: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    return fail(
      "invalid_fixture",
      `${field} must contain exactly ${expected.join(", ")}`,
    );
  }
}

function safeInteger(value: unknown, field: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    return fail(
      "invalid_fixture",
      `${field} must be a safe integer at least ${minimum}`,
    );
  }
  return value as number;
}

function finiteNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fail("invalid_fixture", `${field} must be finite`);
  }
  return value;
}

function boundedText(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value.trim()].length > 2_000
  ) {
    return fail(
      "invalid_fixture",
      `${field} must be a non-empty string of at most 2000 characters`,
    );
  }
  return value.trim();
}

function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameValue(value, right[index]))
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
        sameValue(leftRecord[key], rightRecord[key]),
    )
  );
}

function decodeProblem(
  value: unknown,
  field: string,
): ProjectileMotionProblemSpecV1 {
  try {
    return decodeProjectileMotionProblemSpecV1(value);
  } catch (error) {
    return fail(
      "invalid_fixture",
      `${field} failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
}

function decodeRoute(value: unknown, field: string): ProjectileMotionRouteV1 {
  try {
    return decodeProjectileMotionRouteV1(value);
  } catch (error) {
    return fail(
      "invalid_fixture",
      `${field} failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
}

function validatePhysicsMetadata(
  physicsValue: unknown,
  coverageValue: unknown,
  problemSpec: ProjectileMotionProblemSpecV1,
): void {
  const physics = record(physicsValue, "fixture expectedPhysics");
  exactKeys(
    physics,
    [
      "gravityMps2",
      "initialHorizontalVelocityMps",
      "initialVerticalVelocityMps",
      "apexTimeSeconds",
      "flightTimeSeconds",
      "maximumHeightM",
      "rangeM",
    ],
    "fixture expectedPhysics",
  );
  const gravity = 10;
  const radians = (problemSpec.angleDeg * Math.PI) / 180;
  const horizontal = problemSpec.speedMps * Math.cos(radians);
  const vertical = problemSpec.speedMps * Math.sin(radians);
  const apexTime = vertical / gravity;
  const expected = {
    gravityMps2: gravity,
    initialHorizontalVelocityMps: horizontal,
    initialVerticalVelocityMps: vertical,
    apexTimeSeconds: apexTime,
    flightTimeSeconds: 2 * apexTime,
    maximumHeightM: (vertical * vertical) / (2 * gravity),
    rangeM: horizontal * 2 * apexTime,
  } as const;
  for (const [key, expectedValue] of Object.entries(expected)) {
    const actual = finiteNumber(physics[key], `expectedPhysics ${key}`);
    if (Math.abs(actual - expectedValue) > 1e-9) {
      return fail(
        "invalid_fixture",
        `expectedPhysics ${key} does not match the qualified problem`,
      );
    }
  }

  const coverage = record(coverageValue, "fixture coverage");
  exactKeys(
    coverage,
    [
      "complementaryRangePartner",
      "isQualifiedMaximumRange",
      "isQualifiedMaximumHeight",
    ],
    "fixture coverage",
  );
  const complementaryAngle =
    problemSpec.angleDeg === 30 ? 60 : problemSpec.angleDeg === 60 ? 30 : null;
  if (complementaryAngle === null) {
    if (coverage.complementaryRangePartner !== null) {
      return fail(
        "invalid_fixture",
        "45-degree coverage must not claim a complementary partner",
      );
    }
  } else {
    const partner = decodeProblem(
      coverage.complementaryRangePartner,
      "coverage complementaryRangePartner",
    );
    if (
      partner.speedMps !== problemSpec.speedMps ||
      partner.angleDeg !== complementaryAngle
    ) {
      return fail(
        "invalid_fixture",
        "coverage complementary partner does not match the qualified pair",
      );
    }
  }
  if (
    coverage.isQualifiedMaximumRange !==
      (problemSpec.speedMps === 30 && problemSpec.angleDeg === 45) ||
    coverage.isQualifiedMaximumHeight !==
      (problemSpec.speedMps === 30 && problemSpec.angleDeg === 60)
  ) {
    return fail(
      "invalid_fixture",
      "coverage extrema flags do not match the qualified grid",
    );
  }
}

function decodeCheckpointIds(
  value: unknown,
  field: string,
): readonly ProjectileMotionCheckpointId[] {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.length > MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS
  ) {
    return fail(
      "invalid_fixture",
      `${field} must contain between one and ${MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS} checkpoints`,
    );
  }
  return Object.freeze(
    value.map((candidate, index) => {
      const checkpoint = PROJECTILE_MOTION_CHECKPOINT_IDS.find(
        (value) => value === candidate,
      );
      if (!checkpoint) {
        return fail(
          "invalid_fixture",
          `${field}[${index}] is not a projectile checkpoint`,
        );
      }
      return checkpoint;
    }),
  );
}

function decodeLane(
  value: unknown,
  fixtureProblem: ProjectileMotionProblemSpecV1,
  field: string,
): DecodedFixtureLane {
  const input = record(value, field);
  exactKeys(
    input,
    [
      "scenarioId",
      "generation",
      "problemSpec",
      "frontier",
      "route",
      "checkpointIds",
      "checkpointCount",
      "baseScene",
      "baseSemanticScene",
      "events",
      "expectedTerminal",
    ],
    field,
  );
  const scenarioId = boundedText(input.scenarioId, `${field} scenarioId`);
  if (!FIXTURE_ID_PATTERN.test(scenarioId)) {
    return fail("invalid_fixture", `${field} scenarioId is not SSE-safe`);
  }
  const generation = safeInteger(input.generation, `${field} generation`, 1);
  const problemSpec = decodeProblem(input.problemSpec, `${field} problemSpec`);
  if (!sameProjectileMotionProblem(problemSpec, fixtureProblem)) {
    return fail("invalid_fixture", `${field} changed the fixture problem`);
  }
  const route = decodeRoute(input.route, `${field} route`);
  const checkpointIds = decodeCheckpointIds(
    input.checkpointIds,
    `${field} checkpointIds`,
  );
  if (
    safeInteger(input.checkpointCount, `${field} checkpointCount`, 1) !==
    checkpointIds.length
  ) {
    return fail(
      "invalid_fixture",
      `${field} checkpointCount does not match checkpointIds`,
    );
  }

  let baseRequest: ProjectileMotionRequestV1;
  try {
    baseRequest = decodeProjectileMotionRequestV1({
      protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemSpec,
      generation,
      baseScene: input.baseScene,
      baseSemanticScene: input.baseSemanticScene,
      requestedRoute: route,
    });
  } catch (error) {
    return fail(
      "invalid_fixture",
      `${field} base failed strict request decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  if (!Array.isArray(input.events)) {
    return fail("invalid_fixture", `${field} events must be an array`);
  }
  let events: readonly ProjectileChoreographySceneStreamEventV1[];
  try {
    events = Object.freeze(
      input.events.map(decodeProjectileChoreographySceneStreamEventV1),
    );
  } catch (error) {
    return fail(
      "invalid_fixture",
      `${field} events failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  const checkpoints = Object.freeze(
    events.filter(
      (event): event is ProjectileChoreographySceneCheckpointEventV1 =>
        event.type === "projectile_choreography_scene_checkpoint",
    ),
  );
  const eventTypes = events.map((event) => event.type);
  if (
    events.length !== checkpoints.length + 2 ||
    eventTypes[0] !== "scene_stream_started" ||
    eventTypes.at(-1) !== "scene_stream_completed" ||
    checkpoints.length !== checkpointIds.length ||
    eventTypes
      .slice(1, -1)
      .some(
        (eventType) => eventType !== "projectile_choreography_scene_checkpoint",
      )
  ) {
    return fail(
      "invalid_fixture",
      `${field} must contain started, checkpoint, and completed events only`,
    );
  }
  const started = events[0];
  const completed = events.at(-1);
  if (
    started.type !== "scene_stream_started" ||
    completed?.type !== "scene_stream_completed" ||
    started.generation !== generation ||
    started.attempt !== 1 ||
    started.baseRevision !== baseRequest.baseScene.revision ||
    completed.generation !== generation ||
    completed.patchCount !== checkpoints.length ||
    completed.repaired
  ) {
    return fail(
      "invalid_fixture",
      `${field} lifecycle does not match its generation, base, and checkpoint count`,
    );
  }
  const patchIds = new Set<string>();
  for (const [index, checkpoint] of checkpoints.entries()) {
    if (
      checkpoint.generation !== generation ||
      checkpoint.attempt !== 1 ||
      checkpoint.sequence !== index + 1 ||
      checkpoint.baseRevision !== baseRequest.baseScene.revision + index ||
      checkpoint.semantic.checkpointId !== checkpointIds[index] ||
      !sameValue(checkpoint.semantic.beat.route, route) ||
      patchIds.has(checkpoint.patch.patchId)
    ) {
      return fail(
        "invalid_fixture",
        `${field} checkpoint ${index + 1} does not join its exact lane`,
      );
    }
    patchIds.add(checkpoint.patch.patchId);
  }
  if (
    completed.finalRevision !==
    baseRequest.baseScene.revision + checkpoints.length
  ) {
    return fail(
      "invalid_fixture",
      `${field} completion revision does not match its checkpoints`,
    );
  }
  const frontier = record(input.frontier, `${field} frontier`);
  exactKeys(
    frontier,
    [
      "revision",
      "problemSpec",
      "lastMainCheckpoint",
      "clarifiedTopics",
      "activeClarification",
      "certificateHeadSha256",
    ],
    `${field} frontier`,
  );
  const expectedTerminal = record(
    input.expectedTerminal,
    `${field} expectedTerminal`,
  );
  exactKeys(
    expectedTerminal,
    ["scene", "semanticScene", "frontier", "certificateSha256"],
    `${field} expectedTerminal`,
  );

  return Object.freeze({
    scenarioId,
    generation,
    problemSpec,
    route,
    checkpointIds,
    events,
    checkpoints,
    baseRequest,
    frontierValue: input.frontier,
    expectedTerminalValue: input.expectedTerminal,
  });
}

function decodeEnvelope(value: unknown): DecodedFixtureEnvelope {
  const input = record(value, "projectile choreography fixture");
  exactKeys(
    input,
    [
      "v",
      "fixtureId",
      "protocol",
      "compilerVersion",
      "scenario",
      "problemSpec",
      "expectedPhysics",
      "coverage",
      "providerRequestCount",
      "lanes",
    ],
    "projectile choreography fixture",
  );
  if (
    input.v !== 1 ||
    input.protocol !== PROJECTILE_CHOREOGRAPHY_PROTOCOL ||
    input.compilerVersion !== PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION ||
    input.scenario !== "qualified_projectile_motion" ||
    input.providerRequestCount !== 0
  ) {
    return fail(
      "invalid_fixture",
      "fixture version, protocol, compiler, scenario, or provider count is invalid",
    );
  }
  if (
    typeof input.fixtureId !== "string" ||
    !FIXTURE_ID_PATTERN.test(input.fixtureId)
  ) {
    return fail("invalid_fixture", "fixtureId must be an SSE-safe identifier");
  }
  const problemSpec = decodeProblem(input.problemSpec, "fixture problemSpec");
  if (
    input.fixtureId !==
    `projectile-motion-v${problemSpec.speedMps}-a${problemSpec.angleDeg}`
  ) {
    return fail("invalid_fixture", "fixtureId does not match problemSpec");
  }
  validatePhysicsMetadata(input.expectedPhysics, input.coverage, problemSpec);
  const lanesValue = record(input.lanes, `${input.fixtureId} lanes`);
  const laneNames = Object.keys(lanesValue).sort().join(",");
  const expectedLaneNames = sameProjectileMotionProblem(
    problemSpec,
    PRIMARY_PROBLEM,
  )
    ? "clarifyApex,clarifyHorizontal,clarifySymmetry,continueAfterClarification,main,retargetAfterSummary,retargetAtApex"
    : "main";
  if (laneNames !== expectedLaneNames) {
    return fail(
      "invalid_fixture",
      `${input.fixtureId} does not contain its exact certified lane set`,
    );
  }
  return Object.freeze({
    fixtureId: input.fixtureId,
    problemSpec,
    lanesValue,
  });
}

function frontierValue(frontier: ProjectileChoreographyFrontier): unknown {
  const component = frontier.semanticScene.components[0];
  return {
    revision: frontier.scene.revision,
    problemSpec: component?.problemSpec ?? null,
    lastMainCheckpoint: component?.lastMainCheckpoint ?? null,
    clarifiedTopics: component ? [...component.clarifiedTopics] : [],
    activeClarification: component?.activeClarification ?? null,
    certificateHeadSha256: frontier.certificateHeadSha256,
  };
}

function materializeLane(
  lane: DecodedFixtureLane,
  expectedBase: ProjectileChoreographyFrontier,
  field: string,
): MaterializedFixtureLane {
  if (
    !sameValue(lane.baseRequest.baseScene, expectedBase.scene) ||
    !sameValue(
      lane.baseRequest.baseSemanticScene,
      expectedBase.semanticScene,
    ) ||
    !sameValue(lane.frontierValue, frontierValue(expectedBase))
  ) {
    return fail(
      "invalid_fixture",
      `${field} base does not match its exact certified predecessor`,
    );
  }

  const prefixes: ProjectileChoreographyFrontier[] = [expectedBase];
  let frontier = expectedBase;
  for (const checkpoint of lane.checkpoints) {
    try {
      frontier = prepareProjectileChoreographyCheckpoint(
        frontier,
        checkpoint,
        "cinematic",
      ).target;
    } catch (error) {
      return fail(
        "invalid_fixture",
        `${field} failed certified frontier materialization: ${
          error instanceof Error ? error.message : "unknown error"
        }`,
      );
    }
    prefixes.push(frontier);
  }
  const terminal = record(
    lane.expectedTerminalValue,
    `${field} expectedTerminal`,
  );
  const terminalCertificate = terminal.certificateSha256;
  if (
    typeof terminalCertificate !== "string" ||
    !SHA256_PATTERN.test(terminalCertificate) ||
    terminalCertificate !== frontier.certificateHeadSha256 ||
    !sameValue(terminal.scene, frontier.scene) ||
    !sameValue(terminal.semanticScene, frontier.semanticScene) ||
    !sameValue(terminal.frontier, frontierValue(frontier))
  ) {
    return fail(
      "invalid_fixture",
      `${field} expected terminal does not match its materialized result`,
    );
  }
  return Object.freeze({
    ...lane,
    baseFrontier: expectedBase,
    resultFrontier: frontier,
    prefixes: Object.freeze(prefixes),
  });
}

function requireLaneContract(
  lane: DecodedFixtureLane,
  expected: {
    readonly scenarioId: string;
    readonly generation: number;
    readonly route: ProjectileMotionRouteV1;
    readonly checkpointIds: readonly ProjectileMotionCheckpointId[];
  },
  field: string,
): void {
  if (
    lane.scenarioId !== expected.scenarioId ||
    lane.generation !== expected.generation ||
    !sameValue(lane.route, expected.route) ||
    !sameValue(lane.checkpointIds, expected.checkpointIds)
  ) {
    return fail(
      "invalid_fixture",
      `${field} does not match its closed scenario contract`,
    );
  }
}

function materializeFixture(value: unknown): MaterializedFixture {
  const envelope = decodeEnvelope(value);
  const mainDecoded = decodeLane(
    envelope.lanesValue.main,
    envelope.problemSpec,
    `${envelope.fixtureId} main lane`,
  );
  requireLaneContract(
    mainDecoded,
    {
      scenarioId: "main_solve",
      generation: 1,
      route: ADVANCE_TO_SOLVE,
      checkpointIds: PROJECTILE_MOTION_MAIN_CHECKPOINTS,
    },
    `${envelope.fixtureId} main lane`,
  );
  const main = materializeLane(
    mainDecoded,
    EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
    `${envelope.fixtureId} main lane`,
  );
  if (!sameProjectileMotionProblem(envelope.problemSpec, PRIMARY_PROBLEM)) {
    return Object.freeze({
      fixtureId: envelope.fixtureId,
      problemSpec: envelope.problemSpec,
      main,
    });
  }

  const apexIndex = PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf("apex_state");
  const horizontalIndex =
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf("decompose_velocity");
  const symmetryIndex =
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf("trace_descent");
  const horizontalFrontier = main.prefixes[horizontalIndex + 1];
  const apexFrontier = main.prefixes[apexIndex + 1];
  const symmetryFrontier = main.prefixes[symmetryIndex + 1];
  if (!horizontalFrontier || !apexFrontier || !symmetryFrontier) {
    return fail(
      "invalid_fixture",
      "primary main lane is missing a clarification frontier",
    );
  }
  const clarifyHorizontalDecoded = decodeLane(
    envelope.lanesValue.clarifyHorizontal,
    envelope.problemSpec,
    `${envelope.fixtureId} clarifyHorizontal lane`,
  );
  requireLaneContract(
    clarifyHorizontalDecoded,
    {
      scenarioId: "horizontal_velocity_clarification",
      generation: 2,
      route: { intent: "clarify", topic: "horizontal_velocity" },
      checkpointIds: ["horizontal_velocity_detail"],
    },
    `${envelope.fixtureId} clarifyHorizontal lane`,
  );
  const clarifyHorizontal = materializeLane(
    clarifyHorizontalDecoded,
    horizontalFrontier,
    `${envelope.fixtureId} clarifyHorizontal lane`,
  );

  const clarifyDecoded = decodeLane(
    envelope.lanesValue.clarifyApex,
    envelope.problemSpec,
    `${envelope.fixtureId} clarifyApex lane`,
  );
  requireLaneContract(
    clarifyDecoded,
    {
      scenarioId: "apex_acceleration_clarification",
      generation: 2,
      route: { intent: "clarify", topic: "apex_acceleration" },
      checkpointIds: ["apex_acceleration_detail"],
    },
    `${envelope.fixtureId} clarifyApex lane`,
  );
  const clarifyApex = materializeLane(
    clarifyDecoded,
    apexFrontier,
    `${envelope.fixtureId} clarifyApex lane`,
  );

  const clarifySymmetryDecoded = decodeLane(
    envelope.lanesValue.clarifySymmetry,
    envelope.problemSpec,
    `${envelope.fixtureId} clarifySymmetry lane`,
  );
  requireLaneContract(
    clarifySymmetryDecoded,
    {
      scenarioId: "flight_symmetry_clarification",
      generation: 2,
      route: { intent: "clarify", topic: "flight_symmetry" },
      checkpointIds: ["flight_symmetry_detail"],
    },
    `${envelope.fixtureId} clarifySymmetry lane`,
  );
  const clarifySymmetry = materializeLane(
    clarifySymmetryDecoded,
    symmetryFrontier,
    `${envelope.fixtureId} clarifySymmetry lane`,
  );

  const continuationDecoded = decodeLane(
    envelope.lanesValue.continueAfterClarification,
    envelope.problemSpec,
    `${envelope.fixtureId} continueAfterClarification lane`,
  );
  requireLaneContract(
    continuationDecoded,
    {
      scenarioId: "continue_after_apex_clarification",
      generation: 3,
      route: { intent: "advance", targetStage: "solve" },
      checkpointIds: ["trace_descent", "summary"],
    },
    `${envelope.fixtureId} continueAfterClarification lane`,
  );
  const continueAfterClarification = materializeLane(
    continuationDecoded,
    clarifyApex.resultFrontier,
    `${envelope.fixtureId} continueAfterClarification lane`,
  );

  const retargetAtApexDecoded = decodeLane(
    envelope.lanesValue.retargetAtApex,
    envelope.problemSpec,
    `${envelope.fixtureId} retargetAtApex lane`,
  );
  requireLaneContract(
    retargetAtApexDecoded,
    {
      scenarioId: "retarget_20_45_to_20_60_at_apex",
      generation: 3,
      route: { intent: "retarget", targetProblemSpec: RETARGET_PROBLEM },
      checkpointIds: ["parameters_retargeted"],
    },
    `${envelope.fixtureId} retargetAtApex lane`,
  );
  const retargetAtApex = materializeLane(
    retargetAtApexDecoded,
    clarifyApex.resultFrontier,
    `${envelope.fixtureId} retargetAtApex lane`,
  );

  const retargetAfterSummaryDecoded = decodeLane(
    envelope.lanesValue.retargetAfterSummary,
    envelope.problemSpec,
    `${envelope.fixtureId} retargetAfterSummary lane`,
  );
  requireLaneContract(
    retargetAfterSummaryDecoded,
    {
      scenarioId: "retarget_20_45_to_20_60_after_summary",
      generation: 4,
      route: { intent: "retarget", targetProblemSpec: RETARGET_PROBLEM },
      checkpointIds: ["parameters_retargeted"],
    },
    `${envelope.fixtureId} retargetAfterSummary lane`,
  );
  const retargetAfterSummary = materializeLane(
    retargetAfterSummaryDecoded,
    continueAfterClarification.resultFrontier,
    `${envelope.fixtureId} retargetAfterSummary lane`,
  );

  return Object.freeze({
    fixtureId: envelope.fixtureId,
    problemSpec: envelope.problemSpec,
    main,
    clarifyHorizontal,
    clarifyApex,
    clarifySymmetry,
    continueAfterClarification,
    retargetAtApex,
    retargetAfterSummary,
  });
}

function decodeMode(value: unknown): ProjectileChoreographyFixtureMode {
  if (
    typeof value !== "string" ||
    !PROJECTILE_CHOREOGRAPHY_FIXTURE_MODES.some((mode) => mode === value)
  ) {
    throw new TypeError("fixture mode must be main or adaptive");
  }
  return value as ProjectileChoreographyFixtureMode;
}

function decodeCatalog(
  values: readonly unknown[],
): readonly MaterializedFixture[] {
  if (values.length === 0) {
    return fail("invalid_fixture", "fixture catalog cannot be empty");
  }
  const fixtures = Object.freeze(values.map(materializeFixture));
  const ids = new Set<string>();
  const problems = new Set<string>();
  for (const fixture of fixtures) {
    const problemKey = `${fixture.problemSpec.speedMps}:${fixture.problemSpec.angleDeg}`;
    if (ids.has(fixture.fixtureId) || problems.has(problemKey)) {
      return fail(
        "invalid_fixture",
        "fixture catalog contains a duplicate identity or problem",
      );
    }
    ids.add(fixture.fixtureId);
    problems.add(problemKey);
  }
  return fixtures;
}

function requestMatchesLane(
  request: ProjectileMotionRequestV1,
  fixture: MaterializedFixture,
  lane: MaterializedFixtureLane,
): boolean {
  return (
    request.routingMode === "reflex" &&
    request.generation === lane.generation &&
    sameProjectileMotionProblem(request.problemSpec, fixture.problemSpec) &&
    sameValue(request.requestedRoute, lane.route) &&
    sameValue(request.baseScene, lane.baseFrontier.scene) &&
    sameValue(request.baseSemanticScene, lane.baseFrontier.semanticScene)
  );
}

function derivedMainContinuation(
  request: ProjectileMotionRequestV1,
  fixtures: readonly MaterializedFixture[],
): ProjectileChoreographyFixtureBatch | null {
  if (
    request.routingMode !== "reflex" ||
    request.generation < 2 ||
    !sameValue(request.requestedRoute, ADVANCE_TO_SOLVE)
  ) {
    return null;
  }
  const matches = fixtures.flatMap((fixture) => {
    if (
      !sameProjectileMotionProblem(request.problemSpec, fixture.problemSpec)
    ) {
      return [];
    }
    return fixture.main.prefixes.flatMap((frontier, prefixCount) => {
      const isPartialMainPrefix =
        prefixCount > 0 && prefixCount < fixture.main.checkpoints.length;
      return isPartialMainPrefix &&
        sameValue(request.baseScene, frontier.scene) &&
        sameValue(request.baseSemanticScene, frontier.semanticScene)
        ? [{ fixture, frontier, prefixCount }]
        : [];
    });
  });
  if (matches.length === 0) return null;
  if (matches.length !== 1) {
    return fail(
      "invalid_fixture",
      "main continuation matched more than one certified prefix",
    );
  }
  const { fixture, frontier: baseFrontier, prefixCount } = matches[0];
  const suffix = fixture.main.checkpoints.slice(prefixCount);
  const terminal = fixture.main.events.at(-1);
  if (terminal?.type !== "scene_stream_completed" || suffix.length === 0) {
    return fail(
      "invalid_fixture",
      "main continuation source has no strict suffix terminal",
    );
  }
  const events = Object.freeze([
    decodeProjectileChoreographySceneStreamEventV1({
      type: "scene_stream_started",
      generation: request.generation,
      attempt: 1,
      baseRevision: request.baseScene.revision,
    }),
    ...suffix.map((checkpoint, index) =>
      decodeProjectileChoreographySceneStreamEventV1({
        ...checkpoint,
        generation: request.generation,
        sequence: index + 1,
      }),
    ),
    decodeProjectileChoreographySceneStreamEventV1({
      ...terminal,
      generation: request.generation,
      patchCount: suffix.length,
    }),
  ]);

  let verifiedFrontier = baseFrontier;
  for (const event of events) {
    if (event.type !== "projectile_choreography_scene_checkpoint") continue;
    try {
      verifiedFrontier = prepareProjectileChoreographyCheckpoint(
        verifiedFrontier,
        event,
        "cinematic",
      ).target;
    } catch (error) {
      return fail(
        "invalid_fixture",
        `derived main continuation failed certified preflight: ${
          error instanceof Error ? error.message : "unknown error"
        }`,
      );
    }
  }
  if (!sameValue(verifiedFrontier, fixture.main.resultFrontier)) {
    return fail(
      "invalid_fixture",
      "derived main continuation does not reach the sealed main terminal",
    );
  }
  return Object.freeze({
    fixtureId: fixture.fixtureId,
    lane: "continueMain",
    events,
    holdOpenUntilAbort: false,
  });
}

function selectBatch(
  requestValue: ProjectileMotionRequestV1,
  modeValue: unknown,
  fixtures: readonly MaterializedFixture[],
): ProjectileChoreographyFixtureBatch {
  const mode = decodeMode(modeValue);
  let request: ProjectileMotionRequestV1;
  try {
    request = decodeProjectileMotionRequestV1(requestValue);
  } catch (error) {
    return fail(
      "request_mismatch",
      `request failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  if (request.routingMode !== "reflex") {
    return fail(
      "request_mismatch",
      "deterministic projectile fixtures accept only zero-model Reflex requests",
    );
  }
  const candidates = fixtures.flatMap((fixture) =>
    (
      [
        ["main", fixture.main],
        ["clarifyHorizontal", fixture.clarifyHorizontal],
        ["clarifyApex", fixture.clarifyApex],
        ["clarifySymmetry", fixture.clarifySymmetry],
        ["continueAfterClarification", fixture.continueAfterClarification],
        ["retargetAtApex", fixture.retargetAtApex],
        ["retargetAfterSummary", fixture.retargetAfterSummary],
      ] as const
    ).flatMap(([laneName, lane]) =>
      lane && requestMatchesLane(request, fixture, lane)
        ? [{ fixture, laneName, lane }]
        : [],
    ),
  );
  if (candidates.length !== 1) {
    const continuation = derivedMainContinuation(request, fixtures);
    if (continuation) return continuation;
    return fail(
      "request_mismatch",
      "request does not match one exact fixture problem, generation, route, and certified frontier",
    );
  }
  const selected = candidates[0];
  if (mode === "adaptive" && selected.laneName === "main") {
    if (!selected.fixture.clarifyApex) {
      return fail(
        "request_mismatch",
        "adaptive playback is available only for the primary 20 m/s at 45 degrees fixture",
      );
    }
    const apexIndex = selected.lane.checkpointIds.indexOf("apex_state");
    return Object.freeze({
      fixtureId: selected.fixture.fixtureId,
      lane: "main",
      events: Object.freeze(selected.lane.events.slice(0, apexIndex + 2)),
      holdOpenUntilAbort: true,
    });
  }
  return Object.freeze({
    fixtureId: selected.fixture.fixtureId,
    lane: selected.laneName,
    events: selected.lane.events,
    holdOpenUntilAbort: false,
  });
}

const DEFAULT_CATALOG = decodeCatalog(DEFAULT_FIXTURE_VALUES);

function catalogFor(
  fixtureValues: readonly unknown[] | undefined,
): readonly MaterializedFixture[] {
  return fixtureValues ? decodeCatalog(fixtureValues) : DEFAULT_CATALOG;
}

function nonnegativeDelay(
  value: number | undefined,
  fallback: number,
  field: string,
): number {
  const delay = value ?? fallback;
  if (!Number.isFinite(delay) || delay < 0) {
    throw new TypeError(`${field} must be finite and nonnegative`);
  }
  return delay;
}

/** Select one exact real-compiler fixture lane without opening a stream. */
export function createProjectileChoreographyFixtureBatch(
  request: ProjectileMotionRequestV1,
  options: Pick<
    ProjectileChoreographyFixtureRunnerOptions,
    "mode" | "fixtureValues"
  > = {},
): ProjectileChoreographyFixtureBatch {
  return selectBatch(
    request,
    options.mode ?? PROJECTILE_CHOREOGRAPHY_FIXTURE_MODES[0],
    catalogFor(options.fixtureValues),
  );
}

/** Exercise the production projectile byte decoder with deterministic SSE. */
export function createProjectileChoreographyFixtureRunner(
  options: ProjectileChoreographyFixtureRunnerOptions = {},
): ProjectileChoreographySceneStreamRunner {
  const mode = decodeMode(
    options.mode ?? PROJECTILE_CHOREOGRAPHY_FIXTURE_MODES[0],
  );
  const eventDelayMs = nonnegativeDelay(
    options.eventDelayMs,
    24,
    "eventDelayMs",
  );
  const chunkDelayMs = nonnegativeDelay(
    options.chunkDelayMs,
    1,
    "chunkDelayMs",
  );
  const fixtures = catalogFor(options.fixtureValues);

  return async ({ request, signal, onEvent }) => {
    const batch = selectBatch(request, mode, fixtures);
    const response = createFixtureSseResponse(batch.events, signal, {
      eventDelayMs,
      chunkDelayMs,
      idPrefix: `projectile-fixture-${batch.fixtureId}-${batch.lane}`,
      holdOpenUntilAbort: batch.holdOpenUntilAbort,
    });
    await consumeProjectileChoreographySceneStreamResponse(response, onEvent);
  };
}
