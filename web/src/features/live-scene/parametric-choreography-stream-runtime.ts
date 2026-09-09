import { type RoutedChoreographyRouteV2 } from "@/lib/live-scene";
import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "@/lib/live-scene/parametric-choreography";
import {
  decodeParametricChoreographyRequestV3,
  type ParametricChoreographyRequestV3,
} from "@/lib/live-scene/parametric-choreography-request";
import type {
  ParametricChoreographyDeclineReason,
  ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";
import type {
  ChoreographyLayout,
  CompletingSquareCheckpointId,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";

import type { ChoreographyPlaybackOutcome } from "./choreography-executor";
import {
  CheckpointChoreographyPlayer,
  type CheckpointChoreographyRenderer,
  type CheckpointPlaybackSession,
} from "./checkpoint-choreography-player";
import type { ParametricChoreographySceneStreamRunner } from "./parametric-choreography-model-stream";
import {
  EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
  createAcceptedParametricCheckpoint,
  preflightParametricChoreographyReplay,
  prepareParametricChoreographyCheckpoint,
  type AcceptedParametricChoreographyCheckpoint,
  type ParametricChoreographyFrontier,
  type ParametricSemanticSceneState,
  type PreparedParametricChoreographyCheckpoint,
} from "./parametric-choreography-playback";

export type ParametricChoreographyRuntimePhase =
  | "idle"
  | "connecting"
  | "streaming"
  | "repairing"
  | "completing"
  | "completed"
  | "declined"
  | "failed"
  | "interrupting"
  | "interrupted"
  | "replaying";

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

export interface ParametricChoreographyRuntimeFailure {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface ParametricChoreographyRuntimeSnapshot {
  readonly phase: ParametricChoreographyRuntimePhase;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly committedScene: SceneState;
  readonly provisionalScene: SceneState;
  readonly committedSemanticScene: ParametricSemanticSceneState;
  readonly provisionalSemanticScene: ParametricSemanticSceneState;
  readonly committedViewport: ViewportPoseV1 | null;
  readonly provisionalViewport: ViewportPoseV1 | null;
  readonly accepted: readonly AcceptedParametricChoreographyCheckpoint[];
  readonly queuedCheckpointCount: number;
  readonly activeRevision?: number;
  readonly narration: string;
  readonly visibleCheckpointId?: CompletingSquareCheckpointId;
  readonly rendererTrusted: boolean;
  readonly error?: ParametricChoreographyRuntimeFailure;
  readonly completion?: {
    readonly firstPatchMs: number;
    readonly totalMs: number;
    readonly repaired: boolean;
  };
  readonly decline?: {
    readonly reasonCode: ParametricChoreographyDeclineReason;
  };
}

export type ParametricChoreographyRenderer = CheckpointChoreographyRenderer;

export interface ParametricChoreographyStreamRuntimeOptions {
  readonly renderer: ParametricChoreographyRenderer;
  readonly runStream: ParametricChoreographySceneStreamRunner;
  readonly layout: ChoreographyLayout;
  readonly queueLimit?: number;
}

export type ParametricChoreographyRuntimeErrorCode =
  "runtime_busy" | "runtime_reset_required" | "invalid_command";

export class ParametricChoreographyRuntimeError extends Error {
  readonly code: ParametricChoreographyRuntimeErrorCode;

  constructor(code: ParametricChoreographyRuntimeErrorCode, message: string) {
    super(message);
    this.name = "ParametricChoreographyRuntimeError";
    this.code = code;
  }
}

interface RuntimeToken {
  readonly id: number;
  readonly kind: "stream" | "replay";
  readonly generation: number;
}

interface StreamControl {
  readonly token: RuntimeToken;
  readonly controller: AbortController;
  terminal: "completed" | "declined" | "failed" | undefined;
}

interface ActivePlayback extends CheckpointPlaybackSession<PreparedParametricChoreographyCheckpoint> {
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly replayIndex?: number;
}

interface ReplayRecovery {
  readonly records: readonly AcceptedParametricChoreographyCheckpoint[];
  readonly frontier: ParametricChoreographyFrontier;
  readonly caption: string;
  readonly checkpointId?: CompletingSquareCheckpointId;
}

const MAX_CHECKPOINTS_PER_STREAM = 8;
const MAX_RETAINED_CHECKPOINTS = 9;
const INTERRUPTION_SETTLEMENT_TIMEOUT_MS = 2_000;

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

/** Owns V3 request, provisional, post-paint commit, interruption, and Replay. */
export class ParametricChoreographyStreamRuntime {
  private readonly player: CheckpointChoreographyPlayer;
  private readonly runStream: ParametricChoreographySceneStreamRunner;
  private readonly layout: ChoreographyLayout;
  private readonly queueLimit: number;
  private readonly listeners = new Set<() => void>();

  private tokenSequence = 0;
  private currentToken: RuntimeToken | null = null;
  private streamControl: StreamControl | null = null;
  private phase: ParametricChoreographyRuntimePhase = "idle";
  private generation = 0;
  private attempt = 0;
  private sequence = 0;
  private committed: ParametricChoreographyFrontier =
    EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
  private provisional: ParametricChoreographyFrontier =
    EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
  private accepted: AcceptedParametricChoreographyCheckpoint[] = [];
  private queue: PreparedParametricChoreographyCheckpoint[] = [];
  private active: ActivePlayback | null = null;
  private patchIds = new Set<string>();
  private interruptPending = false;
  private interruptionDeadline: ReturnType<
    typeof globalThis.setTimeout
  > | null = null;
  private replayRecovery: ReplayRecovery | null = null;
  private visibleCheckpointId: CompletingSquareCheckpointId | undefined;
  private committedCaption = "";
  private visibleCaption = "";
  private narration = "Ready for a live parametric lesson.";
  private rendererTrusted = true;
  private runtimeFailure: ParametricChoreographyRuntimeFailure | undefined;
  private completion: ParametricChoreographyRuntimeSnapshot["completion"];
  private decline: ParametricChoreographyRuntimeSnapshot["decline"];
  private snapshot: ParametricChoreographyRuntimeSnapshot;
  private disposed = false;

  constructor(options: ParametricChoreographyStreamRuntimeOptions) {
    if (options.layout !== "cinematic" && options.layout !== "compact") {
      throw new RangeError("layout must be cinematic or compact");
    }
    const queueLimit = options.queueLimit ?? MAX_CHECKPOINTS_PER_STREAM;
    if (
      !Number.isSafeInteger(queueLimit) ||
      queueLimit < 1 ||
      queueLimit > MAX_CHECKPOINTS_PER_STREAM
    ) {
      throw new RangeError(
        `queueLimit must be between 1 and ${MAX_CHECKPOINTS_PER_STREAM}`,
      );
    }
    this.player = new CheckpointChoreographyPlayer(options.renderer);
    this.runStream = options.runStream;
    this.layout = options.layout;
    this.queueLimit = queueLimit;
    this.snapshot = this.buildSnapshot();
  }

  getSnapshot = (): ParametricChoreographyRuntimeSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.assertUsable();
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(command: ParametricChoreographyCommand): number {
    this.assertUsable();
    if (this.isBusy()) {
      throw new ParametricChoreographyRuntimeError(
        "runtime_busy",
        "A parametric generation or Replay is still active",
      );
    }
    if (
      this.phase === "failed" &&
      (!this.rendererTrusted || this.runtimeFailure?.retryable === false)
    ) {
      throw new ParametricChoreographyRuntimeError(
        "runtime_reset_required",
        "Reset or successfully Replay the accepted board before continuing",
      );
    }

    const generation = this.generation + 1;
    let request: ParametricChoreographyRequestV3;
    try {
      const shared = {
        protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
        problemText: command.problemText,
        generation,
        baseScene: this.committed.scene,
        baseSemanticScene: this.committed.semanticScene,
      } as const;
      request = decodeParametricChoreographyRequestV3(
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
    } catch (error) {
      throw new ParametricChoreographyRuntimeError(
        "invalid_command",
        message(error),
      );
    }

    this.invalidate(true);
    const token = this.createToken("stream", generation);
    const controller = new AbortController();
    this.currentToken = token;
    this.streamControl = { token, controller, terminal: undefined };
    this.generation = generation;
    this.attempt = 0;
    this.sequence = 0;
    this.provisional = this.committed;
    this.queue = [];
    this.patchIds = new Set();
    this.interruptPending = false;
    this.phase = "connecting";
    this.narration = this.visibleCaption || "Preparing the first checkpoint…";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();

    void Promise.resolve()
      .then(() => {
        if (this.currentToken !== token || controller.signal.aborted) return;
        return this.runStream({
          request,
          signal: controller.signal,
          onEvent: (event) => this.acceptEvent(token, event),
        });
      })
      .then(() => this.onNetworkSettled(token))
      .catch((error: unknown) => this.onNetworkError(token, error));
    return generation;
  }

  interrupt(): boolean {
    this.assertUsable();
    if (!this.isBusy()) return false;
    if (this.interruptPending) return true;
    this.interruptPending = true;
    this.streamControl?.controller.abort();
    this.queue = [];
    this.provisional = this.active?.prepared.target ?? this.committed;
    if (!this.active) {
      this.currentToken = null;
      this.streamControl = null;
      this.interruptPending = false;
      this.provisional = this.committed;
      this.phase = "interrupted";
      this.narration =
        this.visibleCaption || "Stopped before another checkpoint appeared.";
      this.publish();
      return true;
    }
    this.phase = "interrupting";
    this.narration =
      this.visibleCaption || "Settling the checkpoint already in motion…";
    this.publish();
    try {
      this.player.cancel(this.active.playback);
      this.armInterruptionDeadline(this.active);
    } catch (error) {
      this.failPlayback(this.active.token, message(error));
    }
    return true;
  }

  reset(): void {
    this.assertUsable();
    this.invalidate(true);
    try {
      if (this.active) this.player.close(this.active);
      this.player.cancel(this.active?.playback);
    } catch {
      // Reset remains authoritative even if the renderer is already broken.
    }
    this.active = null;
    this.queue = [];
    try {
      this.player.clear();
    } catch (error) {
      this.rendererTrusted = false;
      this.phase = "failed";
      this.runtimeFailure = Object.freeze({
        code: "renderer_failed",
        message: "The board could not be cleared safely.",
        retryable: false,
      });
      this.narration = this.runtimeFailure.message;
      this.publish();
      console.warn("[LiveScene] Parametric reset failed:", error);
      return;
    }
    this.phase = "idle";
    this.generation = 0;
    this.attempt = 0;
    this.sequence = 0;
    this.committed = EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
    this.provisional = EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
    this.accepted = [];
    this.patchIds = new Set();
    this.interruptPending = false;
    this.replayRecovery = null;
    this.clearInterruptionDeadline();
    this.visibleCheckpointId = undefined;
    this.committedCaption = "";
    this.visibleCaption = "";
    this.narration = "Ready for a live parametric lesson.";
    this.rendererTrusted = true;
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();
  }

  async replayAccepted(): Promise<void> {
    this.assertUsable();
    if (this.isBusy()) {
      throw new ParametricChoreographyRuntimeError(
        "runtime_busy",
        "A parametric generation or Replay is still active",
      );
    }
    if (this.accepted.length === 0) return;

    const originalRecords = [...this.accepted];
    const originalFrontier = this.committed;
    const originalCaption = this.committedCaption;
    const originalCheckpoint = this.visibleCheckpointId;
    const recovery: ReplayRecovery = Object.freeze({
      records: originalRecords,
      frontier: originalFrontier,
      caption: originalCaption,
      ...(originalCheckpoint ? { checkpointId: originalCheckpoint } : {}),
    });
    let replay: ReturnType<typeof preflightParametricChoreographyReplay>;
    try {
      replay = preflightParametricChoreographyReplay(originalRecords);
    } catch (error) {
      this.failReplay(
        originalRecords,
        originalFrontier,
        originalCaption,
        originalCheckpoint,
        message(error),
      );
      return;
    }

    this.invalidate(true);
    const token = this.createToken("replay", this.generation);
    this.currentToken = token;
    this.replayRecovery = recovery;
    this.phase = "replaying";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.narration = `Replaying ${replay.records.length} accepted checkpoint${replay.records.length === 1 ? "" : "s"}.`;
    try {
      this.player.materializeEmpty(
        EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER.scene,
      );
    } catch (error) {
      this.failReplay(
        originalRecords,
        originalFrontier,
        originalCaption,
        originalCheckpoint,
        message(error),
      );
      return;
    }
    this.committed = EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
    this.provisional = EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER;
    this.visibleCaption = "";
    this.committedCaption = "";
    this.visibleCheckpointId = undefined;
    this.publish();

    for (const [index, prepared] of replay.checkpoints.entries()) {
      if (this.currentToken !== token) return;
      let transition: ActivePlayback;
      try {
        transition = this.beginPlayback(token, prepared, "replay", index);
      } catch (error) {
        this.failReplayRecovery(message(error));
        return;
      }
      const outcome = await this.player.outcome(transition.playback);
      if (this.currentToken !== token || this.active !== transition) return;
      this.player.close(transition);
      this.clearInterruptionDeadline();
      if (this.interruptPending) {
        this.finishReplayInterruption(transition, outcome, index);
        return;
      }
      if (!this.player.validSettlement(transition, outcome, "completed")) {
        this.failReplay(
          originalRecords,
          originalFrontier,
          originalCaption,
          originalCheckpoint,
          "Replay checkpoint did not reach its certified paint barrier",
        );
        return;
      }
      this.active = null;
      this.committed = prepared.target;
      this.provisional = prepared.target;
      this.committedCaption = prepared.event.patch.narration;
      this.visibleCaption = this.committedCaption;
      this.visibleCheckpointId = prepared.event.semantic.checkpointId;
      this.narration = this.visibleCaption;
      this.publish();
    }

    if (this.currentToken !== token) return;
    this.accepted = originalRecords;
    this.committed = originalFrontier;
    this.provisional = originalFrontier;
    this.currentToken = null;
    this.replayRecovery = null;
    this.active = null;
    this.phase = "completed";
    this.committedCaption = originalCaption;
    this.visibleCaption = originalCaption;
    this.visibleCheckpointId = originalCheckpoint;
    this.rendererTrusted = true;
    this.narration = originalCaption;
    this.publish();
  }

  dispose(): void {
    if (this.disposed) return;
    this.invalidate(true);
    try {
      if (this.active) this.player.close(this.active);
      this.player.cancel(this.active?.playback);
      this.player.cancelMotion();
    } catch {
      // Disposal has no recoverable user-facing state.
    }
    this.active = null;
    this.queue = [];
    this.listeners.clear();
    this.disposed = true;
  }

  private acceptEvent(
    token: RuntimeToken,
    event: ParametricChoreographySceneStreamEventV3,
  ): void {
    const control = this.streamControl;
    if (
      this.currentToken !== token ||
      !control ||
      control.token !== token ||
      control.terminal
    ) {
      return;
    }
    if (event.generation !== token.generation) {
      this.failProtocol(token, "event generation does not match its request");
      return;
    }
    try {
      switch (event.type) {
        case "scene_stream_started":
          if (
            this.attempt !== 0 ||
            event.attempt !== 1 ||
            event.baseRevision !== this.committed.scene.revision
          ) {
            throw new Error(
              "started event does not match the request frontier",
            );
          }
          this.attempt = 1;
          this.phase = "streaming";
          this.publish();
          return;
        case "scene_stream_repairing":
          if (
            this.attempt !== 1 ||
            event.fromAttempt !== 1 ||
            event.toAttempt !== 2 ||
            event.lastAcceptedRevision !== this.committed.scene.revision ||
            this.sequence !== 0 ||
            this.active ||
            this.queue.length > 0
          ) {
            throw new Error("repair event does not match an unchanged attempt");
          }
          this.attempt = 2;
          this.phase = "repairing";
          this.narration = this.visibleCaption || event.message;
          this.publish();
          return;
        case "parametric_choreography_scene_checkpoint":
          this.acceptCheckpoint(token, event);
          return;
        case "scene_stream_completed":
          if (
            this.attempt === 0 ||
            event.finalRevision !== this.provisional.scene.revision ||
            event.patchCount !== this.sequence ||
            event.repaired !== (this.attempt === 2)
          ) {
            throw new Error("completion does not match the checkpoint ledger");
          }
          control.terminal = "completed";
          this.completion = Object.freeze({
            firstPatchMs: event.firstPatchMs,
            totalMs: event.totalMs,
            repaired: event.repaired,
          });
          this.settleTerminal();
          return;
        case "parametric_choreography_scene_stream_declined":
          if (
            this.attempt === 0 ||
            event.attempt !== this.attempt ||
            event.finalRevision !== this.committed.scene.revision ||
            this.sequence !== 0 ||
            this.active ||
            this.queue.length > 0
          ) {
            throw new Error("decline does not match an unchanged frontier");
          }
          control.terminal = "declined";
          this.phase = "declined";
          this.narration = event.message;
          this.decline = Object.freeze({ reasonCode: event.reasonCode });
          this.publish();
          return;
        case "parametric_choreography_scene_stream_failed":
          if (
            this.attempt === 0 ||
            event.attempt !== this.attempt ||
            event.lastAcceptedRevision !== this.committed.scene.revision ||
            this.sequence !== 0 ||
            this.queue.length > 0 ||
            this.active !== null ||
            !same(this.provisional, this.committed)
          ) {
            throw new Error(
              "failure must precede every checkpoint in the atomic suffix",
            );
          }
          control.terminal = "failed";
          control.controller.abort();
          this.runtimeFailure = Object.freeze({
            code: event.code,
            message: event.message,
            retryable: event.retryable,
          });
          this.settleTerminal();
          return;
      }
    } catch (error) {
      this.failProtocol(token, message(error));
    }
  }

  private acceptCheckpoint(
    token: RuntimeToken,
    event: Extract<
      ParametricChoreographySceneStreamEventV3,
      { type: "parametric_choreography_scene_checkpoint" }
    >,
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.sequence !== this.sequence + 1 ||
      event.baseRevision !== this.provisional.scene.revision ||
      event.semantic.semanticBaseRevision !==
        this.provisional.semanticScene.revision
    ) {
      throw new Error("checkpoint does not join its attempt and frontier");
    }
    if (this.patchIds.has(event.patch.patchId)) {
      throw new Error("checkpoint patch ID was already admitted");
    }
    if (this.queue.length >= this.queueLimit) {
      throw new Error("checkpoint queue is full");
    }
    if (
      this.accepted.length + this.queue.length + (this.active ? 1 : 0) >=
      MAX_RETAINED_CHECKPOINTS
    ) {
      throw new Error("checkpoint history is full");
    }
    const prepared = prepareParametricChoreographyCheckpoint(
      this.provisional,
      event,
      this.layout,
    );
    this.provisional = prepared.target;
    this.sequence = event.sequence;
    this.patchIds.add(event.patch.patchId);
    this.queue.push(prepared);
    this.phase = "streaming";
    this.publish();
    this.pump(token);
  }

  private pump(token: RuntimeToken): void {
    if (this.currentToken !== token || this.active || this.queue.length === 0) {
      this.settleTerminal();
      return;
    }
    const prepared = this.queue.shift();
    if (!prepared) return;
    if (!this.preparedJoinsCommitted(prepared)) {
      this.failPlayback(token, "queued checkpoint lost its accepted base");
      return;
    }
    let transition: ActivePlayback;
    try {
      transition = this.beginPlayback(token, prepared, "stream");
    } catch (error) {
      this.failPlayback(token, message(error));
      return;
    }
    void this.player
      .outcome(transition.playback)
      .then((outcome) => this.finishStreamPlayback(transition, outcome));
  }

  private beginPlayback(
    token: RuntimeToken,
    prepared: PreparedParametricChoreographyCheckpoint,
    source: "stream" | "replay",
    replayIndex?: number,
  ): ActivePlayback {
    const session = this.player.start(prepared, () => {
      if (this.currentToken === token) {
        this.visibleCaption = prepared.event.patch.narration;
        this.visibleCheckpointId = prepared.event.semantic.checkpointId;
        this.narration = this.visibleCaption;
        this.publish();
      }
    });
    const active: ActivePlayback = {
      ...session,
      token,
      source,
      ...(replayIndex === undefined ? {} : { replayIndex }),
    };
    this.active = active;
    this.publish();
    return active;
  }

  private finishStreamPlayback(
    transition: ActivePlayback,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      transition.source !== "stream"
    ) {
      return;
    }
    this.clearInterruptionDeadline();
    this.player.close(transition);
    if (this.interruptPending) {
      this.finishStreamInterruption(transition, outcome);
      return;
    }
    if (!this.player.validSettlement(transition, outcome, "completed")) {
      this.failPlayback(
        transition.token,
        "checkpoint did not reach its complete post-paint settlement",
      );
      return;
    }
    try {
      this.commitPrepared(transition.prepared, outcome);
    } catch (error) {
      this.failPlayback(transition.token, message(error));
      return;
    }
    this.active = null;
    this.publish();
    this.pump(transition.token);
    this.settleTerminal();
  }

  private finishStreamInterruption(
    transition: ActivePlayback,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    let valid = false;
    if (
      this.player.validSettlement(
        transition,
        outcome,
        "cancelled_to_checkpoint",
      )
    ) {
      try {
        this.commitPrepared(transition.prepared, outcome);
        valid = true;
      } catch {
        valid = false;
      }
    } else if (this.player.cancelledBeforePresented(transition, outcome)) {
      valid = this.restoreRenderer(this.committed) === undefined;
    }
    if (!valid) {
      this.failPlayback(
        transition.token,
        "interruption did not settle at a trustworthy checkpoint",
      );
      return;
    }
    this.active = null;
    this.currentToken = null;
    this.streamControl = null;
    this.interruptPending = false;
    this.queue = [];
    this.provisional = this.committed;
    this.phase = "interrupted";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.narration =
      this.visibleCaption || "Stopped before another checkpoint appeared.";
    this.publish();
  }

  private finishReplayInterruption(
    transition: ActivePlayback,
    outcome: ChoreographyPlaybackOutcome,
    index: number,
  ): void {
    const recovery = this.replayRecovery;
    if (!recovery) {
      this.failPlayback(
        transition.token,
        "Replay interruption lost its recovery frontier",
      );
      return;
    }
    let prefixLength = index;
    if (
      this.player.validSettlement(
        transition,
        outcome,
        "cancelled_to_checkpoint",
      )
    ) {
      this.committed = transition.prepared.target;
      this.provisional = this.committed;
      this.committedCaption = transition.prepared.event.patch.narration;
      this.visibleCaption = this.committedCaption;
      this.visibleCheckpointId =
        transition.prepared.event.semantic.checkpointId;
      prefixLength += 1;
    } else if (!this.player.cancelledBeforePresented(transition, outcome)) {
      this.failReplayRecovery(
        "Replay interruption did not settle at a checkpoint",
      );
      return;
    }
    this.accepted = [...recovery.records.slice(0, prefixLength)];
    this.sequence = this.accepted.at(-1)?.event.sequence ?? 0;
    this.active = null;
    this.currentToken = null;
    this.interruptPending = false;
    this.replayRecovery = null;
    this.phase = "interrupted";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.narration =
      this.visibleCaption || "Replay stopped before the next checkpoint.";
    this.publish();
  }

  private commitPrepared(
    prepared: PreparedParametricChoreographyCheckpoint,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    if (this.accepted.length >= MAX_RETAINED_CHECKPOINTS) {
      throw new Error("checkpoint history is full");
    }
    const accepted = createAcceptedParametricCheckpoint(prepared, outcome);
    this.accepted = [...this.accepted, accepted];
    this.committed = prepared.target;
    this.rendererTrusted = true;
    this.committedCaption = prepared.event.patch.narration;
    this.visibleCaption = this.committedCaption;
    this.visibleCheckpointId = prepared.event.semantic.checkpointId;
    this.narration = this.visibleCaption;
  }

  private preparedJoinsCommitted(
    prepared: PreparedParametricChoreographyCheckpoint,
  ): boolean {
    return (
      same(prepared.base.scene, this.committed.scene) &&
      same(prepared.base.semanticScene, this.committed.semanticScene) &&
      prepared.base.certificateHeadSha256 ===
        this.committed.certificateHeadSha256 &&
      prepared.base.layout === this.layout &&
      (this.committed.viewport === null
        ? prepared.bootstrappedViewport
        : same(prepared.base.viewport, this.committed.viewport))
    );
  }

  private settleTerminal(): void {
    const terminal = this.streamControl?.terminal;
    if (!terminal) return;
    if (this.active || this.queue.length > 0) {
      this.phase = "completing";
    } else if (terminal === "completed") {
      this.phase = "completed";
      this.narration = this.visibleCaption;
    } else if (terminal === "failed") {
      this.phase = "failed";
      this.narration =
        this.runtimeFailure?.message || "The visual stream stopped safely.";
    }
    this.publish();
  }

  private failProtocol(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    this.streamControl?.controller.abort();
    if (this.streamControl) this.streamControl.terminal = "failed";
    this.queue = [];
    this.provisional = this.active?.prepared.target ?? this.committed;
    this.runtimeFailure = Object.freeze({
      code: "invalid_stream_event",
      message: "The stream stopped. The last settled checkpoint is safe.",
      retryable: true,
    });
    console.warn("[LiveScene] Rejected parametric event:", detail);
    this.settleTerminal();
  }

  private failPlayback(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    this.clearInterruptionDeadline();
    this.streamControl?.controller.abort();
    if (this.active) this.player.close(this.active);
    this.active = null;
    this.queue = [];
    this.currentToken = null;
    this.streamControl = null;
    this.interruptPending = false;
    this.replayRecovery = null;
    this.provisional = this.committed;
    const restorationFailure = this.restoreRenderer(this.committed);
    this.rendererTrusted = restorationFailure === undefined;
    this.phase = "failed";
    this.runtimeFailure = Object.freeze({
      code: "renderer_failed",
      message: restorationFailure
        ? "The board could not restore its last settled checkpoint. Reset it."
        : "The checkpoint failed to play. The last settled checkpoint was restored.",
      retryable: restorationFailure === undefined,
    });
    this.narration = this.runtimeFailure.message;
    this.visibleCaption = this.committedCaption;
    this.visibleCheckpointId =
      this.accepted.at(-1)?.event.semantic.checkpointId;
    console.warn(
      "[LiveScene] Parametric playback failed:",
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private failReplay(
    records: readonly AcceptedParametricChoreographyCheckpoint[],
    frontier: ParametricChoreographyFrontier,
    caption: string,
    checkpointId: CompletingSquareCheckpointId | undefined,
    detail: string,
  ): void {
    this.clearInterruptionDeadline();
    if (this.active) this.player.close(this.active);
    this.active = null;
    this.currentToken = null;
    this.interruptPending = false;
    this.replayRecovery = null;
    this.accepted = [...records];
    this.committed = frontier;
    this.provisional = frontier;
    this.committedCaption = caption;
    this.visibleCaption = caption;
    this.visibleCheckpointId = checkpointId;
    const restorationFailure = this.restoreRenderer(frontier);
    this.rendererTrusted = restorationFailure === undefined;
    this.phase = "failed";
    this.runtimeFailure = Object.freeze({
      code: "replay_integrity_failed",
      message: restorationFailure
        ? "Replay failed and the accepted board could not be restored. Reset it."
        : "Replay failed. The last accepted board was restored.",
      retryable: false,
    });
    this.narration = this.runtimeFailure.message;
    console.warn(
      "[LiveScene] Parametric Replay failed:",
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private restoreRenderer(
    frontier: ParametricChoreographyFrontier,
  ): string | undefined {
    return this.player.restore(frontier);
  }

  private failReplayRecovery(detail: string): void {
    const recovery = this.replayRecovery;
    if (!recovery) {
      const token = this.currentToken;
      if (token) this.failPlayback(token, detail);
      return;
    }
    this.failReplay(
      recovery.records,
      recovery.frontier,
      recovery.caption,
      recovery.checkpointId,
      detail,
    );
  }

  private armInterruptionDeadline(active: ActivePlayback): void {
    this.clearInterruptionDeadline();
    this.interruptionDeadline = globalThis.setTimeout(() => {
      this.interruptionDeadline = null;
      if (
        this.currentToken !== active.token ||
        this.active !== active ||
        !this.interruptPending
      ) {
        return;
      }
      if (active.source === "replay") {
        this.failReplayRecovery(
          "Replay cancellation did not settle before its deadline",
        );
      } else {
        this.failPlayback(
          active.token,
          "Checkpoint cancellation did not settle before its deadline",
        );
      }
    }, INTERRUPTION_SETTLEMENT_TIMEOUT_MS);
  }

  private clearInterruptionDeadline(): void {
    if (this.interruptionDeadline === null) return;
    globalThis.clearTimeout(this.interruptionDeadline);
    this.interruptionDeadline = null;
  }

  private onNetworkSettled(token: RuntimeToken): void {
    if (this.currentToken !== token || this.streamControl?.token !== token) {
      return;
    }
    if (!this.streamControl.terminal) {
      this.failProtocol(token, "stream ended without a terminal event");
    }
  }

  private onNetworkError(token: RuntimeToken, error: unknown): void {
    if (this.currentToken !== token || this.streamControl?.token !== token) {
      return;
    }
    if (
      this.streamControl.terminal ||
      (this.streamControl.controller.signal.aborted && isAbortError(error))
    ) {
      return;
    }
    this.failProtocol(token, message(error));
  }

  private createToken(
    kind: RuntimeToken["kind"],
    generation: number,
  ): RuntimeToken {
    return Object.freeze({ id: ++this.tokenSequence, kind, generation });
  }

  private invalidate(abort: boolean): void {
    this.clearInterruptionDeadline();
    if (abort) this.streamControl?.controller.abort();
    this.currentToken = null;
    this.streamControl = null;
    this.interruptPending = false;
  }

  private isBusy(): boolean {
    return (
      this.active !== null ||
      this.queue.length > 0 ||
      this.phase === "connecting" ||
      this.phase === "streaming" ||
      this.phase === "repairing" ||
      this.phase === "completing" ||
      this.phase === "interrupting" ||
      this.phase === "replaying"
    );
  }

  private publish(): void {
    this.snapshot = this.buildSnapshot();
    for (const listener of [...this.listeners]) {
      try {
        listener();
      } catch (error) {
        console.error(
          "[LiveScene] Parametric snapshot subscriber failed:",
          error,
        );
      }
    }
  }

  private buildSnapshot(): ParametricChoreographyRuntimeSnapshot {
    return Object.freeze({
      phase: this.phase,
      generation: this.generation,
      attempt: this.attempt,
      sequence: this.sequence,
      committedScene: this.committed.scene,
      provisionalScene: this.provisional.scene,
      committedSemanticScene: this.committed.semanticScene,
      provisionalSemanticScene: this.provisional.semanticScene,
      committedViewport: this.committed.viewport,
      provisionalViewport: this.provisional.viewport,
      accepted: Object.freeze([...this.accepted]),
      queuedCheckpointCount: this.queue.length,
      ...(this.active
        ? { activeRevision: this.active.prepared.target.scene.revision }
        : {}),
      narration: this.narration,
      ...(this.visibleCheckpointId
        ? { visibleCheckpointId: this.visibleCheckpointId }
        : {}),
      rendererTrusted: this.rendererTrusted,
      ...(this.runtimeFailure ? { error: this.runtimeFailure } : {}),
      ...(this.completion ? { completion: this.completion } : {}),
      ...(this.decline ? { decline: this.decline } : {}),
    });
  }

  private assertUsable(): void {
    if (this.disposed) {
      throw new Error("ParametricChoreographyStreamRuntime was disposed");
    }
  }
}
