import {
  decodeViewportPoseV1,
  type ChoreographyCueKind,
  type ChoreographyPlanV1,
  type ViewportPoseV1,
} from "./choreography";
import {
  decodeCompiledCheckpointV2,
  type CompiledCheckpointV2,
} from "./checkpoint";
import { applyLiveScenePatch, LiveSceneProtocolError } from "./patch";
import { createSceneState } from "./state";
import type { MotionPlan, SceneState } from "./types";

export type ChoreographyLayout = "cinematic" | "compact";

export interface CheckpointChoreographyInput {
  readonly checkpoint: CompiledCheckpointV2;
  readonly currentScene: SceneState;
  readonly layout: ChoreographyLayout;
  readonly currentViewport: ViewportPoseV1;
  readonly previousCertificateSha256: string | null;
}

export interface PlannedCheckpointChoreography {
  readonly targetScene: SceneState;
  readonly motionPlan: MotionPlan;
  readonly baseViewport: ViewportPoseV1;
  readonly resultViewport: ViewportPoseV1;
  readonly choreographyPlan: ChoreographyPlanV1;
}

function fail(
  message: string,
  code: "invalid_event" | "invalid_patch" | "revision_mismatch" =
    "invalid_event",
): never {
  throw new LiveSceneProtocolError(code, `choreography planner ${message}`);
}

function sameViewport(left: ViewportPoseV1, right: ViewportPoseV1): boolean {
  return (
    left.v === right.v &&
    left.x === right.x &&
    left.y === right.y &&
    left.width === right.width &&
    left.height === right.height
  );
}

function sameTargets(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length &&
    left.every((target, index) => target === right[index])
  );
}

function cueTargets(
  plan: ChoreographyPlanV1,
  cue: ChoreographyCueKind,
): readonly string[] {
  return plan.phase.cues.find((candidate) => candidate.cue === cue)?.targetIds ?? [];
}

function sortedStepIds(plan: MotionPlan, type: MotionPlan["steps"][number]["type"]): string[] {
  return plan.steps
    .filter((step) => step.type === type)
    .map((step) => step.id)
    .sort();
}

/**
 * Join one certified checkpoint to exact accepted scene, camera, and chain state.
 * Backend hashes remain opaque; this planner validates only cleartext bindings.
 */
export function planCheckpointChoreography({
  checkpoint: checkpointValue,
  currentScene: currentSceneValue,
  layout,
  currentViewport: currentViewportValue,
  previousCertificateSha256,
}: CheckpointChoreographyInput): PlannedCheckpointChoreography {
  const checkpoint = decodeCompiledCheckpointV2(checkpointValue);
  const currentScene = createSceneState(currentSceneValue);
  if (layout !== "cinematic" && layout !== "compact") {
    return fail("layout must be cinematic or compact");
  }
  const currentViewport = decodeViewportPoseV1(currentViewportValue);
  const body = checkpoint.certificate.body;

  if (currentScene.revision !== body.baseRevision) {
    return fail("current scene revision does not match certificate baseRevision", "revision_mismatch");
  }
  if (
    previousCertificateSha256 !== null &&
    !/^[0-9a-f]{64}$/.test(previousCertificateSha256)
  ) {
    return fail("previous certificate must be null or a lowercase SHA-256 digest");
  }
  if (body.previousCertificateSha256 !== previousCertificateSha256) {
    return fail("previous certificate does not join the accepted chain", "revision_mismatch");
  }

  const baseViewport = checkpoint.presentation.baseViewports[layout];
  if (!sameViewport(currentViewport, baseViewport)) {
    return fail("current viewport does not join the selected certified base viewport");
  }

  const namespace = `${checkpoint.beat.componentId}__`;
  const patchTargets = checkpoint.patch.operations.map((operation) =>
    operation.op === "put" ? operation.node.id : operation.id,
  );
  const choreographyTargets = checkpoint.choreography.phase.cues.flatMap(
    (cue) => cue.targetIds,
  );
  if ([...patchTargets, ...choreographyTargets].some((id) => !id.startsWith(namespace))) {
    return fail("patch and cue targets must stay inside the routed component namespace");
  }

  const { scene: targetScene, plan: motionPlan } = applyLiveScenePatch(currentScene, {
    type: "scene_patch",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: body.baseRevision,
    resultRevision: body.resultRevision,
    patch: checkpoint.patch,
  });

  const currentIds = new Set(currentScene.nodes.map((node) => node.id));
  const stepTypeById = new Map(motionPlan.steps.map((step) => [step.id, step.type]));
  for (const operation of checkpoint.patch.operations) {
    const target = operation.op === "put" ? operation.node.id : operation.id;
    const expected = operation.op === "remove" ? "remove" : currentIds.has(target) ? "update" : "enter";
    if (stepTypeById.get(target) !== expected) {
      return fail(`patch target ${target} does not produce its declared visible change`, "invalid_patch");
    }
  }
  if (motionPlan.steps.length !== checkpoint.patch.operations.length) {
    return fail("every patch operation must produce exactly one visible change", "invalid_patch");
  }

  const lifecycleCues = [
    ["enter", "enter"],
    ["exit", "remove"],
    ["transform", "update"],
  ] as const;
  for (const [cue, step] of lifecycleCues) {
    if (!sameTargets(cueTargets(checkpoint.choreography, cue), sortedStepIds(motionPlan, step))) {
      return fail(`${cue} cue targets must exactly match ${step} motion targets`);
    }
  }

  const resultIds = new Set(targetScene.nodes.map((node) => node.id));
  for (const cue of ["emphasize", "focus"] as const) {
    if (cueTargets(checkpoint.choreography, cue).some((id) => !resultIds.has(id))) {
      return fail(`${cue} cue targets must exist in the checkpoint result scene`);
    }
  }

  return Object.freeze({
    targetScene,
    motionPlan,
    baseViewport,
    resultViewport: checkpoint.presentation.resultViewports[layout],
    choreographyPlan: checkpoint.choreography,
  });
}

export const planCompiledCheckpointTransition = planCheckpointChoreography;
