import {
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
  type RoutedChoreographyRouteV2,
} from "@/lib/live-scene";
import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "@/lib/live-scene/parametric-choreography";
import {
  decodeParametricChoreographyRequestV3,
  type ParametricChoreographyRequestV3,
} from "@/lib/live-scene/parametric-choreography-request";
import {
  MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
  type ParametricChoreographyDeclineReason,
  type ParametricChoreographySceneCheckpointEventV3,
  type ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";

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
import type { ParametricChoreographySceneStreamRunner } from "./parametric-choreography-model-stream";
import {
  EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
  createAcceptedParametricCheckpoint,
  preflightParametricChoreographyReplay,
  prepareParametricChoreographyCheckpoint,
  type AcceptedParametricChoreographyCheckpoint,
  type ParametricSemanticSceneState,
  type PreparedParametricChoreographyCheckpoint,
} from "./parametric-choreography-playback";

export type ParametricChoreographyRuntimePhase =
  CertifiedChoreographyRuntimePhase;

export type ParametricChoreographyCommand =
  | {
      readonly routingMode: "reflex";
      readonly problemText: string | null;
      readonly requestedRoute: RoutedChoreographyRouteV2;
    }
  | {
      readonly routingMode: "director";
      readonly problemText: string | null;
      readonly prompt: string;
    };

export type ParametricChoreographyRuntimeFailure =
  CertifiedChoreographyRuntimeFailure;

export type ParametricChoreographyRuntimeSnapshot =
  CertifiedChoreographyRuntimeSnapshot<
    ParametricSemanticSceneState,
    AcceptedParametricChoreographyCheckpoint,
    CompletingSquareCheckpointId,
    ParametricChoreographyDeclineReason
  >;

export type ParametricChoreographyRenderer = CheckpointChoreographyRenderer;

export interface ParametricChoreographyStreamRuntimeOptions {
  readonly renderer: ParametricChoreographyRenderer;
  readonly runStream: ParametricChoreographySceneStreamRunner;
  readonly layout: ChoreographyLayout;
  readonly queueLimit?: number;
}

export type ParametricChoreographyRuntimeErrorCode =
  CertifiedChoreographyRuntimeErrorCode;

export class ParametricChoreographyRuntimeError extends Error {
  readonly code: ParametricChoreographyRuntimeErrorCode;

  constructor(code: ParametricChoreographyRuntimeErrorCode, message: string) {
    super(message);
    this.name = "ParametricChoreographyRuntimeError";
    this.code = code;
  }
}

function createRequest(
  command: ParametricChoreographyCommand,
  generation: number,
  frontier: typeof EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
): ParametricChoreographyRequestV3 {
  const shared = {
    protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    problemText: command.problemText,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
  } as const;
  return decodeParametricChoreographyRequestV3(
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
  event: ParametricChoreographySceneStreamEventV3,
): CertifiedChoreographyStreamEvent<
  ParametricChoreographySceneCheckpointEventV3,
  ParametricChoreographyDeclineReason
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
    case "parametric_choreography_scene_checkpoint":
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
    case "parametric_choreography_scene_stream_declined":
      return Object.freeze({
        kind: "declined",
        generation: event.generation,
        attempt: event.attempt,
        finalRevision: event.finalRevision,
        reasonCode: event.reasonCode,
        message: event.message,
      });
    case "parametric_choreography_scene_stream_failed":
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

const PARAMETRIC_CHOREOGRAPHY_RUNTIME_DOMAIN = Object.freeze({
  emptyFrontier: EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
  maxCheckpointsPerStream: MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
  maxRetainedCheckpoints: 9,
  copy: Object.freeze({
    ready: "Ready for a live parametric lesson.",
    preparing: "Preparing the first checkpoint…",
    busy: "A parametric generation or Replay is still active",
    resetRequired:
      "Reset or successfully Replay the accepted board before continuing",
    stoppedBeforeCheckpoint: "Stopped before another checkpoint appeared.",
    settlingInterruption: "Settling the checkpoint already in motion…",
    resetRendererFailed: "The board could not be cleared safely.",
    replaying: (count: number) =>
      `Replaying ${count} accepted checkpoint${count === 1 ? "" : "s"}.`,
    replayStoppedBeforeCheckpoint: "Replay stopped before the next checkpoint.",
    replaySettlementFailed:
      "Replay checkpoint did not reach its certified paint barrier",
    queuedCheckpointLostBase: "queued checkpoint lost its accepted base",
    checkpointSettlementFailed:
      "checkpoint did not reach its complete post-paint settlement",
    interruptionSettlementFailed:
      "interruption did not settle at a trustworthy checkpoint",
    replayRecoveryLost: "Replay interruption lost its recovery frontier",
    replayInterruptionSettlementFailed:
      "Replay interruption did not settle at a checkpoint",
    protocolRestorationFailed:
      "The stream stopped and the last settled checkpoint could not be restored. Reset it.",
    protocolRestored:
      "The stream stopped. The last settled checkpoint was restored.",
    playbackRestorationFailed:
      "The board could not restore its last settled checkpoint. Reset it.",
    playbackRestored:
      "The checkpoint failed to play. The last settled checkpoint was restored.",
    replayRestorationFailed:
      "Replay failed and the accepted board could not be restored. Reset it.",
    replayRestored: "Replay failed. The last accepted board was restored.",
    interruptionDeadlineFailed:
      "Checkpoint cancellation did not settle before its deadline",
    replayInterruptionDeadlineFailed:
      "Replay cancellation did not settle before its deadline",
    streamEndedWithoutTerminal: "stream ended without a terminal event",
    streamStoppedSafely: "The visual stream stopped safely.",
    disposed: "ParametricChoreographyStreamRuntime was disposed",
    resetFailureLog: "[LiveScene] Parametric reset failed:",
    rejectedEventLog: "[LiveScene] Rejected parametric event:",
    playbackFailureLog: "[LiveScene] Parametric playback failed:",
    replayFailureLog: "[LiveScene] Parametric Replay failed:",
    subscriberFailureLog:
      "[LiveScene] Parametric snapshot subscriber failed:",
  }),
  createRequest,
  interpretEvent,
  prepareCheckpoint: prepareParametricChoreographyCheckpoint,
  createAcceptedCheckpoint: createAcceptedParametricCheckpoint,
  preflightReplay: preflightParametricChoreographyReplay,
  checkpointCaption: (prepared: PreparedParametricChoreographyCheckpoint) =>
    prepared.event.patch.narration,
  checkpointId: (prepared: PreparedParametricChoreographyCheckpoint) =>
    prepared.event.semantic.checkpointId,
  acceptedCheckpointId: (accepted: AcceptedParametricChoreographyCheckpoint) =>
    accepted.event.semantic.checkpointId,
  acceptedSequence: (accepted: AcceptedParametricChoreographyCheckpoint) =>
    accepted.event.sequence,
  runtimeError: (
    code: CertifiedChoreographyRuntimeErrorCode,
    message: string,
  ) =>
    new ParametricChoreographyRuntimeError(code, message),
}) satisfies CertifiedChoreographyStreamDomain<
  ParametricChoreographyCommand,
  ParametricChoreographyRequestV3,
  ParametricChoreographySceneStreamEventV3,
  ParametricChoreographySceneCheckpointEventV3,
  ParametricSemanticSceneState,
  PreparedParametricChoreographyCheckpoint,
  AcceptedParametricChoreographyCheckpoint,
  CompletingSquareCheckpointId,
  ParametricChoreographyDeclineReason
>;

/** Gate 1.6 compatibility surface backed by the protocol-neutral runtime. */
export class ParametricChoreographyStreamRuntime extends CertifiedChoreographyStreamRuntime<
  ParametricChoreographyCommand,
  ParametricChoreographyRequestV3,
  ParametricChoreographySceneStreamEventV3,
  ParametricChoreographySceneCheckpointEventV3,
  ParametricSemanticSceneState,
  PreparedParametricChoreographyCheckpoint,
  AcceptedParametricChoreographyCheckpoint,
  CompletingSquareCheckpointId,
  ParametricChoreographyDeclineReason
> {
  constructor(options: ParametricChoreographyStreamRuntimeOptions) {
    super({ ...options, domain: PARAMETRIC_CHOREOGRAPHY_RUNTIME_DOMAIN });
  }
}
