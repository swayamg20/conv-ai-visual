import {
  decodeViewportPoseV1,
  planParametricCheckpointChoreography,
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
  type PlannedCheckpointChoreography,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";
import {
  PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  decodeParametricCompletingSquareStateV1,
  type ParametricCompletingSquareMainCheckpoint,
  type ParametricCompletingSquareStateV1,
} from "@/lib/live-scene/parametric-choreography";
import {
  decodeParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneCheckpointEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";
import { decodeCompiledCheckpointV3 } from "@/lib/live-scene/parametric-checkpoint";
import { LiveSceneProtocolError } from "@/lib/live-scene/patch";
import { sameCompletingSquareProblem } from "@/lib/live-scene/parametric-problem";
import { createSceneState } from "@/lib/live-scene/state";

import type { ChoreographyPlaybackOutcome } from "./choreography-executor";

const STAGE_LAST_CHECKPOINT: Readonly<
  Record<string, ParametricCompletingSquareMainCheckpoint>
> = Object.freeze({
  setup: "area_model",
  split: "rearrange_halves",
  complete: "balance_and_complete",
  solve: "solve_roots",
});

export interface ParametricSemanticSceneState {
  readonly revision: number;
  readonly components: readonly ParametricCompletingSquareStateV1[];
  readonly certificateHeadSha256?: string;
}

export const EMPTY_PARAMETRIC_SEMANTIC_SCENE: ParametricSemanticSceneState =
  Object.freeze({ revision: 0, components: Object.freeze([]) });

export interface ParametricChoreographyFrontier {
  readonly scene: SceneState;
  readonly semanticScene: ParametricSemanticSceneState;
  readonly viewport: ViewportPoseV1 | null;
  readonly layout: ChoreographyLayout | null;
  readonly certificateHeadSha256: string | null;
}

export interface SettledParametricChoreographyFrontier extends ParametricChoreographyFrontier {
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
}

export interface PreparedParametricChoreographyCheckpoint {
  readonly event: ParametricChoreographySceneCheckpointEventV3;
  readonly layout: ChoreographyLayout;
  readonly base: SettledParametricChoreographyFrontier;
  readonly target: SettledParametricChoreographyFrontier;
  readonly plan: PlannedCheckpointChoreography;
  readonly bootstrappedViewport: boolean;
}

export interface ParametricPresentationReceipt {
  readonly type: "parametric_choreography_checkpoint_presented";
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly layout: ChoreographyLayout;
  readonly resultViewport: ViewportPoseV1;
  readonly settlement: "completed" | "cancelled_to_checkpoint";
}

export interface AcceptedParametricChoreographyCheckpoint {
  readonly event: ParametricChoreographySceneCheckpointEventV3;
  readonly scene: SceneState;
  readonly semanticScene: ParametricSemanticSceneState;
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
  readonly presentation: ParametricPresentationReceipt;
}

export interface PreflightedParametricReplay {
  readonly checkpoints: readonly PreparedParametricChoreographyCheckpoint[];
  readonly records: readonly AcceptedParametricChoreographyCheckpoint[];
  readonly frontier: ParametricChoreographyFrontier;
}

export const EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER: ParametricChoreographyFrontier =
  Object.freeze({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: EMPTY_PARAMETRIC_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "revision_mismatch",
    `parametric choreography playback ${message}`,
  );
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function nextMainCheckpoint(
  current: ParametricCompletingSquareMainCheckpoint | null,
): ParametricCompletingSquareMainCheckpoint | null {
  if (current === null) return PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS[0];
  const index =
    PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(current) + 1;
  return PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS[index] ?? null;
}

function applySemanticCheckpoint(
  current: ParametricSemanticSceneState,
  event: ParametricChoreographySceneCheckpointEventV3,
): ParametricSemanticSceneState {
  if (
    current.revision !== event.baseRevision ||
    event.semantic.semanticBaseRevision !== current.revision ||
    event.semantic.semanticBaseCertificateSha256 !==
      (current.certificateHeadSha256 ?? null)
  ) {
    return fail("checkpoint does not join the semantic frontier");
  }
  if (current.components.length > 1) {
    return fail("frontier may contain only one parametric component");
  }

  const result = decodeParametricCompletingSquareStateV1(
    event.semantic.resultComponent,
  );
  const existing = current.components[0];
  if (existing) {
    if (
      existing.id !== result.id ||
      !sameCompletingSquareProblem(existing.problemSpec, result.problemSpec)
    ) {
      return fail("checkpoint changed the accepted problem identity");
    }
  }

  const checkpoint = event.semantic.checkpointId;
  const route = event.semantic.beat.route;
  if (checkpoint === "corner_detail") {
    if (
      route.intent !== "clarify_corner" ||
      !existing ||
      existing.lastMainCheckpoint !== "missing_corner" ||
      existing.cornerClarified ||
      result.lastMainCheckpoint !== "missing_corner" ||
      !result.cornerClarified
    ) {
      return fail(
        "corner detail does not join the unclarified corner frontier",
      );
    }
  } else {
    if (route.intent !== "advance") {
      return fail("main checkpoints require an advance route");
    }
    const expected = nextMainCheckpoint(existing?.lastMainCheckpoint ?? null);
    if (checkpoint !== expected || result.lastMainCheckpoint !== checkpoint) {
      return fail("main checkpoint is not the exact next predecessor");
    }
    const ceiling = STAGE_LAST_CHECKPOINT[route.targetStage];
    if (
      !ceiling ||
      PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(checkpoint) >
        PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(ceiling)
    ) {
      return fail("main checkpoint exceeds the requested stage");
    }
    if (result.cornerClarified !== (existing?.cornerClarified ?? false)) {
      return fail("main checkpoint changed the corner clarification bit");
    }
  }

  return Object.freeze({
    revision: event.resultRevision,
    components: Object.freeze([result]),
    certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
  });
}

function checkpointEvent(
  value: unknown,
): ParametricChoreographySceneCheckpointEventV3 {
  const decoded = decodeParametricChoreographySceneStreamEventV3(value);
  if (decoded.type !== "parametric_choreography_scene_checkpoint") {
    return fail("only checkpoint events can be prepared");
  }
  return decoded;
}

/** Validate and stage one whole V3 checkpoint without mutating its frontier. */
export function prepareParametricChoreographyCheckpoint(
  frontier: ParametricChoreographyFrontier,
  eventValue: unknown,
  layout: ChoreographyLayout,
): PreparedParametricChoreographyCheckpoint {
  if (layout !== "cinematic" && layout !== "compact") {
    return fail("layout is outside the closed vocabulary");
  }
  if (frontier.scene.revision !== frontier.semanticScene.revision) {
    return fail("low-level and semantic frontier revisions differ");
  }
  if (
    (frontier.semanticScene.certificateHeadSha256 ?? null) !==
    frontier.certificateHeadSha256
  ) {
    return fail("semantic and explicit certificate heads differ");
  }
  if (frontier.layout !== null && frontier.layout !== layout) {
    return fail("checkpoint changed the locked layout");
  }

  const event = checkpointEvent(eventValue);
  if (
    event.baseRevision !== frontier.scene.revision ||
    event.semantic.semanticBaseRevision !== frontier.semanticScene.revision
  ) {
    return fail("checkpoint revisions do not join the provisional frontier");
  }
  const bootstrappedViewport = frontier.viewport === null;
  if (
    bootstrappedViewport &&
    (frontier.scene.revision !== 0 ||
      frontier.scene.nodes.length !== 0 ||
      frontier.semanticScene.revision !== 0 ||
      frontier.semanticScene.components.length !== 0 ||
      frontier.certificateHeadSha256 !== null ||
      frontier.layout !== null)
  ) {
    return fail("only the empty frontier may bootstrap its viewport");
  }

  const checkpoint = decodeCompiledCheckpointV3({
    beat: event.semantic.beat,
    checkpointId: event.semantic.checkpointId,
    patch: event.patch,
    receipt: event.semantic.receipt,
    presentation: event.semantic.presentation,
    choreography: event.semantic.choreography,
    certificate: event.semantic.certificate,
  });
  const currentViewport =
    frontier.viewport ?? checkpoint.presentation.baseViewports[layout];
  const base: SettledParametricChoreographyFrontier = Object.freeze({
    ...frontier,
    viewport: decodeViewportPoseV1(currentViewport),
    layout,
  });
  const semanticTarget = applySemanticCheckpoint(frontier.semanticScene, event);
  const plan = planParametricCheckpointChoreography({
    checkpoint,
    currentScene: frontier.scene,
    layout,
    currentViewport: base.viewport,
    previousCertificateSha256: frontier.certificateHeadSha256,
  });
  if (
    plan.targetScene.revision !== event.resultRevision ||
    semanticTarget.revision !== event.resultRevision
  ) {
    return fail("low-level and semantic results do not share one revision");
  }
  const target: SettledParametricChoreographyFrontier = Object.freeze({
    scene: plan.targetScene,
    semanticScene: semanticTarget,
    viewport: decodeViewportPoseV1(plan.resultViewport),
    layout,
    certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
  });
  return Object.freeze({
    event,
    layout,
    base,
    target,
    plan,
    bootstrappedViewport,
  });
}

export function createAcceptedParametricCheckpoint(
  prepared: PreparedParametricChoreographyCheckpoint,
  outcome: ChoreographyPlaybackOutcome,
): AcceptedParametricChoreographyCheckpoint {
  if (
    (outcome.status !== "completed" &&
      outcome.status !== "cancelled_to_checkpoint") ||
    !outcome.firstCuePresented
  ) {
    return fail("only a fully settled visible checkpoint can be accepted");
  }
  const presentation: ParametricPresentationReceipt = Object.freeze({
    type: "parametric_choreography_checkpoint_presented",
    checkpointId: prepared.event.semantic.checkpointId,
    certificateSha256: prepared.event.semantic.semanticResultCertificateSha256,
    sceneRevision: prepared.target.scene.revision,
    semanticRevision: prepared.target.semanticScene.revision,
    layout: prepared.layout,
    resultViewport: prepared.target.viewport,
    settlement: outcome.status,
  });
  return Object.freeze({
    event: prepared.event,
    scene: prepared.target.scene,
    semanticScene: prepared.target.semanticScene,
    viewport: prepared.target.viewport,
    layout: prepared.layout,
    presentation,
  });
}

function recordMatchesPrepared(
  record: AcceptedParametricChoreographyCheckpoint,
  prepared: PreparedParametricChoreographyCheckpoint,
): boolean {
  return (
    same(record.event, prepared.event) &&
    same(record.scene, prepared.target.scene) &&
    same(record.semanticScene, prepared.target.semanticScene) &&
    same(record.viewport, prepared.target.viewport) &&
    record.layout === prepared.layout &&
    record.presentation.type ===
      "parametric_choreography_checkpoint_presented" &&
    record.presentation.layout === prepared.layout &&
    record.presentation.checkpointId === prepared.event.semantic.checkpointId &&
    record.presentation.certificateSha256 ===
      prepared.target.certificateHeadSha256 &&
    record.presentation.sceneRevision === prepared.target.scene.revision &&
    record.presentation.semanticRevision ===
      prepared.target.semanticScene.revision &&
    same(record.presentation.resultViewport, prepared.target.viewport) &&
    (record.presentation.settlement === "completed" ||
      record.presentation.settlement === "cancelled_to_checkpoint")
  );
}

/** Validate the entire retained ledger before Replay mutates the renderer. */
export function preflightParametricChoreographyReplay(
  recordsValue: readonly AcceptedParametricChoreographyCheckpoint[],
): PreflightedParametricReplay {
  let frontier = EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
  const checkpoints: PreparedParametricChoreographyCheckpoint[] = [];
  const records: AcceptedParametricChoreographyCheckpoint[] = [];
  for (const record of recordsValue) {
    const prepared = prepareParametricChoreographyCheckpoint(
      frontier,
      record.event,
      record.layout,
    );
    if (!recordMatchesPrepared(record, prepared)) {
      return fail("retained checkpoint does not exact-match its replay plan");
    }
    checkpoints.push(prepared);
    records.push(record);
    frontier = prepared.target;
  }
  return Object.freeze({
    checkpoints: Object.freeze(checkpoints),
    records: Object.freeze(records),
    frontier,
  });
}
