import type {
  ChoreographyLayout,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";

import type { ChoreographyPlaybackOutcome } from "./choreography-executor";
import {
  CheckpointChoreographyPlayer,
  type CheckpointChoreographyRenderer,
  type CheckpointPlaybackSession,
  type PlayableCheckpoint,
} from "./checkpoint-choreography-player";

export type CertifiedChoreographyRuntimePhase =
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

export interface CertifiedChoreographyRuntimeFailure {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface CertifiedChoreographySemanticScene {
  readonly revision: number;
}

export interface CertifiedChoreographyFrontier<
  SemanticScene extends CertifiedChoreographySemanticScene,
> {
  readonly scene: SceneState;
  readonly semanticScene: SemanticScene;
  readonly viewport: ViewportPoseV1 | null;
  readonly layout: ChoreographyLayout | null;
  readonly certificateHeadSha256: string | null;
}

export interface CertifiedChoreographyPreparedCheckpoint<
  CheckpointEvent,
  SemanticScene extends CertifiedChoreographySemanticScene,
> extends PlayableCheckpoint {
  readonly event: CheckpointEvent;
  readonly base: CertifiedChoreographyFrontier<SemanticScene> & {
    readonly viewport: ViewportPoseV1;
    readonly layout: ChoreographyLayout;
  };
  readonly target: CertifiedChoreographyFrontier<SemanticScene> & {
    readonly viewport: ViewportPoseV1;
    readonly layout: ChoreographyLayout;
  };
}

export interface CertifiedChoreographyReplay<
  Prepared,
  Accepted,
  SemanticScene extends CertifiedChoreographySemanticScene,
> {
  readonly checkpoints: readonly Prepared[];
  readonly records: readonly Accepted[];
  readonly frontier: CertifiedChoreographyFrontier<SemanticScene>;
}

export type CertifiedChoreographyStreamEvent<
  CheckpointEvent,
  DeclineReason extends string,
> =
  | {
      readonly kind: "started";
      readonly generation: number;
      readonly attempt: number;
      readonly baseRevision: number;
    }
  | {
      readonly kind: "repairing";
      readonly generation: number;
      readonly fromAttempt: number;
      readonly toAttempt: number;
      readonly lastAcceptedRevision: number;
      readonly message: string;
    }
  | {
      readonly kind: "checkpoint";
      readonly generation: number;
      readonly attempt: number;
      readonly sequence: number;
      readonly baseRevision: number;
      readonly semanticBaseRevision: number;
      readonly patchId: string;
      readonly checkpoint: CheckpointEvent;
    }
  | {
      readonly kind: "completed";
      readonly generation: number;
      readonly finalRevision: number;
      readonly patchCount: number;
      readonly firstPatchMs: number;
      readonly totalMs: number;
      readonly repaired: boolean;
    }
  | {
      readonly kind: "declined";
      readonly generation: number;
      readonly attempt: number;
      readonly finalRevision: number;
      readonly reasonCode: DeclineReason;
      readonly message: string;
    }
  | {
      readonly kind: "failed";
      readonly generation: number;
      readonly attempt: number;
      readonly lastAcceptedRevision: number;
      readonly code: string;
      readonly message: string;
      readonly retryable: boolean;
    };

export interface CertifiedChoreographyRuntimeCopy {
  readonly ready: string;
  readonly preparing: string;
  readonly busy: string;
  readonly resetRequired: string;
  readonly stoppedBeforeCheckpoint: string;
  readonly settlingInterruption: string;
  readonly resetRendererFailed: string;
  readonly replaying: (count: number) => string;
  readonly replayStoppedBeforeCheckpoint: string;
  readonly replaySettlementFailed: string;
  readonly queuedCheckpointLostBase: string;
  readonly checkpointSettlementFailed: string;
  readonly interruptionSettlementFailed: string;
  readonly replayRecoveryLost: string;
  readonly replayInterruptionSettlementFailed: string;
  readonly protocolRestorationFailed: string;
  readonly protocolRestored: string;
  readonly playbackRestorationFailed: string;
  readonly playbackRestored: string;
  readonly replayRestorationFailed: string;
  readonly replayRestored: string;
  readonly interruptionDeadlineFailed: string;
  readonly replayInterruptionDeadlineFailed: string;
  readonly streamEndedWithoutTerminal: string;
  readonly streamStoppedSafely: string;
  readonly disposed: string;
  readonly resetFailureLog: string;
  readonly rejectedEventLog: string;
  readonly playbackFailureLog: string;
  readonly replayFailureLog: string;
  readonly subscriberFailureLog: string;
}

export type CertifiedChoreographyRuntimeErrorCode =
  | "runtime_busy"
  | "runtime_reset_required"
  | "invalid_command";

export interface CertifiedChoreographyStreamDomain<
  Command,
  Request,
  StreamEvent,
  CheckpointEvent,
  SemanticScene extends CertifiedChoreographySemanticScene,
  Prepared extends CertifiedChoreographyPreparedCheckpoint<CheckpointEvent, SemanticScene>,
  Accepted,
  CheckpointId extends string,
  DeclineReason extends string,
> {
  readonly emptyFrontier: CertifiedChoreographyFrontier<SemanticScene>;
  readonly maxCheckpointsPerStream: number;
  readonly maxRetainedCheckpoints: number;
  readonly copy: CertifiedChoreographyRuntimeCopy;
  readonly createRequest: (
    command: Command,
    generation: number,
    frontier: CertifiedChoreographyFrontier<SemanticScene>,
  ) => Request;
  readonly interpretEvent: (
    event: StreamEvent,
  ) => CertifiedChoreographyStreamEvent<CheckpointEvent, DeclineReason>;
  readonly prepareCheckpoint: (
    frontier: CertifiedChoreographyFrontier<SemanticScene>,
    event: CheckpointEvent,
    layout: ChoreographyLayout,
  ) => Prepared;
  readonly createAcceptedCheckpoint: (
    prepared: Prepared,
    outcome: ChoreographyPlaybackOutcome,
  ) => Accepted;
  readonly preflightReplay: (
    records: readonly Accepted[],
  ) => CertifiedChoreographyReplay<Prepared, Accepted, SemanticScene>;
  readonly checkpointCaption: (prepared: Prepared) => string;
  readonly checkpointId: (prepared: Prepared) => CheckpointId;
  readonly acceptedCheckpointId: (accepted: Accepted) => CheckpointId;
  readonly acceptedSequence: (accepted: Accepted) => number;
  readonly runtimeError: (
    code: CertifiedChoreographyRuntimeErrorCode,
    message: string,
  ) => Error;
}

export interface CertifiedChoreographyStreamRunInvocation<Request, StreamEvent> {
  readonly request: Request;
  readonly signal: AbortSignal;
  readonly onEvent: (event: StreamEvent) => void;
}

export type CertifiedChoreographyStreamRunner<Request, StreamEvent> = (
  invocation: CertifiedChoreographyStreamRunInvocation<Request, StreamEvent>,
) => Promise<void>;

export interface CertifiedChoreographyRuntimeSnapshot<
  SemanticScene extends CertifiedChoreographySemanticScene,
  Accepted,
  CheckpointId extends string,
  DeclineReason extends string,
> {
  readonly phase: CertifiedChoreographyRuntimePhase;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly committedScene: SceneState;
  readonly provisionalScene: SceneState;
  readonly committedSemanticScene: SemanticScene;
  readonly provisionalSemanticScene: SemanticScene;
  readonly committedViewport: ViewportPoseV1 | null;
  readonly provisionalViewport: ViewportPoseV1 | null;
  readonly accepted: readonly Accepted[];
  readonly queuedCheckpointCount: number;
  readonly activeRevision?: number;
  readonly narration: string;
  readonly visibleCheckpointId?: CheckpointId;
  readonly rendererTrusted: boolean;
  readonly error?: CertifiedChoreographyRuntimeFailure;
  readonly completion?: {
    readonly firstPatchMs: number;
    readonly totalMs: number;
    readonly repaired: boolean;
  };
  readonly decline?: {
    readonly reasonCode: DeclineReason;
  };
}

export interface CertifiedChoreographyStreamRuntimeOptions<
  Command,
  Request,
  StreamEvent,
  CheckpointEvent,
  SemanticScene extends CertifiedChoreographySemanticScene,
  Prepared extends CertifiedChoreographyPreparedCheckpoint<CheckpointEvent, SemanticScene>,
  Accepted,
  CheckpointId extends string,
  DeclineReason extends string,
> {
  readonly renderer: CheckpointChoreographyRenderer;
  readonly runStream: CertifiedChoreographyStreamRunner<Request, StreamEvent>;
  readonly layout: ChoreographyLayout;
  readonly domain: CertifiedChoreographyStreamDomain<
    Command,
    Request,
    StreamEvent,
    CheckpointEvent,
    SemanticScene,
    Prepared,
    Accepted,
    CheckpointId,
    DeclineReason
  >;
  readonly queueLimit?: number;
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

interface ActivePlayback<Prepared extends PlayableCheckpoint>
  extends CheckpointPlaybackSession<Prepared> {
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly replayIndex?: number;
}

interface ReplayRecovery<
  Accepted,
  SemanticScene extends CertifiedChoreographySemanticScene,
  CheckpointId extends string,
> {
  readonly records: readonly Accepted[];
  readonly frontier: CertifiedChoreographyFrontier<SemanticScene>;
  readonly caption: string;
  readonly checkpointId?: CheckpointId;
}

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

/** Owns protocol-neutral request, provisional, post-paint, interrupt, and Replay state. */
export class CertifiedChoreographyStreamRuntime<
  Command,
  Request,
  StreamEvent,
  CheckpointEvent,
  SemanticScene extends CertifiedChoreographySemanticScene,
  Prepared extends CertifiedChoreographyPreparedCheckpoint<CheckpointEvent, SemanticScene>,
  Accepted,
  CheckpointId extends string,
  DeclineReason extends string,
> {
  private readonly player: CheckpointChoreographyPlayer;
  private readonly runStream: CertifiedChoreographyStreamRunner<Request, StreamEvent>;
  private readonly domain: CertifiedChoreographyStreamDomain<
    Command,
    Request,
    StreamEvent,
    CheckpointEvent,
    SemanticScene,
    Prepared,
    Accepted,
    CheckpointId,
    DeclineReason
  >;
  private readonly layout: ChoreographyLayout;
  private readonly queueLimit: number;
  private readonly listeners = new Set<() => void>();

  private tokenSequence = 0;
  private currentToken: RuntimeToken | null = null;
  private streamControl: StreamControl | null = null;
  private phase: CertifiedChoreographyRuntimePhase = "idle";
  private generation = 0;
  private attempt = 0;
  private sequence = 0;
  private committed: CertifiedChoreographyFrontier<SemanticScene>;
  private provisional: CertifiedChoreographyFrontier<SemanticScene>;
  private accepted: Accepted[] = [];
  private queue: Prepared[] = [];
  private active: ActivePlayback<Prepared> | null = null;
  private patchIds = new Set<string>();
  private interruptPending = false;
  private interruptionDeadline: ReturnType<
    typeof globalThis.setTimeout
  > | null = null;
  private replayRecovery: ReplayRecovery<Accepted, SemanticScene, CheckpointId> | null = null;
  private visibleCheckpointId: CheckpointId | undefined;
  private committedCaption = "";
  private visibleCaption = "";
  private narration: string;
  private rendererTrusted = true;
  private runtimeFailure: CertifiedChoreographyRuntimeFailure | undefined;
  private completion: CertifiedChoreographyRuntimeSnapshot<
    SemanticScene,
    Accepted,
    CheckpointId,
    DeclineReason
  >["completion"];
  private decline: CertifiedChoreographyRuntimeSnapshot<
    SemanticScene,
    Accepted,
    CheckpointId,
    DeclineReason
  >["decline"];
  private snapshot: CertifiedChoreographyRuntimeSnapshot<
    SemanticScene,
    Accepted,
    CheckpointId,
    DeclineReason
  >;
  private disposed = false;

  constructor(
    options: CertifiedChoreographyStreamRuntimeOptions<
      Command,
      Request,
      StreamEvent,
      CheckpointEvent,
      SemanticScene,
      Prepared,
      Accepted,
      CheckpointId,
      DeclineReason
    >,
  ) {
    if (options.layout !== "cinematic" && options.layout !== "compact") {
      throw new RangeError("layout must be cinematic or compact");
    }
    const queueLimit =
      options.queueLimit ?? options.domain.maxCheckpointsPerStream;
    if (
      !Number.isSafeInteger(queueLimit) ||
      queueLimit < 1 ||
      queueLimit > options.domain.maxCheckpointsPerStream
    ) {
      throw new RangeError(
        `queueLimit must be between 1 and ${options.domain.maxCheckpointsPerStream}`,
      );
    }
    this.player = new CheckpointChoreographyPlayer(options.renderer);
    this.runStream = options.runStream;
    this.domain = options.domain;
    this.layout = options.layout;
    this.queueLimit = queueLimit;
    this.committed = this.domain.emptyFrontier;
    this.provisional = this.domain.emptyFrontier;
    this.narration = this.domain.copy.ready;
    this.snapshot = this.buildSnapshot();
  }

  getSnapshot = (): CertifiedChoreographyRuntimeSnapshot<
    SemanticScene,
    Accepted,
    CheckpointId,
    DeclineReason
  > => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.assertUsable();
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(command: Command): number {
    this.assertUsable();
    if (this.isBusy()) {
      throw this.domain.runtimeError(
        "runtime_busy",
        this.domain.copy.busy,
      );
    }
    if (
      this.phase === "failed" &&
      (!this.rendererTrusted || this.runtimeFailure?.retryable === false)
    ) {
      throw this.domain.runtimeError(
        "runtime_reset_required",
        this.domain.copy.resetRequired,
      );
    }

    const generation = this.generation + 1;
    let request: Request;
    try {
      request = this.domain.createRequest(command, generation, this.committed);
    } catch (error) {
      throw this.domain.runtimeError(
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
    this.narration = this.visibleCaption || this.domain.copy.preparing;
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
        this.visibleCaption || this.domain.copy.stoppedBeforeCheckpoint;
      this.publish();
      return true;
    }
    this.phase = "interrupting";
    this.narration =
      this.visibleCaption || this.domain.copy.settlingInterruption;
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
        message: this.domain.copy.resetRendererFailed,
        retryable: false,
      });
      this.narration = this.runtimeFailure.message;
      this.publish();
      console.warn(this.domain.copy.resetFailureLog, error);
      return;
    }
    this.phase = "idle";
    this.generation = 0;
    this.attempt = 0;
    this.sequence = 0;
    this.committed = this.domain.emptyFrontier;
    this.provisional = this.domain.emptyFrontier;
    this.accepted = [];
    this.patchIds = new Set();
    this.interruptPending = false;
    this.replayRecovery = null;
    this.clearInterruptionDeadline();
    this.visibleCheckpointId = undefined;
    this.committedCaption = "";
    this.visibleCaption = "";
    this.narration = this.domain.copy.ready;
    this.rendererTrusted = true;
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();
  }

  async replayAccepted(): Promise<void> {
    this.assertUsable();
    if (this.isBusy()) {
      throw this.domain.runtimeError(
        "runtime_busy",
        this.domain.copy.busy,
      );
    }
    if (this.accepted.length === 0) return;

    const originalRecords = [...this.accepted];
    const originalFrontier = this.committed;
    const originalCaption = this.committedCaption;
    const originalCheckpoint = this.visibleCheckpointId;
    const recovery: ReplayRecovery<
      Accepted,
      SemanticScene,
      CheckpointId
    > = Object.freeze({
      records: originalRecords,
      frontier: originalFrontier,
      caption: originalCaption,
      ...(originalCheckpoint ? { checkpointId: originalCheckpoint } : {}),
    });
    let replay: CertifiedChoreographyReplay<Prepared, Accepted, SemanticScene>;
    try {
      replay = this.domain.preflightReplay(originalRecords);
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
    this.narration = this.domain.copy.replaying(replay.records.length);
    try {
      this.player.materializeEmpty(
        this.domain.emptyFrontier.scene,
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
    this.committed = this.domain.emptyFrontier;
    this.provisional = this.domain.emptyFrontier;
    this.visibleCaption = "";
    this.committedCaption = "";
    this.visibleCheckpointId = undefined;
    this.publish();

    for (const [index, prepared] of replay.checkpoints.entries()) {
      if (this.currentToken !== token) return;
      let transition: ActivePlayback<Prepared>;
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
          this.domain.copy.replaySettlementFailed,
        );
        return;
      }
      this.active = null;
      this.committed = prepared.target;
      this.provisional = prepared.target;
      this.committedCaption = this.domain.checkpointCaption(prepared);
      this.visibleCaption = this.committedCaption;
      this.visibleCheckpointId = this.domain.checkpointId(prepared);
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
    eventValue: StreamEvent,
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
    try {
      const event = this.domain.interpretEvent(eventValue);
      if (event.generation !== token.generation) {
        throw new Error("event generation does not match its request");
      }
      switch (event.kind) {
        case "started":
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
        case "repairing":
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
        case "checkpoint":
          this.acceptCheckpoint(token, event);
          return;
        case "completed":
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
        case "declined":
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
        case "failed":
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
      CertifiedChoreographyStreamEvent<CheckpointEvent, DeclineReason>,
      { kind: "checkpoint" }
    >,
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.sequence !== this.sequence + 1 ||
      event.baseRevision !== this.provisional.scene.revision ||
      event.semanticBaseRevision !== this.provisional.semanticScene.revision
    ) {
      throw new Error("checkpoint does not join its attempt and frontier");
    }
    if (this.patchIds.has(event.patchId)) {
      throw new Error("checkpoint patch ID was already admitted");
    }
    if (this.queue.length >= this.queueLimit) {
      throw new Error("checkpoint queue is full");
    }
    if (
      this.accepted.length + this.queue.length + (this.active ? 1 : 0) >=
      this.domain.maxRetainedCheckpoints
    ) {
      throw new Error("checkpoint history is full");
    }
    const prepared = this.domain.prepareCheckpoint(
      this.provisional,
      event.checkpoint,
      this.layout,
    );
    this.provisional = prepared.target;
    this.sequence = event.sequence;
    this.patchIds.add(event.patchId);
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
      this.failPlayback(token, this.domain.copy.queuedCheckpointLostBase);
      return;
    }
    let transition: ActivePlayback<Prepared>;
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
    prepared: Prepared,
    source: "stream" | "replay",
    replayIndex?: number,
  ): ActivePlayback<Prepared> {
    const session = this.player.start(prepared, () => {
      if (this.currentToken === token) {
        this.visibleCaption = this.domain.checkpointCaption(prepared);
        this.visibleCheckpointId = this.domain.checkpointId(prepared);
        this.narration = this.visibleCaption;
        this.publish();
      }
    });
    const active: ActivePlayback<Prepared> = {
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
    transition: ActivePlayback<Prepared>,
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
        this.domain.copy.checkpointSettlementFailed,
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
    transition: ActivePlayback<Prepared>,
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
        this.domain.copy.interruptionSettlementFailed,
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
      this.visibleCaption || this.domain.copy.stoppedBeforeCheckpoint;
    this.publish();
  }

  private finishReplayInterruption(
    transition: ActivePlayback<Prepared>,
    outcome: ChoreographyPlaybackOutcome,
    index: number,
  ): void {
    const recovery = this.replayRecovery;
    if (!recovery) {
      this.failPlayback(
        transition.token,
        this.domain.copy.replayRecoveryLost,
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
      this.committedCaption = this.domain.checkpointCaption(
        transition.prepared,
      );
      this.visibleCaption = this.committedCaption;
      this.visibleCheckpointId = this.domain.checkpointId(
        transition.prepared,
      );
      prefixLength += 1;
    } else if (!this.player.cancelledBeforePresented(transition, outcome)) {
      this.failReplayRecovery(
        this.domain.copy.replayInterruptionSettlementFailed,
      );
      return;
    }
    this.accepted = [...recovery.records.slice(0, prefixLength)];
    const lastAccepted = this.accepted.at(-1);
    this.sequence = lastAccepted
      ? this.domain.acceptedSequence(lastAccepted)
      : 0;
    this.active = null;
    this.currentToken = null;
    this.interruptPending = false;
    this.replayRecovery = null;
    this.phase = "interrupted";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.narration =
      this.visibleCaption || this.domain.copy.replayStoppedBeforeCheckpoint;
    this.publish();
  }

  private commitPrepared(
    prepared: Prepared,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    if (this.accepted.length >= this.domain.maxRetainedCheckpoints) {
      throw new Error("checkpoint history is full");
    }
    const accepted = this.domain.createAcceptedCheckpoint(prepared, outcome);
    this.accepted = [...this.accepted, accepted];
    this.committed = prepared.target;
    this.rendererTrusted = true;
    this.committedCaption = this.domain.checkpointCaption(prepared);
    this.visibleCaption = this.committedCaption;
    this.visibleCheckpointId = this.domain.checkpointId(prepared);
    this.narration = this.visibleCaption;
  }

  private preparedJoinsCommitted(
    prepared: Prepared,
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
        this.runtimeFailure?.message || this.domain.copy.streamStoppedSafely;
    }
    this.publish();
  }

  private failProtocol(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    this.clearInterruptionDeadline();
    const control = this.streamControl;
    const active = this.active;
    control?.controller.abort();
    if (active) this.player.close(active);

    // A protocol or transport failure invalidates the complete provisional
    // suffix.  Detach its token before cancellation can settle so no late
    // playback callback can promote an in-flight checkpoint.
    this.currentToken = null;
    this.streamControl = null;
    this.active = null;
    this.queue = [];
    this.sequence = 0;
    this.patchIds = new Set();
    this.interruptPending = false;
    this.replayRecovery = null;
    this.provisional = this.committed;

    let cancellationFailure: string | undefined;
    try {
      this.player.cancel(active?.playback);
    } catch (error) {
      cancellationFailure = message(error);
    }
    const restorationFailure = this.restoreRenderer(this.committed);
    this.rendererTrusted = restorationFailure === undefined;
    this.visibleCaption = this.committedCaption;
    const lastAccepted = this.accepted.at(-1);
    this.visibleCheckpointId = lastAccepted
      ? this.domain.acceptedCheckpointId(lastAccepted)
      : undefined;
    this.phase = "failed";
    this.runtimeFailure = Object.freeze({
      code: "invalid_stream_event",
      message: restorationFailure
        ? this.domain.copy.protocolRestorationFailed
        : this.domain.copy.protocolRestored,
      retryable: restorationFailure === undefined,
    });
    this.narration = this.runtimeFailure.message;
    console.warn(
      this.domain.copy.rejectedEventLog,
      [detail, cancellationFailure, restorationFailure]
        .filter((part): part is string => Boolean(part))
        .join("; "),
    );
    this.publish();
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
        ? this.domain.copy.playbackRestorationFailed
        : this.domain.copy.playbackRestored,
      retryable: restorationFailure === undefined,
    });
    this.narration = this.runtimeFailure.message;
    this.visibleCaption = this.committedCaption;
    const lastAccepted = this.accepted.at(-1);
    this.visibleCheckpointId = lastAccepted
      ? this.domain.acceptedCheckpointId(lastAccepted)
      : undefined;
    console.warn(
      this.domain.copy.playbackFailureLog,
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private failReplay(
    records: readonly Accepted[],
    frontier: CertifiedChoreographyFrontier<SemanticScene>,
    caption: string,
    checkpointId: CheckpointId | undefined,
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
        ? this.domain.copy.replayRestorationFailed
        : this.domain.copy.replayRestored,
      retryable: false,
    });
    this.narration = this.runtimeFailure.message;
    console.warn(
      this.domain.copy.replayFailureLog,
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private restoreRenderer(
    frontier: CertifiedChoreographyFrontier<SemanticScene>,
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

  private armInterruptionDeadline(active: ActivePlayback<Prepared>): void {
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
          this.domain.copy.replayInterruptionDeadlineFailed,
        );
      } else {
        this.failPlayback(
          active.token,
          this.domain.copy.interruptionDeadlineFailed,
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
      this.failProtocol(token, this.domain.copy.streamEndedWithoutTerminal);
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
          this.domain.copy.subscriberFailureLog,
          error,
        );
      }
    }
  }

  private buildSnapshot(): CertifiedChoreographyRuntimeSnapshot<
    SemanticScene,
    Accepted,
    CheckpointId,
    DeclineReason
  > {
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
      throw new Error(this.domain.copy.disposed);
    }
  }
}
