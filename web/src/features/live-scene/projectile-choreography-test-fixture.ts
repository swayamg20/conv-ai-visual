import type { ChoreographyCueV2 } from "@/lib/live-scene";
import type { ProjectileMotionRequestV1 } from "@/lib/live-scene/projectile-choreography-request";
import {
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  PROJECTILE_MOTION_STAGE_PREFIXES,
  nextProjectileMotionMainCheckpoint,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionClarificationTopic,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
  type ProjectileMotionStateV1,
} from "@/lib/live-scene/projectile-motion";

const PROBLEM_HASHES = Object.freeze({
  "20:30": "f2ac3f33e3a48dacdd0e256937330b6f36d39451968aa3882d92f3ce3c436491",
  "20:45": "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa",
  "20:60": "b6859d7baf2204ebc98de07b974b02c39521a27ea1c5b1eb475e3daa6a083b59",
  "25:30": "6014ed0114f009bfd6e50acd99ccdccd42f28a286465d9dc9a2987cf196ca85e",
  "25:45": "e947b64e547b77b7e7899b1f8936138d49d8f5a027b38e9878f4e991a34665c3",
  "25:60": "98add4003547fa75b0f8debc391ab4e8a17f2b8cc656f0f9b61b838f3fe82d6f",
  "30:30": "aa4172502435083f997288d04ccbde34d0fa446430d37b75700a2d4b8b0f5bfc",
  "30:45": "205df15d13df182f0a62c47c7c850888797d11fd52774db7d490d46c2fca80bb",
  "30:60": "3366d188d60fbfe6a523a63f9ee0ece7bee84eed709e056026d33f5b8859e8c3",
} satisfies Readonly<Record<string, string>>);

export const DEFAULT_PROJECTILE_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 45,
} as const satisfies ProjectileMotionProblemSpecV1);

const NARRATION: Readonly<Record<ProjectileMotionCheckpointId, string>> = {
  setup: "Place the launch on a shared set of axes.",
  decompose_velocity:
    "Split the launch velocity into independent horizontal and vertical parts.",
  trace_ascent:
    "Equal slices of time carry the projectile right while gravity bends it upward more slowly.",
  apex_state:
    "At the apex, vertical velocity is zero for an instant while acceleration still points down.",
  trace_descent:
    "Gravity keeps changing vertical velocity as the projectile returns to the ground.",
  summary:
    "The same launch components determine flight time, maximum height, and range.",
  horizontal_velocity_detail:
    "With no horizontal force, horizontal velocity stays constant throughout the flight.",
  apex_acceleration_detail:
    "Zero vertical velocity at the apex does not mean zero acceleration; gravity still points down.",
  flight_symmetry_detail:
    "With equal launch and landing heights, ascent and descent mirror each other in time.",
  parameters_retargeted:
    "The same physical objects now show the newly selected launch.",
};

interface CheckpointOptions {
  readonly request: ProjectileMotionRequestV1;
  readonly sequence: number;
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly action: "advance" | "clarify" | "retarget";
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly route: ProjectileMotionRouteV1;
  readonly baseComponent: ProjectileMotionStateV1 | null;
  readonly resultComponent: ProjectileMotionStateV1;
  readonly baseRevision: number;
  readonly baseHead: string | null;
}

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function problemHash(value: ProjectileMotionProblemSpecV1): string {
  return PROBLEM_HASHES[`${value.speedMps}:${value.angleDeg}`];
}

function certificateHead(revision: number): string {
  return revision.toString(16).padStart(64, "0");
}

function checkpointEvent(
  options: CheckpointOptions,
): ProjectileChoreographySceneCheckpointEventV1 {
  const resultRevision = options.baseRevision + 1;
  const resultHead = certificateHead(resultRevision);
  const baseProblem = options.baseComponent?.problemSpec ?? null;
  const resultProblem = options.resultComponent.problemSpec;
  const baseProblemSpecSha256 = baseProblem ? problemHash(baseProblem) : null;
  const resultProblemSpecSha256 = problemHash(resultProblem);
  const target = "lesson__equation";
  const narration = NARRATION[options.checkpointId];
  const viewport = { v: 1, x: 0, y: 0, width: 800, height: 600 } as const;
  const presentation = {
    v: 1,
    checkpointId: options.checkpointId,
    checkpointNarration: narration,
    baseViewports: { cinematic: viewport, compact: viewport },
    resultViewports: { cinematic: viewport, compact: viewport },
    transientFree: true,
  } as const;
  const beat = {
    v: 1,
    beatId: `beat_${options.request.generation}_${options.route.intent}`,
    componentKind: "projectile_motion",
    componentId: options.baseComponent?.id ?? "lesson",
    baseProblemSpec:
      options.request.baseSemanticScene.components[0]?.problemSpec ?? null,
    resultProblemSpec: resultProblem,
    route: options.route,
  } as const;
  const cue: ChoreographyCueV2 =
    options.baseRevision === 0
      ? { cue: "enter", targetIds: [target] }
      : { cue: "transform", targetIds: [target] };
  const patch = {
    v: 1,
    patchId: `lesson__cp_${options.checkpointId}`,
    narration,
    operations: [
      {
        op: "put",
        node: {
          id: target,
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 400,
          y: 80,
          text: narration,
          style: {
            color: "hsl(var(--chalk))",
            fontSize: 32,
            opacity: 1,
            anchor: "middle",
          },
        },
      },
    ],
  } as const;
  const receipt = {
    issuer: "projectile_motion_verifier",
    componentKind: "projectile_motion",
    componentId: options.resultComponent.id,
    action: options.action,
    checkpointId: options.checkpointId,
    clarificationTopic: options.clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
    operationTargets: [target],
    obligationCodes: [...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS],
    verified: true,
  } as const;
  const choreography = {
    v: 2,
    phase: {
      cues: [cue],
      durationMs: 800,
      easing: "ease_out_quart",
      holdAfterMs: 500,
    },
  } as const;
  const certificate = {
    body: {
      v: 1,
      issuer: "projectile_motion_compiler",
      compilerVersion: PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
      canonicalization: "murmur-json-v1",
      hashAlgorithm: "sha256",
      beatId: beat.beatId,
      routedBeatSha256: "a".repeat(64),
      componentKind: "projectile_motion",
      componentId: options.resultComponent.id,
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseProblemSpecSha256,
      resultProblemSpecSha256,
      baseLowLevelRevision: options.baseRevision,
      resultLowLevelRevision: resultRevision,
      baseSemanticRevision: options.baseRevision,
      resultSemanticRevision: resultRevision,
      baseLowLevelSceneSha256: "b".repeat(64),
      resultLowLevelSceneSha256: "c".repeat(64),
      baseSemanticSceneSha256: "d".repeat(64),
      resultSemanticSceneSha256: "e".repeat(64),
      patchSha256: "f".repeat(64),
      receiptSha256: "0".repeat(64),
      presentationCheckpoint: clone(presentation),
      choreographySha256: "1".repeat(64),
      previousCertificateSha256: options.baseHead,
    },
    certificateSha256: resultHead,
  } as const;
  const decoded = decodeProjectileChoreographySceneStreamEventV1({
    type: "projectile_choreography_scene_checkpoint",
    generation: options.request.generation,
    attempt: 1,
    sequence: options.sequence,
    baseRevision: options.baseRevision,
    resultRevision,
    patch,
    semantic: {
      baseProblemSpec: baseProblem,
      resultProblemSpec: resultProblem,
      beat,
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseComponent: options.baseComponent,
      resultComponent: options.resultComponent,
      semanticBaseRevision: options.baseRevision,
      semanticResultRevision: resultRevision,
      semanticBaseCertificateSha256: options.baseHead,
      semanticResultCertificateSha256: resultHead,
      receipt,
      presentation,
      choreography,
      certificate,
    },
  });
  if (decoded.type !== "projectile_choreography_scene_checkpoint") {
    throw new TypeError("Fixture did not decode as a projectile checkpoint");
  }
  return decoded;
}

function state(
  base: ProjectileMotionStateV1 | null,
  problemSpec: ProjectileMotionProblemSpecV1,
  lastMainCheckpoint: ProjectileMotionStateV1["lastMainCheckpoint"],
  clarifiedTopics = base?.clarifiedTopics ?? [],
  activeClarification: ProjectileMotionClarificationTopic | null = null,
): ProjectileMotionStateV1 {
  return {
    kind: "projectile_motion",
    id: base?.id ?? "lesson",
    problemSpec,
    lastMainCheckpoint,
    clarifiedTopics,
    activeClarification,
  };
}

export interface ProjectileLifecycleFixtureOptions {
  /** Limit an advance response so product continuation can be exercised. */
  readonly maxAdvanceCheckpoints?: number;
}

/** Build one strict, chain-joined response for the request's closed route. */
export function createProjectileLifecycleFixture(
  request: ProjectileMotionRequestV1,
  options: ProjectileLifecycleFixtureOptions = {},
): readonly ProjectileChoreographySceneStreamEventV1[] {
  if (request.routingMode !== "reflex") {
    throw new TypeError("The provider-free product fixture requires Reflex");
  }
  let baseComponent = request.baseSemanticScene.components[0] ?? null;
  let baseRevision = request.baseScene.revision;
  let baseHead = request.baseSemanticScene.certificateHeadSha256 ?? null;
  const route = request.requestedRoute;
  const checkpoints: ProjectileChoreographySceneCheckpointEventV1[] = [];

  const append = (
    checkpointId: ProjectileMotionCheckpointId,
    action: CheckpointOptions["action"],
    clarificationTopic: ProjectileMotionClarificationTopic | null,
    resultComponent: ProjectileMotionStateV1,
  ) => {
    const event = checkpointEvent({
      request,
      sequence: checkpoints.length + 1,
      checkpointId,
      action,
      clarificationTopic,
      route,
      baseComponent,
      resultComponent,
      baseRevision,
      baseHead,
    });
    checkpoints.push(event);
    baseComponent = resultComponent;
    baseRevision = event.resultRevision;
    baseHead = event.semantic.semanticResultCertificateSha256;
  };

  if (route.intent === "advance") {
    const eligible = PROJECTILE_MOTION_STAGE_PREFIXES[route.targetStage];
    const start = baseComponent?.lastMainCheckpoint
      ? PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf(
          baseComponent.lastMainCheckpoint,
        ) + 1
      : 0;
    const requested = eligible.slice(start);
    const limit = options.maxAdvanceCheckpoints ?? requested.length;
    if (!Number.isSafeInteger(limit) || limit < 1) {
      throw new RangeError("maxAdvanceCheckpoints must be a positive integer");
    }
    for (const checkpointId of requested.slice(0, limit)) {
      const expected = nextProjectileMotionMainCheckpoint(
        baseComponent?.lastMainCheckpoint ?? null,
      );
      if (expected !== checkpointId) {
        throw new TypeError("Fixture advance is not the next main checkpoint");
      }
      append(
        checkpointId,
        "advance",
        null,
        state(baseComponent, request.problemSpec, checkpointId),
      );
    }
  } else if (route.intent === "clarify") {
    if (!baseComponent)
      throw new TypeError("Clarification requires a frontier");
    const topics = PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter(
      (topic) =>
        baseComponent!.clarifiedTopics.includes(topic) || topic === route.topic,
    );
    append(
      PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[route.topic],
      "clarify",
      route.topic,
      state(
        baseComponent,
        baseComponent.problemSpec,
        baseComponent.lastMainCheckpoint,
        topics,
        route.topic,
      ),
    );
  } else {
    if (!baseComponent) throw new TypeError("Retarget requires a frontier");
    append(
      "parameters_retargeted",
      "retarget",
      null,
      state(
        baseComponent,
        route.targetProblemSpec,
        baseComponent.lastMainCheckpoint,
        baseComponent.clarifiedTopics,
        baseComponent.activeClarification,
      ),
    );
  }

  return Object.freeze([
    decodeProjectileChoreographySceneStreamEventV1({
      type: "scene_stream_started",
      generation: request.generation,
      attempt: 1,
      baseRevision: request.baseScene.revision,
    }),
    ...checkpoints,
    decodeProjectileChoreographySceneStreamEventV1({
      type: "scene_stream_completed",
      generation: request.generation,
      finalRevision: baseRevision,
      patchCount: checkpoints.length,
      firstPatchMs: 12,
      totalMs: 24,
      repaired: false,
    }),
  ]);
}
