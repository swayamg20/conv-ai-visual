import {
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
} from "./patch";
import {
  SUPPORTED_PROJECTILE_ANGLES_DEG,
  SUPPORTED_PROJECTILE_SPEEDS_MPS,
  type ProjectileAngleDeg,
  type ProjectileSpeedMps,
} from "./projectile-motion";
import { createSceneState } from "./state";
import type { SceneNode, SceneState } from "./types";

export const PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL =
  "projectile_comparison_storyboard_v1" as const;
export const SEMANTIC_STORYBOARD_RECORD_VERSION = 1 as const;
export const PAIRED_PROJECTILE_COMPARISON_VERSION = 1 as const;
export const PROJECTILE_STORYBOARD_STATE_VERSION = 1 as const;
export const PROJECTILE_STORYBOARD_COMPONENT_ID =
  "projectile-comparison" as const;
export const MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN = 5;
export const MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS = 7;
export const MAX_STORYBOARD_EVIDENCE_IDS = 4;

const PROMPT_EDGE_WHITESPACE = new Set([
  ..."\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff",
]);

export interface PairedProjectileComparisonSpecV1 {
  readonly v: typeof PAIRED_PROJECTILE_COMPARISON_VERSION;
  readonly speedMps: ProjectileSpeedMps;
  readonly anglesDeg: readonly [ProjectileAngleDeg, ProjectileAngleDeg];
}

export const STORYBOARD_CONCEPT_IDS = [
  "range_formula",
  "complementary_angles",
] as const;
export type StoryboardConceptId = (typeof STORYBOARD_CONCEPT_IDS)[number];

export const STORYBOARD_TRAJECTORY_IDS = [
  "lower_angle",
  "higher_angle",
] as const;
export type StoryboardTrajectoryId = (typeof STORYBOARD_TRAJECTORY_IDS)[number];

export const STORYBOARD_CLAIM_IDS = [
  "equal_range",
  "unequal_range",
  "higher_apex",
  "longer_flight",
] as const;
export type StoryboardClaimId = (typeof STORYBOARD_CLAIM_IDS)[number];

export const STORYBOARD_EVIDENCE_IDS = [
  "lower_trajectory",
  "higher_trajectory",
  "range_formula",
  "complementary_angles",
] as const;
export type StoryboardEvidenceId = (typeof STORYBOARD_EVIDENCE_IDS)[number];

export const STORYBOARD_ABSTAIN_REASON_CODES = [
  "already_present",
  "ambiguous_intent",
  "no_forward_progress",
  "unsupported_initial_condition",
  "unsupported_intent",
  "unsupported_physics",
  "unsupported_problem",
] as const;
export type StoryboardAbstainReasonCode =
  (typeof STORYBOARD_ABSTAIN_REASON_CODES)[number];

export interface RevealStoryboardRecordV1 {
  readonly v: typeof SEMANTIC_STORYBOARD_RECORD_VERSION;
  readonly act: "reveal";
  readonly conceptId: StoryboardConceptId;
}

export interface TraceStoryboardRecordV1 {
  readonly v: typeof SEMANTIC_STORYBOARD_RECORD_VERSION;
  readonly act: "trace";
  readonly trajectoryId: StoryboardTrajectoryId;
}

export interface RelateStoryboardRecordV1 {
  readonly v: typeof SEMANTIC_STORYBOARD_RECORD_VERSION;
  readonly act: "relate";
  readonly claimId: StoryboardClaimId;
  readonly evidenceIds: readonly StoryboardEvidenceId[];
}

export interface AbstainStoryboardRecordV1 {
  readonly v: typeof SEMANTIC_STORYBOARD_RECORD_VERSION;
  readonly act: "abstain";
  readonly reasonCode: StoryboardAbstainReasonCode;
}

export type AcceptedSemanticStoryboardRecordV1 =
  RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1;

export type SemanticStoryboardRecordV1 =
  AcceptedSemanticStoryboardRecordV1 | AbstainStoryboardRecordV1;

export interface ProjectileStoryboardStateV1 {
  readonly v: typeof PROJECTILE_STORYBOARD_STATE_VERSION;
  readonly kind: "projectile_comparison_storyboard";
  readonly id: typeof PROJECTILE_STORYBOARD_COMPONENT_ID;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly acceptedRecords: readonly AcceptedSemanticStoryboardRecordV1[];
}

export interface ProjectileStoryboardSemanticSceneStateV1 {
  readonly revision: number;
  readonly components: readonly ProjectileStoryboardStateV1[];
  readonly certificateHeadSha256?: string | null;
}

interface SemanticStoryboardRequestBaseV1 {
  readonly protocol: typeof PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly generation: number;
  readonly baseScene: SceneState;
  readonly baseSemanticScene: ProjectileStoryboardSemanticSceneStateV1;
}

export interface SemanticStoryboardReflexRequestV1 extends SemanticStoryboardRequestBaseV1 {
  readonly routingMode: "reflex";
}

export interface SemanticStoryboardDirectorRequestV1 extends SemanticStoryboardRequestBaseV1 {
  readonly routingMode: "director";
  readonly prompt: string;
}

export type SemanticStoryboardRequestV1 =
  SemanticStoryboardReflexRequestV1 | SemanticStoryboardDirectorRequestV1;

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `semantic storyboard ${message}`,
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
  optional: readonly string[],
  field: string,
): void {
  const allowed = new Set([...required, ...optional]);
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

function integer(value: unknown, field: string, minimum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    fail(`${field} must be a safe integer at least ${minimum}`);
  }
  return value as number;
}

function normalizePromptEdges(value: string): string {
  const characters = [...value];
  let start = 0;
  let end = characters.length;
  while (start < end && PROMPT_EDGE_WHITESPACE.has(characters[start])) start++;
  while (end > start && PROMPT_EDGE_WHITESPACE.has(characters[end - 1])) end--;
  return characters.slice(start, end).join("");
}

function boundedPrompt(value: unknown): string {
  const normalized =
    typeof value === "string" ? normalizePromptEdges(value) : value;
  if (
    typeof normalized !== "string" ||
    normalized.length === 0 ||
    [...normalized].length > 2_000
  ) {
    fail(
      "request prompt must be a non-empty string of at most 2000 characters",
    );
  }
  return normalized;
}

function decodeBaseScene(value: unknown): SceneState {
  const input = record(value, "request baseScene");
  exactKeys(input, ["revision", "nodes"], [], "request baseScene");
  const revision = integer(input.revision, "request baseScene revision", 0);
  if (!Array.isArray(input.nodes))
    fail("request baseScene nodes must be an array");
  if (input.nodes.length > LIVE_SCENE_MAX_NODES) {
    throw new LiveSceneProtocolError(
      "budget_exceeded",
      `semantic storyboard request baseScene exceeds ${LIVE_SCENE_MAX_NODES} nodes`,
    );
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
    const decoded = decodeScenePatchDraft({
      v: 1,
      patchId: `browserValidation_${offset}`,
      narration: "Validate the accepted browser scene.",
      operations: chunk.map((node) => ({ op: "put", node })),
    });
    for (const operation of decoded.operations) {
      if (operation.op !== "put")
        return fail("request baseScene node validation failed");
      nodes.push(operation.node);
    }
  }
  return createSceneState({ revision, nodes });
}

function sameProblem(
  left: PairedProjectileComparisonSpecV1,
  right: PairedProjectileComparisonSpecV1,
): boolean {
  return (
    left.v === right.v &&
    left.speedMps === right.speedMps &&
    left.anglesDeg[0] === right.anglesDeg[0] &&
    left.anglesDeg[1] === right.anglesDeg[1]
  );
}

/** Decode one of the nine server-qualified same-speed, ascending angle pairs. */
export function decodePairedProjectileComparisonSpecV1(
  value: unknown,
): PairedProjectileComparisonSpecV1 {
  const input = record(value, "problem spec");
  exactKeys(input, ["v", "speedMps", "anglesDeg"], [], "problem spec");
  if (input.v !== PAIRED_PROJECTILE_COMPARISON_VERSION) {
    fail(`problem spec v must equal ${PAIRED_PROJECTILE_COMPARISON_VERSION}`);
  }
  if (!Array.isArray(input.anglesDeg) || input.anglesDeg.length !== 2) {
    fail("problem spec anglesDeg must contain exactly two angles");
  }
  const lower = oneOf(
    input.anglesDeg[0],
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    "problem spec lower angle",
  );
  const higher = oneOf(
    input.anglesDeg[1],
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    "problem spec higher angle",
  );
  if (lower >= higher) {
    fail("problem spec anglesDeg must be distinct and ascending");
  }
  return Object.freeze({
    v: PAIRED_PROJECTILE_COMPARISON_VERSION,
    speedMps: oneOf(
      input.speedMps,
      SUPPORTED_PROJECTILE_SPEEDS_MPS,
      "problem spec speedMps",
    ),
    anglesDeg: Object.freeze([lower, higher]) as readonly [
      ProjectileAngleDeg,
      ProjectileAngleDeg,
    ],
  });
}

function decodeEvidenceIds(value: unknown): readonly StoryboardEvidenceId[] {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.length > MAX_STORYBOARD_EVIDENCE_IDS
  ) {
    fail(
      `record evidenceIds must contain 1-${MAX_STORYBOARD_EVIDENCE_IDS} values`,
    );
  }
  const evidenceIds = value.map((evidence) =>
    oneOf(evidence, STORYBOARD_EVIDENCE_IDS, "record evidenceId"),
  );
  if (new Set(evidenceIds).size !== evidenceIds.length) {
    fail("record evidenceIds must be unique");
  }
  const expected = STORYBOARD_EVIDENCE_IDS.filter((evidence) =>
    evidenceIds.includes(evidence),
  );
  if (expected.some((evidence, index) => evidence !== evidenceIds[index])) {
    fail("record evidenceIds must follow canonical catalog order");
  }
  return Object.freeze(evidenceIds);
}

/** Decode exactly one minimal act-discriminated model record. */
export function decodeSemanticStoryboardRecordV1(
  value: unknown,
): SemanticStoryboardRecordV1 {
  const input = record(value, "record");
  if (input.v !== SEMANTIC_STORYBOARD_RECORD_VERSION) {
    fail(`record v must equal ${SEMANTIC_STORYBOARD_RECORD_VERSION}`);
  }
  if (input.act === "reveal") {
    exactKeys(input, ["v", "act", "conceptId"], [], "record");
    return Object.freeze({
      v: SEMANTIC_STORYBOARD_RECORD_VERSION,
      act: "reveal",
      conceptId: oneOf(
        input.conceptId,
        STORYBOARD_CONCEPT_IDS,
        "record conceptId",
      ),
    });
  }
  if (input.act === "trace") {
    exactKeys(input, ["v", "act", "trajectoryId"], [], "record");
    return Object.freeze({
      v: SEMANTIC_STORYBOARD_RECORD_VERSION,
      act: "trace",
      trajectoryId: oneOf(
        input.trajectoryId,
        STORYBOARD_TRAJECTORY_IDS,
        "record trajectoryId",
      ),
    });
  }
  if (input.act === "relate") {
    exactKeys(input, ["v", "act", "claimId", "evidenceIds"], [], "record");
    return Object.freeze({
      v: SEMANTIC_STORYBOARD_RECORD_VERSION,
      act: "relate",
      claimId: oneOf(input.claimId, STORYBOARD_CLAIM_IDS, "record claimId"),
      evidenceIds: decodeEvidenceIds(input.evidenceIds),
    });
  }
  if (input.act === "abstain") {
    exactKeys(input, ["v", "act", "reasonCode"], [], "record");
    return Object.freeze({
      v: SEMANTIC_STORYBOARD_RECORD_VERSION,
      act: "abstain",
      reasonCode: oneOf(
        input.reasonCode,
        STORYBOARD_ABSTAIN_REASON_CODES,
        "record reasonCode",
      ),
    });
  }
  return fail("record act has an unsupported value");
}

function effectKey(record: AcceptedSemanticStoryboardRecordV1): string {
  if (record.act === "reveal") return `${record.act}:${record.conceptId}`;
  if (record.act === "trace") return `${record.act}:${record.trajectoryId}`;
  return `${record.act}:${record.claimId}`;
}

const CONCEPT_EVIDENCE = {
  range_formula: "range_formula",
  complementary_angles: "complementary_angles",
} as const satisfies Readonly<
  Record<StoryboardConceptId, StoryboardEvidenceId>
>;

const TRAJECTORY_EVIDENCE = {
  lower_angle: "lower_trajectory",
  higher_angle: "higher_trajectory",
} as const satisfies Readonly<
  Record<StoryboardTrajectoryId, StoryboardEvidenceId>
>;

const CLAIM_EVIDENCE_OPTIONS = {
  equal_range: [
    ["lower_trajectory", "higher_trajectory"],
    ["range_formula", "complementary_angles"],
  ],
  unequal_range: [["lower_trajectory", "higher_trajectory"], ["range_formula"]],
  higher_apex: [["lower_trajectory", "higher_trajectory"]],
  longer_flight: [["lower_trajectory", "higher_trajectory"]],
} as const satisfies Readonly<
  Record<StoryboardClaimId, readonly (readonly StoryboardEvidenceId[])[]>
>;

function sameEvidence(
  left: readonly StoryboardEvidenceId[],
  right: readonly StoryboardEvidenceId[],
): boolean {
  return (
    left.length === right.length &&
    left.every((evidence, index) => evidence === right[index])
  );
}

function hasComplementaryAngles(
  problemSpec: PairedProjectileComparisonSpecV1,
): boolean {
  return problemSpec.anglesDeg[0] + problemSpec.anglesDeg[1] === 90;
}

function validateProgram(
  problemSpec: PairedProjectileComparisonSpecV1,
  records: readonly AcceptedSemanticStoryboardRecordV1[],
): void {
  if (records.length > MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS) {
    fail("state acceptedRecords exceeds the closed storyboard catalog");
  }
  const effects = new Set<string>();
  const visibleEvidence = new Set<StoryboardEvidenceId>();
  for (const accepted of records) {
    const effect = effectKey(accepted);
    if (effects.has(effect)) {
      fail("state acceptedRecords cannot repeat a semantic effect");
    }
    const complementary = hasComplementaryAngles(problemSpec);
    if (
      (accepted.act === "reveal" &&
        accepted.conceptId === "complementary_angles" &&
        !complementary) ||
      (accepted.act === "relate" &&
        ((accepted.claimId === "equal_range" && !complementary) ||
          (accepted.claimId === "unequal_range" && complementary)))
    ) {
      fail("state acceptedRecords contains an inapplicable catalog selection");
    }
    if (accepted.act === "relate") {
      const validOption = CLAIM_EVIDENCE_OPTIONS[accepted.claimId].some(
        (option) => sameEvidence(accepted.evidenceIds, option),
      );
      if (!validOption) fail("state evidenceIds is not valid for claimId");
      if (
        accepted.evidenceIds.some((evidence) => !visibleEvidence.has(evidence))
      ) {
        fail(
          "state evidenceIds must already be visible in the accepted prefix",
        );
      }
    } else if (accepted.act === "reveal") {
      visibleEvidence.add(CONCEPT_EVIDENCE[accepted.conceptId]);
    } else {
      visibleEvidence.add(TRAJECTORY_EVIDENCE[accepted.trajectoryId]);
    }
    effects.add(effect);
  }
}

/** Return whether this valid frontier still has an applicable unused effect. */
export function storyboardHasForwardCapacity(
  problemSpec: unknown,
  records: unknown,
): boolean {
  const decodedProblem = decodePairedProjectileComparisonSpecV1(problemSpec);
  if (!Array.isArray(records)) {
    fail("state acceptedRecords must be an array");
  }
  if (records.length > MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS) {
    fail("state acceptedRecords exceeds the closed storyboard catalog");
  }
  const decodedRecords = records.map(decodeAcceptedRecord);
  validateProgram(decodedProblem, decodedRecords);
  const applicableEffectCount = hasComplementaryAngles(decodedProblem) ? 7 : 6;
  return decodedRecords.length < applicableEffectCount;
}

function decodeAcceptedRecord(
  value: unknown,
): AcceptedSemanticStoryboardRecordV1 {
  const decoded = decodeSemanticStoryboardRecordV1(value);
  if (decoded.act === "abstain") {
    fail("state acceptedRecords cannot contain abstain");
  }
  return decoded;
}

/** Decode one exact problem-bound, ordered accepted semantic ledger. */
export function decodeProjectileStoryboardStateV1(
  value: unknown,
): ProjectileStoryboardStateV1 {
  const input = record(value, "state");
  exactKeys(
    input,
    ["v", "kind", "id", "problemSpec", "acceptedRecords"],
    [],
    "state",
  );
  if (input.v !== PROJECTILE_STORYBOARD_STATE_VERSION) {
    fail(`state v must equal ${PROJECTILE_STORYBOARD_STATE_VERSION}`);
  }
  if (input.kind !== "projectile_comparison_storyboard") {
    fail("state kind must equal projectile_comparison_storyboard");
  }
  if (input.id !== PROJECTILE_STORYBOARD_COMPONENT_ID) {
    fail(`state id must equal ${PROJECTILE_STORYBOARD_COMPONENT_ID}`);
  }
  if (!Array.isArray(input.acceptedRecords)) {
    fail("state acceptedRecords must be an array");
  }
  if (input.acceptedRecords.length > MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS) {
    fail("state acceptedRecords exceeds the closed storyboard catalog");
  }
  const problemSpec = decodePairedProjectileComparisonSpecV1(input.problemSpec);
  const acceptedRecords = Object.freeze(
    input.acceptedRecords.map(decodeAcceptedRecord),
  );
  validateProgram(problemSpec, acceptedRecords);
  return Object.freeze({
    v: PROJECTILE_STORYBOARD_STATE_VERSION,
    kind: "projectile_comparison_storyboard",
    id: PROJECTILE_STORYBOARD_COMPONENT_ID,
    problemSpec,
    acceptedRecords,
  });
}

/** Decode the isolated Gate 1.8 semantic frontier and its certificate head. */
export function decodeProjectileStoryboardSemanticSceneStateV1(
  value: unknown,
): ProjectileStoryboardSemanticSceneStateV1 {
  const input = record(value, "semantic scene");
  exactKeys(
    input,
    ["revision", "components"],
    ["certificateHeadSha256"],
    "semantic scene",
  );
  const revision = integer(input.revision, "semantic scene revision", 0);
  if (!Array.isArray(input.components)) {
    fail("semantic scene components must be an array");
  }
  if (input.components.length > 1) {
    fail("semantic scene supports at most one storyboard component");
  }
  const components = Object.freeze(
    input.components.map(decodeProjectileStoryboardStateV1),
  );
  const hasCertificateHead = Object.hasOwn(input, "certificateHeadSha256");
  const rawCertificateHead = input.certificateHeadSha256;
  let certificateHeadSha256: string | null | undefined;
  if (hasCertificateHead) {
    if (rawCertificateHead === null) {
      certificateHeadSha256 = null;
    } else if (
      typeof rawCertificateHead === "string" &&
      /^[0-9a-f]{64}$/.test(rawCertificateHead)
    ) {
      certificateHeadSha256 = rawCertificateHead;
    } else {
      fail(
        "semantic scene certificateHeadSha256 must be null or a lowercase digest",
      );
    }
  }

  if (components.length === 0) {
    if (revision !== 0 || certificateHeadSha256 != null) {
      fail("empty semantic scene must be an uncertified revision 0");
    }
  } else {
    if (revision !== 1 + components[0].acceptedRecords.length) {
      fail(
        "semantic scene revision must equal one anchor plus accepted records",
      );
    }
    if (certificateHeadSha256 == null) {
      fail("semantic scene storyboard component requires a certificate head");
    }
  }
  return Object.freeze({
    revision,
    components,
    ...(hasCertificateHead ? { certificateHeadSha256 } : {}),
  });
}

/** Decode and canonicalize a Reflex anchor or Director continuation before POST. */
export function decodeSemanticStoryboardRequestV1(
  value: unknown,
): SemanticStoryboardRequestV1 {
  const input = record(value, "request");
  const shared = [
    "protocol",
    "routingMode",
    "problemSpec",
    "generation",
    "baseScene",
    "baseSemanticScene",
  ] as const;
  if (input.routingMode === "reflex") {
    exactKeys(input, shared, [], "request");
  } else if (input.routingMode === "director") {
    exactKeys(input, [...shared, "prompt"], [], "request");
  } else {
    return fail("request routingMode has an unsupported value");
  }
  if (input.protocol !== PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL) {
    fail(
      `request protocol must equal ${PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL}`,
    );
  }

  const problemSpec = decodePairedProjectileComparisonSpecV1(input.problemSpec);
  const generation = integer(input.generation, "request generation", 1);
  const baseScene = decodeBaseScene(input.baseScene);
  const baseSemanticScene = decodeProjectileStoryboardSemanticSceneStateV1(
    input.baseSemanticScene,
  );
  if (baseScene.revision !== baseSemanticScene.revision) {
    fail("request baseScene and baseSemanticScene revisions must match");
  }
  const existing = baseSemanticScene.components[0];
  if (existing && !sameProblem(existing.problemSpec, problemSpec)) {
    fail("request problemSpec must match the accepted storyboard problem");
  }

  if (input.routingMode === "reflex") {
    if (
      baseScene.revision !== 0 ||
      baseScene.nodes.length !== 0 ||
      baseSemanticScene.components.length !== 0
    ) {
      fail("request storyboard anchor requires empty revision 0 scenes");
    }
    return Object.freeze({
      protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
      routingMode: "reflex",
      problemSpec,
      generation,
      baseScene,
      baseSemanticScene,
    });
  }
  if (!existing) {
    fail("request Director mode requires the certified storyboard anchor");
  }
  return Object.freeze({
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    routingMode: "director",
    problemSpec,
    generation,
    baseScene,
    baseSemanticScene,
    prompt: boundedPrompt(input.prompt),
  });
}
