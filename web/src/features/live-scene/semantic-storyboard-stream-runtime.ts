import type { ChoreographyLayout } from "@/lib/live-scene";
import {
  MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS,
  MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
  storyboardHasForwardCapacity,
  type ProjectileStoryboardSemanticSceneStateV1,
  type SemanticStoryboardRequestV1,
  type StoryboardAbstainReasonCode,
} from "@/lib/live-scene/semantic-storyboard";
import type {
  SemanticStoryboardSceneCheckpointEventV1,
  SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  CertifiedChoreographyStreamRuntime,
  type CertifiedChoreographyRuntimeErrorCode,
  type CertifiedChoreographyRuntimeFailure,
  type CertifiedChoreographyRuntimePhase,
  type CertifiedChoreographyRuntimeSnapshot,
  type CertifiedChoreographyStreamDomain,
} from "./certified-choreography-stream-runtime";
import type { CheckpointChoreographyRenderer } from "./checkpoint-choreography-player";
import type { SemanticStoryboardSceneStreamRunner } from "./semantic-storyboard-model-stream";
import {
  EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
  adaptSemanticStoryboardStreamEvent,
  createAcceptedSemanticStoryboardCheckpoint,
  createSemanticStoryboardRequest,
  preflightSemanticStoryboardReplay,
  prepareSemanticStoryboardCheckpoint,
  type AcceptedSemanticStoryboardCheckpoint,
  type PreparedSemanticStoryboardCheckpoint,
  type SemanticStoryboardCommand,
  type SemanticStoryboardFrontier,
} from "./semantic-storyboard-playback";

export const MAX_RETAINED_SEMANTIC_STORYBOARD_CHECKPOINTS =
  1 + MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS;

const STORYBOARD_HISTORY_FULL_MESSAGE =
  "Reset the storyboard before continuing; its certified beat history is full.";

export type SemanticStoryboardRuntimePhase = CertifiedChoreographyRuntimePhase;
export type SemanticStoryboardRuntimeFailure =
  CertifiedChoreographyRuntimeFailure;
export type SemanticStoryboardRenderer = CheckpointChoreographyRenderer;
export type { SemanticStoryboardCommand };

export type SemanticStoryboardRuntimeSnapshot =
  CertifiedChoreographyRuntimeSnapshot<
    ProjectileStoryboardSemanticSceneStateV1,
    AcceptedSemanticStoryboardCheckpoint,
    string,
    StoryboardAbstainReasonCode
  >;

export interface SemanticStoryboardStreamRuntimeOptions {
  readonly renderer: SemanticStoryboardRenderer;
  readonly runStream: SemanticStoryboardSceneStreamRunner;
  readonly layout: ChoreographyLayout;
  readonly queueLimit?: number;
}

export type SemanticStoryboardRuntimeErrorCode =
  CertifiedChoreographyRuntimeErrorCode;

export class SemanticStoryboardRuntimeError extends Error {
  readonly code: SemanticStoryboardRuntimeErrorCode;

  constructor(code: SemanticStoryboardRuntimeErrorCode, message: string) {
    super(message);
    this.name = "SemanticStoryboardRuntimeError";
    this.code = code;
  }
}

function createRuntimeRequest(
  command: SemanticStoryboardCommand,
  generation: number,
  frontier: SemanticStoryboardFrontier,
): SemanticStoryboardRequestV1 {
  // Validate the complete command/frontier join before the capacity shortcut.
  // The generic runtime turns either failure into its stable invalid-command
  // boundary without starting transport.
  const request = createSemanticStoryboardRequest(
    command,
    generation,
    frontier,
  );
  const component = request.baseSemanticScene.components[0];
  if (
    request.routingMode === "director" &&
    component &&
    !storyboardHasForwardCapacity(
      request.problemSpec,
      component.acceptedRecords,
    )
  ) {
    throw new Error(STORYBOARD_HISTORY_FULL_MESSAGE);
  }
  return request;
}

const SEMANTIC_STORYBOARD_RUNTIME_DOMAIN = Object.freeze({
  emptyFrontier: EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
  maxCheckpointsPerStream: MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
  maxRetainedCheckpoints: MAX_RETAINED_SEMANTIC_STORYBOARD_CHECKPOINTS,
  copy: Object.freeze({
    ready: "Ready to compose a live visual explanation.",
    preparing: "Establishing the certified launch frame…",
    busy: "A storyboard generation or Replay is still active",
    resetRequired:
      "Reset the storyboard or successfully Replay it before continuing",
    stoppedBeforeCheckpoint: "Stopped before another visual beat appeared.",
    settlingInterruption: "Settling the visual beat already in motion…",
    resetRendererFailed: "The storyboard could not be cleared safely.",
    replaying: (count: number) =>
      `Replaying ${count} accepted storyboard checkpoint${count === 1 ? "" : "s"}.`,
    replayStoppedBeforeCheckpoint:
      "Storyboard Replay stopped before the next checkpoint.",
    replaySettlementFailed:
      "Storyboard Replay did not reach its certified paint barrier",
    queuedCheckpointLostBase:
      "queued storyboard checkpoint lost its accepted base",
    checkpointSettlementFailed:
      "storyboard checkpoint did not reach complete post-paint settlement",
    interruptionSettlementFailed:
      "storyboard interruption did not settle at a trustworthy checkpoint",
    replayRecoveryLost: "Storyboard Replay lost its recovery frontier",
    replayInterruptionSettlementFailed:
      "Storyboard Replay interruption did not settle at a checkpoint",
    protocolRestorationFailed:
      "The storyboard stream stopped and the last settled checkpoint could not be restored. Reset it.",
    protocolRestored:
      "The storyboard stream stopped. The last settled checkpoint was restored.",
    playbackRestorationFailed:
      "The storyboard could not restore its last settled checkpoint. Reset it.",
    playbackRestored:
      "The visual beat failed to play. The last settled checkpoint was restored.",
    replayRestorationFailed:
      "Storyboard Replay failed and the accepted board could not be restored. Reset it.",
    replayRestored:
      "Storyboard Replay failed. The last accepted board was restored.",
    interruptionDeadlineFailed:
      "Storyboard checkpoint cancellation did not settle before its deadline",
    replayInterruptionDeadlineFailed:
      "Storyboard Replay cancellation did not settle before its deadline",
    streamEndedWithoutTerminal:
      "semantic storyboard stream ended without a terminal event",
    streamStoppedSafely: "The semantic storyboard stream stopped safely.",
    disposed: "SemanticStoryboardStreamRuntime was disposed",
    resetFailureLog: "[LiveScene] Storyboard reset failed:",
    rejectedEventLog: "[LiveScene] Rejected storyboard event:",
    playbackFailureLog: "[LiveScene] Storyboard playback failed:",
    replayFailureLog: "[LiveScene] Storyboard Replay failed:",
    subscriberFailureLog: "[LiveScene] Storyboard snapshot subscriber failed:",
  }),
  createRequest: createRuntimeRequest,
  interpretEvent: adaptSemanticStoryboardStreamEvent,
  prepareCheckpoint: prepareSemanticStoryboardCheckpoint,
  createAcceptedCheckpoint: createAcceptedSemanticStoryboardCheckpoint,
  preflightReplay: preflightSemanticStoryboardReplay,
  checkpointCaption: (prepared: PreparedSemanticStoryboardCheckpoint) =>
    prepared.event.patch.narration,
  checkpointId: (prepared: PreparedSemanticStoryboardCheckpoint) =>
    prepared.event.transition.checkpoint.checkpointId,
  acceptedCheckpointId: (accepted: AcceptedSemanticStoryboardCheckpoint) =>
    accepted.event.transition.checkpoint.checkpointId,
  acceptedSequence: (accepted: AcceptedSemanticStoryboardCheckpoint) =>
    accepted.event.sequence,
  runtimeError: (
    code: CertifiedChoreographyRuntimeErrorCode,
    message: string,
  ) => new SemanticStoryboardRuntimeError(code, message),
}) satisfies CertifiedChoreographyStreamDomain<
  SemanticStoryboardCommand,
  SemanticStoryboardRequestV1,
  SemanticStoryboardSceneStreamEventV1,
  SemanticStoryboardSceneCheckpointEventV1,
  ProjectileStoryboardSemanticSceneStateV1,
  PreparedSemanticStoryboardCheckpoint,
  AcceptedSemanticStoryboardCheckpoint,
  string,
  StoryboardAbstainReasonCode
>;

/** Gate 1.8 domain adapter backed by the protocol-neutral certified runtime. */
export class SemanticStoryboardStreamRuntime extends CertifiedChoreographyStreamRuntime<
  SemanticStoryboardCommand,
  SemanticStoryboardRequestV1,
  SemanticStoryboardSceneStreamEventV1,
  SemanticStoryboardSceneCheckpointEventV1,
  ProjectileStoryboardSemanticSceneStateV1,
  PreparedSemanticStoryboardCheckpoint,
  AcceptedSemanticStoryboardCheckpoint,
  string,
  StoryboardAbstainReasonCode
> {
  constructor(options: SemanticStoryboardStreamRuntimeOptions) {
    super({ ...options, domain: SEMANTIC_STORYBOARD_RUNTIME_DOMAIN });
  }
}

export type {
  AcceptedSemanticStoryboardCheckpoint,
  PreparedSemanticStoryboardCheckpoint,
  SemanticStoryboardFrontier,
};
