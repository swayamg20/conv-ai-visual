import {
  decodeChoreographyPlanV2,
  decodePresentationCheckpointV1,
  type ChoreographyPlanV2,
  type PresentationCheckpointV1,
} from "./choreography";
import {
  applyLiveScenePatch,
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
  type ScenePatchDraft,
} from "./patch";
import {
  MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS,
  MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
  PROJECTILE_STORYBOARD_COMPONENT_ID,
  STORYBOARD_ABSTAIN_REASON_CODES,
  STORYBOARD_CLAIM_IDS,
  STORYBOARD_CONCEPT_IDS,
  STORYBOARD_EVIDENCE_IDS,
  STORYBOARD_TRAJECTORY_IDS,
  decodePairedProjectileComparisonSpecV1,
  decodeProjectileStoryboardSemanticSceneStateV1,
  decodeSemanticStoryboardRecordV1,
  type AcceptedSemanticStoryboardRecordV1,
  type PairedProjectileComparisonSpecV1,
  type ProjectileStoryboardSemanticSceneStateV1,
  type StoryboardAbstainReasonCode,
  type StoryboardClaimId,
  type StoryboardConceptId,
  type StoryboardEvidenceId,
  type StoryboardTrajectoryId,
} from "./semantic-storyboard";
import { createSceneState } from "./state";
import type { SceneNode, SceneState } from "./types";

export const MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS =
  MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN;
export const MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES = 64 * 1024;

export const SEMANTIC_STORYBOARD_COMPLETION_REASONS = [
  "anchor",
  "model_stop",
  "accepted_prefix",
] as const;
export type SemanticStoryboardCompletionReason =
  (typeof SEMANTIC_STORYBOARD_COMPLETION_REASONS)[number];

export const SEMANTIC_STORYBOARD_ACCEPTED_PREFIX_CAUSES = [
  "invalid_model_stream",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
  "capacity_limit",
  "revision_limit",
  "internal_integrity_error",
] as const;
export type SemanticStoryboardAcceptedPrefixCause =
  (typeof SEMANTIC_STORYBOARD_ACCEPTED_PREFIX_CAUSES)[number];

export const SEMANTIC_STORYBOARD_FAILURE_CODES = [
  "semantic_base_mismatch",
  "storyboard_capacity_exhausted",
  "revision_limit",
  "context_too_large",
  "invalid_model_stream",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
  "storyboard_integrity_error",
] as const;
export type SemanticStoryboardFailureCode =
  (typeof SEMANTIC_STORYBOARD_FAILURE_CODES)[number];

export const SEMANTIC_STORYBOARD_RETRYABLE_FAILURE_CODES = [
  "invalid_model_stream",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
] as const satisfies readonly SemanticStoryboardFailureCode[];

export const SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS = [
  "blueprint_contract",
  "problem_identity",
  "semantic_transition",
  "effect_closure",
  "program_hash",
  "certificate_chain",
  "stable_ids",
  "board_bounds",
  "lane_layout",
  "physics_geometry",
  "label_fact",
  "visual_style",
  "patch",
  "caption",
  "choreography",
  "timing",
  "viewport",
] as const;
export type SemanticStoryboardVerificationObligation =
  (typeof SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS)[number];

export const SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS =
  Object.freeze(
    SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS.filter(
      (code) => code !== "effect_closure" && code !== "certificate_chain",
    ),
  );

export const SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION =
  "murmur.semantic_storyboard_choreography.v1" as const;
export const SEMANTIC_STORYBOARD_CATALOG_VERSION =
  "projectile-comparison-catalog-v1" as const;
export const SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID =
  "storyboard-anchor" as const;

export type SemanticStoryboardCheckpointOrigin = "anchor" | "model_record";

export interface StoryboardSemanticEffectClosureV1 {
  readonly v: 1;
  readonly conceptIds: readonly StoryboardConceptId[];
  readonly trajectoryIds: readonly StoryboardTrajectoryId[];
  readonly claimIds: readonly StoryboardClaimId[];
  readonly producedEvidenceIds: readonly StoryboardEvidenceId[];
  readonly consumedEvidenceIds: readonly StoryboardEvidenceId[];
}

export interface RoutedSemanticStoryboardBeatV1 {
  readonly v: 1;
  readonly beatId: string;
  readonly checkpointId: string;
  readonly componentKind: "projectile_comparison_storyboard";
  readonly componentId: typeof PROJECTILE_STORYBOARD_COMPONENT_ID;
  readonly ordinal: number;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly record: AcceptedSemanticStoryboardRecordV1;
  readonly semanticEffect: StoryboardSemanticEffectClosureV1;
  readonly baseProgramSha256: string;
  readonly resultProgramSha256: string;
  readonly previousCertificateSha256: string;
}

export interface SemanticStoryboardCheckpointVerificationReceiptV1 {
  readonly issuer: "semantic_storyboard_verifier";
  readonly checkpointOrigin: SemanticStoryboardCheckpointOrigin;
  readonly componentKind: "projectile_comparison_storyboard";
  readonly componentId: typeof PROJECTILE_STORYBOARD_COMPONENT_ID;
  readonly checkpointId: string;
  readonly problemSpecSha256: string;
  readonly routedBeatSha256: string | null;
  readonly baseProgramSha256: string;
  readonly resultProgramSha256: string;
  readonly semanticEffect: StoryboardSemanticEffectClosureV1 | null;
  readonly operationTargets: readonly string[];
  readonly obligationCodes: readonly SemanticStoryboardVerificationObligation[];
  readonly verified: true;
}

export interface SemanticStoryboardCheckpointCompilerCertificateBodyV1 {
  readonly v: 1;
  readonly issuer: "semantic_storyboard_compiler";
  readonly compilerVersion: typeof SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION;
  readonly canonicalization: "murmur-json-v1";
  readonly hashAlgorithm: "sha256";
  readonly checkpointOrigin: SemanticStoryboardCheckpointOrigin;
  readonly catalogVersion: typeof SEMANTIC_STORYBOARD_CATALOG_VERSION;
  readonly beatId: string | null;
  readonly routedBeatSha256: string | null;
  readonly recordSha256: string | null;
  readonly componentKind: "projectile_comparison_storyboard";
  readonly componentId: typeof PROJECTILE_STORYBOARD_COMPONENT_ID;
  readonly checkpointId: string;
  readonly problemSpecSha256: string;
  readonly baseProgramSha256: string;
  readonly resultProgramSha256: string;
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

export interface SemanticStoryboardCheckpointCompilerCertificateV1 {
  readonly body: SemanticStoryboardCheckpointCompilerCertificateBodyV1;
  readonly certificateSha256: string;
}

export interface CompiledSemanticStoryboardCheckpointV1 {
  readonly checkpointOrigin: SemanticStoryboardCheckpointOrigin;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly beat: RoutedSemanticStoryboardBeatV1 | null;
  readonly checkpointId: string;
  readonly patch: ScenePatchDraft;
  readonly receipt: SemanticStoryboardCheckpointVerificationReceiptV1;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: ChoreographyPlanV2;
  readonly certificate: SemanticStoryboardCheckpointCompilerCertificateV1;
}

export interface ValidatedSemanticStoryboardTransitionV1 {
  readonly baseScene: SceneState;
  readonly resultScene: SceneState;
  readonly baseSemanticScene: ProjectileStoryboardSemanticSceneStateV1;
  readonly resultSemanticScene: ProjectileStoryboardSemanticSceneStateV1;
  readonly checkpoint: CompiledSemanticStoryboardCheckpointV1;
}

export interface SemanticStoryboardSceneStreamStartedEventV1 {
  readonly type: "semantic_storyboard_scene_stream_started";
  readonly generation: number;
  readonly attempt: 1;
  readonly baseRevision: number;
}

export interface SemanticStoryboardSceneCheckpointEventV1 {
  readonly type: "semantic_storyboard_scene_checkpoint";
  readonly generation: number;
  readonly attempt: 1;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
  readonly transition: ValidatedSemanticStoryboardTransitionV1;
}

export interface SemanticStoryboardSceneStreamCompletedEventV1 {
  readonly type: "semantic_storyboard_scene_stream_completed";
  readonly generation: number;
  readonly attempt: 1;
  readonly baseRevision: number;
  readonly finalRevision: number;
  readonly checkpointCount: number;
  readonly firstCheckpointMs: number;
  readonly totalMs: number;
  readonly reasonCode: SemanticStoryboardCompletionReason;
  readonly acceptedPrefixCause: SemanticStoryboardAcceptedPrefixCause | null;
}

export interface SemanticStoryboardSceneStreamDeclinedEventV1 {
  readonly type: "semantic_storyboard_scene_stream_declined";
  readonly generation: number;
  readonly attempt: 1;
  readonly baseRevision: number;
  readonly finalRevision: number;
  readonly reasonCode: StoryboardAbstainReasonCode;
  readonly message: string;
}

export interface SemanticStoryboardSceneStreamFailedEventV1 {
  readonly type: "semantic_storyboard_scene_stream_failed";
  readonly generation: number;
  readonly attempt: 1;
  readonly baseRevision: number;
  readonly code: SemanticStoryboardFailureCode;
  readonly message: string;
  readonly lastAcceptedRevision: number;
  readonly retryable: boolean;
}

export type SemanticStoryboardSceneStreamEventV1 =
  | SemanticStoryboardSceneStreamStartedEventV1
  | SemanticStoryboardSceneCheckpointEventV1
  | SemanticStoryboardSceneStreamCompletedEventV1
  | SemanticStoryboardSceneStreamDeclinedEventV1
  | SemanticStoryboardSceneStreamFailedEventV1;

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
    `semantic storyboard stream ${message}`,
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
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const PAIRED_PROBLEM_SHA256: Readonly<Record<string, string>> = Object.freeze({
  "20:30:45":
    "059069abd6bf0a559b1b691bb0d34de112d06e47fb2d9102d27fc6471fc172a0",
  "20:30:60":
    "2ce3cc570cdddf8d909f130c783662c6b7e621499e85845b29332c8529636499",
  "20:45:60":
    "0702e00e554c138d9b38ed28461426ee95aa26a4660d1f7c39c365191bd3d383",
  "25:30:45":
    "6687570ac0f8f0ea62a09686960bd93011e08705499cd3d316af4f534997d420",
  "25:30:60":
    "ca63ae343a03875e9e2ccdc0ab5348f7806d49b26fca6969b033473ef8c10ad5",
  "25:45:60":
    "8f5b154a7ad6593012c7fd2835a66037b9f07d8f4e024db98df4b94c06c9f70a",
  "30:30:45":
    "87a4e8436d25eaeb3516eab3d83d8e22860ac69a6bae06e7f3b4ab592b72b6ed",
  "30:30:60":
    "e63c34f8f6fbbfcd5b3120b5fe5f9d1049ea6e68202ff2da050ff116b9072f54",
  "30:45:60":
    "160094c3b18bd1d24514233d160d4767c3549c6fd748cf59e1a6845530fd59be",
});

function identifier(value: unknown, field: string): string {
  if (typeof value !== "string" || !CONTRACT_ID_PATTERN.test(value)) {
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
  const result = nullableDigest(value, field);
  if (result === null) fail(`${field} must be a lowercase SHA-256 digest`);
  return result;
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function problemDigest(problem: PairedProjectileComparisonSpecV1): string {
  return PAIRED_PROBLEM_SHA256[
    `${problem.speedMps}:${problem.anglesDeg[0]}:${problem.anglesDeg[1]}`
  ];
}

function decodeSceneState(value: unknown, field: string): SceneState {
  const input = record(value, field);
  exactKeys(input, ["revision", "nodes"], field);
  const revision = integer(input.revision, `${field} revision`, 0);
  if (!Array.isArray(input.nodes)) fail(`${field} nodes must be an array`);
  if (input.nodes.length > LIVE_SCENE_MAX_NODES) {
    fail(`${field} exceeds ${LIVE_SCENE_MAX_NODES} nodes`, "budget_exceeded");
  }
  const nodes: SceneNode[] = [];
  for (
    let offset = 0;
    offset < input.nodes.length;
    offset += LIVE_SCENE_MAX_PATCH_OPERATIONS
  ) {
    const chunk = input.nodes.slice(
      offset,
      offset + LIVE_SCENE_MAX_PATCH_OPERATIONS,
    );
    const patch = decodeScenePatchDraft({
      v: 1,
      patchId: `browserValidation_${offset}`,
      narration: "Validate one storyboard scene snapshot.",
      operations: chunk.map((node) => ({ op: "put", node })),
    });
    for (const operation of patch.operations) {
      if (operation.op !== "put")
        return fail(`${field} node validation failed`);
      nodes.push(operation.node);
    }
  }
  return createSceneState({ revision, nodes });
}

function decodeEnumArray<const Value extends string>(
  value: unknown,
  allowed: readonly Value[],
  field: string,
): readonly Value[] {
  if (!Array.isArray(value)) fail(`${field} must be an array`);
  if (value.length > allowed.length) {
    fail(`${field} exceeds its closed vocabulary`, "budget_exceeded");
  }
  const decoded = value.map((item) => oneOf(item, allowed, field));
  if (new Set(decoded).size !== decoded.length) fail(`${field} must be unique`);
  return Object.freeze(decoded);
}

function decodeSemanticEffect(
  value: unknown,
): StoryboardSemanticEffectClosureV1 {
  const input = record(value, "semantic effect");
  exactKeys(
    input,
    [
      "v",
      "conceptIds",
      "trajectoryIds",
      "claimIds",
      "producedEvidenceIds",
      "consumedEvidenceIds",
    ],
    "semantic effect",
  );
  const conceptIds = decodeEnumArray(
    input.conceptIds,
    STORYBOARD_CONCEPT_IDS,
    "semantic effect conceptIds",
  );
  const trajectoryIds = decodeEnumArray(
    input.trajectoryIds,
    STORYBOARD_TRAJECTORY_IDS,
    "semantic effect trajectoryIds",
  );
  const claimIds = decodeEnumArray(
    input.claimIds,
    STORYBOARD_CLAIM_IDS,
    "semantic effect claimIds",
  );
  const producedEvidenceIds = decodeEnumArray(
    input.producedEvidenceIds,
    STORYBOARD_EVIDENCE_IDS,
    "semantic effect producedEvidenceIds",
  );
  const consumedEvidenceIds = decodeEnumArray(
    input.consumedEvidenceIds,
    STORYBOARD_EVIDENCE_IDS,
    "semantic effect consumedEvidenceIds",
  );
  if (conceptIds.length + trajectoryIds.length + claimIds.length !== 1) {
    fail("semantic effect must own exactly one catalog effect");
  }
  if (producedEvidenceIds.length > 0 && consumedEvidenceIds.length > 0) {
    fail("semantic effect cannot both produce and consume evidence");
  }
  return Object.freeze({
    v: literal(input.v, 1, "semantic effect v"),
    conceptIds,
    trajectoryIds,
    claimIds,
    producedEvidenceIds,
    consumedEvidenceIds,
  });
}

function recordSlug(record: AcceptedSemanticStoryboardRecordV1): string {
  const target =
    record.act === "reveal"
      ? record.conceptId
      : record.act === "trace"
        ? record.trajectoryId
        : record.claimId;
  return `${record.act}-${target.replaceAll("_", "-")}`;
}

function expectedEffect(
  record: AcceptedSemanticStoryboardRecordV1,
): StoryboardSemanticEffectClosureV1 {
  const empty = Object.freeze([]) as readonly never[];
  if (record.act === "reveal") {
    return {
      v: 1,
      conceptIds: [record.conceptId],
      trajectoryIds: empty,
      claimIds: empty,
      producedEvidenceIds: [record.conceptId],
      consumedEvidenceIds: empty,
    };
  }
  if (record.act === "trace") {
    return {
      v: 1,
      conceptIds: empty,
      trajectoryIds: [record.trajectoryId],
      claimIds: empty,
      producedEvidenceIds: [
        record.trajectoryId === "lower_angle"
          ? "lower_trajectory"
          : "higher_trajectory",
      ],
      consumedEvidenceIds: empty,
    };
  }
  return {
    v: 1,
    conceptIds: empty,
    trajectoryIds: empty,
    claimIds: [record.claimId],
    producedEvidenceIds: empty,
    consumedEvidenceIds: record.evidenceIds,
  };
}

function decodeRoutedBeat(value: unknown): RoutedSemanticStoryboardBeatV1 {
  const input = record(value, "routed beat");
  exactKeys(
    input,
    [
      "v",
      "beatId",
      "checkpointId",
      "componentKind",
      "componentId",
      "ordinal",
      "problemSpec",
      "record",
      "semanticEffect",
      "baseProgramSha256",
      "resultProgramSha256",
      "previousCertificateSha256",
    ],
    "routed beat",
  );
  const decodedRecord = decodeSemanticStoryboardRecordV1(input.record);
  if (decodedRecord.act === "abstain")
    fail("routed beat cannot contain abstain");
  const semanticEffect = decodeSemanticEffect(input.semanticEffect);
  if (!same(semanticEffect, expectedEffect(decodedRecord))) {
    fail("routed beat semanticEffect must be the exact record closure");
  }
  const slug = recordSlug(decodedRecord);
  const baseProgramSha256 = digest(
    input.baseProgramSha256,
    "routed beat baseProgramSha256",
  );
  const resultProgramSha256 = digest(
    input.resultProgramSha256,
    "routed beat resultProgramSha256",
  );
  if (baseProgramSha256 === resultProgramSha256) {
    fail("routed beat must advance the program hash");
  }
  return Object.freeze({
    v: literal(input.v, 1, "routed beat v"),
    beatId: literal(
      input.beatId,
      `storyboard-beat-${slug}`,
      "routed beat beatId",
    ),
    checkpointId: literal(
      input.checkpointId,
      `storyboard-checkpoint-${slug}`,
      "routed beat checkpointId",
    ),
    componentKind: literal(
      input.componentKind,
      "projectile_comparison_storyboard",
      "routed beat componentKind",
    ),
    componentId: literal(
      input.componentId,
      PROJECTILE_STORYBOARD_COMPONENT_ID,
      "routed beat componentId",
    ),
    ordinal: integer(
      input.ordinal,
      "routed beat ordinal",
      1,
      MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS,
    ),
    problemSpec: decodePairedProjectileComparisonSpecV1(input.problemSpec),
    record: decodedRecord,
    semanticEffect,
    baseProgramSha256,
    resultProgramSha256,
    previousCertificateSha256: digest(
      input.previousCertificateSha256,
      "routed beat previousCertificateSha256",
    ),
  });
}

function decodeReceipt(
  value: unknown,
): SemanticStoryboardCheckpointVerificationReceiptV1 {
  const input = record(value, "verification receipt");
  exactKeys(
    input,
    [
      "issuer",
      "checkpointOrigin",
      "componentKind",
      "componentId",
      "checkpointId",
      "problemSpecSha256",
      "routedBeatSha256",
      "baseProgramSha256",
      "resultProgramSha256",
      "semanticEffect",
      "operationTargets",
      "obligationCodes",
      "verified",
    ],
    "verification receipt",
  );
  const checkpointOrigin = oneOf(
    input.checkpointOrigin,
    ["anchor", "model_record"],
    "verification receipt checkpointOrigin",
  );
  if (!Array.isArray(input.operationTargets)) {
    fail("verification receipt operationTargets must be an array");
  }
  if (
    input.operationTargets.length === 0 ||
    input.operationTargets.length > LIVE_SCENE_MAX_PATCH_OPERATIONS
  ) {
    fail(
      `verification receipt operationTargets must contain 1-${LIVE_SCENE_MAX_PATCH_OPERATIONS} entries`,
      "budget_exceeded",
    );
  }
  const operationTargets = input.operationTargets.map((target, index) =>
    identifier(target, `verification receipt operationTargets[${index}]`),
  );
  if (new Set(operationTargets).size !== operationTargets.length) {
    fail("verification receipt operationTargets must be unique");
  }
  if (!Array.isArray(input.obligationCodes)) {
    fail("verification receipt obligationCodes must be an array");
  }
  if (
    input.obligationCodes.length >
    SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS.length
  ) {
    fail("verification receipt obligationCodes exceeds the verifier suite");
  }
  const obligationCodes = input.obligationCodes.map((code) =>
    oneOf(
      code,
      SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS,
      "verification receipt obligationCode",
    ),
  );
  const expectedObligations =
    checkpointOrigin === "anchor"
      ? SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS
      : SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS;
  if (!same(obligationCodes, expectedObligations)) {
    fail("verification receipt must bind the exact origin obligation suite");
  }
  const semanticEffect =
    input.semanticEffect === null
      ? null
      : decodeSemanticEffect(input.semanticEffect);
  const routedBeatSha256 = nullableDigest(
    input.routedBeatSha256,
    "verification receipt routedBeatSha256",
  );
  const baseProgramSha256 = digest(
    input.baseProgramSha256,
    "verification receipt baseProgramSha256",
  );
  const resultProgramSha256 = digest(
    input.resultProgramSha256,
    "verification receipt resultProgramSha256",
  );
  if (checkpointOrigin === "anchor") {
    if (
      input.checkpointId !== SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID ||
      routedBeatSha256 !== null ||
      semanticEffect !== null ||
      baseProgramSha256 !== resultProgramSha256
    ) {
      fail("anchor receipt has inconsistent origin bindings");
    }
  } else if (
    input.checkpointId === SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID ||
    routedBeatSha256 === null ||
    semanticEffect === null ||
    baseProgramSha256 === resultProgramSha256
  ) {
    fail("model receipt has inconsistent origin bindings");
  }
  return Object.freeze({
    issuer: literal(
      input.issuer,
      "semantic_storyboard_verifier",
      "verification receipt issuer",
    ),
    checkpointOrigin,
    componentKind: literal(
      input.componentKind,
      "projectile_comparison_storyboard",
      "verification receipt componentKind",
    ),
    componentId: literal(
      input.componentId,
      PROJECTILE_STORYBOARD_COMPONENT_ID,
      "verification receipt componentId",
    ),
    checkpointId: identifier(
      input.checkpointId,
      "verification receipt checkpointId",
    ),
    problemSpecSha256: digest(
      input.problemSpecSha256,
      "verification receipt problemSpecSha256",
    ),
    routedBeatSha256,
    baseProgramSha256,
    resultProgramSha256,
    semanticEffect,
    operationTargets: Object.freeze(operationTargets),
    obligationCodes: Object.freeze(obligationCodes),
    verified: literal(input.verified, true, "verification receipt verified"),
  });
}

function decodeCertificateBody(
  value: unknown,
): SemanticStoryboardCheckpointCompilerCertificateBodyV1 {
  const input = record(value, "certificate body");
  exactKeys(
    input,
    [
      "v",
      "issuer",
      "compilerVersion",
      "canonicalization",
      "hashAlgorithm",
      "checkpointOrigin",
      "catalogVersion",
      "beatId",
      "routedBeatSha256",
      "recordSha256",
      "componentKind",
      "componentId",
      "checkpointId",
      "problemSpecSha256",
      "baseProgramSha256",
      "resultProgramSha256",
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
  const checkpointOrigin = oneOf(
    input.checkpointOrigin,
    ["anchor", "model_record"],
    "certificate body checkpointOrigin",
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
  if (
    resultLowLevelRevision !== baseLowLevelRevision + 1 ||
    resultSemanticRevision !== baseSemanticRevision + 1 ||
    baseLowLevelRevision !== baseSemanticRevision ||
    resultLowLevelRevision !== resultSemanticRevision
  ) {
    fail(
      "certificate revisions must be one atomic aligned advance",
      "revision_mismatch",
    );
  }
  const beatId =
    input.beatId === null
      ? null
      : identifier(input.beatId, "certificate body beatId");
  const routedBeatSha256 = nullableDigest(
    input.routedBeatSha256,
    "certificate body routedBeatSha256",
  );
  const recordSha256 = nullableDigest(
    input.recordSha256,
    "certificate body recordSha256",
  );
  const previousCertificateSha256 = nullableDigest(
    input.previousCertificateSha256,
    "certificate body previousCertificateSha256",
  );
  const checkpointId = identifier(
    input.checkpointId,
    "certificate body checkpointId",
  );
  const baseProgramSha256 = digest(
    input.baseProgramSha256,
    "certificate body baseProgramSha256",
  );
  const resultProgramSha256 = digest(
    input.resultProgramSha256,
    "certificate body resultProgramSha256",
  );
  if (checkpointOrigin === "anchor") {
    if (
      checkpointId !== SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID ||
      beatId !== null ||
      routedBeatSha256 !== null ||
      recordSha256 !== null ||
      previousCertificateSha256 !== null ||
      baseLowLevelRevision !== 0 ||
      baseProgramSha256 !== resultProgramSha256
    ) {
      fail("anchor certificate has inconsistent origin bindings");
    }
  } else if (
    checkpointId === SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID ||
    beatId === null ||
    routedBeatSha256 === null ||
    recordSha256 === null ||
    previousCertificateSha256 === null ||
    baseLowLevelRevision === 0 ||
    baseProgramSha256 === resultProgramSha256
  ) {
    fail("model certificate has inconsistent origin bindings");
  }
  const presentationCheckpoint = decodePresentationCheckpointV1(
    input.presentationCheckpoint,
  );
  if (presentationCheckpoint.checkpointId !== checkpointId) {
    fail("certificate presentation checkpointId must match checkpointId");
  }
  return Object.freeze({
    v: literal(input.v, 1, "certificate body v"),
    issuer: literal(
      input.issuer,
      "semantic_storyboard_compiler",
      "certificate body issuer",
    ),
    compilerVersion: literal(
      input.compilerVersion,
      SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
      "certificate body compilerVersion",
    ),
    canonicalization: literal(
      input.canonicalization,
      "murmur-json-v1",
      "certificate body canonicalization",
    ),
    hashAlgorithm: literal(
      input.hashAlgorithm,
      "sha256",
      "certificate body hashAlgorithm",
    ),
    checkpointOrigin,
    catalogVersion: literal(
      input.catalogVersion,
      SEMANTIC_STORYBOARD_CATALOG_VERSION,
      "certificate body catalogVersion",
    ),
    beatId,
    routedBeatSha256,
    recordSha256,
    componentKind: literal(
      input.componentKind,
      "projectile_comparison_storyboard",
      "certificate body componentKind",
    ),
    componentId: literal(
      input.componentId,
      PROJECTILE_STORYBOARD_COMPONENT_ID,
      "certificate body componentId",
    ),
    checkpointId,
    problemSpecSha256: digest(
      input.problemSpecSha256,
      "certificate body problemSpecSha256",
    ),
    baseProgramSha256,
    resultProgramSha256,
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
    previousCertificateSha256,
  });
}

function decodeCertificate(
  value: unknown,
): SemanticStoryboardCheckpointCompilerCertificateV1 {
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

function decodeCompiledCheckpoint(
  value: unknown,
): CompiledSemanticStoryboardCheckpointV1 {
  const input = record(value, "compiled checkpoint");
  exactKeys(
    input,
    [
      "checkpointOrigin",
      "problemSpec",
      "beat",
      "checkpointId",
      "patch",
      "receipt",
      "presentation",
      "choreography",
      "certificate",
    ],
    "compiled checkpoint",
  );
  const checkpointOrigin = oneOf(
    input.checkpointOrigin,
    ["anchor", "model_record"],
    "compiled checkpoint checkpointOrigin",
  );
  const problemSpec = decodePairedProjectileComparisonSpecV1(input.problemSpec);
  const beat = input.beat === null ? null : decodeRoutedBeat(input.beat);
  const checkpointId = identifier(
    input.checkpointId,
    "compiled checkpoint checkpointId",
  );
  const patch = decodeScenePatchDraft(input.patch);
  const receipt = decodeReceipt(input.receipt);
  const presentation = decodePresentationCheckpointV1(input.presentation);
  const choreography = decodeChoreographyPlanV2(input.choreography);
  const certificate = decodeCertificate(input.certificate);
  const body = certificate.body;

  if (
    receipt.checkpointOrigin !== checkpointOrigin ||
    body.checkpointOrigin !== checkpointOrigin ||
    receipt.checkpointId !== checkpointId ||
    body.checkpointId !== checkpointId ||
    presentation.checkpointId !== checkpointId ||
    patch.narration !== presentation.checkpointNarration ||
    !same(body.presentationCheckpoint, presentation) ||
    receipt.problemSpecSha256 !== body.problemSpecSha256 ||
    receipt.baseProgramSha256 !== body.baseProgramSha256 ||
    receipt.resultProgramSha256 !== body.resultProgramSha256
  ) {
    fail("compiled checkpoint cleartext bindings disagree");
  }
  const expectedProblemDigest = problemDigest(problemSpec);
  if (
    receipt.problemSpecSha256 !== expectedProblemDigest ||
    body.problemSpecSha256 !== expectedProblemDigest
  ) {
    fail("problemSpecSha256 commitments do not match the bound problem");
  }
  const targets = patch.operations.map(operationTarget);
  if (!same(targets, receipt.operationTargets)) {
    fail("receipt operationTargets must match the ordered patch targets");
  }

  if (checkpointOrigin === "anchor") {
    if (
      beat !== null ||
      checkpointId !== SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID ||
      patch.patchId !== `${PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_anchor`
    ) {
      fail("anchor checkpoint has inconsistent identity");
    }
  } else {
    if (beat === null) fail("model checkpoint requires a routed beat");
    const slug = recordSlug(beat.record);
    if (
      !same(problemSpec, beat.problemSpec) ||
      checkpointId !== beat.checkpointId ||
      patch.patchId !== `${PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_${slug}` ||
      receipt.routedBeatSha256 === null ||
      receipt.routedBeatSha256 !== body.routedBeatSha256 ||
      !same(receipt.semanticEffect, beat.semanticEffect) ||
      receipt.baseProgramSha256 !== beat.baseProgramSha256 ||
      receipt.resultProgramSha256 !== beat.resultProgramSha256 ||
      body.beatId !== beat.beatId ||
      body.previousCertificateSha256 !== beat.previousCertificateSha256
    ) {
      fail("model checkpoint does not match its routed beat");
    }
  }
  return Object.freeze({
    checkpointOrigin,
    problemSpec,
    beat,
    checkpointId,
    patch,
    receipt,
    presentation,
    choreography,
    certificate,
  });
}

function componentProblem(
  scene: ProjectileStoryboardSemanticSceneStateV1,
): PairedProjectileComparisonSpecV1 | null {
  return scene.components[0]?.problemSpec ?? null;
}

/** Decode one complete certified transition without dropping any proof fields. */
export function decodeValidatedSemanticStoryboardTransitionV1(
  value: unknown,
): ValidatedSemanticStoryboardTransitionV1 {
  const input = record(value, "validated transition");
  exactKeys(
    input,
    [
      "baseScene",
      "resultScene",
      "baseSemanticScene",
      "resultSemanticScene",
      "checkpoint",
    ],
    "validated transition",
  );
  const baseScene = decodeSceneState(input.baseScene, "transition baseScene");
  const resultScene = decodeSceneState(
    input.resultScene,
    "transition resultScene",
  );
  const baseSemanticScene = decodeProjectileStoryboardSemanticSceneStateV1(
    input.baseSemanticScene,
  );
  const resultSemanticScene = decodeProjectileStoryboardSemanticSceneStateV1(
    input.resultSemanticScene,
  );
  const checkpoint = decodeCompiledCheckpoint(input.checkpoint);
  const body = checkpoint.certificate.body;
  if (
    baseScene.revision !== baseSemanticScene.revision ||
    resultScene.revision !== resultSemanticScene.revision ||
    resultScene.revision !== baseScene.revision + 1 ||
    body.baseLowLevelRevision !== baseScene.revision ||
    body.resultLowLevelRevision !== resultScene.revision ||
    body.baseSemanticRevision !== baseSemanticScene.revision ||
    body.resultSemanticRevision !== resultSemanticScene.revision
  ) {
    fail(
      "transition revisions do not form one aligned advance",
      "revision_mismatch",
    );
  }
  const applied = applyLiveScenePatch(baseScene, {
    type: "scene_patch",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: baseScene.revision,
    resultRevision: resultScene.revision,
    patch: checkpoint.patch,
  }).scene;
  if (!same(applied, resultScene)) {
    fail("transition patch does not materialize resultScene");
  }
  const baseComponent = baseSemanticScene.components[0] ?? null;
  const resultComponent = resultSemanticScene.components[0] ?? null;
  const baseHead = baseSemanticScene.certificateHeadSha256 ?? null;
  if (
    resultComponent === null ||
    !same(resultComponent.problemSpec, checkpoint.problemSpec) ||
    (baseComponent !== null &&
      !same(baseComponent.problemSpec, checkpoint.problemSpec)) ||
    resultSemanticScene.certificateHeadSha256 !==
      checkpoint.certificate.certificateSha256
  ) {
    fail("transition semantic frontier does not match the checkpoint");
  }
  if (checkpoint.checkpointOrigin === "anchor") {
    if (
      baseScene.revision !== 0 ||
      baseScene.nodes.length !== 0 ||
      baseComponent !== null ||
      baseHead !== null ||
      resultComponent.acceptedRecords.length !== 0 ||
      body.previousCertificateSha256 !== null
    ) {
      fail("anchor transition must create the first empty-program frontier");
    }
  } else {
    const beat = checkpoint.beat;
    if (
      beat === null ||
      baseComponent === null ||
      baseHead === null ||
      beat.previousCertificateSha256 !== baseHead ||
      body.previousCertificateSha256 !== baseHead ||
      beat.ordinal !== baseComponent.acceptedRecords.length + 1 ||
      resultComponent.acceptedRecords.length !==
        baseComponent.acceptedRecords.length + 1 ||
      !same(
        resultComponent.acceptedRecords.slice(0, -1),
        baseComponent.acceptedRecords,
      ) ||
      !same(resultComponent.acceptedRecords.at(-1), beat.record)
    ) {
      fail("model transition must append exactly its routed record");
    }
  }
  if (
    !same(componentProblem(resultSemanticScene), checkpoint.problemSpec) ||
    (componentProblem(baseSemanticScene) !== null &&
      !same(componentProblem(baseSemanticScene), checkpoint.problemSpec))
  ) {
    fail("transition problem identity changed");
  }
  return Object.freeze({
    baseScene,
    resultScene,
    baseSemanticScene,
    resultSemanticScene,
    checkpoint,
  });
}

/** Decode one exact storyboard checkpoint event and its full transition. */
export function decodeSemanticStoryboardSceneCheckpointEventV1(
  value: unknown,
): SemanticStoryboardSceneCheckpointEventV1 {
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
      "transition",
    ],
    "checkpoint event",
  );
  literal(
    input.type,
    "semantic_storyboard_scene_checkpoint",
    "checkpoint event type",
  );
  const baseRevision = integer(
    input.baseRevision,
    "checkpoint baseRevision",
    0,
  );
  const resultRevision = integer(
    input.resultRevision,
    "checkpoint resultRevision",
    1,
  );
  if (resultRevision !== baseRevision + 1) {
    fail("checkpoint revisions must advance exactly once", "revision_mismatch");
  }
  const patch = decodeScenePatchDraft(input.patch);
  const transition = decodeValidatedSemanticStoryboardTransitionV1(
    input.transition,
  );
  if (
    transition.baseScene.revision !== baseRevision ||
    transition.resultScene.revision !== resultRevision ||
    transition.baseSemanticScene.revision !== baseRevision ||
    transition.resultSemanticScene.revision !== resultRevision
  ) {
    fail(
      "event revisions must match the certified transition",
      "revision_mismatch",
    );
  }
  if (!same(patch, transition.checkpoint.patch)) {
    fail("top-level patch must equal the certified transition patch");
  }
  const sequence = integer(
    input.sequence,
    "checkpoint sequence",
    1,
    MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
  );
  if (
    transition.checkpoint.checkpointOrigin === "anchor" &&
    (sequence !== 1 || baseRevision !== 0 || resultRevision !== 1)
  ) {
    fail("anchor checkpoint must be sequence one from revision zero");
  }
  if (
    transition.checkpoint.checkpointOrigin === "model_record" &&
    baseRevision < 1
  ) {
    fail("model checkpoint requires the accepted anchor revision");
  }
  return Object.freeze({
    type: "semantic_storyboard_scene_checkpoint",
    generation: integer(input.generation, "checkpoint generation", 1),
    attempt: literal(input.attempt, 1, "checkpoint attempt"),
    sequence,
    baseRevision,
    resultRevision,
    patch,
    transition,
  });
}

function decodeStarted(
  input: UnknownRecord,
): SemanticStoryboardSceneStreamStartedEventV1 {
  exactKeys(
    input,
    ["type", "generation", "attempt", "baseRevision"],
    "started event",
  );
  return Object.freeze({
    type: "semantic_storyboard_scene_stream_started",
    generation: integer(input.generation, "started generation", 1),
    attempt: literal(input.attempt, 1, "started attempt"),
    baseRevision: integer(input.baseRevision, "started baseRevision", 0),
  });
}

function decodeCompleted(
  input: UnknownRecord,
): SemanticStoryboardSceneStreamCompletedEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "baseRevision",
      "finalRevision",
      "checkpointCount",
      "firstCheckpointMs",
      "totalMs",
      "reasonCode",
      "acceptedPrefixCause",
    ],
    "completed event",
  );
  const baseRevision = integer(input.baseRevision, "completed baseRevision", 0);
  const finalRevision = integer(
    input.finalRevision,
    "completed finalRevision",
    1,
  );
  const checkpointCount = integer(
    input.checkpointCount,
    "completed checkpointCount",
    1,
    MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
  );
  if (finalRevision !== baseRevision + checkpointCount) {
    fail(
      "completed finalRevision must match its accepted prefix",
      "revision_mismatch",
    );
  }
  const firstCheckpointMs = milliseconds(
    input.firstCheckpointMs,
    "completed firstCheckpointMs",
  );
  const totalMs = milliseconds(input.totalMs, "completed totalMs");
  if (totalMs < firstCheckpointMs) {
    fail("completed totalMs must not precede firstCheckpointMs");
  }
  const reasonCode = oneOf(
    input.reasonCode,
    SEMANTIC_STORYBOARD_COMPLETION_REASONS,
    "completed reasonCode",
  );
  const acceptedPrefixCause =
    input.acceptedPrefixCause === null
      ? null
      : oneOf(
          input.acceptedPrefixCause,
          SEMANTIC_STORYBOARD_ACCEPTED_PREFIX_CAUSES,
          "completed acceptedPrefixCause",
        );
  if (reasonCode === "anchor") {
    if (
      baseRevision !== 0 ||
      finalRevision !== 1 ||
      checkpointCount !== 1 ||
      acceptedPrefixCause !== null
    ) {
      fail("anchor completion must describe exactly the anchor checkpoint");
    }
  } else {
    if (baseRevision < 1) fail("model completion requires an accepted anchor");
    if ((reasonCode === "accepted_prefix") !== (acceptedPrefixCause !== null)) {
      fail("acceptedPrefixCause must appear only for accepted_prefix");
    }
  }
  return Object.freeze({
    type: "semantic_storyboard_scene_stream_completed",
    generation: integer(input.generation, "completed generation", 1),
    attempt: literal(input.attempt, 1, "completed attempt"),
    baseRevision,
    finalRevision,
    checkpointCount,
    firstCheckpointMs,
    totalMs,
    reasonCode,
    acceptedPrefixCause,
  });
}

function decodeDeclined(
  input: UnknownRecord,
): SemanticStoryboardSceneStreamDeclinedEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "baseRevision",
      "finalRevision",
      "reasonCode",
      "message",
    ],
    "declined event",
  );
  const baseRevision = integer(input.baseRevision, "declined baseRevision", 1);
  const finalRevision = integer(
    input.finalRevision,
    "declined finalRevision",
    1,
  );
  if (finalRevision !== baseRevision) {
    fail("declined event cannot change revision", "revision_mismatch");
  }
  return Object.freeze({
    type: "semantic_storyboard_scene_stream_declined",
    generation: integer(input.generation, "declined generation", 1),
    attempt: literal(input.attempt, 1, "declined attempt"),
    baseRevision,
    finalRevision,
    reasonCode: oneOf(
      input.reasonCode,
      STORYBOARD_ABSTAIN_REASON_CODES,
      "declined reasonCode",
    ),
    message: boundedString(input.message, "declined message"),
  });
}

function decodeFailed(
  input: UnknownRecord,
): SemanticStoryboardSceneStreamFailedEventV1 {
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "baseRevision",
      "code",
      "message",
      "lastAcceptedRevision",
      "retryable",
    ],
    "failed event",
  );
  const baseRevision = integer(input.baseRevision, "failed baseRevision", 0);
  const lastAcceptedRevision = integer(
    input.lastAcceptedRevision,
    "failed lastAcceptedRevision",
    0,
  );
  if (lastAcceptedRevision !== baseRevision) {
    fail(
      "failed event cannot follow an accepted checkpoint",
      "revision_mismatch",
    );
  }
  const code = oneOf(
    input.code,
    SEMANTIC_STORYBOARD_FAILURE_CODES,
    "failed code",
  );
  if (typeof input.retryable !== "boolean") {
    fail("failed retryable must be a boolean");
  }
  const expectedRetryable = (
    SEMANTIC_STORYBOARD_RETRYABLE_FAILURE_CODES as readonly string[]
  ).includes(code);
  if (input.retryable !== expectedRetryable) {
    fail(
      `failed retryable must be ${String(expectedRetryable)} for code ${code}`,
    );
  }
  return Object.freeze({
    type: "semantic_storyboard_scene_stream_failed",
    generation: integer(input.generation, "failed generation", 1),
    attempt: literal(input.attempt, 1, "failed attempt"),
    baseRevision,
    code,
    message: boundedString(input.message, "failed message"),
    lastAcceptedRevision,
    retryable: input.retryable,
  });
}

/** Decode only the five dedicated Gate 1.8 event discriminators. */
export function decodeSemanticStoryboardSceneStreamEventV1(
  value: unknown,
): SemanticStoryboardSceneStreamEventV1 {
  const input = record(value, "scene stream event");
  switch (input.type) {
    case "semantic_storyboard_scene_stream_started":
      return decodeStarted(input);
    case "semantic_storyboard_scene_checkpoint":
      return decodeSemanticStoryboardSceneCheckpointEventV1(input);
    case "semantic_storyboard_scene_stream_completed":
      return decodeCompleted(input);
    case "semantic_storyboard_scene_stream_declined":
      return decodeDeclined(input);
    case "semantic_storyboard_scene_stream_failed":
      return decodeFailed(input);
    default:
      return fail("event type is unsupported");
  }
}

/** Parse one JSON payload after the bounded SSE layer extracts its data field. */
export function parseSemanticStoryboardSceneStreamEventV1(
  data: string,
): SemanticStoryboardSceneStreamEventV1 {
  const wireBytes = new TextEncoder().encode(`data: ${data}\n\n`).byteLength;
  if (wireBytes > MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES) {
    return fail(
      `SSE event exceeds ${MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES} bytes`,
      "budget_exceeded",
    );
  }
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return fail("SSE data must be valid JSON", "invalid_json");
  }
  return decodeSemanticStoryboardSceneStreamEventV1(value);
}
