import type {
  ChoreographyPlanV1,
  PresentationCheckpointV1,
} from "./choreography";
import type { CompletingSquareCheckpointId } from "./checkpoint";
import {
  LiveSceneProtocolError,
  type ScenePatchDraft,
} from "./patch";
import {
  decodeParametricCompletingSquareStateV1,
  type ParametricCompletingSquareStateV1,
  type RoutedChoreographyBeatV3,
} from "./parametric-choreography";
import {
  decodeCompiledCheckpointV3,
  type CheckpointCompilerCertificateV3,
  type CheckpointVerificationReceiptV3,
} from "./parametric-checkpoint";
import {
  decodeCompletingSquareProblemSpecV1,
  sameCompletingSquareProblem,
  type CompletingSquareProblemSpecV1,
} from "./parametric-problem";

export const MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS = 8;

export const PARAMETRIC_CHOREOGRAPHY_DECLINE_REASONS = [
  "unsupported_intent",
  "no_forward_progress",
  "problem_required",
  "problem_ambiguous",
  "problem_unsupported",
  "problem_conflict",
] as const;
export type ParametricChoreographyDeclineReason =
  (typeof PARAMETRIC_CHOREOGRAPHY_DECLINE_REASONS)[number];

export const PARAMETRIC_CHOREOGRAPHY_FAILURE_CODES = [
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
export type ParametricChoreographyFailureCode =
  (typeof PARAMETRIC_CHOREOGRAPHY_FAILURE_CODES)[number];

export const PARAMETRIC_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES = [
  "invalid_visual_act",
  "provider_rate_limited",
  "provider_timeout",
  "provider_error",
] as const satisfies readonly ParametricChoreographyFailureCode[];

export interface ParametricCheckpointSemanticMetadataV3 {
  readonly problemSpec: CompletingSquareProblemSpecV1;
  readonly beat: RoutedChoreographyBeatV3;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly resultComponent: ParametricCompletingSquareStateV1;
  readonly semanticBaseRevision: number;
  readonly semanticResultRevision: number;
  readonly semanticBaseCertificateSha256: string | null;
  readonly semanticResultCertificateSha256: string;
  readonly receipt: CheckpointVerificationReceiptV3;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: ChoreographyPlanV1;
  readonly certificate: CheckpointCompilerCertificateV3;
}

export interface ParametricChoreographySceneCheckpointEventV3 {
  readonly type: "parametric_choreography_scene_checkpoint";
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
  readonly semantic: ParametricCheckpointSemanticMetadataV3;
}

export interface ParametricChoreographySceneStreamDeclinedEventV3 {
  readonly type: "parametric_choreography_scene_stream_declined";
  readonly generation: number;
  readonly attempt: number;
  readonly finalRevision: number;
  readonly reasonCode: ParametricChoreographyDeclineReason;
  readonly message: string;
}

export interface ParametricChoreographySceneStreamFailedEventV3 {
  readonly type: "parametric_choreography_scene_stream_failed";
  readonly generation: number;
  readonly attempt: number;
  readonly code: ParametricChoreographyFailureCode;
  readonly message: string;
  readonly lastAcceptedRevision: number;
  readonly retryable: boolean;
}

export interface ParametricSceneStreamStartedEventV3 {
  readonly type: "scene_stream_started";
  readonly generation: number;
  readonly attempt: number;
  readonly baseRevision: number;
}

export interface ParametricSceneStreamRepairingEventV3 {
  readonly type: "scene_stream_repairing";
  readonly generation: number;
  readonly fromAttempt: number;
  readonly toAttempt: number;
  readonly lastAcceptedRevision: number;
  readonly message: string;
}

export interface ParametricSceneStreamCompletedEventV3 {
  readonly type: "scene_stream_completed";
  readonly generation: number;
  readonly finalRevision: number;
  readonly patchCount: number;
  readonly firstPatchMs: number;
  readonly totalMs: number;
  readonly repaired: boolean;
}

export type ParametricChoreographySceneStreamEventV3 =
  | ParametricSceneStreamStartedEventV3
  | ParametricChoreographySceneCheckpointEventV3
  | ParametricChoreographySceneStreamDeclinedEventV3
  | ParametricSceneStreamRepairingEventV3
  | ParametricSceneStreamCompletedEventV3
  | ParametricChoreographySceneStreamFailedEventV3;

type UnknownRecord = Record<string, unknown>;

function fail(
  message: string,
  code:
    | "invalid_json"
    | "invalid_event"
    | "revision_mismatch"
    | "budget_exceeded" = "invalid_event",
): never {
  throw new LiveSceneProtocolError(code, `parametric choreography stream ${message}`);
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

const SHA256_PATTERN = /^[0-9a-f]{64}$/;

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

interface DecodedSemanticMetadata {
  readonly semantic: ParametricCheckpointSemanticMetadataV3;
  readonly patch: ScenePatchDraft;
}

function decodeSemanticMetadata(
  value: unknown,
  patch: unknown,
): DecodedSemanticMetadata {
  const input = record(value, "semantic metadata");
  exactKeys(
    input,
    [
      "problemSpec",
      "beat",
      "checkpointId",
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
  const compiled = decodeCompiledCheckpointV3({
    beat: input.beat,
    checkpointId: input.checkpointId,
    patch,
    receipt: input.receipt,
    presentation: input.presentation,
    choreography: input.choreography,
    certificate: input.certificate,
  });
  const problemSpec = decodeCompletingSquareProblemSpecV1(input.problemSpec);
  const resultComponent = decodeParametricCompletingSquareStateV1(
    input.resultComponent,
  );
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
  if (!sameCompletingSquareProblem(problemSpec, compiled.beat.problemSpec)) {
    fail("problemSpec must match routed beat problemSpec");
  }
  if (resultComponent.id !== compiled.beat.componentId) {
    fail("resultComponent id must match routed beat componentId");
  }
  if (!sameCompletingSquareProblem(resultComponent.problemSpec, problemSpec)) {
    fail("resultComponent problemSpec must match semantic problemSpec");
  }
  if (compiled.checkpointId === "corner_detail") {
    if (
      resultComponent.lastMainCheckpoint !== "missing_corner" ||
      !resultComponent.cornerClarified
    ) {
      fail("corner_detail must settle the clarified missing_corner frontier");
    }
  } else if (resultComponent.lastMainCheckpoint !== compiled.checkpointId) {
    fail("main checkpoint must match resultComponent lastMainCheckpoint");
  }

  const body = compiled.certificate.body;
  if (
    body.baseRevision !== semanticBaseRevision ||
    body.resultRevision !== semanticResultRevision
  ) {
    fail("certificate revisions must match semantic revisions", "revision_mismatch");
  }
  if (body.componentId !== resultComponent.id) {
    fail("certificate componentId must match resultComponent id");
  }
  const semanticBaseCertificateSha256 = nullableDigest(
    input.semanticBaseCertificateSha256,
    "semantic metadata semanticBaseCertificateSha256",
  );
  const semanticResultCertificateSha256 = digest(
    input.semanticResultCertificateSha256,
    "semantic metadata semanticResultCertificateSha256",
  );
  if (body.previousCertificateSha256 !== semanticBaseCertificateSha256) {
    fail(
      "certificate previousCertificateSha256 must match semantic base chain head",
      "revision_mismatch",
    );
  }
  if (compiled.certificate.certificateSha256 !== semanticResultCertificateSha256) {
    fail(
      "semantic result chain head must match certificate certificateSha256",
      "revision_mismatch",
    );
  }

  return {
    patch: compiled.patch,
    semantic: Object.freeze({
      problemSpec,
      beat: compiled.beat,
      checkpointId: compiled.checkpointId,
      resultComponent,
      semanticBaseRevision,
      semanticResultRevision,
      semanticBaseCertificateSha256,
      semanticResultCertificateSha256,
      receipt: compiled.receipt,
      presentation: compiled.presentation,
      choreography: compiled.choreography,
      certificate: compiled.certificate,
    }),
  };
}

export function decodeParametricChoreographySceneCheckpointEventV3(
  value: unknown,
): ParametricChoreographySceneCheckpointEventV3 {
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
  if (input.type !== "parametric_choreography_scene_checkpoint") {
    fail("checkpoint event type is unsupported");
  }
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
  const semantic = decoded.semantic;
  if (
    semantic.semanticBaseRevision !== baseRevision ||
    semantic.semanticResultRevision !== resultRevision
  ) {
    fail("semantic and low-level checkpoint revisions must match", "revision_mismatch");
  }
  return Object.freeze({
    type: "parametric_choreography_scene_checkpoint",
    generation: integer(input.generation, "checkpoint generation", 1),
    attempt: integer(input.attempt, "checkpoint attempt", 1, 2),
    sequence: integer(
      input.sequence,
      "checkpoint sequence",
      1,
      MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
    ),
    baseRevision,
    resultRevision,
    patch: decoded.patch,
    semantic,
  });
}

function decodeStarted(
  input: UnknownRecord,
): ParametricSceneStreamStartedEventV3 {
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
): ParametricSceneStreamRepairingEventV3 {
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
): ParametricSceneStreamCompletedEventV3 {
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
      MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
    ),
    firstPatchMs,
    totalMs,
    repaired: input.repaired,
  });
}

function decodeDeclined(
  input: UnknownRecord,
): ParametricChoreographySceneStreamDeclinedEventV3 {
  exactKeys(
    input,
    ["type", "generation", "attempt", "finalRevision", "reasonCode", "message"],
    "declined event",
  );
  return Object.freeze({
    type: "parametric_choreography_scene_stream_declined",
    generation: integer(input.generation, "declined generation", 1),
    attempt: integer(input.attempt, "declined attempt", 1, 2),
    finalRevision: integer(input.finalRevision, "declined finalRevision", 0),
    reasonCode: oneOf(
      input.reasonCode,
      PARAMETRIC_CHOREOGRAPHY_DECLINE_REASONS,
      "declined reasonCode",
    ),
    message: boundedString(input.message, "declined message"),
  });
}

function decodeFailed(
  input: UnknownRecord,
): ParametricChoreographySceneStreamFailedEventV3 {
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
    PARAMETRIC_CHOREOGRAPHY_FAILURE_CODES,
    "failed code",
  );
  if (typeof input.retryable !== "boolean") {
    fail("failed retryable must be a boolean");
  }
  const expectedRetryable = (
    PARAMETRIC_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES as readonly string[]
  ).includes(code);
  if (input.retryable !== expectedRetryable) {
    fail(`failed retryable must be ${String(expectedRetryable)} for code ${code}`);
  }
  return Object.freeze({
    type: "parametric_choreography_scene_stream_failed",
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

/** Decode one complete V3 event without falling back to any V2 discriminator. */
export function decodeParametricChoreographySceneStreamEventV3(
  value: unknown,
): ParametricChoreographySceneStreamEventV3 {
  const input = record(value, "scene stream event");
  switch (input.type) {
    case "scene_stream_started":
      return decodeStarted(input);
    case "parametric_choreography_scene_checkpoint":
      return decodeParametricChoreographySceneCheckpointEventV3(input);
    case "parametric_choreography_scene_stream_declined":
      return decodeDeclined(input);
    case "scene_stream_repairing":
      return decodeRepairing(input);
    case "scene_stream_completed":
      return decodeCompleted(input);
    case "parametric_choreography_scene_stream_failed":
      return decodeFailed(input);
    default:
      return fail("event type is unsupported");
  }
}

export function parseParametricChoreographySceneStreamEventV3(
  data: string,
): ParametricChoreographySceneStreamEventV3 {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return fail("SSE data must be valid JSON", "invalid_json");
  }
  return decodeParametricChoreographySceneStreamEventV3(value);
}
