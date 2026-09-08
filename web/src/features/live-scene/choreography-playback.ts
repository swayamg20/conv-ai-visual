import {
  CHOREOGRAPHY_CUE_ORDER,
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  decodeCompiledCheckpointV2,
  decodeViewportPoseV1,
  planCheckpointChoreography,
  type ChoreographyCueKind,
  type ChoreographyLayout,
  type CompiledCheckpointV2,
  type CompletingSquareCheckpointId,
  type CompletingSquareStage,
  type PlannedCheckpointChoreography,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";
import {
  LIVE_SCENE_MAX_NODES,
  LiveSceneProtocolError,
  type LiveSceneProtocolErrorCode,
} from "@/lib/live-scene/patch";
import {
  createSemanticSceneState,
  type PythagoreanAreaIdentityState,
} from "@/lib/live-scene/semantic";
import { createSceneState } from "@/lib/live-scene/state";

import {
  COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  decodeChoreographySceneStreamEvent,
  type ChoreographySceneCheckpointEvent,
  type ChoreographySemanticSceneState,
  type CompletingSquareStateV2,
} from "./choreography-model-stream";
import type { ChoreographyPlaybackOutcome } from "./choreography-executor";

type UnknownRecord = Record<string, unknown>;

const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const COMPONENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const STAGE_LAST_CHECKPOINT: Readonly<
  Record<
    CompletingSquareStage,
    Exclude<CompletingSquareCheckpointId, "corner_detail">
  >
> = Object.freeze({
  setup: "area_model",
  split: "rearrange_halves",
  complete: "balance_and_complete",
  solve: "solve_roots",
});

/**
 * One trace can cover the full non-pruned checkpoint ledger once. Callers start
 * a fresh trace for Replay so ordinal evidence remains deterministic.
 */
export const LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS =
  (COMPLETING_SQUARE_MAIN_CHECKPOINTS.length + 1) *
  (CHOREOGRAPHY_CUE_ORDER.length + 2);
export const LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS =
  COMPLETING_SQUARE_MAIN_CHECKPOINTS.length + 1;

export const EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE: ChoreographySemanticSceneState =
  Object.freeze({
    revision: 0,
    components: Object.freeze([]),
  });

export interface ChoreographyFrontier {
  readonly scene: SceneState;
  readonly semanticScene: ChoreographySemanticSceneState;
  readonly viewport: ViewportPoseV1 | null;
  readonly layout: ChoreographyLayout | null;
  readonly certificateHeadSha256: string | null;
}

export interface SettledChoreographyFrontier extends ChoreographyFrontier {
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
}

export interface PreparedChoreographyCheckpoint {
  readonly event: ChoreographySceneCheckpointEvent;
  readonly checkpoint: CompiledCheckpointV2;
  readonly layout: ChoreographyLayout;
  readonly base: SettledChoreographyFrontier;
  readonly target: SettledChoreographyFrontier;
  readonly plan: PlannedCheckpointChoreography;
  readonly bootstrappedViewport: boolean;
}

export type ChoreographyCheckpointSettlement =
  "completed" | "cancelled_to_checkpoint";

export interface ChoreographyPresentationReceipt {
  readonly type: "choreography_checkpoint_presented";
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly layout: ChoreographyLayout;
  readonly resultViewport: ViewportPoseV1;
  readonly settlement: ChoreographyCheckpointSettlement;
}

export type ChoreographyPresentationEvaluation =
  | {
      readonly kind: "presented";
      readonly receipt: ChoreographyPresentationReceipt;
    }
  | { readonly kind: "not_presented" }
  | { readonly kind: "invalid" };

export interface AcceptedChoreographyRevision {
  readonly event: ChoreographySceneCheckpointEvent;
  readonly scene: SceneState;
  readonly semanticScene: ChoreographySemanticSceneState;
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
  readonly presentation: ChoreographyPresentationReceipt;
}

export interface PreflightedChoreographyReplay {
  readonly checkpoints: readonly PreparedChoreographyCheckpoint[];
  readonly records: readonly AcceptedChoreographyRevision[];
  readonly frontier: ChoreographyFrontier;
}

const presentedReceiptOwners = new WeakMap<
  ChoreographyPresentationReceipt,
  PreparedChoreographyCheckpoint
>();

interface ChoreographyEvidenceBase {
  readonly ordinal: number;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
}

export interface ChoreographyCueStartedEvidence extends ChoreographyEvidenceBase {
  readonly type: "cueStarted";
  readonly cue: ChoreographyCueKind;
}

export interface ChoreographyFirstCuePresentedEvidence extends ChoreographyEvidenceBase {
  readonly type: "firstCuePresented";
}

export interface ChoreographyCheckpointSettledEvidence extends ChoreographyEvidenceBase {
  readonly type: "checkpointSettled";
  readonly settlement: ChoreographyCheckpointSettlement;
}

export type ChoreographyEvidenceTraceEvent =
  | ChoreographyCueStartedEvidence
  | ChoreographyFirstCuePresentedEvidence
  | ChoreographyCheckpointSettledEvidence;

type EvidenceWithoutOrdinal<Event> =
  Event extends ChoreographyEvidenceTraceEvent ? Omit<Event, "ordinal"> : never;

function fail(
  message: string,
  code: LiveSceneProtocolErrorCode = "invalid_event",
): never {
  throw new LiveSceneProtocolError(code, `choreography playback ${message}`);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return fail(`${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    return fail(`${field} must be a plain object`);
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
    if (!allowed.has(key))
      return fail(`${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key))
      return fail(`${field} is missing field ${key}`);
  }
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum = MAX_SAFE_INTEGER,
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    return fail(
      `${field} must be a safe integer between ${minimum} and ${maximum}`,
    );
  }
  return value as number;
}

function digest(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    return fail(`${field} must be a lowercase SHA-256 digest`);
  }
  return value;
}

function componentId(value: unknown, field: string): string {
  if (typeof value !== "string" || !COMPONENT_ID_PATTERN.test(value)) {
    return fail(`${field} has an unsafe component identifier`);
  }
  return value;
}

function layout(value: unknown): ChoreographyLayout {
  if (value !== "cinematic" && value !== "compact") {
    return fail("layout must be cinematic or compact");
  }
  return value;
}

function checkpointId(value: unknown): CompletingSquareCheckpointId {
  const resolved = COMPLETING_SQUARE_CHECKPOINT_IDS.find(
    (candidate) => candidate === value,
  );
  if (!resolved) return fail("checkpointId is outside the closed vocabulary");
  return resolved;
}

function cueKind(value: unknown): ChoreographyCueKind {
  const resolved = CHOREOGRAPHY_CUE_ORDER.find(
    (candidate) => candidate === value,
  );
  if (!resolved)
    return fail("cue is outside the closed choreography vocabulary");
  return resolved;
}

function sameCanonicalValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function decodeCompletingSquareState(value: unknown): CompletingSquareStateV2 {
  const input = record(value, "completing-square component");
  exactKeys(
    input,
    ["kind", "id", "lastMainCheckpoint", "cornerClarified"],
    [],
    "completing-square component",
  );
  if (input.kind !== "completing_square") {
    return fail("completing-square component kind is invalid");
  }
  const lastMainCheckpoint =
    input.lastMainCheckpoint === null
      ? null
      : COMPLETING_SQUARE_MAIN_CHECKPOINTS.find(
          (candidate) => candidate === input.lastMainCheckpoint,
        );
  if (lastMainCheckpoint === undefined) {
    return fail("completing-square component checkpoint is invalid");
  }
  if (typeof input.cornerClarified !== "boolean") {
    return fail("completing-square component cornerClarified must be boolean");
  }
  if (
    input.cornerClarified &&
    (lastMainCheckpoint === null ||
      COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(lastMainCheckpoint) <
        COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf("missing_corner"))
  ) {
    return fail(
      "completing-square component cannot clarify before missing_corner",
      "revision_mismatch",
    );
  }
  return Object.freeze({
    kind: "completing_square",
    id: componentId(input.id, "completing-square component id"),
    lastMainCheckpoint,
    cornerClarified: input.cornerClarified,
  });
}

function decodePythagoreanState(value: unknown): PythagoreanAreaIdentityState {
  const semantic = createSemanticSceneState({
    revision: 0,
    components: [value as PythagoreanAreaIdentityState],
  });
  return semantic.components[0];
}

/** Strictly clone and freeze the mixed V2 semantic frontier. */
export function createChoreographySemanticSceneState(
  value: unknown,
): ChoreographySemanticSceneState {
  const input = record(value, "choreography semantic scene");
  exactKeys(
    input,
    ["revision", "components"],
    ["certificateHeadSha256"],
    "choreography semantic scene",
  );
  const revision = integer(input.revision, "semantic revision", 0);
  if (!Array.isArray(input.components)) {
    return fail("semantic components must be an array");
  }
  if (input.components.length > LIVE_SCENE_MAX_NODES) {
    return fail(
      `semantic components exceed ${LIVE_SCENE_MAX_NODES}`,
      "budget_exceeded",
    );
  }

  const components = input.components.map((candidate) => {
    const component = record(candidate, "semantic component");
    if (component.kind === "completing_square") {
      return decodeCompletingSquareState(component);
    }
    if (component.kind === "pythagorean_area_identity") {
      return decodePythagoreanState(component);
    }
    return fail("semantic component kind is outside the closed vocabulary");
  });
  const ids = new Set<string>();
  for (const component of components) {
    if (ids.has(component.id)) {
      return fail(`semantic component id ${component.id} is duplicated`);
    }
    ids.add(component.id);
  }

  const headValue = input.certificateHeadSha256;
  const head =
    headValue === undefined || headValue === null
      ? null
      : digest(headValue, "semantic certificateHeadSha256");
  if (revision === 0 && (components.length > 0 || head !== null)) {
    return fail(
      "revision zero must have no components or certificate head",
      "revision_mismatch",
    );
  }
  if (revision > 0 && (components.length === 0 || head === null)) {
    return fail(
      "a committed semantic frontier requires components and a certificate head",
      "revision_mismatch",
    );
  }
  if (
    revision > 0 &&
    components.some(
      (component) =>
        component.kind === "completing_square" &&
        component.lastMainCheckpoint === null,
    )
  ) {
    return fail(
      "a committed completing-square component requires a checkpoint frontier",
      "revision_mismatch",
    );
  }

  return Object.freeze({
    revision,
    components: Object.freeze(components),
    ...(head === null ? {} : { certificateHeadSha256: head }),
  });
}

/** Validate a paired low-level, semantic, camera, and certificate frontier. */
export function createChoreographyFrontier(
  value: unknown,
): ChoreographyFrontier {
  const input = record(value, "choreography frontier");
  exactKeys(
    input,
    ["scene", "semanticScene", "viewport", "layout", "certificateHeadSha256"],
    [],
    "choreography frontier",
  );
  const scene = createSceneState(input.scene as SceneState);
  const semanticScene = createChoreographySemanticSceneState(
    input.semanticScene,
  );
  if (scene.revision !== semanticScene.revision) {
    return fail(
      "low-level and semantic revisions must match",
      "revision_mismatch",
    );
  }
  if (
    scene.revision === 0 &&
    (scene.nodes.length > 0 || semanticScene.components.length > 0)
  ) {
    return fail("revision-zero frontier must be empty", "revision_mismatch");
  }

  const certificateHeadSha256 =
    input.certificateHeadSha256 === null
      ? null
      : digest(input.certificateHeadSha256, "frontier certificate head");
  const semanticHead = semanticScene.certificateHeadSha256 ?? null;
  if (certificateHeadSha256 !== semanticHead) {
    return fail(
      "explicit certificate head must match the semantic frontier",
      "revision_mismatch",
    );
  }
  if (scene.revision === 0 && certificateHeadSha256 !== null) {
    return fail(
      "revision-zero frontier cannot carry a certificate head",
      "revision_mismatch",
    );
  }
  if (scene.revision > 0 && certificateHeadSha256 === null) {
    return fail(
      "committed frontier requires a certificate head",
      "revision_mismatch",
    );
  }
  if (input.viewport === null && scene.revision > 0) {
    return fail(
      "only an empty revision-zero frontier may omit its viewport",
      "revision_mismatch",
    );
  }
  const selectedLayout = input.layout === null ? null : layout(input.layout);
  if ((input.viewport === null) !== (selectedLayout === null)) {
    return fail(
      "viewport and locked layout must either both be absent or both be present",
      "revision_mismatch",
    );
  }
  if (selectedLayout === null && scene.revision > 0) {
    return fail(
      "a committed frontier requires a locked layout",
      "revision_mismatch",
    );
  }

  return Object.freeze({
    scene,
    semanticScene,
    viewport:
      input.viewport === null ? null : decodeViewportPoseV1(input.viewport),
    layout: selectedLayout,
    certificateHeadSha256,
  });
}

function compiledCheckpoint(
  event: ChoreographySceneCheckpointEvent,
): CompiledCheckpointV2 {
  return decodeCompiledCheckpointV2({
    beat: event.semantic.beat,
    checkpointId: event.semantic.checkpointId,
    patch: event.patch,
    receipt: event.semantic.receipt,
    presentation: event.semantic.presentation,
    choreography: event.semantic.choreography,
    certificate: event.semantic.certificate,
  });
}

function decodeCheckpointEvent(
  value: unknown,
): ChoreographySceneCheckpointEvent {
  const event = decodeChoreographySceneStreamEvent(value);
  if (event.type !== "choreography_scene_checkpoint") {
    return fail("only checkpoint events can be prepared");
  }
  return event;
}

function nextMainCheckpoint(
  checkpoint: CompletingSquareStateV2["lastMainCheckpoint"],
): CompletingSquareStateV2["lastMainCheckpoint"] {
  if (checkpoint === null) return COMPLETING_SQUARE_MAIN_CHECKPOINTS[0];
  const nextIndex = COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(checkpoint) + 1;
  return COMPLETING_SQUARE_MAIN_CHECKPOINTS[nextIndex] ?? null;
}

function applySemanticCheckpoint(
  current: ChoreographySemanticSceneState,
  event: ChoreographySceneCheckpointEvent,
): ChoreographySemanticSceneState {
  if (
    event.baseRevision !== current.revision ||
    event.semantic.semanticBaseRevision !== current.revision
  ) {
    return fail(
      "checkpoint does not join the semantic base revision",
      "revision_mismatch",
    );
  }

  const componentId = event.semantic.beat.componentId;
  const existingIndex = current.components.findIndex(
    (component) => component.id === componentId,
  );
  const existing =
    existingIndex === -1 ? undefined : current.components[existingIndex];
  if (
    current.components.length > 1 ||
    (current.components.length === 1 && existing === undefined)
  ) {
    return fail(
      "checkpoint must continue the sole accepted completing-square component",
      "revision_mismatch",
    );
  }
  if (existing && existing.kind !== "completing_square") {
    return fail(
      "checkpoint component kind does not match the existing component",
      "revision_mismatch",
    );
  }

  const checkpoint = event.semantic.checkpointId;
  const result = event.semantic.resultComponent;
  if (checkpoint === "corner_detail") {
    if (event.semantic.beat.route.intent !== "clarify_corner") {
      return fail("corner_detail requires the clarify_corner route");
    }
    if (
      !existing ||
      existing.lastMainCheckpoint !== "missing_corner" ||
      existing.cornerClarified
    ) {
      return fail(
        "corner_detail is legal only at the unclarified missing_corner frontier",
        "revision_mismatch",
      );
    }
  } else {
    const route = event.semantic.beat.route;
    if (route.intent !== "advance") {
      return fail("main checkpoints require an advance route");
    }
    if (
      COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(checkpoint) >
      COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
        STAGE_LAST_CHECKPOINT[route.targetStage],
      )
    ) {
      return fail(
        "main checkpoint exceeds the routed target stage",
        "revision_mismatch",
      );
    }
    const expected = nextMainCheckpoint(existing?.lastMainCheckpoint ?? null);
    if (expected !== checkpoint) {
      return fail(
        "main checkpoint is not the exact next semantic predecessor",
        "revision_mismatch",
      );
    }
    if (result.cornerClarified !== (existing?.cornerClarified ?? false)) {
      return fail(
        "main checkpoint must preserve the corner clarification bit",
        "revision_mismatch",
      );
    }
  }

  const components = [...current.components];
  if (existingIndex === -1) components.push(result);
  else components[existingIndex] = result;
  return createChoreographySemanticSceneState({
    revision: event.resultRevision,
    components,
    certificateHeadSha256: event.semantic.certificate.certificateSha256,
  });
}

/**
 * Atomically prepare one whole checkpoint without mutating the accepted
 * frontier. Geometry, patch/cue bindings, camera joins, and certificate joins
 * remain owned by the existing decoder and planner.
 */
export function prepareChoreographyCheckpoint(
  frontierValue: ChoreographyFrontier,
  eventValue: unknown,
  layoutValue: ChoreographyLayout,
): PreparedChoreographyCheckpoint {
  const frontier = createChoreographyFrontier(frontierValue);
  const event = decodeCheckpointEvent(eventValue);
  const selectedLayout = layout(layoutValue);
  if (frontier.layout !== null && frontier.layout !== selectedLayout) {
    return fail(
      "checkpoint cannot change the locked choreography layout",
      "revision_mismatch",
    );
  }
  const checkpoint = compiledCheckpoint(event);
  const bootstrappedViewport =
    frontier.viewport === null && frontier.layout === null;
  if (
    bootstrappedViewport &&
    (frontier.scene.revision !== 0 ||
      frontier.scene.nodes.length !== 0 ||
      frontier.semanticScene.revision !== 0 ||
      frontier.semanticScene.components.length !== 0 ||
      frontier.certificateHeadSha256 !== null ||
      frontier.layout !== null)
  ) {
    return fail(
      "only the empty revision-zero frontier may bootstrap a viewport",
      "revision_mismatch",
    );
  }

  const currentViewport =
    frontier.viewport ?? checkpoint.presentation.baseViewports[selectedLayout];
  const base: SettledChoreographyFrontier = Object.freeze({
    ...frontier,
    viewport: decodeViewportPoseV1(currentViewport),
    layout: selectedLayout,
  });
  const semanticTarget = applySemanticCheckpoint(frontier.semanticScene, event);
  const plan = planCheckpointChoreography({
    checkpoint,
    currentScene: frontier.scene,
    layout: selectedLayout,
    currentViewport: base.viewport,
    previousCertificateSha256: frontier.certificateHeadSha256,
  });
  if (
    plan.targetScene.revision !== event.resultRevision ||
    semanticTarget.revision !== event.resultRevision
  ) {
    return fail(
      "planned low-level and semantic targets must share the result revision",
      "revision_mismatch",
    );
  }

  const target: SettledChoreographyFrontier = Object.freeze({
    scene: plan.targetScene,
    semanticScene: semanticTarget,
    viewport: decodeViewportPoseV1(plan.resultViewport),
    layout: selectedLayout,
    certificateHeadSha256: event.semantic.certificate.certificateSha256,
  });
  return Object.freeze({
    event,
    checkpoint,
    layout: selectedLayout,
    base,
    target,
    plan,
    bootstrappedViewport,
  });
}

function settlement(value: unknown): ChoreographyCheckpointSettlement {
  if (value !== "completed" && value !== "cancelled_to_checkpoint") {
    return fail("checkpoint settlement is outside the closed vocabulary");
  }
  return value;
}

export function decodeChoreographyPresentationReceipt(
  value: unknown,
): ChoreographyPresentationReceipt {
  const input = record(value, "choreography presentation receipt");
  exactKeys(
    input,
    [
      "type",
      "checkpointId",
      "certificateSha256",
      "sceneRevision",
      "semanticRevision",
      "layout",
      "resultViewport",
      "settlement",
    ],
    [],
    "choreography presentation receipt",
  );
  if (input.type !== "choreography_checkpoint_presented") {
    return fail("presentation receipt type is invalid");
  }
  return Object.freeze({
    type: "choreography_checkpoint_presented",
    checkpointId: checkpointId(input.checkpointId),
    certificateSha256: digest(
      input.certificateSha256,
      "presentation receipt certificateSha256",
    ),
    sceneRevision: integer(input.sceneRevision, "receipt sceneRevision", 1),
    semanticRevision: integer(
      input.semanticRevision,
      "receipt semanticRevision",
      1,
    ),
    layout: layout(input.layout),
    resultViewport: decodeViewportPoseV1(input.resultViewport),
    settlement: settlement(input.settlement),
  });
}

function createChoreographyPresentationReceipt(
  prepared: PreparedChoreographyCheckpoint,
  settlementValue: ChoreographyCheckpointSettlement,
): ChoreographyPresentationReceipt {
  return decodeChoreographyPresentationReceipt({
    type: "choreography_checkpoint_presented",
    checkpointId: prepared.event.semantic.checkpointId,
    certificateSha256: prepared.event.semantic.certificate.certificateSha256,
    sceneRevision: prepared.target.scene.revision,
    semanticRevision: prepared.target.semanticScene.revision,
    layout: prepared.layout,
    resultViewport: prepared.target.viewport,
    settlement: settlementValue,
  });
}

/** Decode the renderer's terminal value at the runtime trust boundary. */
export function decodeChoreographyPlaybackOutcome(
  value: unknown,
): ChoreographyPlaybackOutcome {
  const input = record(value, "choreography playback outcome");
  exactKeys(
    input,
    ["status", "firstCuePresented"],
    ["error"],
    "choreography playback outcome",
  );
  if (
    input.status !== "completed" &&
    input.status !== "cancelled_before_presented" &&
    input.status !== "cancelled_to_checkpoint" &&
    input.status !== "failed"
  ) {
    return fail("playback outcome status is outside the closed vocabulary");
  }
  if (typeof input.firstCuePresented !== "boolean") {
    return fail("playback outcome firstCuePresented must be boolean");
  }
  if (
    Object.hasOwn(input, "error") &&
    (typeof input.error !== "string" ||
      input.error.length === 0 ||
      [...input.error].length > 512)
  ) {
    return fail("playback outcome error must be 1 to 512 characters");
  }
  if (input.status !== "failed" && Object.hasOwn(input, "error")) {
    return fail("only failed playback may include an error");
  }
  const error = typeof input.error === "string" ? input.error : undefined;
  return Object.freeze({
    status: input.status,
    firstCuePresented: input.firstCuePresented,
    ...(error === undefined ? {} : { error }),
  });
}

/**
 * Turn only a coherent terminal executor result into a post-paint receipt.
 * The earlier first-cue promise is deliberately insufficient to commit.
 */
export function evaluateChoreographyPresentation(
  prepared: PreparedChoreographyCheckpoint,
  outcomeValue: unknown,
): ChoreographyPresentationEvaluation {
  let outcome: ChoreographyPlaybackOutcome;
  try {
    outcome = decodeChoreographyPlaybackOutcome(outcomeValue);
  } catch {
    return Object.freeze({ kind: "invalid" });
  }
  if (
    outcome.status === "cancelled_before_presented" &&
    !outcome.firstCuePresented
  ) {
    return Object.freeze({ kind: "not_presented" });
  }
  if (
    outcome.firstCuePresented &&
    (outcome.status === "completed" ||
      outcome.status === "cancelled_to_checkpoint")
  ) {
    const receipt = createChoreographyPresentationReceipt(
      prepared,
      outcome.status,
    );
    presentedReceiptOwners.set(receipt, prepared);
    return Object.freeze({
      kind: "presented",
      receipt,
    });
  }
  return Object.freeze({ kind: "invalid" });
}

function createAcceptedChoreographyRevisionFromReceipt(
  prepared: PreparedChoreographyCheckpoint,
  receiptValue: unknown,
): AcceptedChoreographyRevision {
  const presentation = decodeChoreographyPresentationReceipt(receiptValue);
  const expected = createChoreographyPresentationReceipt(
    prepared,
    presentation.settlement,
  );
  if (!sameCanonicalValue(presentation, expected)) {
    return fail(
      "presentation receipt does not bind the prepared target checkpoint",
      "revision_mismatch",
    );
  }
  return Object.freeze({
    event: prepared.event,
    scene: prepared.target.scene,
    semanticScene: prepared.target.semanticScene,
    viewport: prepared.target.viewport,
    layout: prepared.layout,
    presentation,
  });
}

/** Bind only a module-issued terminal presentation result to live state. */
export function createAcceptedChoreographyRevision(
  prepared: PreparedChoreographyCheckpoint,
  evaluationValue: unknown,
): AcceptedChoreographyRevision {
  const evaluation = record(
    evaluationValue,
    "choreography presentation evaluation",
  );
  exactKeys(
    evaluation,
    ["kind", "receipt"],
    [],
    "presented choreography evaluation",
  );
  if (
    evaluation.kind !== "presented" ||
    typeof evaluation.receipt !== "object" ||
    evaluation.receipt === null ||
    presentedReceiptOwners.get(
      evaluation.receipt as ChoreographyPresentationReceipt,
    ) !== prepared
  ) {
    return fail(
      "acceptance requires this checkpoint's terminal presented evaluation",
      "revision_mismatch",
    );
  }
  return createAcceptedChoreographyRevisionFromReceipt(
    prepared,
    evaluation.receipt,
  );
}

/**
 * Compare a stored ledger record with a freshly re-prepared replay transition.
 * Any malformed, extra, or drifted field returns false rather than partially
 * trusting the stored checkpoint.
 */
export function choreographyReplayRecordMatches(
  recordValue: unknown,
  prepared: PreparedChoreographyCheckpoint,
): recordValue is AcceptedChoreographyRevision {
  try {
    const input = record(recordValue, "accepted choreography record");
    exactKeys(
      input,
      ["event", "scene", "semanticScene", "viewport", "layout", "presentation"],
      [],
      "accepted choreography record",
    );
    const event = decodeCheckpointEvent(input.event);
    const scene = createSceneState(input.scene as SceneState);
    const semanticScene = createChoreographySemanticSceneState(
      input.semanticScene,
    );
    const viewport = decodeViewportPoseV1(input.viewport);
    const selectedLayout = layout(input.layout);
    const presentation = decodeChoreographyPresentationReceipt(
      input.presentation,
    );
    const normalized = Object.freeze({
      event,
      scene,
      semanticScene,
      viewport,
      layout: selectedLayout,
      presentation,
    });
    const expected = createAcceptedChoreographyRevisionFromReceipt(
      prepared,
      presentation,
    );
    return sameCanonicalValue(normalized, expected);
  } catch {
    return false;
  }
}

/**
 * Re-prepare every stored checkpoint from the empty frontier before Replay.
 * Callers must not invoke a renderer until this whole-ledger preflight returns.
 */
export function preflightChoreographyReplay(
  value: unknown,
): PreflightedChoreographyReplay {
  if (!Array.isArray(value)) return fail("replay ledger must be an array");
  if (value.length > LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS) {
    return fail(
      `replay ledger exceeds ${LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS} checkpoints`,
      "budget_exceeded",
    );
  }

  let frontier: ChoreographyFrontier = createChoreographyFrontier({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });
  const checkpoints: PreparedChoreographyCheckpoint[] = [];
  const records: AcceptedChoreographyRevision[] = [];
  for (const recordValue of value) {
    const input = record(recordValue, "accepted choreography record");
    exactKeys(
      input,
      ["event", "scene", "semanticScene", "viewport", "layout", "presentation"],
      [],
      "accepted choreography record",
    );
    const prepared = prepareChoreographyCheckpoint(
      frontier,
      input.event,
      layout(input.layout),
    );
    if (!choreographyReplayRecordMatches(input, prepared)) {
      return fail(
        "replay ledger contains a checkpoint that does not exact-match its prepared transition",
        "revision_mismatch",
      );
    }
    const accepted = createAcceptedChoreographyRevisionFromReceipt(
      prepared,
      input.presentation,
    );
    checkpoints.push(prepared);
    records.push(accepted);
    frontier = prepared.target;
  }

  return Object.freeze({
    checkpoints: Object.freeze(checkpoints),
    records: Object.freeze(records),
    frontier,
  });
}

function evidenceBase(
  prepared: PreparedChoreographyCheckpoint,
): Omit<ChoreographyEvidenceBase, "ordinal"> {
  return {
    generation: prepared.event.generation,
    attempt: prepared.event.attempt,
    sequence: prepared.event.sequence,
    checkpointId: prepared.event.semantic.checkpointId,
    certificateSha256: prepared.event.semantic.certificate.certificateSha256,
  };
}

function decodeEvidenceEvent(
  value: unknown,
  expectedOrdinal: number,
): ChoreographyEvidenceTraceEvent {
  const input = record(value, "choreography evidence event");
  const sharedKeys = [
    "type",
    "ordinal",
    "generation",
    "attempt",
    "sequence",
    "checkpointId",
    "certificateSha256",
  ];
  if (input.type === "cueStarted") {
    exactKeys(input, [...sharedKeys, "cue"], [], "cueStarted evidence");
  } else if (input.type === "firstCuePresented") {
    exactKeys(input, sharedKeys, [], "firstCuePresented evidence");
  } else if (input.type === "checkpointSettled") {
    exactKeys(
      input,
      [...sharedKeys, "settlement"],
      [],
      "checkpointSettled evidence",
    );
  } else {
    return fail("evidence event type is outside the closed vocabulary");
  }
  const common = {
    ordinal: integer(input.ordinal, "evidence ordinal", 1),
    generation: integer(input.generation, "evidence generation", 1),
    attempt: integer(input.attempt, "evidence attempt", 1, 2),
    sequence: integer(input.sequence, "evidence sequence", 1, 8),
    checkpointId: checkpointId(input.checkpointId),
    certificateSha256: digest(
      input.certificateSha256,
      "evidence certificateSha256",
    ),
  };
  if (common.ordinal !== expectedOrdinal) {
    return fail("evidence ordinals must be contiguous", "revision_mismatch");
  }
  if (input.type === "cueStarted") {
    return Object.freeze({
      type: "cueStarted",
      ...common,
      cue: cueKind(input.cue),
    });
  }
  if (input.type === "firstCuePresented") {
    return Object.freeze({ type: "firstCuePresented", ...common });
  }
  if (input.type === "checkpointSettled") {
    return Object.freeze({
      type: "checkpointSettled",
      ...common,
      settlement: settlement(input.settlement),
    });
  }
  return fail("evidence event type became invalid");
}

function evidenceKey(event: ChoreographyEvidenceTraceEvent): string {
  return `${event.generation}:${event.attempt}:${event.sequence}:${event.checkpointId}:${event.certificateSha256}`;
}

function preparedEvidenceKey(prepared: PreparedChoreographyCheckpoint): string {
  return evidenceKey({
    type: "firstCuePresented",
    ordinal: 1,
    ...evidenceBase(prepared),
  });
}

function hasExactStartedCues(
  trace: readonly ChoreographyEvidenceTraceEvent[],
  prepared: PreparedChoreographyCheckpoint,
): boolean {
  const expectedCues = prepared.plan.choreographyPlan.phase.cues.map(
    (cue) => cue.cue,
  );
  const key = preparedEvidenceKey(prepared);
  const started = trace.filter(
    (event): event is ChoreographyCueStartedEvidence =>
      event.type === "cueStarted" && evidenceKey(event) === key,
  );
  return (
    started.length === expectedCues.length &&
    started.every((event, index) => event.cue === expectedCues[index])
  );
}

/** Strictly validate, clone, freeze, and lifecycle-check a closed trace. */
export function createChoreographyEvidenceTrace(
  value: unknown = [],
): readonly ChoreographyEvidenceTraceEvent[] {
  if (!Array.isArray(value)) return fail("evidence trace must be an array");
  if (value.length > LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS) {
    return fail(
      `evidence trace exceeds ${LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS} events`,
      "budget_exceeded",
    );
  }
  const decoded = value.map((event, index) =>
    decodeEvidenceEvent(event, index + 1),
  );
  let activeKey: string | null = null;
  let lastCueOrder = -1;
  let presented = false;
  let settled = false;
  const closedKeys = new Set<string>();
  for (const event of decoded) {
    const key = evidenceKey(event);
    if (activeKey !== key) {
      if (activeKey !== null && !settled) {
        return fail("evidence cannot interleave checkpoint groups");
      }
      if (closedKeys.has(key)) {
        return fail("evidence cannot reopen a settled checkpoint group");
      }
      activeKey = key;
      lastCueOrder = -1;
      presented = false;
      settled = false;
    }
    if (event.type === "cueStarted") {
      const cueOrder = CHOREOGRAPHY_CUE_ORDER.indexOf(event.cue);
      if (presented || settled || cueOrder <= lastCueOrder) {
        return fail("cueStarted evidence is duplicated or noncanonical");
      }
      lastCueOrder = cueOrder;
    } else if (event.type === "firstCuePresented") {
      if (presented || settled || lastCueOrder === -1) {
        return fail("firstCuePresented requires one or more started cues");
      }
      presented = true;
    } else {
      if (!presented || settled) {
        return fail("checkpointSettled requires firstCuePresented");
      }
      settled = true;
      closedKeys.add(key);
    }
  }
  return Object.freeze(decoded);
}

function appendEvidence(
  traceValue: readonly ChoreographyEvidenceTraceEvent[],
  event: EvidenceWithoutOrdinal<ChoreographyEvidenceTraceEvent>,
): readonly ChoreographyEvidenceTraceEvent[] {
  const trace = createChoreographyEvidenceTrace(traceValue);
  if (trace.length >= LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS) {
    return fail(
      `evidence trace exceeds ${LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS} events`,
      "budget_exceeded",
    );
  }
  return createChoreographyEvidenceTrace([
    ...trace,
    { ...event, ordinal: trace.length + 1 },
  ]);
}

/** Record one actually-started closed cue in certified canonical order. */
export function appendChoreographyCueStarted(
  traceValue: readonly ChoreographyEvidenceTraceEvent[],
  prepared: PreparedChoreographyCheckpoint,
  cueValue: ChoreographyCueKind,
): readonly ChoreographyEvidenceTraceEvent[] {
  const trace = createChoreographyEvidenceTrace(traceValue);
  const expectedCues = prepared.plan.choreographyPlan.phase.cues.map(
    (cue) => cue.cue,
  );
  const key = preparedEvidenceKey(prepared);
  const startedCount = trace.filter(
    (event) => event.type === "cueStarted" && evidenceKey(event) === key,
  ).length;
  const cue = cueKind(cueValue);
  if (expectedCues[startedCount] !== cue) {
    return fail("cueStarted does not match the next certified cue");
  }
  return appendEvidence(trace, {
    type: "cueStarted",
    ...evidenceBase(prepared),
    cue,
  });
}

/** Record the first post-paint acknowledgement without advancing a frontier. */
export function appendChoreographyFirstCuePresented(
  traceValue: readonly ChoreographyEvidenceTraceEvent[],
  prepared: PreparedChoreographyCheckpoint,
): readonly ChoreographyEvidenceTraceEvent[] {
  const trace = createChoreographyEvidenceTrace(traceValue);
  if (!hasExactStartedCues(trace, prepared)) {
    return fail(
      "firstCuePresented requires the exact certified cue-start order",
    );
  }
  return appendEvidence(trace, {
    type: "firstCuePresented",
    ...evidenceBase(prepared),
  });
}

/** Record settlement only after an exact accepted whole-checkpoint record. */
export function appendChoreographyCheckpointSettled(
  traceValue: readonly ChoreographyEvidenceTraceEvent[],
  prepared: PreparedChoreographyCheckpoint,
  accepted: AcceptedChoreographyRevision,
): readonly ChoreographyEvidenceTraceEvent[] {
  const trace = createChoreographyEvidenceTrace(traceValue);
  if (!choreographyReplayRecordMatches(accepted, prepared)) {
    return fail("settlement evidence requires an exact accepted checkpoint");
  }
  if (!hasExactStartedCues(trace, prepared)) {
    return fail("settlement evidence requires the exact certified cue set");
  }
  return appendEvidence(trace, {
    type: "checkpointSettled",
    ...evidenceBase(prepared),
    settlement: accepted.presentation.settlement,
  });
}

/**
 * Remove only the active checkpoint's evidence when its terminal result did
 * not commit, retaining every previously settled checkpoint verbatim.
 */
export function discardUnacceptedChoreographyEvidence(
  traceValue: unknown,
  prepared: PreparedChoreographyCheckpoint,
  outcomeValue: unknown,
): readonly ChoreographyEvidenceTraceEvent[] {
  const trace = createChoreographyEvidenceTrace(traceValue);
  if (
    evaluateChoreographyPresentation(prepared, outcomeValue).kind ===
    "presented"
  ) {
    return fail("presented checkpoint evidence cannot be discarded");
  }
  const key = preparedEvidenceKey(prepared);
  let start = trace.length;
  while (start > 0 && evidenceKey(trace[start - 1]) === key) start -= 1;
  if (start === trace.length) {
    if (
      trace.length > 0 &&
      trace[trace.length - 1].type !== "checkpointSettled"
    ) {
      return fail("active evidence belongs to a different checkpoint");
    }
    return trace;
  }
  if (trace.slice(start).some((event) => event.type === "checkpointSettled")) {
    return fail("settled checkpoint evidence cannot be discarded");
  }
  return createChoreographyEvidenceTrace(trace.slice(0, start));
}

/**
 * Close the evidence boundary by requiring a byte-exact event sequence for
 * every record in a wholly preflighted accepted ledger.
 */
export function choreographyEvidenceTraceMatchesAccepted(
  traceValue: unknown,
  recordsValue: unknown,
): boolean {
  try {
    const trace = createChoreographyEvidenceTrace(traceValue);
    const replay = preflightChoreographyReplay(recordsValue);
    let expected: readonly ChoreographyEvidenceTraceEvent[] = [];
    replay.checkpoints.forEach((prepared, index) => {
      for (const cue of prepared.plan.choreographyPlan.phase.cues) {
        expected = appendChoreographyCueStarted(expected, prepared, cue.cue);
      }
      expected = appendChoreographyFirstCuePresented(expected, prepared);
      expected = appendChoreographyCheckpointSettled(
        expected,
        prepared,
        replay.records[index],
      );
    });
    return sameCanonicalValue(trace, expected);
  } catch {
    return false;
  }
}
