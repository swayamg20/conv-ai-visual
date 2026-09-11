import type { ChoreographyLayout } from "@/lib/live-scene";
import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import {
  MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  type ProjectileChoreographyDeclineReason,
  type ProjectileChoreographySceneCheckpointEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import type {
  ProjectileMotionCheckpointId,
  ProjectileMotionProblemSpecV1,
  ProjectileMotionRouteV1,
} from "@/lib/live-scene/projectile-motion";

import {
  CertifiedChoreographyStreamRuntime,
  type CertifiedChoreographyRuntimeErrorCode,
  type CertifiedChoreographyRuntimeFailure,
  type CertifiedChoreographyRuntimePhase,
  type CertifiedChoreographyRuntimeSnapshot,
  type CertifiedChoreographyStreamDomain,
  type CertifiedChoreographyStreamEvent,
} from "./certified-choreography-stream-runtime";
import type { CheckpointChoreographyRenderer } from "./checkpoint-choreography-player";
import type { ProjectileChoreographySceneStreamRunner } from "./projectile-choreography-model-stream";
import {
  EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
  createAcceptedProjectileCheckpoint,
  preflightProjectileChoreographyReplay,
  prepareProjectileChoreographyCheckpoint,
  type AcceptedProjectileChoreographyCheckpoint,
  type PreparedProjectileChoreographyCheckpoint,
  type ProjectileChoreographyFrontier,
  type ProjectileSemanticSceneState,
} from "./projectile-choreography-playback";

export const MAX_RETAINED_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS = 17;

const PROJECTILE_HISTORY_FULL_MESSAGE =
  "Reset the projectile board before continuing; its certified checkpoint history is full.";

export type ProjectileChoreographyRuntimePhase =
  CertifiedChoreographyRuntimePhase;

export type ProjectileChoreographyCommand =
  | {
      readonly routingMode: "reflex";
      readonly problemSpec: ProjectileMotionProblemSpecV1;
      readonly requestedRoute: ProjectileMotionRouteV1;
    }
  | {
      readonly routingMode: "director";
      readonly problemSpec: ProjectileMotionProblemSpecV1;
      readonly prompt: string;
    };

export type ProjectileChoreographyRuntimeFailure =
  CertifiedChoreographyRuntimeFailure;

export type ProjectileChoreographyRuntimeSnapshot =
  CertifiedChoreographyRuntimeSnapshot<
    ProjectileSemanticSceneState,
    AcceptedProjectileChoreographyCheckpoint,
    ProjectileMotionCheckpointId,
    ProjectileChoreographyDeclineReason
  >;

export type ProjectileChoreographyRenderer = CheckpointChoreographyRenderer;

export interface ProjectileChoreographyStreamRuntimeOptions {
  readonly renderer: ProjectileChoreographyRenderer;
  readonly runStream: ProjectileChoreographySceneStreamRunner;
  readonly layout: ChoreographyLayout;
  readonly queueLimit?: number;
}

export type ProjectileChoreographyRuntimeErrorCode =
  CertifiedChoreographyRuntimeErrorCode;

export class ProjectileChoreographyRuntimeError extends Error {
  readonly code: ProjectileChoreographyRuntimeErrorCode;

  constructor(code: ProjectileChoreographyRuntimeErrorCode, message: string) {
    super(message);
    this.name = "ProjectileChoreographyRuntimeError";
    this.code = code;
  }
}

function createRequest(
  command: ProjectileChoreographyCommand,
  generation: number,
  frontier: ProjectileChoreographyFrontier,
): ProjectileMotionRequestV1 {
  const shared = {
    protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    problemSpec: command.problemSpec,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
  } as const;
  return decodeProjectileMotionRequestV1(
    command.routingMode === "reflex"
      ? {
          ...shared,
          routingMode: "reflex",
          requestedRoute: command.requestedRoute,
        }
      : {
          ...shared,
          routingMode: "director",
          prompt: command.prompt,
        },
  );
}

function interpretEvent(
  event: ProjectileChoreographySceneStreamEventV1,
): CertifiedChoreographyStreamEvent<
  ProjectileChoreographySceneCheckpointEventV1,
  ProjectileChoreographyDeclineReason
> {
  switch (event.type) {
    case "scene_stream_started":
      return Object.freeze({
        kind: "started",
        generation: event.generation,
        attempt: event.attempt,
        baseRevision: event.baseRevision,
      });
    case "scene_stream_repairing":
      return Object.freeze({
        kind: "repairing",
        generation: event.generation,
        fromAttempt: event.fromAttempt,
        toAttempt: event.toAttempt,
        lastAcceptedRevision: event.lastAcceptedRevision,
        message: event.message,
      });
    case "projectile_choreography_scene_checkpoint":
      return Object.freeze({
        kind: "checkpoint",
        generation: event.generation,
        attempt: event.attempt,
        sequence: event.sequence,
        baseRevision: event.baseRevision,
        semanticBaseRevision: event.semantic.semanticBaseRevision,
        patchId: event.patch.patchId,
        checkpoint: event,
      });
    case "scene_stream_completed":
      return Object.freeze({
        kind: "completed",
        generation: event.generation,
        finalRevision: event.finalRevision,
        patchCount: event.patchCount,
        firstPatchMs: event.firstPatchMs,
        totalMs: event.totalMs,
        repaired: event.repaired,
      });
    case "projectile_choreography_scene_stream_declined":
      return Object.freeze({
        kind: "declined",
        generation: event.generation,
        attempt: event.attempt,
        finalRevision: event.finalRevision,
        reasonCode: event.reasonCode,
        message: event.message,
      });
    case "projectile_choreography_scene_stream_failed":
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

const PROJECTILE_CHOREOGRAPHY_RUNTIME_DOMAIN = Object.freeze({
  emptyFrontier: EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
  maxCheckpointsPerStream: MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  maxRetainedCheckpoints: MAX_RETAINED_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  copy: Object.freeze({
    ready: "Ready for a live projectile lesson.",
    preparing: "Preparing the launch checkpoint…",
    busy: "A projectile generation or Replay is still active",
    resetRequired:
      "Reset the projectile board or successfully Replay it before continuing",
    stoppedBeforeCheckpoint: "Stopped before another flight checkpoint appeared.",
    settlingInterruption: "Settling the flight checkpoint already in motion…",
    resetRendererFailed: "The projectile board could not be cleared safely.",
    replaying: (count: number) =>
      `Replaying ${count} accepted projectile checkpoint${count === 1 ? "" : "s"}.`,
    replayStoppedBeforeCheckpoint:
      "Projectile Replay stopped before the next checkpoint.",
    replaySettlementFailed:
      "Projectile Replay did not reach its certified paint barrier",
    queuedCheckpointLostBase:
      "queued projectile checkpoint lost its accepted base",
    checkpointSettlementFailed:
      "projectile checkpoint did not reach complete post-paint settlement",
    interruptionSettlementFailed:
      "projectile interruption did not settle at a trustworthy checkpoint",
    replayRecoveryLost: "Projectile Replay lost its recovery frontier",
    replayInterruptionSettlementFailed:
      "Projectile Replay interruption did not settle at a checkpoint",
    protocolRestorationFailed:
      "The flight stream stopped and the last settled checkpoint could not be restored. Reset it.",
    protocolRestored:
      "The flight stream stopped. The last settled checkpoint was restored.",
    playbackRestorationFailed:
      "The projectile board could not restore its last settled checkpoint. Reset it.",
    playbackRestored:
      "The flight checkpoint failed to play. The last settled checkpoint was restored.",
    replayRestorationFailed:
      "Projectile Replay failed and the accepted board could not be restored. Reset it.",
    replayRestored:
      "Projectile Replay failed. The last accepted board was restored.",
    interruptionDeadlineFailed:
      "Projectile checkpoint cancellation did not settle before its deadline",
    replayInterruptionDeadlineFailed:
      "Projectile Replay cancellation did not settle before its deadline",
    streamEndedWithoutTerminal:
      "projectile stream ended without a terminal event",
    streamStoppedSafely: "The projectile stream stopped safely.",
    disposed: "ProjectileChoreographyStreamRuntime was disposed",
    resetFailureLog: "[LiveScene] Projectile reset failed:",
    rejectedEventLog: "[LiveScene] Rejected projectile event:",
    playbackFailureLog: "[LiveScene] Projectile playback failed:",
    replayFailureLog: "[LiveScene] Projectile Replay failed:",
    subscriberFailureLog:
      "[LiveScene] Projectile snapshot subscriber failed:",
  }),
  createRequest,
  interpretEvent,
  prepareCheckpoint: prepareProjectileChoreographyCheckpoint,
  createAcceptedCheckpoint: createAcceptedProjectileCheckpoint,
  preflightReplay: preflightProjectileChoreographyReplay,
  checkpointCaption: (prepared: PreparedProjectileChoreographyCheckpoint) =>
    prepared.event.patch.narration,
  checkpointId: (prepared: PreparedProjectileChoreographyCheckpoint) =>
    prepared.event.semantic.checkpointId,
  acceptedCheckpointId: (accepted: AcceptedProjectileChoreographyCheckpoint) =>
    accepted.event.semantic.checkpointId,
  acceptedSequence: (accepted: AcceptedProjectileChoreographyCheckpoint) =>
    accepted.event.sequence,
  runtimeError: (
    code: CertifiedChoreographyRuntimeErrorCode,
    message: string,
  ) => new ProjectileChoreographyRuntimeError(code, message),
}) satisfies CertifiedChoreographyStreamDomain<
  ProjectileChoreographyCommand,
  ProjectileMotionRequestV1,
  ProjectileChoreographySceneStreamEventV1,
  ProjectileChoreographySceneCheckpointEventV1,
  ProjectileSemanticSceneState,
  PreparedProjectileChoreographyCheckpoint,
  AcceptedProjectileChoreographyCheckpoint,
  ProjectileMotionCheckpointId,
  ProjectileChoreographyDeclineReason
>;

/** Gate 1.7 projectile adapter backed by the protocol-neutral runtime. */
export class ProjectileChoreographyStreamRuntime extends CertifiedChoreographyStreamRuntime<
  ProjectileChoreographyCommand,
  ProjectileMotionRequestV1,
  ProjectileChoreographySceneStreamEventV1,
  ProjectileChoreographySceneCheckpointEventV1,
  ProjectileSemanticSceneState,
  PreparedProjectileChoreographyCheckpoint,
  AcceptedProjectileChoreographyCheckpoint,
  ProjectileMotionCheckpointId,
  ProjectileChoreographyDeclineReason
> {
  constructor(options: ProjectileChoreographyStreamRuntimeOptions) {
    super({ ...options, domain: PROJECTILE_CHOREOGRAPHY_RUNTIME_DOMAIN });
  }

  override start(command: ProjectileChoreographyCommand): number {
    if (
      this.getSnapshot().accepted.length >=
      MAX_RETAINED_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS
    ) {
      throw new ProjectileChoreographyRuntimeError(
        "runtime_reset_required",
        PROJECTILE_HISTORY_FULL_MESSAGE,
      );
    }
    return super.start(command);
  }
}
