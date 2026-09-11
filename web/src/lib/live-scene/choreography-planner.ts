import {
  decodeViewportPoseV1,
  type ChoreographyCueKind,
  type ChoreographyPlan,
  type ChoreographyPlanV1,
  type ChoreographyPlanV2,
  type PresentationCheckpointV1,
  type TracePathCueV2,
  type ViewportPoseV1,
} from "./choreography";
import {
  decodeCompiledCheckpointV2,
  type CompiledCheckpointV2,
} from "./checkpoint";
import {
  decodeCompiledCheckpointV3,
  type CompiledCheckpointV3,
} from "./parametric-checkpoint";
import {
  applyLiveScenePatch,
  LiveSceneProtocolError,
  type ScenePatchDraft,
} from "./patch";
import {
  decodeProjectileChoreographySceneCheckpointEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
} from "./projectile-choreography-stream";
import { createSceneState } from "./state";
import type {
  MotionPlan,
  PathSceneNode,
  ScenePoint,
  SceneState,
} from "./types";

export type ChoreographyLayout = "cinematic" | "compact";

export interface CheckpointChoreographyInput {
  readonly checkpoint: CompiledCheckpointV2;
  readonly currentScene: SceneState;
  readonly layout: ChoreographyLayout;
  readonly currentViewport: ViewportPoseV1;
  readonly previousCertificateSha256: string | null;
}

export interface ParametricCheckpointChoreographyInput extends Omit<
  CheckpointChoreographyInput,
  "checkpoint"
> {
  readonly checkpoint: CompiledCheckpointV3;
}

export interface ProjectileCheckpointChoreographyInput extends Omit<
  CheckpointChoreographyInput,
  "checkpoint"
> {
  readonly checkpoint: ProjectileChoreographySceneCheckpointEventV1;
}

export interface PlannedCheckpointChoreography<
  Plan extends ChoreographyPlan = ChoreographyPlanV1,
> {
  readonly targetScene: SceneState;
  readonly motionPlan: MotionPlan;
  readonly baseViewport: ViewportPoseV1;
  readonly resultViewport: ViewportPoseV1;
  readonly choreographyPlan: Plan;
}

function fail(
  message: string,
  code:
    "invalid_event" | "invalid_patch" | "revision_mismatch" = "invalid_event",
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

function sameTargets(
  left: readonly string[],
  right: readonly string[],
): boolean {
  return (
    left.length === right.length &&
    left.every((target, index) => target === right[index])
  );
}

function cueTargets(
  plan: ChoreographyPlan,
  cue: ChoreographyCueKind,
): readonly string[] {
  const candidate = plan.phase.cues.find((candidate) => candidate.cue === cue);
  return candidate?.cue === "trace_path" ? [] : (candidate?.targetIds ?? []);
}

function choreographyTargets(plan: ChoreographyPlan): readonly string[] {
  return plan.phase.cues.flatMap((cue) =>
    cue.cue === "trace_path" ? [cue.pathId, cue.markerId] : cue.targetIds,
  );
}

function traceCue(plan: ChoreographyPlanV2): TracePathCueV2 | undefined {
  return plan.phase.cues.find(
    (cue): cue is TracePathCueV2 => cue.cue === "trace_path",
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

interface PlannerCheckpoint<Plan extends ChoreographyPlan> {
  readonly componentId: string;
  readonly patch: ScenePatchDraft;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: Plan;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly previousCertificateSha256: string | null;
}

interface DecodedCheckpointChoreographyInput<Plan extends ChoreographyPlan> {
  readonly checkpoint: PlannerCheckpoint<Plan>;
  readonly currentScene: SceneState;
  readonly layout: ChoreographyLayout;
  readonly currentViewport: ViewportPoseV1;
  readonly previousCertificateSha256: string | null;
}

function normalizeCompiledCheckpoint(
  checkpoint: CompiledCheckpointV2 | CompiledCheckpointV3,
): PlannerCheckpoint<ChoreographyPlanV1> {
  const body = checkpoint.certificate.body;
  return {
    componentId: checkpoint.beat.componentId,
    patch: checkpoint.patch,
    presentation: checkpoint.presentation,
    choreography: checkpoint.choreography,
    baseRevision: body.baseRevision,
    resultRevision: body.resultRevision,
    previousCertificateSha256: body.previousCertificateSha256,
  };
}

function normalizeProjectileCheckpoint(
  checkpoint: ProjectileChoreographySceneCheckpointEventV1,
): PlannerCheckpoint<ChoreographyPlanV2> {
  return {
    componentId: checkpoint.semantic.beat.componentId,
    patch: checkpoint.patch,
    presentation: checkpoint.semantic.presentation,
    choreography: checkpoint.semantic.choreography,
    baseRevision: checkpoint.baseRevision,
    resultRevision: checkpoint.resultRevision,
    previousCertificateSha256:
      checkpoint.semantic.certificate.body.previousCertificateSha256,
  };
}

function samePoint(left: ScenePoint, right: ScenePoint): boolean {
  const tolerance = 1e-9;
  return (
    Math.abs(left[0] - right[0]) <= tolerance &&
    Math.abs(left[1] - right[1]) <= tolerance
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

/** Keep path tracing as an exact partition of one enter and one retained update. */
function validateProjectileTraceOwnership(
  plan: ChoreographyPlanV2,
  motionPlan: MotionPlan,
): void {
  const trace = traceCue(plan);
  if (!trace) return;

  const pathStep = motionPlan.steps.find((step) => step.id === trace.pathId);
  if (
    !pathStep ||
    pathStep.type !== "enter" ||
    pathStep.node.kind !== "path" ||
    pathStep.node.closed
  ) {
    return fail("trace path must be one newly entered open path");
  }

  const markerStep = motionPlan.steps.find(
    (step) => step.id === trace.markerId,
  );
  if (
    !markerStep ||
    markerStep.type !== "update" ||
    markerStep.previous.kind !== "path" ||
    markerStep.next.kind !== "path" ||
    !markerStep.previous.closed ||
    !markerStep.next.closed
  ) {
    return fail("trace marker must be one changed retained closed path");
  }

  const pathStart = pathStep.node.points[0];
  const pathEnd = pathStep.node.points.at(-1);
  if (
    !pathStart ||
    !pathEnd ||
    !samePoint(pathCenter(markerStep.previous), pathStart) ||
    !samePoint(pathCenter(markerStep.next), pathEnd)
  ) {
    return fail("trace marker must move from the path start to its endpoint");
  }
}

/**
 * Join one certified checkpoint to exact accepted scene, camera, and chain state.
 * Backend hashes remain opaque; this planner validates only cleartext bindings.
 */
function planDecodedCheckpointChoreography<Plan extends ChoreographyPlan>({
  checkpoint,
  currentScene: currentSceneValue,
  layout,
  currentViewport: currentViewportValue,
  previousCertificateSha256,
}: DecodedCheckpointChoreographyInput<Plan>): PlannedCheckpointChoreography<Plan> {
  const currentScene = createSceneState(currentSceneValue);
  if (layout !== "cinematic" && layout !== "compact") {
    return fail("layout must be cinematic or compact");
  }
  const currentViewport = decodeViewportPoseV1(currentViewportValue);

  if (currentScene.revision !== checkpoint.baseRevision) {
    return fail(
      "current scene revision does not match certificate baseRevision",
      "revision_mismatch",
    );
  }
  if (
    previousCertificateSha256 !== null &&
    !/^[0-9a-f]{64}$/.test(previousCertificateSha256)
  ) {
    return fail(
      "previous certificate must be null or a lowercase SHA-256 digest",
    );
  }
  if (checkpoint.previousCertificateSha256 !== previousCertificateSha256) {
    return fail(
      "previous certificate does not join the accepted chain",
      "revision_mismatch",
    );
  }

  const baseViewport = checkpoint.presentation.baseViewports[layout];
  if (!sameViewport(currentViewport, baseViewport)) {
    return fail(
      "current viewport does not join the selected certified base viewport",
    );
  }

  const namespace = `${checkpoint.componentId}__`;
  const patchTargets = checkpoint.patch.operations.map((operation) =>
    operation.op === "put" ? operation.node.id : operation.id,
  );
  if (
    [...patchTargets, ...choreographyTargets(checkpoint.choreography)].some(
      (id) => !id.startsWith(namespace),
    )
  ) {
    return fail(
      "patch and cue targets must stay inside the routed component namespace",
    );
  }

  const { scene: targetScene, plan: motionPlan } = applyLiveScenePatch(
    currentScene,
    {
      type: "scene_patch",
      generation: 1,
      attempt: 1,
      sequence: 1,
      baseRevision: checkpoint.baseRevision,
      resultRevision: checkpoint.resultRevision,
      patch: checkpoint.patch,
    },
  );

  const currentIds = new Set(currentScene.nodes.map((node) => node.id));
  const stepTypeById = new Map(
    motionPlan.steps.map((step) => [step.id, step.type]),
  );
  for (const operation of checkpoint.patch.operations) {
    const target = operation.op === "put" ? operation.node.id : operation.id;
    const expected =
      operation.op === "remove"
        ? "remove"
        : currentIds.has(target)
          ? "update"
          : "enter";
    if (stepTypeById.get(target) !== expected) {
      return fail(
        `patch target ${target} does not produce its declared visible change`,
        "invalid_patch",
      );
    }
  }
  if (motionPlan.steps.length !== checkpoint.patch.operations.length) {
    return fail(
      "every patch operation must produce exactly one visible change",
      "invalid_patch",
    );
  }

  const trace =
    checkpoint.choreography.v === 2
      ? traceCue(checkpoint.choreography)
      : undefined;
  const lifecycleTargets = {
    enter: sortedStepIds(motionPlan, "enter"),
    exit: sortedStepIds(motionPlan, "remove"),
    transform: sortedStepIds(motionPlan, "update").filter(
      (id) => id !== trace?.markerId,
    ),
  } as const;
  for (const cue of ["enter", "exit", "transform"] as const) {
    if (
      !sameTargets(
        cueTargets(checkpoint.choreography, cue),
        lifecycleTargets[cue],
      )
    ) {
      return fail(
        `${cue} cue targets must exactly match ${cue === "exit" ? "remove" : cue === "transform" ? "update" : "enter"} motion targets`,
      );
    }
  }

  if (checkpoint.choreography.v === 2) {
    validateProjectileTraceOwnership(checkpoint.choreography, motionPlan);
  }

  const resultIds = new Set(targetScene.nodes.map((node) => node.id));
  for (const cue of ["emphasize", "focus"] as const) {
    if (
      cueTargets(checkpoint.choreography, cue).some((id) => !resultIds.has(id))
    ) {
      return fail(
        `${cue} cue targets must exist in the checkpoint result scene`,
      );
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

export function planCheckpointChoreography({
  checkpoint,
  ...input
}: CheckpointChoreographyInput): PlannedCheckpointChoreography {
  return planDecodedCheckpointChoreography({
    ...input,
    checkpoint: normalizeCompiledCheckpoint(
      decodeCompiledCheckpointV2(checkpoint),
    ),
  });
}

/** Plan V3 through the same renderer-safe transition checks as V2. */
export function planParametricCheckpointChoreography({
  checkpoint,
  ...input
}: ParametricCheckpointChoreographyInput): PlannedCheckpointChoreography {
  return planDecodedCheckpointChoreography({
    ...input,
    checkpoint: normalizeCompiledCheckpoint(
      decodeCompiledCheckpointV3(checkpoint),
    ),
  });
}

/** Plan one decoded projectile event while preserving V2 trace ownership. */
export function planProjectileCheckpointChoreography({
  checkpoint,
  ...input
}: ProjectileCheckpointChoreographyInput): PlannedCheckpointChoreography<ChoreographyPlanV2> {
  return planDecodedCheckpointChoreography({
    ...input,
    checkpoint: normalizeProjectileCheckpoint(
      decodeProjectileChoreographySceneCheckpointEventV1(checkpoint),
    ),
  });
}

export const planCompiledCheckpointTransition = planCheckpointChoreography;
