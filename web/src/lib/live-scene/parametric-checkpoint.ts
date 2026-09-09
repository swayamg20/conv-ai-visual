import {
  decodeChoreographyPlanV1,
  decodePresentationCheckpointV1,
  type ChoreographyPlanV1,
  type PresentationCheckpointV1,
} from "./choreography";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type CompletingSquareCheckpointId,
} from "./checkpoint";
import {
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
  type ScenePatchDraft,
} from "./patch";
import {
  decodeRoutedChoreographyBeatV3,
  type RoutedChoreographyBeatV3,
} from "./parametric-choreography";

export const CHECKPOINT_CERTIFICATE_V3_VERSION = 3 as const;
export const CHECKPOINT_COMPILER_V3_VERSION =
  "murmur.completing_square_choreography.v2" as const;
export const CHECKPOINT_CERTIFICATE_V3_ISSUER = "semantic_compiler" as const;
export const CHECKPOINT_RECEIPT_V3_ISSUER =
  "completing_square_verifier" as const;
export const CHECKPOINT_V3_CANONICALIZATION = "murmur-json-v1" as const;
export const CHECKPOINT_V3_HASH_ALGORITHM = "sha256" as const;

export const CHECKPOINT_VERIFICATION_OBLIGATIONS_V3 = [
  "stable_id",
  "unique_ids",
  "board_bounds",
  "component_ownership",
  "patch_materialization",
  "compatible_morph",
  "viewport_containment",
  "equation_identity",
  "area_model",
  "equal_linear_split",
  "adjacent_rearrangement",
  "missing_corner",
  "balanced_completion",
  "factorization",
  "roots",
  "geometry_domain",
  "problem_identity",
  "caption_facts",
  "authored_timing",
] as const;

export type CheckpointVerificationObligationV3 =
  (typeof CHECKPOINT_VERIFICATION_OBLIGATIONS_V3)[number];

export interface CheckpointVerificationReceiptV3 {
  readonly issuer: typeof CHECKPOINT_RECEIPT_V3_ISSUER;
  readonly componentKind: "completing_square_parametric";
  readonly componentId: string;
  readonly problemSpecSha256: string;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly operationTargets: readonly string[];
  readonly obligationCodes: readonly CheckpointVerificationObligationV3[];
  readonly verified: true;
}

export interface CheckpointCompilerCertificateBodyV3 {
  readonly v: typeof CHECKPOINT_CERTIFICATE_V3_VERSION;
  readonly issuer: typeof CHECKPOINT_CERTIFICATE_V3_ISSUER;
  readonly compilerVersion: typeof CHECKPOINT_COMPILER_V3_VERSION;
  readonly canonicalization: typeof CHECKPOINT_V3_CANONICALIZATION;
  readonly hashAlgorithm: typeof CHECKPOINT_V3_HASH_ALGORITHM;
  readonly beatId: string;
  readonly routedBeatSha256: string;
  readonly componentKind: "completing_square_parametric";
  readonly componentId: string;
  readonly problemSpecSha256: string;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly baseRevision: number;
  readonly resultRevision: number;
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

export interface CheckpointCompilerCertificateV3 {
  readonly body: CheckpointCompilerCertificateBodyV3;
  readonly certificateSha256: string;
}

export interface CompiledCheckpointV3 {
  readonly beat: RoutedChoreographyBeatV3;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly patch: ScenePatchDraft;
  readonly receipt: CheckpointVerificationReceiptV3;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: ChoreographyPlanV1;
  readonly certificate: CheckpointCompilerCertificateV3;
}

type UnknownRecord = Record<string, unknown>;

function fail(
  message: string,
  code:
    | "invalid_json"
    | "invalid_event"
    | "revision_mismatch"
    | "budget_exceeded" = "invalid_event",
): never {
  throw new LiveSceneProtocolError(code, `parametric checkpoint ${message}`);
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

const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const COMPONENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

function identifier(value: unknown, field: string, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

function digest(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    fail(`${field} must be a lowercase SHA-256 digest`);
  }
  return value;
}

function revision(value: unknown, field: string, minimum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    fail(`${field} must be a safe integer at least ${minimum}`);
  }
  return value as number;
}

function decodeReceipt(value: unknown): CheckpointVerificationReceiptV3 {
  const input = record(value, "verification receipt");
  exactKeys(
    input,
    [
      "issuer",
      "componentKind",
      "componentId",
      "problemSpecSha256",
      "checkpointId",
      "operationTargets",
      "obligationCodes",
      "verified",
    ],
    "verification receipt",
  );
  if (!Array.isArray(input.operationTargets)) {
    fail("verification receipt operationTargets must be an array");
  }
  if (input.operationTargets.length === 0) {
    fail("verification receipt operationTargets must be non-empty");
  }
  if (input.operationTargets.length > LIVE_SCENE_MAX_PATCH_OPERATIONS) {
    fail(
      `verification receipt operationTargets exceeds ${LIVE_SCENE_MAX_PATCH_OPERATIONS}`,
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
  if (!Array.isArray(input.obligationCodes) || input.obligationCodes.length === 0) {
    fail("verification receipt obligationCodes must be a non-empty array");
  }
  const obligationCodes = input.obligationCodes.map((code) =>
    oneOf(
      code,
      CHECKPOINT_VERIFICATION_OBLIGATIONS_V3,
      "verification receipt obligationCode",
    ),
  );
  if (new Set(obligationCodes).size !== obligationCodes.length) {
    fail("verification receipt obligationCodes must be unique");
  }

  return Object.freeze({
    issuer: literal(
      input.issuer,
      CHECKPOINT_RECEIPT_V3_ISSUER,
      "verification receipt issuer",
    ),
    componentKind: literal(
      input.componentKind,
      "completing_square_parametric",
      "verification receipt componentKind",
    ),
    componentId: identifier(
      input.componentId,
      "verification receipt componentId",
      COMPONENT_ID_PATTERN,
    ),
    problemSpecSha256: digest(
      input.problemSpecSha256,
      "verification receipt problemSpecSha256",
    ),
    checkpointId: oneOf(
      input.checkpointId,
      COMPLETING_SQUARE_CHECKPOINT_IDS,
      "verification receipt checkpointId",
    ),
    operationTargets: Object.freeze(operationTargets),
    obligationCodes: Object.freeze(obligationCodes),
    verified: literal(input.verified, true, "verification receipt verified"),
  });
}

function decodeCertificateBody(
  value: unknown,
): CheckpointCompilerCertificateBodyV3 {
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
      "problemSpecSha256",
      "checkpointId",
      "baseRevision",
      "resultRevision",
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
  const baseRevision = revision(input.baseRevision, "certificate body baseRevision", 0);
  const resultRevision = revision(
    input.resultRevision,
    "certificate body resultRevision",
    1,
  );
  if (resultRevision !== baseRevision + 1) {
    fail("certificate body revisions must advance exactly once", "revision_mismatch");
  }
  const checkpointId = oneOf(
    input.checkpointId,
    COMPLETING_SQUARE_CHECKPOINT_IDS,
    "certificate body checkpointId",
  );
  const presentationCheckpoint = decodePresentationCheckpointV1(
    input.presentationCheckpoint,
  );
  if (presentationCheckpoint.checkpointId !== checkpointId) {
    fail("certificate presentation checkpointId must match certificate checkpointId");
  }
  const previous = input.previousCertificateSha256;

  return Object.freeze({
    v: literal(
      input.v,
      CHECKPOINT_CERTIFICATE_V3_VERSION,
      "certificate body v",
    ),
    issuer: literal(
      input.issuer,
      CHECKPOINT_CERTIFICATE_V3_ISSUER,
      "certificate body issuer",
    ),
    compilerVersion: literal(
      input.compilerVersion,
      CHECKPOINT_COMPILER_V3_VERSION,
      "certificate body compilerVersion",
    ),
    canonicalization: literal(
      input.canonicalization,
      CHECKPOINT_V3_CANONICALIZATION,
      "certificate body canonicalization",
    ),
    hashAlgorithm: literal(
      input.hashAlgorithm,
      CHECKPOINT_V3_HASH_ALGORITHM,
      "certificate body hashAlgorithm",
    ),
    beatId: identifier(input.beatId, "certificate body beatId", CONTRACT_ID_PATTERN),
    routedBeatSha256: digest(
      input.routedBeatSha256,
      "certificate body routedBeatSha256",
    ),
    componentKind: literal(
      input.componentKind,
      "completing_square_parametric",
      "certificate body componentKind",
    ),
    componentId: identifier(
      input.componentId,
      "certificate body componentId",
      COMPONENT_ID_PATTERN,
    ),
    problemSpecSha256: digest(
      input.problemSpecSha256,
      "certificate body problemSpecSha256",
    ),
    checkpointId,
    baseRevision,
    resultRevision,
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
    previousCertificateSha256:
      previous === null
        ? null
        : digest(previous, "certificate body previousCertificateSha256"),
  });
}

function decodeCertificate(value: unknown): CheckpointCompilerCertificateV3 {
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

function samePresentation(
  left: PresentationCheckpointV1,
  right: PresentationCheckpointV1,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * Decode one complete V3 compiler claim and all browser-verifiable joins.
 * Digests remain opaque server-issued commitments and are never re-derived.
 */
export function decodeCompiledCheckpointV3(value: unknown): CompiledCheckpointV3 {
  const input = record(value, "compiled checkpoint");
  exactKeys(
    input,
    [
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
  const beat = decodeRoutedChoreographyBeatV3(input.beat);
  const checkpointId = oneOf(
    input.checkpointId,
    COMPLETING_SQUARE_CHECKPOINT_IDS,
    "compiled checkpoint checkpointId",
  );
  const patch = decodeScenePatchDraft(input.patch);
  const receipt = decodeReceipt(input.receipt);
  const presentation = decodePresentationCheckpointV1(input.presentation);
  const choreography = decodeChoreographyPlanV1(input.choreography);
  const certificate = decodeCertificate(input.certificate);

  if (patch.patchId !== `${beat.componentId}__cp_${checkpointId}`) {
    fail("patchId must match its deterministic identity");
  }
  if (patch.narration !== presentation.checkpointNarration) {
    fail("patch narration must match checkpointNarration");
  }
  if (presentation.checkpointId !== checkpointId) {
    fail("presentation checkpointId must match compiled checkpointId");
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
  if (receipt.componentKind !== beat.componentKind) {
    fail("receipt componentKind must match routed beat");
  }
  if (receipt.componentId !== beat.componentId) {
    fail("receipt componentId must match routed beat componentId");
  }
  if (receipt.checkpointId !== checkpointId) {
    fail("receipt checkpointId must match compiled checkpointId");
  }

  const body = certificate.body;
  if (body.beatId !== beat.beatId) {
    fail("certificate beatId must match routed beat");
  }
  if (body.componentKind !== beat.componentKind) {
    fail("certificate componentKind must match routed beat");
  }
  if (body.componentId !== beat.componentId) {
    fail("certificate componentId must match routed beat");
  }
  if (body.problemSpecSha256 !== receipt.problemSpecSha256) {
    fail("receipt and certificate problemSpecSha256 commitments must match");
  }
  if (body.checkpointId !== checkpointId) {
    fail("certificate checkpointId must match compiled checkpointId");
  }
  if (!samePresentation(body.presentationCheckpoint, presentation)) {
    fail("certificate presentationCheckpoint must match presentation");
  }

  return Object.freeze({
    beat,
    checkpointId,
    patch,
    receipt,
    presentation,
    choreography,
    certificate,
  });
}

export function parseCompiledCheckpointV3(data: string): CompiledCheckpointV3 {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return fail("data must be valid JSON", "invalid_json");
  }
  return decodeCompiledCheckpointV3(value);
}
