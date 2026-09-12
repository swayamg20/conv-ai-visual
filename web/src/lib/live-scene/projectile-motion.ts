import { LiveSceneProtocolError } from "./patch";

export const PROJECTILE_MOTION_PROBLEM_VERSION = 1 as const;
export const PROJECTILE_MOTION_ROUTED_BEAT_VERSION = 1 as const;

export const SUPPORTED_PROJECTILE_SPEEDS_MPS = [20, 25, 30] as const;
export type ProjectileSpeedMps =
  (typeof SUPPORTED_PROJECTILE_SPEEDS_MPS)[number];

export const SUPPORTED_PROJECTILE_ANGLES_DEG = [30, 45, 60] as const;
export type ProjectileAngleDeg =
  (typeof SUPPORTED_PROJECTILE_ANGLES_DEG)[number];

export interface ProjectileMotionProblemSpecV1 {
  readonly v: typeof PROJECTILE_MOTION_PROBLEM_VERSION;
  readonly speedMps: ProjectileSpeedMps;
  readonly angleDeg: ProjectileAngleDeg;
}

export const PROJECTILE_MOTION_MAIN_CHECKPOINTS = [
  "setup",
  "decompose_velocity",
  "trace_ascent",
  "apex_state",
  "trace_descent",
  "summary",
] as const;
export type ProjectileMotionMainCheckpoint =
  (typeof PROJECTILE_MOTION_MAIN_CHECKPOINTS)[number];

export const PROJECTILE_MOTION_STAGES = [
  "setup",
  "launch",
  "flight",
  "solve",
] as const;
export type ProjectileMotionStage = (typeof PROJECTILE_MOTION_STAGES)[number];

export const PROJECTILE_MOTION_STAGE_PREFIXES = Object.freeze({
  setup: Object.freeze(PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, 1)),
  launch: Object.freeze(PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, 2)),
  flight: Object.freeze(PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, 5)),
  solve: Object.freeze([...PROJECTILE_MOTION_MAIN_CHECKPOINTS]),
}) satisfies Readonly<
  Record<ProjectileMotionStage, readonly ProjectileMotionMainCheckpoint[]>
>;

export const PROJECTILE_MOTION_CLARIFICATION_TOPICS = [
  "horizontal_velocity",
  "apex_acceleration",
  "flight_symmetry",
] as const;
export type ProjectileMotionClarificationTopic =
  (typeof PROJECTILE_MOTION_CLARIFICATION_TOPICS)[number];

export const PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES = Object.freeze({
  horizontal_velocity: "decompose_velocity",
  apex_acceleration: "apex_state",
  flight_symmetry: "trace_descent",
}) satisfies Readonly<
  Record<ProjectileMotionClarificationTopic, ProjectileMotionMainCheckpoint>
>;

export const PROJECTILE_MOTION_CHECKPOINT_IDS = [
  ...PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  "horizontal_velocity_detail",
  "apex_acceleration_detail",
  "flight_symmetry_detail",
  "parameters_retargeted",
] as const;
export type ProjectileMotionCheckpointId =
  (typeof PROJECTILE_MOTION_CHECKPOINT_IDS)[number];

export const PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS = Object.freeze({
  horizontal_velocity: "horizontal_velocity_detail",
  apex_acceleration: "apex_acceleration_detail",
  flight_symmetry: "flight_symmetry_detail",
}) satisfies Readonly<
  Record<ProjectileMotionClarificationTopic, ProjectileMotionCheckpointId>
>;

export interface ProjectileMotionStateV1 {
  readonly kind: "projectile_motion";
  readonly id: string;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly lastMainCheckpoint: ProjectileMotionMainCheckpoint | null;
  readonly clarifiedTopics: readonly ProjectileMotionClarificationTopic[];
  readonly activeClarification: ProjectileMotionClarificationTopic | null;
}

export interface AdvanceProjectileMotionRouteV1 {
  readonly intent: "advance";
  readonly targetStage: ProjectileMotionStage;
}

export interface ClarifyProjectileMotionRouteV1 {
  readonly intent: "clarify";
  readonly topic: ProjectileMotionClarificationTopic;
}

export interface RetargetProjectileMotionRouteV1 {
  readonly intent: "retarget";
  readonly targetProblemSpec: ProjectileMotionProblemSpecV1;
}

export type ProjectileMotionRouteV1 =
  | AdvanceProjectileMotionRouteV1
  | ClarifyProjectileMotionRouteV1
  | RetargetProjectileMotionRouteV1;

export interface RoutedProjectileMotionBeatV1 {
  readonly v: typeof PROJECTILE_MOTION_ROUTED_BEAT_VERSION;
  readonly beatId: string;
  readonly componentKind: "projectile_motion";
  readonly componentId: string;
  readonly baseProblemSpec: ProjectileMotionProblemSpecV1 | null;
  readonly resultProblemSpec: ProjectileMotionProblemSpecV1;
  readonly route: ProjectileMotionRouteV1;
}

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `projectile motion ${message}`,
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

function oneOf<const Value extends string | number>(
  value: unknown,
  allowed: readonly Value[],
  field: string,
): Value {
  if (!allowed.includes(value as Value)) {
    fail(`${field} has an unsupported value`);
  }
  return value as Value;
}

const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;

function identifier(value: unknown, field: string, maximum: number): string {
  if (
    typeof value !== "string" ||
    [...value].length > maximum ||
    !CONTRACT_ID_PATTERN.test(value)
  ) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

/** Decode exactly one of the nine qualified speed/angle pairs. */
export function decodeProjectileMotionProblemSpecV1(
  value: unknown,
): ProjectileMotionProblemSpecV1 {
  const input = record(value, "problem spec");
  exactKeys(input, ["v", "speedMps", "angleDeg"], "problem spec");
  if (input.v !== PROJECTILE_MOTION_PROBLEM_VERSION) {
    fail(`problem spec v must equal ${PROJECTILE_MOTION_PROBLEM_VERSION}`);
  }
  return Object.freeze({
    v: PROJECTILE_MOTION_PROBLEM_VERSION,
    speedMps: oneOf(
      input.speedMps,
      SUPPORTED_PROJECTILE_SPEEDS_MPS,
      "problem spec speedMps",
    ),
    angleDeg: oneOf(
      input.angleDeg,
      SUPPORTED_PROJECTILE_ANGLES_DEG,
      "problem spec angleDeg",
    ),
  });
}

export function sameProjectileMotionProblem(
  left: ProjectileMotionProblemSpecV1,
  right: ProjectileMotionProblemSpecV1,
): boolean {
  return (
    left.v === right.v &&
    left.speedMps === right.speedMps &&
    left.angleDeg === right.angleDeg
  );
}

export function projectileMotionCheckpointPrefix(
  lastCheckpoint: ProjectileMotionMainCheckpoint | null,
): readonly ProjectileMotionMainCheckpoint[] {
  if (lastCheckpoint === null) return Object.freeze([]);
  const ordinal = PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf(lastCheckpoint);
  if (ordinal < 0) fail("last main checkpoint is unsupported");
  return Object.freeze(
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, ordinal + 1),
  );
}

export function projectileMotionCheckpointsThrough(
  stage: ProjectileMotionStage,
): readonly ProjectileMotionMainCheckpoint[] {
  const prefix = PROJECTILE_MOTION_STAGE_PREFIXES[stage];
  if (!prefix) fail("stage is unsupported");
  return prefix;
}

export function nextProjectileMotionMainCheckpoint(
  lastCheckpoint: ProjectileMotionMainCheckpoint | null,
): ProjectileMotionMainCheckpoint | null {
  const prefix = projectileMotionCheckpointPrefix(lastCheckpoint);
  return PROJECTILE_MOTION_MAIN_CHECKPOINTS[prefix.length] ?? null;
}

/** Decode the exact semantic frontier, including its canonical one-shot ledger. */
export function decodeProjectileMotionStateV1(
  value: unknown,
): ProjectileMotionStateV1 {
  const input = record(value, "state");
  exactKeys(
    input,
    [
      "kind",
      "id",
      "problemSpec",
      "lastMainCheckpoint",
      "clarifiedTopics",
      "activeClarification",
    ],
    "state",
  );
  if (input.kind !== "projectile_motion") {
    fail("state kind must equal projectile_motion");
  }
  const lastMainCheckpoint =
    input.lastMainCheckpoint === null
      ? null
      : oneOf(
          input.lastMainCheckpoint,
          PROJECTILE_MOTION_MAIN_CHECKPOINTS,
          "state lastMainCheckpoint",
        );
  if (!Array.isArray(input.clarifiedTopics)) {
    fail("state clarifiedTopics must be an array");
  }
  if (
    input.clarifiedTopics.length >
    PROJECTILE_MOTION_CLARIFICATION_TOPICS.length
  ) {
    fail("state clarifiedTopics exceeds the closed topic count");
  }
  const clarifiedTopics = input.clarifiedTopics.map((topic) =>
    oneOf(topic, PROJECTILE_MOTION_CLARIFICATION_TOPICS, "state clarified topic"),
  );
  if (new Set(clarifiedTopics).size !== clarifiedTopics.length) {
    fail("state clarifiedTopics must be unique");
  }
  const expectedOrder = PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter((topic) =>
    clarifiedTopics.includes(topic),
  );
  if (expectedOrder.some((topic, index) => topic !== clarifiedTopics[index])) {
    fail("state clarifiedTopics must use canonical pedagogical order");
  }
  const prefix = projectileMotionCheckpointPrefix(lastMainCheckpoint);
  for (const topic of clarifiedTopics) {
    if (!prefix.includes(PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic])) {
      fail(`${topic} clarification is premature for the accepted frontier`);
    }
  }
  const activeClarification =
    input.activeClarification === null
      ? null
      : oneOf(
          input.activeClarification,
          PROJECTILE_MOTION_CLARIFICATION_TOPICS,
          "state activeClarification",
        );
  if (
    activeClarification !== null &&
    !clarifiedTopics.includes(activeClarification)
  ) {
    fail("state activeClarification must be present in clarifiedTopics");
  }
  return Object.freeze({
    kind: "projectile_motion",
    id: identifier(input.id, "state id", 32),
    problemSpec: decodeProjectileMotionProblemSpecV1(input.problemSpec),
    lastMainCheckpoint,
    clarifiedTopics: Object.freeze(clarifiedTopics),
    activeClarification,
  });
}

/** Decode one of the three closed, client-selectable route variants. */
export function decodeProjectileMotionRouteV1(
  value: unknown,
): ProjectileMotionRouteV1 {
  const input = record(value, "route");
  if (input.intent === "advance") {
    exactKeys(input, ["intent", "targetStage"], "route");
    return Object.freeze({
      intent: "advance",
      targetStage: oneOf(
        input.targetStage,
        PROJECTILE_MOTION_STAGES,
        "route targetStage",
      ),
    });
  }
  if (input.intent === "clarify") {
    exactKeys(input, ["intent", "topic"], "route");
    return Object.freeze({
      intent: "clarify",
      topic: oneOf(
        input.topic,
        PROJECTILE_MOTION_CLARIFICATION_TOPICS,
        "route topic",
      ),
    });
  }
  if (input.intent === "retarget") {
    exactKeys(input, ["intent", "targetProblemSpec"], "route");
    return Object.freeze({
      intent: "retarget",
      targetProblemSpec: decodeProjectileMotionProblemSpecV1(
        input.targetProblemSpec,
      ),
    });
  }
  return fail("route intent has an unsupported value");
}

/** Decode and join one server-lowered problem transition. */
export function decodeRoutedProjectileMotionBeatV1(
  value: unknown,
): RoutedProjectileMotionBeatV1 {
  const input = record(value, "routed beat");
  exactKeys(
    input,
    [
      "v",
      "beatId",
      "componentKind",
      "componentId",
      "baseProblemSpec",
      "resultProblemSpec",
      "route",
    ],
    "routed beat",
  );
  if (input.v !== PROJECTILE_MOTION_ROUTED_BEAT_VERSION) {
    fail(
      `routed beat v must equal ${PROJECTILE_MOTION_ROUTED_BEAT_VERSION}`,
    );
  }
  if (input.componentKind !== "projectile_motion") {
    fail("routed beat componentKind must equal projectile_motion");
  }
  const baseProblemSpec =
    input.baseProblemSpec === null
      ? null
      : decodeProjectileMotionProblemSpecV1(input.baseProblemSpec);
  const resultProblemSpec = decodeProjectileMotionProblemSpecV1(
    input.resultProblemSpec,
  );
  const route = decodeProjectileMotionRouteV1(input.route);
  if (baseProblemSpec === null && route.intent !== "advance") {
    fail("only a fresh advance may omit baseProblemSpec");
  }
  if (baseProblemSpec !== null && route.intent === "retarget") {
    if (!sameProjectileMotionProblem(route.targetProblemSpec, resultProblemSpec)) {
      fail("retarget targetProblemSpec must match resultProblemSpec");
    }
    if (sameProjectileMotionProblem(baseProblemSpec, resultProblemSpec)) {
      fail("retarget must change the problem specification");
    }
  } else if (
    baseProblemSpec !== null &&
    !sameProjectileMotionProblem(baseProblemSpec, resultProblemSpec)
  ) {
    fail("advance and clarification must preserve the problem specification");
  }
  return Object.freeze({
    v: PROJECTILE_MOTION_ROUTED_BEAT_VERSION,
    beatId: identifier(input.beatId, "routed beat beatId", 64),
    componentKind: "projectile_motion",
    componentId: identifier(input.componentId, "routed beat componentId", 32),
    baseProblemSpec,
    resultProblemSpec,
    route,
  });
}
