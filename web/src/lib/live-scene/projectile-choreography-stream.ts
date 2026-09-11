import {
  decodeChoreographyPlanV2,
  decodePresentationCheckpointV1,
  type ChoreographyPlanV2,
  type PresentationCheckpointV1,
} from "./choreography";
import {
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
  type ScenePatchDraft,
} from "./patch";
import {
  decodeProjectileMotionProblemSpecV1,
  decodeProjectileMotionStateV1,
  decodeRoutedProjectileMotionBeatV1,
  nextProjectileMotionMainCheckpoint,
  PROJECTILE_MOTION_CHECKPOINT_IDS,
  PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  sameProjectileMotionProblem,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionClarificationTopic,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionStateV1,
  type RoutedProjectileMotionBeatV1,
} from "./projectile-motion";

export const MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS = 8;
export const MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES = 64 * 1024;

export const PROJECTILE_CHOREOGRAPHY_DECLINE_REASONS = [
  "unsupported_intent",
  "no_forward_progress",
  "problem_conflict",
] as const;
export type ProjectileChoreographyDeclineReason =
  (typeof PROJECTILE_CHOREOGRAPHY_DECLINE_REASONS)[number];

export const PROJECTILE_CHOREOGRAPHY_FAILURE_CODES = [
  "semantic_base_mismatch",
  "choreography_capacity_exceeded",
  "choreography_capacity_limit",
  "revision_limit",
  "context_too_large",
  "invalid_visual_act",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
  "choreography_integrity_error",
] as const;
export type ProjectileChoreographyFailureCode =
  (typeof PROJECTILE_CHOREOGRAPHY_FAILURE_CODES)[number];

export const PROJECTILE_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES = [
  "invalid_visual_act",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
] as const satisfies readonly ProjectileChoreographyFailureCode[];

export const PROJECTILE_MOTION_CHECKPOINT_ACTIONS = [
  "advance",
  "clarify",
  "retarget",
] as const;
export type ProjectileMotionCheckpointAction =
  (typeof PROJECTILE_MOTION_CHECKPOINT_ACTIONS)[number];

export const PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS = [
  "blueprint_contract",
  "problem_identity",
  "transition",
  "stable_ids",
  "board_bounds",
  "text_collision",
  "physics_geometry",
  "label_fact",
  "label_layout",
  "visual_style",
  "patch",
  "caption",
  "choreography",
  "timing",
  "viewport",
] as const;
export type ProjectileMotionVerificationObligation =
  (typeof PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS)[number];

export const PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION = 1 as const;
export const PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION =
  "murmur.projectile_motion_choreography.v1" as const;
export const PROJECTILE_MOTION_CHECKPOINT_RECEIPT_ISSUER =
  "projectile_motion_verifier" as const;
export const PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_ISSUER =
  "projectile_motion_compiler" as const;
export const PROJECTILE_MOTION_CHECKPOINT_CANONICALIZATION =
  "murmur-json-v1" as const;
export const PROJECTILE_MOTION_CHECKPOINT_HASH_ALGORITHM = "sha256" as const;

export interface ProjectileMotionCheckpointVerificationReceiptV1 {
  readonly issuer: typeof PROJECTILE_MOTION_CHECKPOINT_RECEIPT_ISSUER;
  readonly componentKind: "projectile_motion";
  readonly componentId: string;
  readonly action: ProjectileMotionCheckpointAction;
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly baseProblemSpecSha256: string | null;
  readonly resultProblemSpecSha256: string;
  readonly operationTargets: readonly string[];
  readonly obligationCodes: readonly ProjectileMotionVerificationObligation[];
  readonly verified: true;
}

export interface ProjectileMotionCheckpointCompilerCertificateBodyV1 {
  readonly v: typeof PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION;
  readonly issuer: typeof PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_ISSUER;
  readonly compilerVersion: typeof PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION;
  readonly canonicalization: typeof PROJECTILE_MOTION_CHECKPOINT_CANONICALIZATION;
  readonly hashAlgorithm: typeof PROJECTILE_MOTION_CHECKPOINT_HASH_ALGORITHM;
  readonly beatId: string;
  readonly routedBeatSha256: string;
  readonly componentKind: "projectile_motion";
  readonly componentId: string;
  readonly action: ProjectileMotionCheckpointAction;
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly baseProblemSpecSha256: string | null;
  readonly resultProblemSpecSha256: string;
  readonly baseLowLevelRevision: number;
  readonly resultLowLevelRevision: number;
  readonly baseSemanticRevision: number;
  readonly resultSemanticRevision: number;
  readonly baseLowLevelSceneSha256: string;
  readonly resultLowLevelSceneSha256: string;
  readonly baseSemanticSceneSha256: string;
  readonly resultSemanticSceneSha256: string;
  readonly patchSha256: string;
  readonly receiptSha256: string;
  readonly presentationCheckpoint: PresentationCheckpointV1;
  readonly choreographySha256: string;
  readonly previousCertificateSha256: string | null;
}

export interface ProjectileMotionCheckpointCompilerCertificateV1 {
  readonly body: ProjectileMotionCheckpointCompilerCertificateBodyV1;
  readonly certificateSha256: string;
}

export interface ProjectileCheckpointSemanticMetadataV1 {
  readonly baseProblemSpec: ProjectileMotionProblemSpecV1 | null;
  readonly resultProblemSpec: ProjectileMotionProblemSpecV1;
  readonly beat: RoutedProjectileMotionBeatV1;
  readonly action: ProjectileMotionCheckpointAction;
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly baseComponent: ProjectileMotionStateV1 | null;
  readonly resultComponent: ProjectileMotionStateV1;
  readonly semanticBaseRevision: number;
  readonly semanticResultRevision: number;
  readonly semanticBaseCertificateSha256: string | null;
  readonly semanticResultCertificateSha256: string;
  readonly receipt: ProjectileMotionCheckpointVerificationReceiptV1;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: ChoreographyPlanV2;
  readonly certificate: ProjectileMotionCheckpointCompilerCertificateV1;
}

export interface ProjectileChoreographySceneCheckpointEventV1 {
  readonly type: "projectile_choreography_scene_checkpoint";
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
  readonly semantic: ProjectileCheckpointSemanticMetadataV1;
}

export interface ProjectileChoreographySceneStreamDeclinedEventV1 {
  readonly type: "projectile_choreography_scene_stream_declined";
  readonly generation: number;
  readonly attempt: number;
  readonly finalRevision: number;
  readonly reasonCode: ProjectileChoreographyDeclineReason;
  readonly message: string;
}

export interface ProjectileChoreographySceneStreamFailedEventV1 {
  readonly type: "projectile_choreography_scene_stream_failed";
  readonly generation: number;
  readonly attempt: number;
  readonly code: ProjectileChoreographyFailureCode;
  readonly message: string;
  readonly lastAcceptedRevision: number;
  readonly retryable: boolean;
}

export interface ProjectileSceneStreamStartedEventV1 {
  readonly type: "scene_stream_started";
  readonly generation: number;
  readonly attempt: number;
  readonly baseRevision: number;
}

export interface ProjectileSceneStreamRepairingEventV1 {
  readonly type: "scene_stream_repairing";
  readonly generation: number;
  readonly fromAttempt: number;
  readonly toAttempt: number;
  readonly lastAcceptedRevision: number;
  readonly message: string;
}

export interface ProjectileSceneStreamCompletedEventV1 {
  readonly type: "scene_stream_completed";
  readonly generation: number;
  readonly finalRevision: number;
  readonly patchCount: number;
  readonly firstPatchMs: number;
  readonly totalMs: number;
  readonly repaired: boolean;
}

export type ProjectileChoreographySceneStreamEventV1 =
  | ProjectileSceneStreamStartedEventV1
  | ProjectileChoreographySceneCheckpointEventV1
  | ProjectileChoreographySceneStreamDeclinedEventV1
  | ProjectileSceneStreamRepairingEventV1
  | ProjectileSceneStreamCompletedEventV1
  | ProjectileChoreographySceneStreamFailedEventV1;

type UnknownRecord = Record<string, unknown>;

function fail(
  message: string,
  code:
    | "invalid_json"
    | "invalid_event"
    | "revision_mismatch"
    | "budget_exceeded" = "invalid_event",
): never {
  throw new LiveSceneProtocolError(
    code,
    `projectile choreography stream ${message}`,
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

function literal<const Value extends string | number | boolean>(
  value: unknown,
  expected: Value,
  field: string,
): Value {
  if (value !== expected) fail(`${field} must equal ${String(expected)}`);
  return expected;
}

function oneOf<const Value extends string>(
  value: unknown,
  allowed: readonly Value[],
  field: string,
): Value {
  if (typeof value !== "string" || !allowed.includes(value as Value)) {
    fail(`${field} has an unsupported value`);
  }
  return value as Value;
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    fail(`${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function milliseconds(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    fail(`${field} must be a finite non-negative number`);
  }
  return value;
}

function boundedString(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value.trim()].length > 512
  ) {
    fail(`${field} must be a non-empty string of at most 512 characters`);
  }
  return value.trim();
}

const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const COMPONENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

function identifier(value: unknown, field: string, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

function nullableDigest(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    fail(`${field} must be null or a lowercase SHA-256 digest`);
  }
  return value;
}

function digest(value: unknown, field: string): string {
  const decoded = nullableDigest(value, field);
  if (decoded === null) fail(`${field} must be a lowercase SHA-256 digest`);
  return decoded;
}

const PROJECTILE_PROBLEM_SHA256 = Object.freeze({
  "20:30": "f2ac3f33e3a48dacdd0e256937330b6f36d39451968aa3882d92f3ce3c436491",
  "20:45": "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa",
  "20:60": "b6859d7baf2204ebc98de07b974b02c39521a27ea1c5b1eb475e3daa6a083b59",
  "25:30": "6014ed0114f009bfd6e50acd99ccdccd42f28a286465d9dc9a2987cf196ca85e",
  "25:45": "e947b64e547b77b7e7899b1f8936138d49d8f5a027b38e9878f4e991a34665c3",
  "25:60": "98add4003547fa75b0f8debc391ab4e8a17f2b8cc656f0f9b61b838f3fe82d6f",
  "30:30": "aa4172502435083f997288d04ccbde34d0fa446430d37b75700a2d4b8b0f5bfc",
  "30:45": "205df15d13df182f0a62c47c7c850888797d11fd52774db7d490d46c2fca80bb",
  "30:60": "3366d188d60fbfe6a523a63f9ee0ece7bee84eed709e056026d33f5b8859e8c3",
} satisfies Readonly<Record<string, string>>);

function problemDigest(problem: ProjectileMotionProblemSpecV1): string {
  const result = PROJECTILE_PROBLEM_SHA256[
    `${problem.speedMps}:${problem.angleDeg}`
  ];
  if (!result) fail("problem specification has no canonical digest");
  return result;
}

function sameNullableProblem(
  left: ProjectileMotionProblemSpecV1 | null,
  right: ProjectileMotionProblemSpecV1 | null,
): boolean {
  return (
    (left === null && right === null) ||
    (left !== null && right !== null && sameProjectileMotionProblem(left, right))
  );
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function expectedAction(
  beat: RoutedProjectileMotionBeatV1,
): readonly [ProjectileMotionCheckpointAction, ProjectileMotionClarificationTopic | null] {
  if (beat.route.intent === "advance") return ["advance", null];
  if (beat.route.intent === "clarify") return ["clarify", beat.route.topic];
  return ["retarget", null];
}

function validateActionAndProblemTransition(
  action: ProjectileMotionCheckpointAction,
  checkpointId: ProjectileMotionCheckpointId,
  clarificationTopic: ProjectileMotionClarificationTopic | null,
  baseProblemSpecSha256: string | null,
  resultProblemSpecSha256: string,
): void {
  if (action === "advance") {
    if (!(PROJECTILE_MOTION_MAIN_CHECKPOINTS as readonly string[]).includes(checkpointId)) {
      fail("advance action requires a main checkpointId");
    }
    if (clarificationTopic !== null) {
      fail("advance action must not bind clarificationTopic");
    }
    if (baseProblemSpecSha256 === null) {
      if (checkpointId !== "setup") {
        fail("baseProblemSpecSha256 may be null only for fresh setup");
      }
    } else if (baseProblemSpecSha256 !== resultProblemSpecSha256) {
      fail("advance action must preserve the problem digest");
    }
    return;
  }
  if (action === "clarify") {
    if (clarificationTopic === null) {
      fail("clarify action requires clarificationTopic");
    }
    if (checkpointId !== PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[clarificationTopic]) {
      fail("clarificationTopic must match checkpointId");
    }
    if (
      baseProblemSpecSha256 === null ||
      baseProblemSpecSha256 !== resultProblemSpecSha256
    ) {
      fail("clarify action must preserve a non-null problem digest");
    }
    return;
  }
  if (checkpointId !== "parameters_retargeted") {
    fail("retarget action requires parameters_retargeted checkpointId");
  }
  if (clarificationTopic !== null) {
    fail("retarget action must not bind clarificationTopic");
  }
  if (
    baseProblemSpecSha256 === null ||
    baseProblemSpecSha256 === resultProblemSpecSha256
  ) {
    fail("retarget action must change a non-null problem digest");
  }
}

function decodeReceipt(
  value: unknown,
): ProjectileMotionCheckpointVerificationReceiptV1 {
  const input = record(value, "verification receipt");
  exactKeys(
    input,
    [
      "issuer",
      "componentKind",
      "componentId",
      "action",
      "checkpointId",
      "clarificationTopic",
      "baseProblemSpecSha256",
      "resultProblemSpecSha256",
      "operationTargets",
      "obligationCodes",
      "verified",
    ],
    "verification receipt",
  );
  if (!Array.isArray(input.operationTargets)) {
    fail("verification receipt operationTargets must be an array");
  }
  if (
    input.operationTargets.length === 0 ||
    input.operationTargets.length > LIVE_SCENE_MAX_PATCH_OPERATIONS
  ) {
    fail(
      `verification receipt operationTargets must contain 1 to ${LIVE_SCENE_MAX_PATCH_OPERATIONS} entries`,
      "budget_exceeded",
    );
  }
  const operationTargets = input.operationTargets.map((target, index) =>
    identifier(
      target,
      `verification receipt operationTargets[${index}]`,
      CONTRACT_ID_PATTERN,
    ),
  );
  if (new Set(operationTargets).size !== operationTargets.length) {
    fail("verification receipt operationTargets must be unique");
  }
  if (!Array.isArray(input.obligationCodes)) {
    fail("verification receipt obligationCodes must be an array");
  }
  const obligationCodes = input.obligationCodes.map((code) =>
    oneOf(
      code,
      PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
      "verification receipt obligationCode",
    ),
  );
  if (
    obligationCodes.length !== PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS.length ||
    obligationCodes.some(
      (code, index) => code !== PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS[index],
    )
  ) {
    fail("verification receipt must bind the complete obligation suite");
  }
  const action = oneOf(
    input.action,
    PROJECTILE_MOTION_CHECKPOINT_ACTIONS,
    "verification receipt action",
  );
  const checkpointId = oneOf(
    input.checkpointId,
    PROJECTILE_MOTION_CHECKPOINT_IDS,
    "verification receipt checkpointId",
  );
  const clarificationTopic =
    input.clarificationTopic === null
      ? null
      : oneOf(
          input.clarificationTopic,
          PROJECTILE_MOTION_CLARIFICATION_TOPICS,
          "verification receipt clarificationTopic",
        );
  const baseProblemSpecSha256 = nullableDigest(
    input.baseProblemSpecSha256,
    "verification receipt baseProblemSpecSha256",
  );
  const resultProblemSpecSha256 = digest(
    input.resultProblemSpecSha256,
    "verification receipt resultProblemSpecSha256",
  );
  validateActionAndProblemTransition(
    action,
    checkpointId,
    clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
  );
  return Object.freeze({
    issuer: literal(
      input.issuer,
      PROJECTILE_MOTION_CHECKPOINT_RECEIPT_ISSUER,
      "verification receipt issuer",
    ),
    componentKind: literal(
      input.componentKind,
      "projectile_motion",
      "verification receipt componentKind",
    ),
    componentId: identifier(
      input.componentId,
      "verification receipt componentId",
      COMPONENT_ID_PATTERN,
    ),
    action,
    checkpointId,
    clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
    operationTargets: Object.freeze(operationTargets),
    obligationCodes: Object.freeze(obligationCodes),
    verified: literal(input.verified, true, "verification receipt verified"),
  });
}

function decodeCertificateBody(
  value: unknown,
): ProjectileMotionCheckpointCompilerCertificateBodyV1 {
  const input = record(value, "certificate body");
  exactKeys(
    input,
    [
      "v",
      "issuer",
      "compilerVersion",
      "canonicalization",
      "hashAlgorithm",
      "beatId",
      "routedBeatSha256",
      "componentKind",
      "componentId",
      "action",
      "checkpointId",
      "clarificationTopic",
      "baseProblemSpecSha256",
      "resultProblemSpecSha256",
      "baseLowLevelRevision",
      "resultLowLevelRevision",
      "baseSemanticRevision",
      "resultSemanticRevision",
      "baseLowLevelSceneSha256",
      "resultLowLevelSceneSha256",
      "baseSemanticSceneSha256",
      "resultSemanticSceneSha256",
      "patchSha256",
      "receiptSha256",
      "presentationCheckpoint",
      "choreographySha256",
      "previousCertificateSha256",
    ],
    "certificate body",
  );
  const action = oneOf(
    input.action,
    PROJECTILE_MOTION_CHECKPOINT_ACTIONS,
    "certificate body action",
  );
  const checkpointId = oneOf(
    input.checkpointId,
    PROJECTILE_MOTION_CHECKPOINT_IDS,
    "certificate body checkpointId",
  );
  const clarificationTopic =
    input.clarificationTopic === null
      ? null
      : oneOf(
          input.clarificationTopic,
          PROJECTILE_MOTION_CLARIFICATION_TOPICS,
          "certificate body clarificationTopic",
        );
  const baseProblemSpecSha256 = nullableDigest(
    input.baseProblemSpecSha256,
    "certificate body baseProblemSpecSha256",
  );
  const resultProblemSpecSha256 = digest(
    input.resultProblemSpecSha256,
    "certificate body resultProblemSpecSha256",
  );
  validateActionAndProblemTransition(
    action,
    checkpointId,
    clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
  );
  const baseLowLevelRevision = integer(
    input.baseLowLevelRevision,
    "certificate body baseLowLevelRevision",
    0,
  );
  const resultLowLevelRevision = integer(
    input.resultLowLevelRevision,
    "certificate body resultLowLevelRevision",
    1,
  );
  const baseSemanticRevision = integer(
    input.baseSemanticRevision,
    "certificate body baseSemanticRevision",
    0,
  );
  const resultSemanticRevision = integer(
    input.resultSemanticRevision,
    "certificate body resultSemanticRevision",
    1,
  );
  if (resultLowLevelRevision !== baseLowLevelRevision + 1) {
    fail("certificate low-level revisions must advance exactly once", "revision_mismatch");
  }
  if (resultSemanticRevision !== baseSemanticRevision + 1) {
    fail("certificate semantic revisions must advance exactly once", "revision_mismatch");
  }
  if (
    baseLowLevelRevision !== baseSemanticRevision ||
    resultLowLevelRevision !== resultSemanticRevision
  ) {
    fail("certificate low-level and semantic revisions must match", "revision_mismatch");
  }
  const presentationCheckpoint = decodePresentationCheckpointV1(
    input.presentationCheckpoint,
  );
  if (presentationCheckpoint.checkpointId !== checkpointId) {
    fail("certificate presentation checkpointId must match checkpointId");
  }
  return Object.freeze({
    v: literal(
      input.v,
      PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION,
      "certificate body v",
    ),
    issuer: literal(
      input.issuer,
      PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_ISSUER,
      "certificate body issuer",
    ),
    compilerVersion: literal(
      input.compilerVersion,
      PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
      "certificate body compilerVersion",
    ),
    canonicalization: literal(
      input.canonicalization,
      PROJECTILE_MOTION_CHECKPOINT_CANONICALIZATION,
      "certificate body canonicalization",
    ),
    hashAlgorithm: literal(
      input.hashAlgorithm,
      PROJECTILE_MOTION_CHECKPOINT_HASH_ALGORITHM,
      "certificate body hashAlgorithm",
    ),
    beatId: identifier(input.beatId, "certificate body beatId", CONTRACT_ID_PATTERN),
    routedBeatSha256: digest(
      input.routedBeatSha256,
      "certificate body routedBeatSha256",
    ),
    componentKind: literal(
      input.componentKind,
      "projectile_motion",
      "certificate body componentKind",
    ),
    componentId: identifier(
      input.componentId,
      "certificate body componentId",
      COMPONENT_ID_PATTERN,
    ),
    action,
    checkpointId,
    clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
    baseLowLevelRevision,
    resultLowLevelRevision,
    baseSemanticRevision,
    resultSemanticRevision,
    baseLowLevelSceneSha256: digest(
      input.baseLowLevelSceneSha256,
      "certificate body baseLowLevelSceneSha256",
    ),
    resultLowLevelSceneSha256: digest(
      input.resultLowLevelSceneSha256,
      "certificate body resultLowLevelSceneSha256",
    ),
    baseSemanticSceneSha256: digest(
      input.baseSemanticSceneSha256,
      "certificate body baseSemanticSceneSha256",
    ),
    resultSemanticSceneSha256: digest(
      input.resultSemanticSceneSha256,
      "certificate body resultSemanticSceneSha256",
    ),
    patchSha256: digest(input.patchSha256, "certificate body patchSha256"),
    receiptSha256: digest(
      input.receiptSha256,
      "certificate body receiptSha256",
    ),
    presentationCheckpoint,
    choreographySha256: digest(
      input.choreographySha256,
      "certificate body choreographySha256",
    ),
    previousCertificateSha256: nullableDigest(
      input.previousCertificateSha256,
      "certificate body previousCertificateSha256",
    ),
  });
}

function decodeCertificate(
  value: unknown,
): ProjectileMotionCheckpointCompilerCertificateV1 {
  const input = record(value, "certificate");
  exactKeys(input, ["body", "certificateSha256"], "certificate");
  return Object.freeze({
    body: decodeCertificateBody(input.body),
    certificateSha256: digest(
      input.certificateSha256,
      "certificate certificateSha256",
    ),
  });
}

function operationTarget(
  operation: ScenePatchDraft["operations"][number],
): string {
  return operation.op === "put" ? operation.node.id : operation.id;
}

function validateComponentTransition(
  action: ProjectileMotionCheckpointAction,
  checkpointId: ProjectileMotionCheckpointId,
  clarificationTopic: ProjectileMotionClarificationTopic | null,
  baseComponent: ProjectileMotionStateV1 | null,
  resultComponent: ProjectileMotionStateV1,
): void {
  if (baseComponent === null) {
    if (
      action !== "advance" ||
      checkpointId !== "setup" ||
      resultComponent.lastMainCheckpoint !== "setup" ||
      resultComponent.clarifiedTopics.length !== 0 ||
      resultComponent.activeClarification !== null
    ) {
      fail("only a fresh setup advance may omit baseComponent");
    }
    return;
  }
  if (resultComponent.id !== baseComponent.id) {
    fail("resultComponent id must preserve baseComponent id");
  }
  if (action === "advance") {
    const next = nextProjectileMotionMainCheckpoint(
      baseComponent.lastMainCheckpoint,
    );
    if (
      next === null ||
      checkpointId !== next ||
      resultComponent.lastMainCheckpoint !== next
    ) {
      fail("advance must settle the next main checkpoint");
    }
    if (
      resultComponent.clarifiedTopics.length !==
        baseComponent.clarifiedTopics.length ||
      resultComponent.clarifiedTopics.some(
        (topic, index) => topic !== baseComponent.clarifiedTopics[index],
      )
    ) {
      fail("advance must preserve the clarification ledger");
    }
    if (resultComponent.activeClarification !== null) {
      fail("advance must clear activeClarification");
    }
    return;
  }
  if (action === "clarify") {
    if (
      clarificationTopic === null ||
      baseComponent.clarifiedTopics.includes(clarificationTopic)
    ) {
      fail("clarificationTopic must be a new one-shot topic");
    }
    const expectedTopics = PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter(
      (topic) =>
        baseComponent.clarifiedTopics.includes(topic) ||
        topic === clarificationTopic,
    );
    if (
      resultComponent.lastMainCheckpoint !== baseComponent.lastMainCheckpoint ||
      resultComponent.clarifiedTopics.length !== expectedTopics.length ||
      resultComponent.clarifiedTopics.some(
        (topic, index) => topic !== expectedTopics[index],
      ) ||
      resultComponent.activeClarification !== clarificationTopic
    ) {
      fail("clarification must preserve the frontier and append its canonical topic");
    }
    return;
  }
  if (
    checkpointId !== "parameters_retargeted" ||
    baseComponent.lastMainCheckpoint === null ||
    resultComponent.lastMainCheckpoint !== baseComponent.lastMainCheckpoint ||
    resultComponent.clarifiedTopics.length !==
      baseComponent.clarifiedTopics.length ||
    resultComponent.clarifiedTopics.some(
      (topic, index) => topic !== baseComponent.clarifiedTopics[index],
    ) ||
    resultComponent.activeClarification !== baseComponent.activeClarification
  ) {
    fail("retarget must preserve the settled frontier and clarification state");
  }
}

interface DecodedSemanticMetadata {
  readonly patch: ScenePatchDraft;
  readonly semantic: ProjectileCheckpointSemanticMetadataV1;
}

/**
 * Decode all browser-verifiable joins in one certified checkpoint.
 *
 * The nine problem digests are re-derived from the closed problem domain.
 * Remaining artifact digests are opaque backend-issued commitments here: the
 * synchronous browser trust boundary validates their syntax, revision/chain
 * placement, and every cleartext equality without duplicating backend hashing.
 */
function decodeSemanticMetadata(
  value: unknown,
  patchValue: unknown,
): DecodedSemanticMetadata {
  const input = record(value, "semantic metadata");
  exactKeys(
    input,
    [
      "baseProblemSpec",
      "resultProblemSpec",
      "beat",
      "action",
      "checkpointId",
      "clarificationTopic",
      "baseComponent",
      "resultComponent",
      "semanticBaseRevision",
      "semanticResultRevision",
      "semanticBaseCertificateSha256",
      "semanticResultCertificateSha256",
      "receipt",
      "presentation",
      "choreography",
      "certificate",
    ],
    "semantic metadata",
  );
  const baseProblemSpec =
    input.baseProblemSpec === null
      ? null
      : decodeProjectileMotionProblemSpecV1(input.baseProblemSpec);
  const resultProblemSpec = decodeProjectileMotionProblemSpecV1(
    input.resultProblemSpec,
  );
  const beat = decodeRoutedProjectileMotionBeatV1(input.beat);
  const action = oneOf(
    input.action,
    PROJECTILE_MOTION_CHECKPOINT_ACTIONS,
    "semantic metadata action",
  );
  const checkpointId = oneOf(
    input.checkpointId,
    PROJECTILE_MOTION_CHECKPOINT_IDS,
    "semantic metadata checkpointId",
  );
  const clarificationTopic =
    input.clarificationTopic === null
      ? null
      : oneOf(
          input.clarificationTopic,
          PROJECTILE_MOTION_CLARIFICATION_TOPICS,
          "semantic metadata clarificationTopic",
        );
  const baseComponent =
    input.baseComponent === null
      ? null
      : decodeProjectileMotionStateV1(input.baseComponent);
  const resultComponent = decodeProjectileMotionStateV1(input.resultComponent);
  const semanticBaseRevision = integer(
    input.semanticBaseRevision,
    "semantic metadata semanticBaseRevision",
    0,
  );
  const semanticResultRevision = integer(
    input.semanticResultRevision,
    "semantic metadata semanticResultRevision",
    1,
  );
  if (semanticResultRevision !== semanticBaseRevision + 1) {
    fail("semantic metadata revisions must advance exactly once", "revision_mismatch");
  }
  const semanticBaseCertificateSha256 = nullableDigest(
    input.semanticBaseCertificateSha256,
    "semantic metadata semanticBaseCertificateSha256",
  );
  const semanticResultCertificateSha256 = digest(
    input.semanticResultCertificateSha256,
    "semantic metadata semanticResultCertificateSha256",
  );
  const patch = decodeScenePatchDraft(patchValue);
  const receipt = decodeReceipt(input.receipt);
  const presentation = decodePresentationCheckpointV1(input.presentation);
  const choreography = decodeChoreographyPlanV2(input.choreography);
  const certificate = decodeCertificate(input.certificate);

  const [expectedActionValue, expectedTopic] = expectedAction(beat);
  if (action !== expectedActionValue || clarificationTopic !== expectedTopic) {
    fail("action and clarificationTopic must match routed beat");
  }
  if (
    !sameProjectileMotionProblem(resultProblemSpec, resultComponent.problemSpec) ||
    !sameProjectileMotionProblem(resultProblemSpec, beat.resultProblemSpec)
  ) {
    fail("resultProblemSpec must match resultComponent and routed beat");
  }
  const componentBaseProblem =
    baseComponent === null ? null : baseComponent.problemSpec;
  if (!sameNullableProblem(baseProblemSpec, componentBaseProblem)) {
    fail("baseProblemSpec must match baseComponent problemSpec");
  }
  let expectedBeatBase = beat.baseProblemSpec;
  if (expectedBeatBase === null && checkpointId !== "setup") {
    expectedBeatBase = beat.resultProblemSpec;
  }
  if (!sameNullableProblem(baseProblemSpec, expectedBeatBase)) {
    fail("baseProblemSpec must match routed checkpoint transition");
  }
  if (
    (baseComponent !== null && baseComponent.id !== beat.componentId) ||
    resultComponent.id !== beat.componentId
  ) {
    fail("component ids must match routed beat componentId");
  }
  validateComponentTransition(
    action,
    checkpointId,
    clarificationTopic,
    baseComponent,
    resultComponent,
  );

  const expectedBaseProblemDigest =
    baseProblemSpec === null ? null : problemDigest(baseProblemSpec);
  const expectedResultProblemDigest = problemDigest(resultProblemSpec);
  if (
    receipt.baseProblemSpecSha256 !== expectedBaseProblemDigest ||
    receipt.resultProblemSpecSha256 !== expectedResultProblemDigest
  ) {
    fail("receipt problem hashes must match cleartext problem specifications");
  }
  const body = certificate.body;
  if (
    body.baseProblemSpecSha256 !== expectedBaseProblemDigest ||
    body.resultProblemSpecSha256 !== expectedResultProblemDigest
  ) {
    fail("certificate problem hashes must match cleartext problem specifications");
  }

  if (patch.patchId !== `${beat.componentId}__cp_${checkpointId}`) {
    fail("patchId must match its deterministic checkpoint identity");
  }
  if (patch.narration !== presentation.checkpointNarration) {
    fail("patch narration must match checkpointNarration");
  }
  if (presentation.checkpointId !== checkpointId) {
    fail("presentation checkpointId must match checkpointId");
  }
  const operationTargets = patch.operations.map(operationTarget);
  if (
    operationTargets.length !== receipt.operationTargets.length ||
    operationTargets.some(
      (target, index) => target !== receipt.operationTargets[index],
    )
  ) {
    fail("receipt operationTargets must match ordered patch targets");
  }
  if (
    receipt.componentKind !== beat.componentKind ||
    receipt.componentId !== beat.componentId ||
    receipt.action !== action ||
    receipt.checkpointId !== checkpointId ||
    receipt.clarificationTopic !== clarificationTopic
  ) {
    fail("receipt identity must match routed checkpoint");
  }
  if (
    body.beatId !== beat.beatId ||
    body.componentKind !== beat.componentKind ||
    body.componentId !== beat.componentId ||
    body.action !== action ||
    body.checkpointId !== checkpointId ||
    body.clarificationTopic !== clarificationTopic
  ) {
    fail("certificate identity must match routed checkpoint");
  }
  if (
    body.baseProblemSpecSha256 !== receipt.baseProblemSpecSha256 ||
    body.resultProblemSpecSha256 !== receipt.resultProblemSpecSha256
  ) {
    fail("certificate problem commitments must match receipt");
  }
  if (!sameJson(body.presentationCheckpoint, presentation)) {
    fail("certificate presentationCheckpoint must match presentation");
  }
  if (
    body.baseSemanticRevision !== semanticBaseRevision ||
    body.resultSemanticRevision !== semanticResultRevision
  ) {
    fail("certificate semantic revisions must match metadata", "revision_mismatch");
  }
  if (body.previousCertificateSha256 !== semanticBaseCertificateSha256) {
    fail("certificate previous hash must match semantic base chain", "revision_mismatch");
  }
  if (certificate.certificateSha256 !== semanticResultCertificateSha256) {
    fail("semantic result chain must match certificate hash", "revision_mismatch");
  }

  return Object.freeze({
    patch,
    semantic: Object.freeze({
      baseProblemSpec,
      resultProblemSpec,
      beat,
      action,
      checkpointId,
      clarificationTopic,
      baseComponent,
      resultComponent,
      semanticBaseRevision,
      semanticResultRevision,
      semanticBaseCertificateSha256,
      semanticResultCertificateSha256,
      receipt,
      presentation,
      choreography,
      certificate,
    }),
  });
}

export function decodeProjectileChoreographySceneCheckpointEventV1(
  value: unknown,
): ProjectileChoreographySceneCheckpointEventV1 {
  const input = record(value, "checkpoint event");
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
      "semantic",
    ],
    "checkpoint event",
  );
  literal(
    input.type,
    "projectile_choreography_scene_checkpoint",
    "checkpoint event type",
  );
  const baseRevision = integer(input.baseRevision, "checkpoint baseRevision", 0);
  const resultRevision = integer(
    input.resultRevision,
    "checkpoint resultRevision",
    1,
  );
  if (resultRevision !== baseRevision + 1) {
    fail("checkpoint revisions must advance exactly once", "revision_mismatch");
  }
  const decoded = decodeSemanticMetadata(input.semantic, input.patch);
  const body = decoded.semantic.certificate.body;
  if (
    decoded.semantic.semanticBaseRevision !== baseRevision ||
    decoded.semantic.semanticResultRevision !== resultRevision ||
    body.baseLowLevelRevision !== baseRevision ||
    body.resultLowLevelRevision !== resultRevision
  ) {
    fail("event, semantic, and certificate revisions must match", "revision_mismatch");
  }
  return Object.freeze({
    type: "projectile_choreography_scene_checkpoint",
    generation: integer(input.generation, "checkpoint generation", 1),
    attempt: integer(input.attempt, "checkpoint attempt", 1, 2),
    sequence: integer(
      input.sequence,
      "checkpoint sequence",
      1,
      MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
    ),
    baseRevision,
    resultRevision,
    patch: decoded.patch,
    semantic: decoded.semantic,
  });
}

function decodeStarted(input: UnknownRecord): ProjectileSceneStreamStartedEventV1 {
  exactKeys(input, ["type", "generation", "attempt", "baseRevision"], "started event");
  return Object.freeze({
    type: "scene_stream_started",
    generation: integer(input.generation, "started generation", 1),
    attempt: integer(input.attempt, "started attempt", 1, 2),
    baseRevision: integer(input.baseRevision, "started baseRevision", 0),
  });
}

function decodeRepairing(
  input: UnknownRecord,
): ProjectileSceneStreamRepairingEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "fromAttempt",
      "toAttempt",
      "lastAcceptedRevision",
      "message",
    ],
    "repairing event",
  );
  const fromAttempt = integer(input.fromAttempt, "repairing fromAttempt", 1, 2);
  const toAttempt = integer(input.toAttempt, "repairing toAttempt", 1, 2);
  if (toAttempt !== fromAttempt + 1) {
    fail("repairing toAttempt must follow fromAttempt");
  }
  return Object.freeze({
    type: "scene_stream_repairing",
    generation: integer(input.generation, "repairing generation", 1),
    fromAttempt,
    toAttempt,
    lastAcceptedRevision: integer(
      input.lastAcceptedRevision,
      "repairing lastAcceptedRevision",
      0,
    ),
    message: boundedString(input.message, "repairing message"),
  });
}

function decodeCompleted(
  input: UnknownRecord,
): ProjectileSceneStreamCompletedEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "finalRevision",
      "patchCount",
      "firstPatchMs",
      "totalMs",
      "repaired",
    ],
    "completed event",
  );
  const firstPatchMs = milliseconds(input.firstPatchMs, "completed firstPatchMs");
  const totalMs = milliseconds(input.totalMs, "completed totalMs");
  if (totalMs < firstPatchMs) {
    fail("completed totalMs must not precede firstPatchMs");
  }
  if (typeof input.repaired !== "boolean") {
    fail("completed repaired must be a boolean");
  }
  return Object.freeze({
    type: "scene_stream_completed",
    generation: integer(input.generation, "completed generation", 1),
    finalRevision: integer(input.finalRevision, "completed finalRevision", 1),
    patchCount: integer(
      input.patchCount,
      "completed patchCount",
      1,
      MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
    ),
    firstPatchMs,
    totalMs,
    repaired: input.repaired,
  });
}

function decodeDeclined(
  input: UnknownRecord,
): ProjectileChoreographySceneStreamDeclinedEventV1 {
  exactKeys(
    input,
    ["type", "generation", "attempt", "finalRevision", "reasonCode", "message"],
    "declined event",
  );
  return Object.freeze({
    type: "projectile_choreography_scene_stream_declined",
    generation: integer(input.generation, "declined generation", 1),
    attempt: integer(input.attempt, "declined attempt", 1, 2),
    finalRevision: integer(input.finalRevision, "declined finalRevision", 0),
    reasonCode: oneOf(
      input.reasonCode,
      PROJECTILE_CHOREOGRAPHY_DECLINE_REASONS,
      "declined reasonCode",
    ),
    message: boundedString(input.message, "declined message"),
  });
}

function decodeFailed(
  input: UnknownRecord,
): ProjectileChoreographySceneStreamFailedEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "code",
      "message",
      "lastAcceptedRevision",
      "retryable",
    ],
    "failed event",
  );
  const code = oneOf(
    input.code,
    PROJECTILE_CHOREOGRAPHY_FAILURE_CODES,
    "failed code",
  );
  if (typeof input.retryable !== "boolean") {
    fail("failed retryable must be a boolean");
  }
  const expectedRetryable = (
    PROJECTILE_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES as readonly string[]
  ).includes(code);
  if (input.retryable !== expectedRetryable) {
    fail(`failed retryable must be ${String(expectedRetryable)} for code ${code}`);
  }
  return Object.freeze({
    type: "projectile_choreography_scene_stream_failed",
    generation: integer(input.generation, "failed generation", 1),
    attempt: integer(input.attempt, "failed attempt", 1, 2),
    code,
    message: boundedString(input.message, "failed message"),
    lastAcceptedRevision: integer(
      input.lastAcceptedRevision,
      "failed lastAcceptedRevision",
      0,
    ),
    retryable: input.retryable,
  });
}

/** Decode one complete V1 event without accepting earlier protocol discriminators. */
export function decodeProjectileChoreographySceneStreamEventV1(
  value: unknown,
): ProjectileChoreographySceneStreamEventV1 {
  const input = record(value, "scene stream event");
  switch (input.type) {
    case "scene_stream_started":
      return decodeStarted(input);
    case "projectile_choreography_scene_checkpoint":
      return decodeProjectileChoreographySceneCheckpointEventV1(input);
    case "projectile_choreography_scene_stream_declined":
      return decodeDeclined(input);
    case "scene_stream_repairing":
      return decodeRepairing(input);
    case "scene_stream_completed":
      return decodeCompleted(input);
    case "projectile_choreography_scene_stream_failed":
      return decodeFailed(input);
    default:
      return fail("event type is unsupported");
  }
}

/** Parse one JSON payload after the bounded SSE layer has extracted its data field. */
export function parseProjectileChoreographySceneStreamEventV1(
  data: string,
): ProjectileChoreographySceneStreamEventV1 {
  const wireBytes = new TextEncoder().encode(`data: ${data}\n\n`).byteLength;
  if (wireBytes > MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES) {
    return fail(
      `SSE event exceeds ${MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES} bytes`,
      "budget_exceeded",
    );
  }
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return fail("SSE data must be valid JSON", "invalid_json");
  }
  return decodeProjectileChoreographySceneStreamEventV1(value);
}
