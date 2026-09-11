import {
  decodeViewportPoseV1,
  planProjectileCheckpointChoreography,
  type ChoreographyLayout,
  type ChoreographyPlanV2,
  type PlannedCheckpointChoreography,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";
import {
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
  PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_STAGE_PREFIXES,
  decodeProjectileMotionStateV1,
  nextProjectileMotionMainCheckpoint,
  sameProjectileMotionProblem,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionStateV1,
} from "@/lib/live-scene/projectile-motion";
import { LiveSceneProtocolError } from "@/lib/live-scene/patch";
import { createSceneState } from "@/lib/live-scene/state";

import type { ChoreographyPlaybackOutcome } from "./choreography-executor";

export interface ProjectileSemanticSceneState {
  readonly revision: number;
  readonly components: readonly ProjectileMotionStateV1[];
  readonly certificateHeadSha256?: string;
}

export const EMPTY_PROJECTILE_SEMANTIC_SCENE: ProjectileSemanticSceneState =
  Object.freeze({ revision: 0, components: Object.freeze([]) });

export interface ProjectileChoreographyFrontier {
  readonly scene: SceneState;
  readonly semanticScene: ProjectileSemanticSceneState;
  readonly viewport: ViewportPoseV1 | null;
  readonly layout: ChoreographyLayout | null;
  readonly certificateHeadSha256: string | null;
}

export interface SettledProjectileChoreographyFrontier
  extends ProjectileChoreographyFrontier {
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
}

export interface PreparedProjectileChoreographyCheckpoint {
  readonly event: ProjectileChoreographySceneCheckpointEventV1;
  readonly layout: ChoreographyLayout;
  readonly base: SettledProjectileChoreographyFrontier;
  readonly target: SettledProjectileChoreographyFrontier;
  readonly plan: PlannedCheckpointChoreography<ChoreographyPlanV2>;
  readonly bootstrappedViewport: boolean;
}

export interface ProjectilePresentationReceipt {
  readonly type: "projectile_choreography_checkpoint_presented";
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly certificateSha256: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly layout: ChoreographyLayout;
  readonly resultViewport: ViewportPoseV1;
  readonly settlement: "completed" | "cancelled_to_checkpoint";
}

export interface AcceptedProjectileChoreographyCheckpoint {
  readonly event: ProjectileChoreographySceneCheckpointEventV1;
  readonly scene: SceneState;
  readonly semanticScene: ProjectileSemanticSceneState;
  readonly viewport: ViewportPoseV1;
  readonly layout: ChoreographyLayout;
  readonly presentation: ProjectilePresentationReceipt;
}

export interface PreflightedProjectileReplay {
  readonly checkpoints: readonly PreparedProjectileChoreographyCheckpoint[];
  readonly records: readonly AcceptedProjectileChoreographyCheckpoint[];
  readonly frontier: ProjectileChoreographyFrontier;
}

export const EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER: ProjectileChoreographyFrontier =
  Object.freeze({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: EMPTY_PROJECTILE_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "revision_mismatch",
    `projectile choreography playback ${message}`,
  );
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sameTopics(
  left: ProjectileMotionStateV1["clarifiedTopics"],
  right: ProjectileMotionStateV1["clarifiedTopics"],
): boolean {
  return (
    left.length === right.length &&
    left.every((topic, index) => topic === right[index])
  );
}

function acceptedComponent(
  current: ProjectileSemanticSceneState,
): ProjectileMotionStateV1 | null {
  if (current.components.length > 1) {
    return fail("frontier may contain only one projectile component");
  }
  if (current.revision === 0) {
    if (
      current.components.length !== 0 ||
      current.certificateHeadSha256 !== undefined
    ) {
      return fail("revision-zero semantic frontier must be empty");
    }
    return null;
  }
  if (
    current.components.length !== 1 ||
    !current.certificateHeadSha256 ||
    !/^[0-9a-f]{64}$/.test(current.certificateHeadSha256)
  ) {
    return fail("committed semantic frontier requires one component and head");
  }
  return decodeProjectileMotionStateV1(current.components[0]);
}

function requireExactEventBase(
  current: ProjectileMotionStateV1 | null,
  event: ProjectileChoreographySceneCheckpointEventV1,
): void {
  const eventBase = event.semantic.baseComponent;
  if (
    (current === null && eventBase !== null) ||
    (current !== null && (eventBase === null || !same(current, eventBase)))
  ) {
    fail("checkpoint base component does not exact-match the accepted frontier");
  }
}

function validateAdvance(
  current: ProjectileMotionStateV1 | null,
  result: ProjectileMotionStateV1,
  event: ProjectileChoreographySceneCheckpointEventV1,
): void {
  const route = event.semantic.beat.route;
  if (route.intent !== "advance" || event.semantic.action !== "advance") {
    fail("main checkpoints require an advance route");
  }
  const expected = nextProjectileMotionMainCheckpoint(
    current?.lastMainCheckpoint ?? null,
  );
  if (
    expected === null ||
    event.semantic.checkpointId !== expected ||
    result.lastMainCheckpoint !== expected
  ) {
    fail("main checkpoint is not the exact next predecessor");
  }
  if (!PROJECTILE_MOTION_STAGE_PREFIXES[route.targetStage].includes(expected)) {
    fail("main checkpoint exceeds the requested stage");
  }
  if (
    current !== null &&
    (result.id !== current.id ||
      !sameProjectileMotionProblem(result.problemSpec, current.problemSpec) ||
      !sameTopics(result.clarifiedTopics, current.clarifiedTopics))
  ) {
    fail("advance changed accepted component identity or clarification ledger");
  }
  if (result.activeClarification !== null) {
    fail("advance must clear the active clarification");
  }
}

function validateClarification(
  current: ProjectileMotionStateV1 | null,
  result: ProjectileMotionStateV1,
  event: ProjectileChoreographySceneCheckpointEventV1,
): void {
  const route = event.semantic.beat.route;
  if (
    route.intent !== "clarify" ||
    event.semantic.action !== "clarify" ||
    event.semantic.clarificationTopic !== route.topic ||
    event.semantic.checkpointId !==
      PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[route.topic]
  ) {
    fail("clarification checkpoint does not match its routed topic");
  }
  if (current === null || current.lastMainCheckpoint === null) {
    fail("clarification requires an accepted main frontier");
  }
  const prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[route.topic];
  const acceptedPrefix = PROJECTILE_MOTION_STAGE_PREFIXES.solve.slice(
    0,
    PROJECTILE_MOTION_STAGE_PREFIXES.solve.indexOf(
      current.lastMainCheckpoint,
    ) + 1,
  );
  if (
    !acceptedPrefix.includes(prerequisite) ||
    current.clarifiedTopics.includes(route.topic)
  ) {
    fail("clarification is premature or repeats a one-shot topic");
  }
  const expectedTopics = PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter(
    (topic) =>
      current.clarifiedTopics.includes(topic) || topic === route.topic,
  );
  if (
    result.id !== current.id ||
    !sameProjectileMotionProblem(result.problemSpec, current.problemSpec) ||
    result.lastMainCheckpoint !== current.lastMainCheckpoint ||
    !sameTopics(result.clarifiedTopics, expectedTopics) ||
    result.activeClarification !== route.topic
  ) {
    fail("clarification did not append its canonical one-shot ledger entry");
  }
}

function validateRetarget(
  current: ProjectileMotionStateV1 | null,
  result: ProjectileMotionStateV1,
  event: ProjectileChoreographySceneCheckpointEventV1,
): void {
  const route = event.semantic.beat.route;
  if (
    route.intent !== "retarget" ||
    event.semantic.action !== "retarget" ||
    event.semantic.checkpointId !== "parameters_retargeted"
  ) {
    fail("retarget checkpoint does not match its routed action");
  }
  if (current === null || current.lastMainCheckpoint === null) {
    fail("retarget requires an accepted main frontier");
  }
  if (
    result.id !== current.id ||
    result.lastMainCheckpoint !== current.lastMainCheckpoint ||
    !sameTopics(result.clarifiedTopics, current.clarifiedTopics) ||
    result.activeClarification !== current.activeClarification ||
    !sameProjectileMotionProblem(
      result.problemSpec,
      route.targetProblemSpec,
    ) ||
    sameProjectileMotionProblem(result.problemSpec, current.problemSpec)
  ) {
    fail("retarget must change only the supported problem specification");
  }
}

function applySemanticCheckpoint(
  current: ProjectileSemanticSceneState,
  event: ProjectileChoreographySceneCheckpointEventV1,
): ProjectileSemanticSceneState {
  if (
    current.revision !== event.baseRevision ||
    event.semantic.semanticBaseRevision !== current.revision ||
    event.semantic.semanticBaseCertificateSha256 !==
      (current.certificateHeadSha256 ?? null)
  ) {
    return fail("checkpoint does not join the semantic frontier");
  }

  const existing = acceptedComponent(current);
  requireExactEventBase(existing, event);
  const result = decodeProjectileMotionStateV1(
    event.semantic.resultComponent,
  );

  switch (event.semantic.action) {
    case "advance":
      validateAdvance(existing, result, event);
      break;
    case "clarify":
      validateClarification(existing, result, event);
      break;
    case "retarget":
      validateRetarget(existing, result, event);
      break;
  }

  return Object.freeze({
    revision: event.resultRevision,
    components: Object.freeze([result]),
    certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
  });
}

function checkpointEvent(
  value: unknown,
): ProjectileChoreographySceneCheckpointEventV1 {
  const decoded = decodeProjectileChoreographySceneStreamEventV1(value);
  if (decoded.type !== "projectile_choreography_scene_checkpoint") {
    return fail("only checkpoint events can be prepared");
  }
  return decoded;
}

/** Validate and stage one whole projectile checkpoint without mutating state. */
export function prepareProjectileChoreographyCheckpoint(
  frontier: ProjectileChoreographyFrontier,
  eventValue: unknown,
  layout: ChoreographyLayout,
): PreparedProjectileChoreographyCheckpoint {
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

  const currentViewport =
    frontier.viewport ?? event.semantic.presentation.baseViewports[layout];
  const base: SettledProjectileChoreographyFrontier = Object.freeze({
    ...frontier,
    viewport: decodeViewportPoseV1(currentViewport),
    layout,
  });
  const semanticTarget = applySemanticCheckpoint(
    frontier.semanticScene,
    event,
  );
  const plan = planProjectileCheckpointChoreography({
    checkpoint: event,
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
  const target: SettledProjectileChoreographyFrontier = Object.freeze({
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

export function createAcceptedProjectileCheckpoint(
  prepared: PreparedProjectileChoreographyCheckpoint,
  outcome: ChoreographyPlaybackOutcome,
): AcceptedProjectileChoreographyCheckpoint {
  if (
    (outcome.status !== "completed" &&
      outcome.status !== "cancelled_to_checkpoint") ||
    !outcome.firstCuePresented
  ) {
    return fail("only a fully settled visible checkpoint can be accepted");
  }
  const presentation: ProjectilePresentationReceipt = Object.freeze({
    type: "projectile_choreography_checkpoint_presented",
    checkpointId: prepared.event.semantic.checkpointId,
    certificateSha256:
      prepared.event.semantic.semanticResultCertificateSha256,
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
  record: AcceptedProjectileChoreographyCheckpoint,
  prepared: PreparedProjectileChoreographyCheckpoint,
): boolean {
  return (
    same(record.event, prepared.event) &&
    same(record.scene, prepared.target.scene) &&
    same(record.semanticScene, prepared.target.semanticScene) &&
    same(record.viewport, prepared.target.viewport) &&
    record.layout === prepared.layout &&
    record.presentation.type ===
      "projectile_choreography_checkpoint_presented" &&
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

/** Validate the complete retained ledger before Replay mutates the renderer. */
export function preflightProjectileChoreographyReplay(
  recordsValue: readonly AcceptedProjectileChoreographyCheckpoint[],
): PreflightedProjectileReplay {
  let frontier = EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER;
  const checkpoints: PreparedProjectileChoreographyCheckpoint[] = [];
  const records: AcceptedProjectileChoreographyCheckpoint[] = [];
  for (const record of recordsValue) {
    const prepared = prepareProjectileChoreographyCheckpoint(
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
