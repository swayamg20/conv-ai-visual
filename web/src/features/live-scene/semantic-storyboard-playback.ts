import {
  decodeViewportPoseV1,
  type ChoreographyCueKindV2,
  type ChoreographyLayout,
  type ChoreographyPlanV2,
  type MotionPlan,
  type PathSceneNode,
  type PlannedCheckpointChoreography,
  type ScenePoint,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";
import {
  applyLiveScenePatch,
  LiveSceneProtocolError,
} from "@/lib/live-scene/patch";
import {
  MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS,
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  decodeProjectileStoryboardSemanticSceneStateV1,
  decodeSemanticStoryboardRequestV1,
  type PairedProjectileComparisonSpecV1,
  type ProjectileStoryboardSemanticSceneStateV1,
  type SemanticStoryboardRequestV1,
  type StoryboardAbstainReasonCode,
} from "@/lib/live-scene/semantic-storyboard";
import {
  MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
  decodeSemanticStoryboardSceneCheckpointEventV1,
  decodeSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardAcceptedPrefixCause,
  type SemanticStoryboardCompletionReason,
  type SemanticStoryboardSceneCheckpointEventV1,
  type SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";
import { createSceneState } from "@/lib/live-scene/state";

import type {
  CertifiedChoreographyFrontier,
  CertifiedChoreographyStreamEvent,
} from "./certified-choreography-stream-runtime";
import type { ChoreographyPlaybackOutcome } from "./choreography-executor";

export type SemanticStoryboardCommand =
  | {
      readonly routingMode: "reflex";
      readonly problemSpec: PairedProjectileComparisonSpecV1;
    }
  | {
      readonly routingMode: "director";
      readonly problemSpec: PairedProjectileComparisonSpecV1;
      readonly prompt: string;
    };

export interface SemanticStoryboardFrontier extends CertifiedChoreographyFrontier<ProjectileStoryboardSemanticSceneStateV1> {}

export interface SettledSemanticStoryboardFrontier extends SemanticStoryboardFrontier {
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
}

export interface PreparedSemanticStoryboardCheckpoint {
  readonly event: SemanticStoryboardSceneCheckpointEventV1;
  readonly layout: ChoreographyLayout;
  readonly base: SettledSemanticStoryboardFrontier;
  readonly target: SettledSemanticStoryboardFrontier;
  readonly plan: PlannedCheckpointChoreography<ChoreographyPlanV2>;
  readonly bootstrappedViewport: boolean;
}

export interface SemanticStoryboardPresentationReceipt {
  readonly type: "semantic_storyboard_checkpoint_presented";
  readonly checkpointId: string;
  readonly certificateSha256: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly layout: ChoreographyLayout;
  readonly resultViewport: ViewportPoseV1;
  readonly settlement: "completed" | "cancelled_to_checkpoint";
}

export interface AcceptedSemanticStoryboardCheckpoint {
  readonly event: SemanticStoryboardSceneCheckpointEventV1;
  readonly scene: SceneState;
  readonly semanticScene: ProjectileStoryboardSemanticSceneStateV1;
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
  readonly presentation: SemanticStoryboardPresentationReceipt;
}

export interface PreflightedSemanticStoryboardReplay {
  readonly checkpoints: readonly PreparedSemanticStoryboardCheckpoint[];
  readonly records: readonly AcceptedSemanticStoryboardCheckpoint[];
  readonly frontier: SemanticStoryboardFrontier;
}

export const EMPTY_SEMANTIC_STORYBOARD_FRONTIER: SemanticStoryboardFrontier =
  Object.freeze({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: Object.freeze({
      revision: 0,
      components: Object.freeze([]),
    }),
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });

type BaseAdaptedEvent = CertifiedChoreographyStreamEvent<
  SemanticStoryboardSceneCheckpointEventV1,
  StoryboardAbstainReasonCode
>;
type BaseCompletedEvent = Extract<BaseAdaptedEvent, { kind: "completed" }>;

export type SemanticStoryboardAdaptedStreamEvent =
  | Exclude<BaseAdaptedEvent, { kind: "completed" }>
  | (BaseCompletedEvent & {
      readonly completionMetadata: {
        readonly reasonCode: SemanticStoryboardCompletionReason;
        readonly detailCode: SemanticStoryboardAcceptedPrefixCause | null;
      };
    });

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "revision_mismatch",
    `semantic storyboard playback ${message}`,
  );
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function operationTarget(
  operation: SemanticStoryboardSceneCheckpointEventV1["patch"]["operations"][number],
): string {
  return operation.op === "put" ? operation.node.id : operation.id;
}

function cueTargets(
  plan: ChoreographyPlanV2,
  cue: ChoreographyCueKindV2,
): readonly string[] {
  const candidate = plan.phase.cues.find((item) => item.cue === cue);
  return candidate?.cue === "trace_path" ? [] : (candidate?.targetIds ?? []);
}

function choreographyTargets(plan: ChoreographyPlanV2): readonly string[] {
  return plan.phase.cues.flatMap((cue) =>
    cue.cue === "trace_path" ? [cue.pathId, cue.markerId] : cue.targetIds,
  );
}

function sortedStepIds(
  plan: MotionPlan,
  type: MotionPlan["steps"][number]["type"],
): string[] {
  return plan.steps
    .filter((step) => step.type === type)
    .map((step) => step.id)
    .sort();
}

function samePoint(left: ScenePoint, right: ScenePoint): boolean {
  return (
    Math.abs(left[0] - right[0]) <= 1e-9 && Math.abs(left[1] - right[1]) <= 1e-9
  );
}

function pathCenter(node: PathSceneNode): ScenePoint {
  const xs = node.points.map(([x]) => x);
  const ys = node.points.map(([, y]) => y);
  return [
    (Math.min(...xs) + Math.max(...xs)) / 2,
    (Math.min(...ys) + Math.max(...ys)) / 2,
  ];
}

function validateTraceOwnership(
  choreography: ChoreographyPlanV2,
  motionPlan: MotionPlan,
): void {
  const trace = choreography.phase.cues.find((cue) => cue.cue === "trace_path");
  if (!trace || trace.cue !== "trace_path") return;
  const pathStep = motionPlan.steps.find((step) => step.id === trace.pathId);
  const markerStep = motionPlan.steps.find(
    (step) => step.id === trace.markerId,
  );
  if (
    !pathStep ||
    pathStep.type !== "enter" ||
    pathStep.node.kind !== "path" ||
    pathStep.node.closed ||
    !markerStep ||
    markerStep.type !== "update" ||
    markerStep.previous.kind !== "path" ||
    markerStep.next.kind !== "path" ||
    !markerStep.previous.closed ||
    !markerStep.next.closed
  ) {
    fail("trace cue does not own one path enter and retained marker update");
  }
  const pathStart = pathStep.node.points[0];
  const pathEnd = pathStep.node.points.at(-1);
  if (
    !pathStart ||
    !pathEnd ||
    !samePoint(pathCenter(markerStep.previous), pathStart) ||
    !samePoint(pathCenter(markerStep.next), pathEnd)
  ) {
    fail("trace marker does not move between the path endpoints");
  }
}

function planCheckpoint(
  event: SemanticStoryboardSceneCheckpointEventV1,
  currentScene: SceneState,
  currentViewport: ViewportPoseV1,
  layout: ChoreographyLayout,
): PlannedCheckpointChoreography<ChoreographyPlanV2> {
  const checkpoint = event.transition.checkpoint;
  const presentation = checkpoint.presentation;
  if (!same(currentViewport, presentation.baseViewports[layout])) {
    fail("current viewport does not join the certified base viewport");
  }
  const namespace = `${checkpoint.receipt.componentId}__`;
  if (
    [
      ...checkpoint.patch.operations.map(operationTarget),
      ...choreographyTargets(checkpoint.choreography),
    ].some((id) => !id.startsWith(namespace))
  ) {
    fail("patch and choreography targets leave the storyboard namespace");
  }
  const { scene: targetScene, plan: motionPlan } = applyLiveScenePatch(
    currentScene,
    {
      type: "scene_patch",
      generation: event.generation,
      attempt: event.attempt,
      sequence: event.sequence,
      baseRevision: event.baseRevision,
      resultRevision: event.resultRevision,
      patch: event.patch,
    },
  );
  if (!same(targetScene, event.transition.resultScene)) {
    fail("playback patch does not materialize the certified resultScene");
  }
  const currentIds = new Set(currentScene.nodes.map((node) => node.id));
  const stepTypeById = new Map(
    motionPlan.steps.map((step) => [step.id, step.type]),
  );
  for (const operation of checkpoint.patch.operations) {
    const target = operationTarget(operation);
    const expected =
      operation.op === "remove"
        ? "remove"
        : currentIds.has(target)
          ? "update"
          : "enter";
    if (stepTypeById.get(target) !== expected) {
      fail(`patch target ${target} does not produce its declared change`);
    }
  }
  if (motionPlan.steps.length !== checkpoint.patch.operations.length) {
    fail("each patch operation must produce exactly one visible change");
  }
  const trace = checkpoint.choreography.phase.cues.find(
    (cue) => cue.cue === "trace_path",
  );
  const lifecycleTargets = {
    enter: sortedStepIds(motionPlan, "enter"),
    exit: sortedStepIds(motionPlan, "remove"),
    transform: sortedStepIds(motionPlan, "update").filter(
      (id) => trace?.cue !== "trace_path" || id !== trace.markerId,
    ),
  } as const;
  for (const cue of ["enter", "exit", "transform"] as const) {
    const actual = [...cueTargets(checkpoint.choreography, cue)].sort();
    if (!same(actual, lifecycleTargets[cue])) {
      fail(`${cue} cue targets do not match the visible transition`);
    }
  }
  validateTraceOwnership(checkpoint.choreography, motionPlan);
  const resultIds = new Set(targetScene.nodes.map((node) => node.id));
  for (const cue of ["emphasize", "focus"] as const) {
    if (
      cueTargets(checkpoint.choreography, cue).some(
        (target) => !resultIds.has(target),
      )
    ) {
      fail(`${cue} cue targets are absent from the certified result scene`);
    }
  }
  return Object.freeze({
    targetScene,
    motionPlan,
    baseViewport: currentViewport,
    resultViewport: presentation.resultViewports[layout],
    choreographyPlan: checkpoint.choreography,
  });
}

/** Build one exact Reflex or Director request from the accepted frontier. */
export function createSemanticStoryboardRequest(
  command: SemanticStoryboardCommand,
  generation: number,
  frontier: SemanticStoryboardFrontier,
): SemanticStoryboardRequestV1 {
  const shared = {
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    problemSpec: command.problemSpec,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
  } as const;
  return decodeSemanticStoryboardRequestV1(
    command.routingMode === "reflex"
      ? { ...shared, routingMode: "reflex" }
      : {
          ...shared,
          routingMode: "director",
          prompt: command.prompt,
        },
  );
}

/** Map the dedicated wire lifecycle into the protocol-neutral runtime contract. */
export function adaptSemanticStoryboardStreamEvent(
  value: unknown,
): SemanticStoryboardAdaptedStreamEvent {
  const event = decodeSemanticStoryboardSceneStreamEventV1(value);
  switch (event.type) {
    case "semantic_storyboard_scene_stream_started":
      return Object.freeze({
        kind: "started",
        generation: event.generation,
        attempt: event.attempt,
        baseRevision: event.baseRevision,
      });
    case "semantic_storyboard_scene_checkpoint":
      return Object.freeze({
        kind: "checkpoint",
        generation: event.generation,
        attempt: event.attempt,
        sequence: event.sequence,
        baseRevision: event.baseRevision,
        semanticBaseRevision: event.transition.baseSemanticScene.revision,
        patchId: event.patch.patchId,
        checkpoint: event,
      });
    case "semantic_storyboard_scene_stream_completed":
      return Object.freeze({
        kind: "completed",
        generation: event.generation,
        finalRevision: event.finalRevision,
        patchCount: event.checkpointCount,
        firstPatchMs: event.firstCheckpointMs,
        totalMs: event.totalMs,
        repaired: false,
        completionMetadata: Object.freeze({
          reasonCode: event.reasonCode,
          detailCode: event.acceptedPrefixCause,
        }),
      });
    case "semantic_storyboard_scene_stream_declined":
      return Object.freeze({
        kind: "declined",
        generation: event.generation,
        attempt: event.attempt,
        finalRevision: event.finalRevision,
        reasonCode: event.reasonCode,
        message: event.message,
      });
    case "semantic_storyboard_scene_stream_failed":
      return Object.freeze({
        kind: "failed",
        generation: event.generation,
        attempt: event.attempt,
        lastAcceptedRevision: event.lastAcceptedRevision,
        code: event.code,
        message: event.message,
        retryable: event.retryable,
      });
  }
}

/** Validate and stage one complete certified checkpoint without mutating state. */
export function prepareSemanticStoryboardCheckpoint(
  frontierValue: SemanticStoryboardFrontier,
  eventValue: unknown,
  layout: ChoreographyLayout,
): PreparedSemanticStoryboardCheckpoint {
  if (layout !== "cinematic" && layout !== "compact") {
    return fail("layout is outside the closed vocabulary");
  }
  const scene = createSceneState(frontierValue.scene);
  const semanticScene = decodeProjectileStoryboardSemanticSceneStateV1(
    frontierValue.semanticScene,
  );
  if (scene.revision !== semanticScene.revision) {
    return fail("low-level and semantic frontier revisions differ");
  }
  const semanticHead = semanticScene.certificateHeadSha256 ?? null;
  if (frontierValue.certificateHeadSha256 !== semanticHead) {
    return fail("semantic and explicit certificate heads differ");
  }
  if (frontierValue.layout !== null && frontierValue.layout !== layout) {
    return fail("checkpoint changed the locked layout");
  }
  const event = decodeSemanticStoryboardSceneCheckpointEventV1(eventValue);
  if (
    !same(scene, event.transition.baseScene) ||
    !same(semanticScene, event.transition.baseSemanticScene)
  ) {
    return fail("certified transition base does not exact-match the frontier");
  }
  const previousCertificate =
    event.transition.checkpoint.certificate.body.previousCertificateSha256;
  if (previousCertificate !== frontierValue.certificateHeadSha256) {
    return fail("checkpoint certificate does not join the accepted head");
  }
  const bootstrappedViewport = frontierValue.viewport === null;
  if (
    bootstrappedViewport &&
    (scene.revision !== 0 ||
      scene.nodes.length !== 0 ||
      semanticScene.components.length !== 0 ||
      frontierValue.certificateHeadSha256 !== null ||
      frontierValue.layout !== null)
  ) {
    return fail("only the empty frontier may bootstrap its viewport");
  }
  const viewport = decodeViewportPoseV1(
    frontierValue.viewport ??
      event.transition.checkpoint.presentation.baseViewports[layout],
  );
  const base: SettledSemanticStoryboardFrontier = Object.freeze({
    scene,
    semanticScene,
    viewport,
    layout,
    certificateHeadSha256: frontierValue.certificateHeadSha256,
  });
  const plan = planCheckpoint(event, scene, viewport, layout);
  const target: SettledSemanticStoryboardFrontier = Object.freeze({
    scene: event.transition.resultScene,
    semanticScene: event.transition.resultSemanticScene,
    viewport: decodeViewportPoseV1(plan.resultViewport),
    layout,
    certificateHeadSha256:
      event.transition.checkpoint.certificate.certificateSha256,
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

/** Promote only a visibly settled checkpoint into the retained ledger. */
export function createAcceptedSemanticStoryboardCheckpoint(
  prepared: PreparedSemanticStoryboardCheckpoint,
  outcome: ChoreographyPlaybackOutcome,
): AcceptedSemanticStoryboardCheckpoint {
  if (
    (outcome.status !== "completed" &&
      outcome.status !== "cancelled_to_checkpoint") ||
    !outcome.firstCuePresented
  ) {
    return fail("only a fully settled visible checkpoint can be accepted");
  }
  const checkpoint = prepared.event.transition.checkpoint;
  const presentation: SemanticStoryboardPresentationReceipt = Object.freeze({
    type: "semantic_storyboard_checkpoint_presented",
    checkpointId: checkpoint.checkpointId,
    certificateSha256: checkpoint.certificate.certificateSha256,
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
  record: AcceptedSemanticStoryboardCheckpoint,
  prepared: PreparedSemanticStoryboardCheckpoint,
): boolean {
  const checkpoint = prepared.event.transition.checkpoint;
  return (
    same(record.event, prepared.event) &&
    same(record.scene, prepared.target.scene) &&
    same(record.semanticScene, prepared.target.semanticScene) &&
    same(record.viewport, prepared.target.viewport) &&
    record.layout === prepared.layout &&
    record.presentation.type === "semantic_storyboard_checkpoint_presented" &&
    record.presentation.checkpointId === checkpoint.checkpointId &&
    record.presentation.certificateSha256 ===
      checkpoint.certificate.certificateSha256 &&
    record.presentation.sceneRevision === prepared.target.scene.revision &&
    record.presentation.semanticRevision ===
      prepared.target.semanticScene.revision &&
    record.presentation.layout === prepared.layout &&
    same(record.presentation.resultViewport, prepared.target.viewport) &&
    (record.presentation.settlement === "completed" ||
      record.presentation.settlement === "cancelled_to_checkpoint")
  );
}

/** Validate the entire retained ledger before Replay mutates the renderer. */
export function preflightSemanticStoryboardReplay(
  recordsValue: readonly AcceptedSemanticStoryboardCheckpoint[],
): PreflightedSemanticStoryboardReplay {
  if (recordsValue.length > MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS + 1) {
    return fail("retained checkpoint history exceeds the closed catalog");
  }
  let frontier = EMPTY_SEMANTIC_STORYBOARD_FRONTIER;
  const checkpoints: PreparedSemanticStoryboardCheckpoint[] = [];
  const records: AcceptedSemanticStoryboardCheckpoint[] = [];
  for (const record of recordsValue) {
    const prepared = prepareSemanticStoryboardCheckpoint(
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

export const SEMANTIC_STORYBOARD_MAX_CHECKPOINTS_PER_STREAM =
  MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS;
