import {
  applyLiveScenePatch,
  decodeScenePatchEvent,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_PATCH_VERSION,
  LiveSceneProtocolError,
  type ScenePatchDraft,
  type ScenePatchEvent,
} from "./patch";
import { createSceneState } from "./state";
import type { MotionPlan, SceneState } from "./types";

export const SEMANTIC_COMPILER_VERSION =
  "murmur.pythagorean_area_identity.v2" as const;
export const SEMANTIC_CANONICALIZATION = "murmur-json-v1" as const;
export const SEMANTIC_HASH_ALGORITHM = "sha256" as const;

export const PYTHAGOREAN_ROLE_ORDER = [
  "triangle",
  "square_a",
  "label_a2",
  "square_b",
  "label_b2",
  "square_c",
  "label_c2",
  "identity",
  "altitude",
  "partition",
  "region_a",
  "region_a_label",
  "region_b",
  "region_b_label",
  "projection_identity",
  "proof_conclusion",
] as const;

export const PYTHAGOREAN_IDENTITY_ROLE_COUNT = 8;

export type TeachingAct = "introduce" | "derive" | "connect" | "emphasize";
export type PythagoreanStage = "triangle" | "areas" | "identity" | "proof";
export type PythagoreanRole = (typeof PYTHAGOREAN_ROLE_ORDER)[number];

export type VerificationObligation =
  | "stable_id"
  | "unique_ids"
  | "board_bounds"
  | "right_angle"
  | "attached_square"
  | "square_edge_length"
  | "hypotenuse_ratio"
  | "label_containment"
  | "label_separation"
  | "compiler_identity"
  | "altitude_projection"
  | "square_partition"
  | "area_equivalence"
  | "proof_conclusion";

export interface PythagoreanAreaIdentityDirective {
  readonly kind: "pythagorean_area_identity";
  readonly id: string;
  readonly revealThrough: PythagoreanStage;
}

export interface TeachingBeatDraft {
  readonly v: typeof LIVE_SCENE_PATCH_VERSION;
  readonly beatId: string;
  readonly narration: string;
  readonly act: TeachingAct;
  readonly directive: PythagoreanAreaIdentityDirective;
}

export interface PythagoreanAreaIdentityState {
  readonly kind: "pythagorean_area_identity";
  readonly id: string;
  readonly revealedRoles: readonly PythagoreanRole[];
}

export interface SemanticSceneState {
  readonly revision: number;
  readonly components: readonly PythagoreanAreaIdentityState[];
  readonly certificateHeadSha256?: string | null;
}

/**
 * An opaque same-origin server claim about a compiler-emitted node. The browser
 * validates its structure and binds it to presentation; it does not independently
 * re-run the geometry verifier.
 */
export interface VerificationReceipt {
  readonly issuer: "semantic_verifier";
  readonly componentId: string;
  readonly role: PythagoreanRole;
  readonly nodeId: string;
  readonly obligationCodes: readonly VerificationObligation[];
  readonly verified: true;
}

export interface CompilerCertificateBodyV1 {
  readonly v: 1;
  readonly issuer: "semantic_compiler";
  readonly compilerVersion: typeof SEMANTIC_COMPILER_VERSION;
  readonly canonicalization: typeof SEMANTIC_CANONICALIZATION;
  readonly hashAlgorithm: typeof SEMANTIC_HASH_ALGORITHM;
  readonly atomId: string;
  readonly beatId: string;
  readonly beatSha256: string;
  readonly componentId: string;
  readonly role: PythagoreanRole;
  readonly nodeId: string;
  readonly atomOrdinal: number;
  readonly baseSemanticRevision: number;
  readonly resultSemanticRevision: number;
  readonly baseSceneSha256: string;
  readonly resultSceneSha256: string;
  readonly patchSha256: string;
  readonly receiptSha256: string;
  readonly previousCertificateSha256: string | null;
}

export interface CompilerCertificateV1 {
  readonly body: CompilerCertificateBodyV1;
  readonly certificateSha256: string;
}

export interface SemanticAtomMetadata {
  readonly beat: TeachingBeatDraft;
  readonly atomId: string;
  readonly componentId: string;
  readonly role: PythagoreanRole;
  readonly atomOrdinal: number;
  readonly semanticBaseRevision: number;
  readonly semanticResultRevision: number;
  readonly receipt: VerificationReceipt;
  readonly certificate: CompilerCertificateV1;
}

export interface SemanticScenePatchEvent {
  readonly type: "semantic_scene_patch";
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
  readonly semantic: SemanticAtomMetadata;
}

export interface AppliedSemanticScenePatch {
  readonly scene: SceneState;
  readonly semanticScene: SemanticSceneState;
  readonly plan: MotionPlan;
}

type UnknownRecord = Record<string, unknown>;

const MAX_SAFE_SEQUENCE = Number.MAX_SAFE_INTEGER;
const MAX_SEMANTIC_ID_CHARS = 32;
const MAX_NARRATION_CHARS = 512;
const SEMANTIC_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const CONTRACT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const TEACHING_ACTS = new Set<TeachingAct>([
  "introduce",
  "derive",
  "connect",
  "emphasize",
]);
const PYTHAGOREAN_STAGES = new Set<PythagoreanStage>([
  "triangle",
  "areas",
  "identity",
  "proof",
]);
const PYTHAGOREAN_ROLES = new Set<PythagoreanRole>(PYTHAGOREAN_ROLE_ORDER);
const VERIFICATION_OBLIGATIONS = new Set<VerificationObligation>([
  "stable_id",
  "unique_ids",
  "board_bounds",
  "right_angle",
  "attached_square",
  "square_edge_length",
  "hypotenuse_ratio",
  "label_containment",
  "label_separation",
  "compiler_identity",
  "altitude_projection",
  "square_partition",
  "area_equivalence",
  "proof_conclusion",
]);

function fail(
  message: string,
  code: "invalid_event" | "revision_mismatch" | "budget_exceeded" = "invalid_event"
): never {
  throw new LiveSceneProtocolError(code, `semantic scene ${message}`);
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
  optional: readonly string[],
  field: string
): void {
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`${field} is missing field ${key}`);
  }
}

function integer(value: unknown, field: string, minimum: number, maximum = MAX_SAFE_SEQUENCE): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    fail(`${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function nonEmptyString(value: unknown, field: string, maximum: number, trim = false): string {
  if (typeof value !== "string") fail(`${field} must be a string`);
  const normalized = trim ? value.trim() : value;
  if (normalized.length === 0) fail(`${field} must be non-empty`);
  if ([...normalized].length > maximum) {
    fail(`${field} exceeds ${maximum} characters`, "budget_exceeded");
  }
  return normalized;
}

function semanticId(value: unknown, field: string): string {
  const id = nonEmptyString(value, field, MAX_SEMANTIC_ID_CHARS);
  if (!SEMANTIC_ID_PATTERN.test(id)) fail(`${field} has an unsafe semantic identifier`);
  return id;
}

function contractId(value: unknown, field: string): string {
  const id = nonEmptyString(value, field, 64);
  if (!CONTRACT_ID_PATTERN.test(id)) fail(`${field} has an unsafe identifier`);
  return id;
}

function sha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    fail(`${field} must be a lowercase SHA-256 digest`);
  }
  return value;
}

function literal<T extends string | number | boolean>(
  value: unknown,
  expected: T,
  field: string
): T {
  if (value !== expected) fail(`${field} must equal ${String(expected)}`);
  return expected;
}

function oneOf<T extends string>(value: unknown, allowed: ReadonlySet<T>, field: string): T {
  if (typeof value !== "string" || !allowed.has(value as T)) {
    fail(`${field} has an unsupported value`);
  }
  return value as T;
}

function rolesThrough(stage: PythagoreanStage): readonly PythagoreanRole[] {
  if (stage === "triangle") return PYTHAGOREAN_ROLE_ORDER.slice(0, 1);
  if (stage === "areas") return PYTHAGOREAN_ROLE_ORDER.slice(0, 7);
  if (stage === "identity") {
    return PYTHAGOREAN_ROLE_ORDER.slice(0, PYTHAGOREAN_IDENTITY_ROLE_COUNT);
  }
  return PYTHAGOREAN_ROLE_ORDER;
}

function semanticNodeId(componentId: string, role: PythagoreanRole): string {
  return `${componentId}__${role}`;
}

function semanticAtomId(componentId: string, role: PythagoreanRole): string {
  return `${componentId}__atom_${role}`;
}

function decodeDirective(value: unknown): PythagoreanAreaIdentityDirective {
  const input = record(value, "beat directive");
  exactKeys(input, ["kind", "id", "revealThrough"], [], "beat directive");
  return Object.freeze({
    kind: literal(input.kind, "pythagorean_area_identity", "beat directive kind"),
    id: semanticId(input.id, "beat directive id"),
    revealThrough: oneOf(input.revealThrough, PYTHAGOREAN_STAGES, "beat directive revealThrough"),
  });
}

function decodeTeachingBeat(value: unknown): TeachingBeatDraft {
  const input = record(value, "teaching beat");
  exactKeys(input, ["v", "beatId", "narration", "act", "directive"], [], "teaching beat");
  return Object.freeze({
    v: literal(input.v, LIVE_SCENE_PATCH_VERSION, "teaching beat v"),
    beatId: semanticId(input.beatId, "teaching beat beatId"),
    narration: nonEmptyString(input.narration, "teaching beat narration", MAX_NARRATION_CHARS, true),
    act: oneOf(input.act, TEACHING_ACTS, "teaching beat act"),
    directive: decodeDirective(input.directive),
  });
}

function decodeComponent(value: unknown, index: number): PythagoreanAreaIdentityState {
  const input = record(value, `component ${index}`);
  exactKeys(input, ["kind", "id", "revealedRoles"], [], `component ${index}`);
  literal(input.kind, "pythagorean_area_identity", `component ${index} kind`);
  if (!Array.isArray(input.revealedRoles)) {
    fail(`component ${index} revealedRoles must be an array`);
  }
  if (input.revealedRoles.length > PYTHAGOREAN_ROLE_ORDER.length) {
    fail(`component ${index} has too many revealed roles`, "budget_exceeded");
  }
  const revealedRoles = input.revealedRoles.map((role, roleIndex) =>
    oneOf(role, PYTHAGOREAN_ROLES, `component ${index} revealedRoles[${roleIndex}]`)
  );
  const expected = PYTHAGOREAN_ROLE_ORDER.slice(0, revealedRoles.length);
  if (!revealedRoles.every((role, roleIndex) => role === expected[roleIndex])) {
    fail(`component ${index} revealedRoles must be an ordered role prefix`);
  }
  return Object.freeze({
    kind: "pythagorean_area_identity",
    id: semanticId(input.id, `component ${index} id`),
    revealedRoles: Object.freeze(revealedRoles),
  });
}

/** Validate, deep-clone, and freeze one semantic scene snapshot. */
export function createSemanticSceneState(inputValue: SemanticSceneState): SemanticSceneState {
  const input = record(inputValue, "state");
  exactKeys(input, ["revision", "components"], ["certificateHeadSha256"], "state");
  if (!Array.isArray(input.components)) fail("state components must be an array");
  if (input.components.length > LIVE_SCENE_MAX_NODES) {
    fail(`state exceeds ${LIVE_SCENE_MAX_NODES} components`, "budget_exceeded");
  }
  const components = input.components.map(decodeComponent);
  const ids = new Set<string>();
  for (const component of components) {
    if (ids.has(component.id)) fail(`state component id ${component.id} is duplicated`);
    ids.add(component.id);
  }
  const certificateHead = input.certificateHeadSha256;
  const normalizedCertificateHead =
    certificateHead === undefined || certificateHead === null
      ? undefined
      : sha256(certificateHead, "state certificateHeadSha256");
  return Object.freeze({
    revision: integer(input.revision, "state revision", 0),
    components: Object.freeze(components),
    ...(normalizedCertificateHead === undefined
      ? {}
      : { certificateHeadSha256: normalizedCertificateHead }),
  });
}

function decodeReceipt(value: unknown): VerificationReceipt {
  const input = record(value, "semantic receipt");
  exactKeys(
    input,
    ["issuer", "componentId", "role", "nodeId", "obligationCodes", "verified"],
    [],
    "semantic receipt"
  );
  if (!Array.isArray(input.obligationCodes) || input.obligationCodes.length === 0) {
    fail("semantic receipt obligationCodes must be a non-empty array");
  }
  const obligationCodes = input.obligationCodes.map((code, index) =>
    oneOf(code, VERIFICATION_OBLIGATIONS, `semantic receipt obligationCodes[${index}]`)
  );
  if (new Set(obligationCodes).size !== obligationCodes.length) {
    fail("semantic receipt obligationCodes must be unique");
  }
  return Object.freeze({
    issuer: literal(input.issuer, "semantic_verifier", "semantic receipt issuer"),
    componentId: semanticId(input.componentId, "semantic receipt componentId"),
    role: oneOf(input.role, PYTHAGOREAN_ROLES, "semantic receipt role"),
    nodeId: contractId(input.nodeId, "semantic receipt nodeId"),
    obligationCodes: Object.freeze(obligationCodes),
    verified: literal(input.verified, true, "semantic receipt verified"),
  });
}

function decodeCertificateBody(value: unknown): CompilerCertificateBodyV1 {
  const input = record(value, "compiler certificate body");
  exactKeys(
    input,
    [
      "v",
      "issuer",
      "compilerVersion",
      "canonicalization",
      "hashAlgorithm",
      "atomId",
      "beatId",
      "beatSha256",
      "componentId",
      "role",
      "nodeId",
      "atomOrdinal",
      "baseSemanticRevision",
      "resultSemanticRevision",
      "baseSceneSha256",
      "resultSceneSha256",
      "patchSha256",
      "receiptSha256",
      "previousCertificateSha256",
    ],
    [],
    "compiler certificate body"
  );
  const role = oneOf(input.role, PYTHAGOREAN_ROLES, "compiler certificate body role");
  const atomOrdinal = integer(
    input.atomOrdinal,
    "compiler certificate body atomOrdinal",
    1,
    PYTHAGOREAN_ROLE_ORDER.length
  );
  if (PYTHAGOREAN_ROLE_ORDER[atomOrdinal - 1] !== role) {
    fail("compiler certificate body atomOrdinal must be the absolute role ordinal");
  }
  const baseSemanticRevision = integer(
    input.baseSemanticRevision,
    "compiler certificate body baseSemanticRevision",
    0
  );
  const resultSemanticRevision = integer(
    input.resultSemanticRevision,
    "compiler certificate body resultSemanticRevision",
    1
  );
  if (resultSemanticRevision !== baseSemanticRevision + 1) {
    fail("compiler certificate body revisions must advance exactly once", "revision_mismatch");
  }
  const previous = input.previousCertificateSha256;
  const previousCertificateSha256 =
    previous === null
      ? null
      : sha256(previous, "compiler certificate body previousCertificateSha256");
  return Object.freeze({
    v: literal(input.v, 1, "compiler certificate body v"),
    issuer: literal(input.issuer, "semantic_compiler", "compiler certificate body issuer"),
    compilerVersion: literal(
      input.compilerVersion,
      SEMANTIC_COMPILER_VERSION,
      "compiler certificate body compilerVersion"
    ),
    canonicalization: literal(
      input.canonicalization,
      SEMANTIC_CANONICALIZATION,
      "compiler certificate body canonicalization"
    ),
    hashAlgorithm: literal(
      input.hashAlgorithm,
      SEMANTIC_HASH_ALGORITHM,
      "compiler certificate body hashAlgorithm"
    ),
    atomId: contractId(input.atomId, "compiler certificate body atomId"),
    beatId: semanticId(input.beatId, "compiler certificate body beatId"),
    beatSha256: sha256(input.beatSha256, "compiler certificate body beatSha256"),
    componentId: semanticId(input.componentId, "compiler certificate body componentId"),
    role,
    nodeId: contractId(input.nodeId, "compiler certificate body nodeId"),
    atomOrdinal,
    baseSemanticRevision,
    resultSemanticRevision,
    baseSceneSha256: sha256(input.baseSceneSha256, "compiler certificate body baseSceneSha256"),
    resultSceneSha256: sha256(input.resultSceneSha256, "compiler certificate body resultSceneSha256"),
    patchSha256: sha256(input.patchSha256, "compiler certificate body patchSha256"),
    receiptSha256: sha256(input.receiptSha256, "compiler certificate body receiptSha256"),
    previousCertificateSha256,
  });
}

function decodeCertificate(value: unknown): CompilerCertificateV1 {
  const input = record(value, "compiler certificate");
  exactKeys(input, ["body", "certificateSha256"], [], "compiler certificate");
  return Object.freeze({
    body: decodeCertificateBody(input.body),
    certificateSha256: sha256(input.certificateSha256, "compiler certificate certificateSha256"),
  });
}

function decodeSemanticMetadata(value: unknown, patch: ScenePatchDraft): SemanticAtomMetadata {
  const input = record(value, "semantic metadata");
  exactKeys(
    input,
    [
      "beat",
      "atomId",
      "componentId",
      "role",
      "atomOrdinal",
      "semanticBaseRevision",
      "semanticResultRevision",
      "receipt",
      "certificate",
    ],
    [],
    "semantic metadata"
  );
  const beat = decodeTeachingBeat(input.beat);
  const atomId = contractId(input.atomId, "semantic metadata atomId");
  const componentId = semanticId(input.componentId, "semantic metadata componentId");
  const role = oneOf(input.role, PYTHAGOREAN_ROLES, "semantic metadata role");
  const atomOrdinal = integer(
    input.atomOrdinal,
    "semantic metadata atomOrdinal",
    1,
    PYTHAGOREAN_ROLE_ORDER.length
  );
  const semanticBaseRevision = integer(
    input.semanticBaseRevision,
    "semantic metadata semanticBaseRevision",
    0
  );
  const semanticResultRevision = integer(
    input.semanticResultRevision,
    "semantic metadata semanticResultRevision",
    1
  );
  const receipt = decodeReceipt(input.receipt);
  const certificate = decodeCertificate(input.certificate);
  const targetRoles = rolesThrough(beat.directive.revealThrough);
  const operation = patch.operations[0];
  const expectedAtomId = semanticAtomId(componentId, role);
  const expectedNodeId = semanticNodeId(componentId, role);

  if (beat.directive.id !== componentId) fail("beat directive id must match componentId");
  if (atomOrdinal > targetRoles.length || targetRoles[atomOrdinal - 1] !== role) {
    fail("atom role and ordinal must belong to the beat revealThrough prefix");
  }
  if (semanticResultRevision !== semanticBaseRevision + 1) {
    fail("metadata semantic revisions must advance exactly once", "revision_mismatch");
  }
  if (atomId !== expectedAtomId) {
    fail(`atomId must equal deterministic semantic atom id ${expectedAtomId}`);
  }
  if (patch.patchId !== atomId) fail("patchId must equal semantic atomId");
  if (patch.operations.length !== 1 || operation?.op !== "put") {
    fail("each semantic atom patch must contain exactly one put operation");
  }
  const targetId = operation.node.id;
  if (targetId !== expectedNodeId) {
    fail(`put target must equal deterministic semantic node id ${expectedNodeId}`);
  }
  if (receipt.componentId !== componentId || receipt.role !== role || receipt.nodeId !== targetId) {
    fail("verification receipt must match the semantic atom owner and target node");
  }

  const body = certificate.body;
  if (
    body.atomId !== atomId ||
    body.beatId !== beat.beatId ||
    body.componentId !== componentId ||
    body.role !== role ||
    body.nodeId !== targetId ||
    body.atomOrdinal !== atomOrdinal
  ) {
    fail("compiler certificate identity must match the semantic atom");
  }
  if (
    body.baseSemanticRevision !== semanticBaseRevision ||
    body.resultSemanticRevision !== semanticResultRevision
  ) {
    fail("compiler certificate revisions must match semantic metadata", "revision_mismatch");
  }

  return Object.freeze({
    beat,
    atomId,
    componentId,
    role,
    atomOrdinal,
    semanticBaseRevision,
    semanticResultRevision,
    receipt,
    certificate,
  });
}

/**
 * Strictly decode one same-origin server-claimed semantic scene atom.
 *
 * SHA-256 fields and verifier receipts remain opaque claims here. This decoder
 * checks their grammar and cross-field continuity; browser acceptance additionally
 * binds the claimed node to the exact presentation plan. It does not recompute a
 * digest or independently verify the compiler's geometry.
 */
export function decodeSemanticScenePatchEvent(inputValue: unknown): SemanticScenePatchEvent {
  const input = record(inputValue, "semantic_scene_patch event");
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
    [],
    "semantic_scene_patch event"
  );
  literal(input.type, "semantic_scene_patch", "event type");

  // Keep the existing low-level decoder as the single authority for scene
  // envelopes, node grammar, budgets, and immutable patch materialization.
  const rawEvent: ScenePatchEvent = decodeScenePatchEvent({
    type: "scene_patch",
    generation: input.generation,
    attempt: input.attempt,
    sequence: input.sequence,
    baseRevision: input.baseRevision,
    resultRevision: input.resultRevision,
    patch: input.patch,
  });
  const semantic = decodeSemanticMetadata(input.semantic, rawEvent.patch);

  if (rawEvent.patch.narration !== semantic.beat.narration) {
    fail("patch narration must match teaching beat narration");
  }
  if (
    semantic.semanticBaseRevision !== rawEvent.baseRevision ||
    semantic.semanticResultRevision !== rawEvent.resultRevision
  ) {
    fail("semantic and low-level revisions must match", "revision_mismatch");
  }

  return Object.freeze({
    type: "semantic_scene_patch",
    generation: rawEvent.generation,
    attempt: rawEvent.attempt,
    sequence: rawEvent.sequence,
    baseRevision: rawEvent.baseRevision,
    resultRevision: rawEvent.resultRevision,
    patch: rawEvent.patch,
    semantic,
  });
}

function validateAcceptedSemanticPrefix(
  acceptedScene: SceneState,
  acceptedSemantic: SemanticSceneState
): void {
  const nodeIds = new Set(acceptedScene.nodes.map((node) => node.id));

  for (const component of acceptedSemantic.components) {
    const revealedRoleCount = component.revealedRoles.length;
    for (const [roleIndex, role] of PYTHAGOREAN_ROLE_ORDER.entries()) {
      const nodeId = semanticNodeId(component.id, role);
      if (roleIndex < revealedRoleCount && !nodeIds.has(nodeId)) {
        fail(
          `accepted revealed role ${role} is missing stable node ${nodeId}`,
          "revision_mismatch"
        );
      }
      if (roleIndex >= revealedRoleCount && nodeIds.has(nodeId)) {
        fail(
          `accepted unrevealed role ${role} already has stable node ${nodeId}`,
          "revision_mismatch"
        );
      }
    }
  }
}

function validateSemanticPresentationPlan(plan: MotionPlan, expectedNodeId: string): void {
  const step = plan.steps[0];
  if (
    plan.steps.length !== 1 ||
    step?.type !== "enter" ||
    step.id !== expectedNodeId ||
    step.node.id !== expectedNodeId
  ) {
    fail(
      `semantic atom must produce exactly one enter motion for node ${expectedNodeId}`
    );
  }
}

function advanceSemanticScene(
  accepted: SemanticSceneState,
  event: SemanticScenePatchEvent
): SemanticSceneState {
  const { semantic } = event;
  const existingIndex = accepted.components.findIndex(
    (component) => component.id === semantic.componentId
  );
  const existing = existingIndex === -1 ? undefined : accepted.components[existingIndex];
  const expectedPriorRoles = PYTHAGOREAN_ROLE_ORDER.slice(0, semantic.atomOrdinal - 1);
  if (
    existing?.kind !== undefined &&
    existing.kind !== semantic.beat.directive.kind
  ) {
    fail("existing component kind does not match the teaching beat");
  }
  const actualPriorRoles = existing?.revealedRoles ?? [];
  if (
    actualPriorRoles.length !== expectedPriorRoles.length ||
    !actualPriorRoles.every((role, index) => role === expectedPriorRoles[index])
  ) {
    fail("atom is not the exact next role in the accepted semantic prefix", "revision_mismatch");
  }
  const priorHead = accepted.certificateHeadSha256 ?? null;
  if (semantic.certificate.body.previousCertificateSha256 !== priorHead) {
    fail("certificate previousCertificateSha256 does not match the accepted chain head", "revision_mismatch");
  }

  const nextComponent: PythagoreanAreaIdentityState = Object.freeze({
    kind: "pythagorean_area_identity",
    id: semantic.componentId,
    revealedRoles: Object.freeze(
      PYTHAGOREAN_ROLE_ORDER.slice(0, semantic.atomOrdinal)
    ),
  });
  const components = [...accepted.components];
  if (existingIndex === -1) components.push(nextComponent);
  else components[existingIndex] = nextComponent;

  return createSemanticSceneState({
    revision: event.resultRevision,
    components,
    certificateHeadSha256: semantic.certificate.certificateSha256,
  });
}

/**
 * Apply one semantic atom to paired low-level and semantic snapshots.
 * Neither accepted snapshot is mutated if decoding or either transition fails.
 */
export function applySemanticScenePatch(
  currentScene: SceneState,
  currentSemanticScene: SemanticSceneState,
  eventValue: SemanticScenePatchEvent
): AppliedSemanticScenePatch {
  const event = decodeSemanticScenePatchEvent(eventValue);
  const acceptedScene = createSceneState(currentScene);
  const acceptedSemantic = createSemanticSceneState(currentSemanticScene);
  if (acceptedScene.revision !== acceptedSemantic.revision) {
    fail("accepted low-level and semantic revisions must match", "revision_mismatch");
  }
  const hasCommittedRoles = acceptedSemantic.components.some(
    (component) => component.revealedRoles.length > 0
  );
  const hasCertificateHead = acceptedSemantic.certificateHeadSha256 !== undefined;
  if (hasCommittedRoles !== hasCertificateHead) {
    fail(
      "accepted committed roles and certificate chain head must agree",
      "revision_mismatch"
    );
  }
  if (event.baseRevision !== acceptedSemantic.revision) {
    fail("event baseRevision does not match the accepted semantic revision", "revision_mismatch");
  }

  const expectedNodeId = event.semantic.receipt.nodeId;
  if (acceptedScene.nodes.some((node) => node.id === expectedNodeId)) {
    fail(
      `incoming semantic target ${expectedNodeId} must be absent from the accepted scene`,
      "revision_mismatch"
    );
  }
  validateAcceptedSemanticPrefix(acceptedScene, acceptedSemantic);

  const applied = applyLiveScenePatch(acceptedScene, {
    type: "scene_patch",
    generation: event.generation,
    attempt: event.attempt,
    sequence: event.sequence,
    baseRevision: event.baseRevision,
    resultRevision: event.resultRevision,
    patch: event.patch,
  });
  validateSemanticPresentationPlan(applied.plan, expectedNodeId);
  const semanticScene = advanceSemanticScene(acceptedSemantic, event);

  return Object.freeze({
    scene: applied.scene,
    semanticScene,
    plan: applied.plan,
  });
}
