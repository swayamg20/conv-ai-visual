import type { SVGCanvasHandle } from "@/features/canvas/types";
import {
  createSceneState,
  type ChoreographyLayout,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";

import type {
  ChoreographyExecutorObserver,
  ChoreographyExecutorSignal,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import type {
  ChoreographySceneCheckpointEvent,
  ChoreographySceneStreamDeclinedEvent,
  ChoreographySceneStreamEvent,
  ChoreographySceneStreamRequest,
  ChoreographySceneStreamRunner,
  ChoreographySemanticSceneState,
} from "./choreography-model-stream";
import {
  EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS,
  appendChoreographyCheckpointSettled,
  appendChoreographyCueStarted,
  appendChoreographyFirstCuePresented,
  choreographyEvidenceTraceMatchesAccepted,
  choreographyReplayRecordMatches,
  createAcceptedChoreographyRevision,
  createChoreographyEvidenceTrace,
  createChoreographyFrontier,
  decodeChoreographyPlaybackOutcome,
  discardUnacceptedChoreographyEvidence,
  evaluateChoreographyPresentation,
  preflightChoreographyReplay,
  prepareChoreographyCheckpoint,
  type AcceptedChoreographyRevision,
  type ChoreographyEvidenceTraceEvent,
  type ChoreographyFrontier,
  type PreparedChoreographyCheckpoint,
} from "./choreography-playback";
import {
  beginPresentationInterrupt,
  beginPresentationReplay,
  beginPresentationRequest,
  createRuntimePresentationMetricsState,
  markFirstPresented,
  resetPresentationMetrics,
  settlePresentationMetrics,
  type RuntimePresentationMetricsSnapshot,
  type RuntimePresentationMetricsState,
} from "./runtime-presentation-metrics";

const MAX_PROMPT_LENGTH = 2_000;
const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });
const EMPTY_FRONTIER = createChoreographyFrontier({
  scene: EMPTY_SCENE,
  semanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  viewport: null,
  layout: null,
  certificateHeadSha256: null,
});

export type ChoreographyRuntimePhase =
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

export interface ChoreographyRuntimeFailure {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface ChoreographyRuntimeCompletion {
  readonly firstPatchMs: number;
  readonly totalMs: number;
  readonly repaired: boolean;
}

export interface ChoreographyRuntimeDetailSnapshot {
  readonly committedSemanticScene: ChoreographySemanticSceneState;
  readonly provisionalSemanticScene: ChoreographySemanticSceneState;
  readonly committedViewport: ViewportPoseV1 | null;
  readonly provisionalViewport: ViewportPoseV1 | null;
  readonly layout: ChoreographyLayout;
  readonly accepted: readonly AcceptedChoreographyRevision[];
  readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
  readonly committedCaption: string;
  readonly visibleCaption: string;
  /** False means the canvas is quarantined and must not be presented as truth. */
  readonly rendererTrusted: boolean;
  readonly commitFrontier?: AcceptedChoreographyRevision["presentation"];
}

/** Structurally matches SceneStreamRuntimeSnapshot without importing its owner. */
export interface ChoreographyRuntimeSnapshot {
  readonly phase: ChoreographyRuntimePhase;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly committedScene: SceneState;
  readonly provisionalScene: SceneState;
  readonly accepted: readonly [];
  readonly queuedPatchCount: number;
  readonly activeRevision?: number;
  readonly narration: string;
  readonly error?: ChoreographyRuntimeFailure;
  readonly completion?: ChoreographyRuntimeCompletion;
  readonly presentationMetrics?: RuntimePresentationMetricsSnapshot;
  readonly decline?: {
    readonly reasonCode: ChoreographySceneStreamDeclinedEvent["reasonCode"];
  };
  readonly choreography: ChoreographyRuntimeDetailSnapshot;
}

export type ChoreographySceneStreamRenderer = Pick<
  SVGCanvasHandle,
  | "playCheckpointChoreography"
  | "materializeScene"
  | "materializeViewport"
  | "cancelMotion"
  | "clear"
>;

export interface ChoreographyStreamRuntimeOptions {
  readonly renderer: ChoreographySceneStreamRenderer;
  readonly runStream: ChoreographySceneStreamRunner;
  readonly layout: ChoreographyLayout;
  readonly queueLimit?: number;
  readonly now?: () => number;
}

const INTERRUPTION_SETTLEMENT_TIMEOUT_MS = 2_000;

export type ChoreographyRuntimeErrorCode =
  "runtime_busy" | "runtime_reset_required" | "invalid_prompt";

export class ChoreographyRuntimeError extends Error {
  readonly code: ChoreographyRuntimeErrorCode;

  constructor(code: ChoreographyRuntimeErrorCode, message: string) {
    super(message);
    this.name = "ChoreographyRuntimeError";
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
  networkSettled: boolean;
}

interface EvidenceRef {
  readonly before: readonly ChoreographyEvidenceTraceEvent[];
  trace: readonly ChoreographyEvidenceTraceEvent[];
  settlement: "completed" | "cancelled_to_checkpoint" | null;
  invalid: Error | null;
  closed: boolean;
}

interface ActiveCheckpoint {
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly prepared: PreparedChoreographyCheckpoint;
  readonly playback: ChoreographyPlayback;
  readonly evidence: EvidenceRef;
  readonly replayIndex?: number;
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function failure(
  code: string,
  message: string,
  retryable: boolean,
): ChoreographyRuntimeFailure {
  return Object.freeze({ code, message, retryable });
}

function abortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function choreographyPlayback(value: unknown): ChoreographyPlayback {
  if (typeof value !== "object" || value === null) {
    throw new TypeError("Checkpoint renderer returned no playback handle");
  }
  const candidate = value as Partial<ChoreographyPlayback>;
  if (
    typeof candidate.cancel !== "function" ||
    typeof candidate.finished !== "object" ||
    candidate.finished === null ||
    typeof candidate.finished.then !== "function" ||
    typeof candidate.firstCuePresented !== "object" ||
    candidate.firstCuePresented === null ||
    typeof candidate.firstCuePresented.then !== "function"
  ) {
    throw new TypeError(
      "Checkpoint renderer returned an invalid playback handle",
    );
  }
  return candidate as ChoreographyPlayback;
}

/** Owns the V2 transactional lane; the legacy runtime only delegates to it. */
export class ChoreographyStreamRuntime {
  private readonly renderer: ChoreographySceneStreamRenderer;
  private readonly runStream: ChoreographySceneStreamRunner;
  private readonly layout: ChoreographyLayout;
  private readonly queueLimit: number;
  private readonly now: () => number;
  private readonly listeners = new Set<() => void>();

  private tokenSequence = 0;
  private currentToken: RuntimeToken | null = null;
  private streamControl: StreamControl | null = null;
  private pendingInterruptToken: RuntimeToken | null = null;
  private phase: ChoreographyRuntimePhase = "idle";
  private generation = 0;
  private attempt = 0;
  private sequence = 0;
  private committed: ChoreographyFrontier = EMPTY_FRONTIER;
  private provisional: ChoreographyFrontier = EMPTY_FRONTIER;
  private accepted: AcceptedChoreographyRevision[] = [];
  private queue: PreparedChoreographyCheckpoint[] = [];
  private active: ActiveCheckpoint | null = null;
  private patchIds = new Set<string>();
  private replayPrefixLength = 0;
  private replayPresented: AcceptedChoreographyRevision[] = [];
  private interruptionDeadline: ReturnType<
    typeof globalThis.setTimeout
  > | null = null;
  private evidence: readonly ChoreographyEvidenceTraceEvent[] =
    createChoreographyEvidenceTrace();
  private viewportInitialized = false;
  private initializedViewport: ViewportPoseV1 | null = null;
  private committedCaption = "";
  private visibleCaption = "";
  private rendererTrusted = true;
  private narration = "Ready for a visual explanation.";
  private runtimeFailure: ChoreographyRuntimeFailure | undefined;
  private completion: ChoreographyRuntimeCompletion | undefined;
  private decline:
    | {
        readonly reasonCode: ChoreographySceneStreamDeclinedEvent["reasonCode"];
      }
    | undefined;
  private presentationTiming: RuntimePresentationMetricsState =
    createRuntimePresentationMetricsState();
  private snapshot: ChoreographyRuntimeSnapshot;
  private disposed = false;

  constructor(options: ChoreographyStreamRuntimeOptions) {
    this.renderer = options.renderer;
    this.runStream = options.runStream;
    this.layout = options.layout;
    this.queueLimit =
      options.queueLimit ?? LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS;
    this.now = options.now ?? (() => globalThis.performance.now());
    if (
      !Number.isSafeInteger(this.queueLimit) ||
      this.queueLimit < 1 ||
      this.queueLimit > LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS
    ) {
      throw new RangeError(
        `queueLimit must be between 1 and ${LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS}`,
      );
    }
    if (this.layout !== "cinematic" && this.layout !== "compact") {
      throw new RangeError("layout must be cinematic or compact");
    }
    this.snapshot = this.buildSnapshot();
  }

  getSnapshot = (): ChoreographyRuntimeSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.assertUsable();
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(promptValue: string): number {
    this.assertUsable();
    if (this.phase === "failed" && this.runtimeFailure?.retryable === false) {
      throw new ChoreographyRuntimeError(
        "runtime_reset_required",
        "Reset the board or successfully replay its retained prefix before starting another generation",
      );
    }
    if (this.isBusy()) {
      throw new ChoreographyRuntimeError(
        "runtime_busy",
        "A choreography generation or replay is still active",
      );
    }
    if (typeof promptValue !== "string") {
      throw new ChoreographyRuntimeError(
        "invalid_prompt",
        "Prompt must be text",
      );
    }
    const prompt = promptValue.trim();
    if (prompt.length === 0 || prompt.length > MAX_PROMPT_LENGTH) {
      throw new ChoreographyRuntimeError(
        "invalid_prompt",
        `Prompt must contain between 1 and ${MAX_PROMPT_LENGTH} characters`,
      );
    }

    this.presentationTiming = beginPresentationRequest(this.now());
    this.invalidateToken(true);
    const generation = this.generation + 1;
    const token = this.createToken("stream", generation);
    const controller = new AbortController();
    this.currentToken = token;
    this.streamControl = {
      token,
      controller,
      terminal: undefined,
      networkSettled: false,
    };
    this.generation = generation;
    this.attempt = 0;
    this.sequence = 0;
    this.patchIds = new Set();
    this.queue = [];
    this.replayPrefixLength = 0;
    this.replayPresented = [];
    this.provisional = this.committed;
    this.phase = "connecting";
    this.narration = this.visibleCaption || "Preparing the live board…";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();

    const request: ChoreographySceneStreamRequest = Object.freeze({
      prompt,
      generation,
      baseScene: this.committed.scene,
      baseSemanticScene: this.committed.semanticScene,
    });
    void Promise.resolve()
      .then(() => {
        if (
          this.disposed ||
          this.currentToken !== token ||
          controller.signal.aborted
        ) {
          return;
        }
        return this.runStream(
          Object.freeze({
            request,
            signal: controller.signal,
            onEvent: (event: ChoreographySceneStreamEvent) =>
              this.acceptEvent(token, event),
          }),
        );
      })
      .then(() => this.onNetworkSettled(token))
      .catch((error: unknown) => this.onNetworkError(token, error));
    return generation;
  }

  interrupt(): boolean {
    this.assertUsable();
    if (!this.isBusy()) return false;
    if (this.pendingInterruptToken) return true;

    this.presentationTiming = beginPresentationInterrupt(
      this.presentationTiming,
      this.now(),
    );
    const token = this.currentToken;
    const active = this.active;
    const control = this.streamControl;
    this.streamControl = null;
    control?.controller.abort();
    this.queue = [];
    this.runtimeFailure = undefined;
    this.completion = undefined;

    if (active && token === active.token) {
      this.pendingInterruptToken = token;
      this.provisional = active.prepared.target;
      this.phase = "interrupting";
      this.narration =
        this.visibleCaption || "Settling the visible checkpoint…";
      this.publish();
      try {
        active.playback.cancel();
        if (
          this.currentToken === token &&
          this.pendingInterruptToken === token &&
          this.active === active
        ) {
          this.armInterruptionDeadline(active);
        }
      } catch (error) {
        this.failRenderer(
          token,
          error instanceof Error
            ? error.message
            : "Checkpoint cancellation failed",
        );
      }
      return true;
    }

    if (token?.kind === "replay") {
      this.truncateHistory(this.replayPrefixLength);
    }
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.provisional = this.committed;
    this.sequence = this.lastSequenceForGeneration(this.generation);
    this.phase = "interrupted";
    this.narration =
      this.visibleCaption ||
      "Generation interrupted before a checkpoint was presented.";
    this.publish();
    return true;
  }

  reset(): void {
    this.assertUsable();
    this.invalidateToken(true);
    const active = this.active;
    if (active) active.evidence.closed = true;
    this.active = null;
    this.queue = [];
    this.replayPresented = [];
    try {
      active?.playback.cancel();
    } catch {
      // Reset remains authoritative even if a broken renderer throws.
    }
    const cancellationFailure = this.cancelRendererMotion();
    let clearFailure: string | undefined;
    try {
      this.renderer.clear();
    } catch (error) {
      clearFailure = errorMessage(error);
    }
    if (cancellationFailure || clearFailure) {
      this.rendererTrusted = false;
      this.provisional = this.committed;
      this.evidence = this.buildEvidence(this.accepted);
      this.sequence = this.lastSequenceForGeneration(this.generation);
      this.phase = "failed";
      this.completion = undefined;
      this.runtimeFailure = failure(
        "renderer_failed",
        "The board could not be cleared safely. Retry reset before starting another lesson.",
        false,
      );
      this.narration = this.runtimeFailure.message;
      console.warn(
        "[LiveScene] Choreography reset cleanup failed:",
        [cancellationFailure, clearFailure].filter(Boolean).join("; "),
      );
      this.publish();
      return;
    }
    this.rendererTrusted = true;
    this.phase = "idle";
    this.generation = 0;
    this.attempt = 0;
    this.sequence = 0;
    this.committed = EMPTY_FRONTIER;
    this.provisional = EMPTY_FRONTIER;
    this.accepted = [];
    this.patchIds = new Set();
    this.replayPrefixLength = 0;
    this.replayPresented = [];
    this.evidence = createChoreographyEvidenceTrace();
    this.viewportInitialized = false;
    this.initializedViewport = null;
    this.committedCaption = "";
    this.visibleCaption = "";
    this.narration = "Ready for a visual explanation.";
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.presentationTiming = resetPresentationMetrics();
    this.publish();
  }

  async replayAccepted(): Promise<void> {
    this.assertUsable();
    if (this.isBusy()) {
      throw new ChoreographyRuntimeError(
        "runtime_busy",
        "A choreography generation or replay is still active",
      );
    }
    if (this.accepted.length === 0) return;

    const originalRecords = [...this.accepted];
    let replay: ReturnType<typeof preflightChoreographyReplay>;
    try {
      replay = preflightChoreographyReplay(originalRecords);
    } catch (error) {
      const prefix = this.longestValidPrefix(originalRecords);
      const restorationFailure = this.restoreReplayPrefix(
        originalRecords,
        prefix.records.length,
      );
      this.sequence = this.lastSequenceForGeneration(this.generation);
      this.phase = "failed";
      this.runtimeFailure = failure(
        "replay_integrity_failed",
        "Replay found a corrupt checkpoint. Reset the board or replay the retained prefix.",
        false,
      );
      this.completion = undefined;
      this.narration =
        "Replay integrity was lost before drawing. Reset or replay the retained prefix.";
      console.warn(
        "[LiveScene] Choreography replay preflight failed:",
        [errorMessage(error), restorationFailure].filter(Boolean).join("; "),
      );
      this.publish();
      return;
    }

    this.presentationTiming = beginPresentationReplay(
      this.presentationTiming,
      this.now(),
    );
    this.invalidateToken(true);
    const token = this.createToken("replay", this.generation);
    this.currentToken = token;
    this.replayPrefixLength = 0;
    this.replayPresented = [];
    this.evidence = createChoreographyEvidenceTrace();
    const first = replay.checkpoints[0];

    // The complete ledger is trusted before the first renderer mutation.
    try {
      this.renderer.cancelMotion();
      this.renderer.clear();
      this.renderer.materializeScene(first.base.scene);
      this.renderer.materializeViewport(first.base.viewport);
    } catch (error) {
      this.finishReplayFailure(
        token,
        error instanceof Error
          ? error.message
          : "Replay base materialization failed",
        0,
      );
      return;
    }
    this.rendererTrusted = true;
    this.viewportInitialized = true;
    this.initializedViewport = first.base.viewport;
    this.committed = first.base;
    this.provisional = first.base;
    this.committedCaption = "";
    this.visibleCaption = "";
    this.phase = "replaying";
    this.narration = `Replaying ${replay.records.length} accepted checkpoint${replay.records.length === 1 ? "" : "s"}.`;
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();

    for (const [index, prepared] of replay.checkpoints.entries()) {
      if (this.currentToken !== token) return;
      const transition = this.startPlayback(token, prepared, "replay", index);
      if (!transition) return;
      const outcome = await this.playbackOutcome(transition.playback);
      if (this.currentToken !== token || this.active !== transition) return;
      this.clearInterruptionDeadline();
      if (this.pendingInterruptToken === token) {
        this.finishInterruption(transition, outcome);
        return;
      }

      transition.evidence.closed = true;
      const evaluation = evaluateChoreographyPresentation(prepared, outcome);
      if (
        !this.signalsMatchOutcome(transition, outcome) ||
        evaluation.kind !== "presented" ||
        evaluation.receipt.settlement !== "completed" ||
        !choreographyReplayRecordMatches(replay.records[index], prepared)
      ) {
        this.finishReplayFailure(
          token,
          "A replayed checkpoint did not reproduce its certified presentation boundary",
          index,
        );
        return;
      }
      try {
        // Minting a fresh result proves the live terminal outcome; the stored
        // record remains byte-stable, including an original cancel receipt.
        const fresh = createAcceptedChoreographyRevision(prepared, evaluation);
        this.evidence = appendChoreographyCheckpointSettled(
          transition.evidence.trace,
          prepared,
          fresh,
        );
        this.replayPresented = [...this.replayPresented, fresh];
      } catch (error) {
        this.finishReplayFailure(
          token,
          error instanceof Error ? error.message : "Replay evidence failed",
          index,
        );
        return;
      }
      this.active = null;
      this.rendererTrusted = true;
      this.committed = prepared.target;
      this.provisional = prepared.target;
      this.committedCaption = prepared.event.patch.narration;
      this.visibleCaption = this.committedCaption;
      this.narration = this.visibleCaption;
      this.sequence = prepared.event.sequence;
      this.replayPrefixLength = index + 1;
      this.publish();
    }

    if (this.currentToken !== token) return;
    if (
      this.replayPresented.length !== replay.records.length ||
      !choreographyEvidenceTraceMatchesAccepted(
        this.evidence,
        this.replayPresented,
      )
    ) {
      this.finishReplayFailure(
        token,
        "Replay evidence does not exactly match its accepted ledger",
        replay.records.length,
      );
      return;
    }
    this.accepted = originalRecords;
    this.currentToken = null;
    this.replayPrefixLength = 0;
    this.replayPresented = [];
    this.phase = "completed";
    this.narration = this.visibleCaption;
    this.publish();
  }

  dispose(): void {
    if (this.disposed) return;
    this.invalidateToken(true);
    const active = this.active;
    if (active) active.evidence.closed = true;
    this.active = null;
    this.queue = [];
    this.replayPresented = [];
    try {
      active?.playback.cancel();
    } catch {
      // Disposal intentionally ignores renderer teardown failures.
    }
    const cancellationFailure = this.cancelRendererMotion();
    this.listeners.clear();
    this.disposed = true;
    if (cancellationFailure) {
      console.warn(
        "[LiveScene] Choreography disposal cancellation failed:",
        cancellationFailure,
      );
    }
  }

  private acceptEvent(
    token: RuntimeToken,
    event: ChoreographySceneStreamEvent,
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
      this.failProtocol(token, "Event generation does not match its request");
      return;
    }
    try {
      switch (event.type) {
        case "scene_stream_started":
          this.acceptStarted(token, event.attempt, event.baseRevision);
          break;
        case "scene_stream_repairing":
          this.acceptRepairing(token, event);
          break;
        case "choreography_scene_checkpoint":
          this.acceptCheckpoint(token, event);
          break;
        case "scene_stream_completed":
          this.acceptCompleted(token, event);
          break;
        case "choreography_scene_stream_declined":
          this.acceptDeclined(token, event);
          break;
        case "scene_stream_failed":
          this.acceptFailed(token, event);
          break;
        default:
          throw new Error("Unsupported choreography stream event");
      }
    } catch (error) {
      this.failProtocol(
        token,
        error instanceof Error ? error.message : "Event validation failed",
      );
    }
  }

  private acceptStarted(
    token: RuntimeToken,
    attempt: number,
    baseRevision: number,
  ): void {
    if (
      this.attempt !== 0 ||
      attempt !== 1 ||
      baseRevision !== this.provisional.scene.revision ||
      !this.frontierCoherent(this.provisional)
    ) {
      throw new Error("Started event does not match the generation boundary");
    }
    this.attempt = 1;
    this.phase = "streaming";
    this.narration =
      this.visibleCaption || "The model is authoring the first checkpoint…";
    this.publishIfCurrent(token);
  }

  private acceptRepairing(
    token: RuntimeToken,
    event: Extract<
      ChoreographySceneStreamEvent,
      { type: "scene_stream_repairing" }
    >,
  ): void {
    if (
      this.attempt !== 1 ||
      event.fromAttempt !== 1 ||
      event.toAttempt !== 2 ||
      this.sequence !== 0 ||
      this.active !== null ||
      this.queue.length !== 0 ||
      this.patchIds.size !== 0 ||
      !same(this.provisional, this.committed) ||
      event.lastAcceptedRevision !== this.committed.scene.revision ||
      !this.frontierCoherent(this.provisional)
    ) {
      throw new Error("Repair event does not match the provisional frontier");
    }
    this.attempt = 2;
    this.phase = "repairing";
    this.narration = this.visibleCaption || event.message;
    this.publishIfCurrent(token);
  }

  private acceptCheckpoint(
    token: RuntimeToken,
    event: ChoreographySceneCheckpointEvent,
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.sequence !== this.sequence + 1 ||
      event.baseRevision !== this.provisional.scene.revision ||
      event.semantic.semanticBaseRevision !==
        this.provisional.semanticScene.revision
    ) {
      throw new Error(
        "Checkpoint does not match the attempt, sequence, or provisional frontier",
      );
    }
    if (this.patchIds.has(event.patch.patchId)) {
      throw new Error("Checkpoint patch ID was already accepted");
    }
    if (this.queue.length >= this.queueLimit) {
      throw new Error(`Checkpoint queue exceeds ${this.queueLimit} entries`);
    }
    if (
      this.accepted.length + this.outstandingCount() >=
      LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS
    ) {
      throw new Error(
        `Checkpoint ledger cannot exceed ${LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS} entries`,
      );
    }

    const prepared = prepareChoreographyCheckpoint(
      this.provisional,
      event,
      this.layout,
    );
    this.provisional = prepared.target;
    this.sequence = prepared.event.sequence;
    this.patchIds.add(prepared.event.patch.patchId);
    this.queue.push(prepared);
    this.phase = "streaming";
    this.narration =
      this.visibleCaption || "The model is authoring the first checkpoint…";
    this.publishIfCurrent(token);
    this.pump(token);
  }

  private acceptCompleted(
    token: RuntimeToken,
    event: Extract<
      ChoreographySceneStreamEvent,
      { type: "scene_stream_completed" }
    >,
  ): void {
    if (
      this.attempt === 0 ||
      event.finalRevision !== this.provisional.scene.revision ||
      event.patchCount !== this.sequence ||
      event.repaired !== (this.attempt === 2) ||
      !this.frontierCoherent(this.provisional)
    ) {
      throw new Error("Completion event does not match the checkpoint ledger");
    }
    const control = this.requireControl(token);
    control.terminal = "completed";
    this.completion = Object.freeze({
      firstPatchMs: event.firstPatchMs,
      totalMs: event.totalMs,
      repaired: event.repaired,
    });
    this.settlePhase();
    this.publishIfCurrent(token);
  }

  private acceptDeclined(
    token: RuntimeToken,
    event: ChoreographySceneStreamDeclinedEvent,
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.finalRevision !== this.provisional.scene.revision ||
      this.sequence !== 0 ||
      this.active !== null ||
      this.queue.length !== 0 ||
      !this.frontierCoherent(this.provisional)
    ) {
      throw new Error("Decline does not match an unchanged generation");
    }
    const control = this.requireControl(token);
    control.terminal = "declined";
    this.phase = "declined";
    this.narration = event.message;
    this.runtimeFailure = undefined;
    this.completion = undefined;
    this.decline = Object.freeze({ reasonCode: event.reasonCode });
    this.publishIfCurrent(token);
  }

  private acceptFailed(
    token: RuntimeToken,
    event: Extract<
      ChoreographySceneStreamEvent,
      { type: "scene_stream_failed" }
    >,
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.lastAcceptedRevision !== this.provisional.scene.revision ||
      !this.frontierCoherent(this.provisional)
    ) {
      throw new Error("Failure event does not match the provisional frontier");
    }
    const control = this.requireControl(token);
    control.terminal = "failed";
    control.controller.abort();
    this.runtimeFailure = failure(event.code, event.message, event.retryable);
    this.narration = event.message;
    this.settlePhase();
    this.publishIfCurrent(token);
  }

  private pump(token: RuntimeToken): void {
    if (this.currentToken !== token || this.active || this.queue.length === 0) {
      return;
    }
    const prepared = this.queue.shift();
    if (!prepared) return;
    if (!this.preparedJoinsCommitted(prepared)) {
      this.failRenderer(
        token,
        "Queued checkpoint no longer joins the committed frontier",
      );
      return;
    }
    const transition = this.startPlayback(token, prepared, "stream");
    if (!transition) return;
    void this.playbackOutcome(transition.playback).then((outcome) =>
      this.onPlaybackFinished(transition, outcome),
    );
  }

  private startPlayback(
    token: RuntimeToken,
    prepared: PreparedChoreographyCheckpoint,
    source: "stream" | "replay",
    replayIndex?: number,
  ): ActiveCheckpoint | null {
    const evidence: EvidenceRef = {
      before: this.evidence,
      trace: this.evidence,
      settlement: null,
      invalid: null,
      closed: false,
    };
    let transition: ActiveCheckpoint | null = null;
    const observer: ChoreographyExecutorObserver = (signal) => {
      if (evidence.closed) return;
      try {
        this.acceptExecutorSignal(prepared, evidence, signal);
        if (
          transition &&
          this.currentToken === token &&
          this.active === transition
        ) {
          if (signal.type === "firstCuePresented") {
            this.presentationTiming = markFirstPresented(
              this.presentationTiming,
              this.now(),
            );
          }
          this.evidence = evidence.trace;
          this.publish();
        }
      } catch (error) {
        evidence.invalid =
          error instanceof Error ? error : new Error(String(error));
        throw evidence.invalid;
      }
    };

    try {
      this.bootstrapViewport(prepared);
      const playback = choreographyPlayback(
        this.renderer.playCheckpointChoreography(prepared.plan, observer),
      );
      transition = {
        token,
        source,
        prepared,
        playback,
        evidence,
        ...(replayIndex === undefined ? {} : { replayIndex }),
      };
      this.active = transition;
      this.evidence = evidence.trace;
      this.publishIfCurrent(token);
      return transition;
    } catch (error) {
      this.evidence = evidence.before;
      if (source === "replay") {
        this.finishReplayFailure(
          token,
          error instanceof Error ? error.message : "Replay could not start",
          replayIndex ?? 0,
        );
      } else {
        this.failRenderer(
          token,
          error instanceof Error
            ? error.message
            : "Checkpoint playback could not start",
        );
      }
      return null;
    }
  }

  private bootstrapViewport(prepared: PreparedChoreographyCheckpoint): void {
    if (!prepared.bootstrappedViewport) return;
    if (!this.viewportInitialized) {
      this.renderer.materializeViewport(prepared.base.viewport);
      this.viewportInitialized = true;
      this.initializedViewport = prepared.base.viewport;
      return;
    }
    if (!same(this.initializedViewport, prepared.base.viewport)) {
      throw new Error("The initial certified viewport changed after bootstrap");
    }
  }

  private acceptExecutorSignal(
    prepared: PreparedChoreographyCheckpoint,
    evidence: EvidenceRef,
    signal: ChoreographyExecutorSignal,
  ): void {
    if (evidence.invalid) throw evidence.invalid;
    switch (signal.type) {
      case "cueStarted":
        if (evidence.settlement) {
          throw new Error("A settled checkpoint cannot start another cue");
        }
        evidence.trace = appendChoreographyCueStarted(
          evidence.trace,
          prepared,
          signal.cue,
        );
        return;
      case "firstCuePresented":
        if (evidence.settlement) {
          throw new Error("A settled checkpoint cannot present another cue");
        }
        evidence.trace = appendChoreographyFirstCuePresented(
          evidence.trace,
          prepared,
        );
        return;
      case "checkpointSettled":
        if (
          signal.settlement !== "completed" &&
          signal.settlement !== "cancelled_to_checkpoint"
        ) {
          throw new Error(
            "Checkpoint settlement is outside the closed vocabulary",
          );
        }
        if (evidence.settlement) {
          throw new Error("Checkpoint settlement signal was emitted twice");
        }
        evidence.settlement = signal.settlement;
        return;
      default:
        throw new Error("Executor signal is outside the closed vocabulary");
    }
  }

  private onPlaybackFinished(
    transition: ActiveCheckpoint,
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
    if (this.pendingInterruptToken === transition.token) {
      this.finishInterruption(transition, outcome);
      return;
    }
    transition.evidence.closed = true;
    const evaluation = evaluateChoreographyPresentation(
      transition.prepared,
      outcome,
    );
    if (
      !this.signalsMatchOutcome(transition, outcome) ||
      evaluation.kind !== "presented" ||
      evaluation.receipt.settlement !== "completed"
    ) {
      this.discardEvidence(transition, outcome);
      this.failRenderer(
        transition.token,
        "Checkpoint returned an invalid terminal presentation receipt",
      );
      return;
    }

    try {
      const accepted = createAcceptedChoreographyRevision(
        transition.prepared,
        evaluation,
      );
      if (this.accepted.length >= LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS) {
        throw new Error("Checkpoint ledger is full");
      }
      this.evidence = appendChoreographyCheckpointSettled(
        transition.evidence.trace,
        transition.prepared,
        accepted,
      );
      this.accepted = [...this.accepted, accepted];
    } catch (error) {
      this.failRenderer(
        transition.token,
        error instanceof Error ? error.message : "Acceptance failed",
      );
      return;
    }

    this.active = null;
    this.rendererTrusted = true;
    this.committed = transition.prepared.target;
    this.committedCaption = transition.prepared.event.patch.narration;
    this.visibleCaption = this.committedCaption;
    this.narration =
      this.streamControl?.terminal === "failed" && this.runtimeFailure
        ? this.runtimeFailure.message
        : this.visibleCaption;
    this.publish();
    this.pump(transition.token);
    this.settlePhase();
    this.publishIfCurrent(transition.token);
  }

  private finishInterruption(
    transition: ActiveCheckpoint,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      this.pendingInterruptToken !== transition.token
    ) {
      return;
    }
    this.clearInterruptionDeadline();
    transition.evidence.closed = true;
    const evaluation = evaluateChoreographyPresentation(
      transition.prepared,
      outcome,
    );
    let invalid =
      !this.signalsMatchOutcome(transition, outcome) ||
      evaluation.kind === "invalid";
    let restorationFailure: string | undefined;

    if (!invalid && evaluation.kind === "presented") {
      try {
        const fresh = createAcceptedChoreographyRevision(
          transition.prepared,
          evaluation,
        );
        const stored =
          transition.source === "replay"
            ? this.accepted[transition.replayIndex ?? -1]
            : undefined;
        if (
          transition.source === "replay" &&
          (!stored ||
            !choreographyReplayRecordMatches(stored, transition.prepared))
        ) {
          throw new Error("Interrupted replay record no longer exact-matches");
        }
        this.evidence = appendChoreographyCheckpointSettled(
          transition.evidence.trace,
          transition.prepared,
          fresh,
        );
        if (transition.source === "stream") {
          if (
            this.accepted.length >= LIVE_CHOREOGRAPHY_MAX_ACCEPTED_CHECKPOINTS
          ) {
            throw new Error("Checkpoint ledger is full");
          }
          this.accepted = [...this.accepted, fresh];
        } else {
          const replayIndex = transition.replayIndex ?? -1;
          this.replayPresented = [
            ...this.replayPresented.slice(0, replayIndex),
            fresh,
          ];
          const replayEvidence = this.evidence;
          this.replayPrefixLength = replayIndex + 1;
          this.truncateHistory(this.replayPrefixLength);
          this.evidence = replayEvidence;
        }
        this.committed = transition.prepared.target;
        this.committedCaption = transition.prepared.event.patch.narration;
        this.visibleCaption = this.committedCaption;
        this.rendererTrusted = true;
      } catch (error) {
        invalid = true;
        console.warn("[LiveScene] Interrupted checkpoint failed:", error);
      }
    } else if (!invalid) {
      this.discardEvidence(transition, outcome);
      if (transition.source === "replay") {
        const replayIndex = transition.replayIndex ?? 0;
        const replayEvidence = this.evidence;
        this.replayPresented = this.replayPresented.slice(0, replayIndex);
        this.truncateHistory(replayIndex);
        this.evidence = replayEvidence;
      }
    }

    if (invalid) {
      if (transition.source === "replay") {
        const replayIndex = transition.replayIndex ?? 0;
        this.replayPresented = this.replayPresented.slice(0, replayIndex);
        restorationFailure = this.restoreReplayPrefix(
          this.accepted,
          replayIndex,
        );
        this.evidence = this.buildEvidence(this.replayPresented);
      } else {
        this.evidence = this.buildEvidence(this.accepted);
        restorationFailure = this.reconcileRenderer(this.committed);
        this.rendererTrusted = restorationFailure === undefined;
      }
    }
    this.active = null;
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.replayPrefixLength = 0;
    this.replayPresented = [];
    this.queue = [];
    this.provisional = this.committed;
    this.sequence = this.lastSequenceForGeneration(this.generation);
    this.completion = undefined;

    if (invalid) {
      this.phase = "failed";
      this.runtimeFailure = failure(
        "renderer_failed",
        "The visible checkpoint no longer has a trustworthy frontier. Reset the board or replay the retained prefix.",
        false,
      );
      this.narration =
        "Checkpoint presentation integrity was lost. Reset or replay the retained prefix.";
      console.warn(
        "[LiveScene] Interrupted checkpoint integrity failed:",
        restorationFailure ?? "terminal presentation evidence was invalid",
      );
    } else {
      this.phase = "interrupted";
      this.runtimeFailure = undefined;
      this.narration =
        this.visibleCaption ||
        "Generation interrupted before a checkpoint was presented.";
    }
    this.publish();
  }

  private signalsMatchOutcome(
    transition: ActiveCheckpoint,
    outcome: ChoreographyPlaybackOutcome,
  ): boolean {
    if (transition.evidence.invalid) return false;
    const suffix = transition.evidence.trace.slice(
      transition.evidence.before.length,
    );
    const firstCount = suffix.filter(
      (event) => event.type === "firstCuePresented",
    ).length;
    if (outcome.firstCuePresented !== (firstCount === 1)) return false;
    if (
      outcome.status === "completed" ||
      outcome.status === "cancelled_to_checkpoint"
    ) {
      return (
        outcome.firstCuePresented &&
        transition.evidence.settlement === outcome.status
      );
    }
    return transition.evidence.settlement === null;
  }

  private discardEvidence(
    transition: ActiveCheckpoint,
    outcome: ChoreographyPlaybackOutcome,
  ): void {
    try {
      this.evidence = discardUnacceptedChoreographyEvidence(
        transition.evidence.trace,
        transition.prepared,
        outcome,
      );
    } catch {
      this.evidence = transition.evidence.before;
    }
  }

  private preparedJoinsCommitted(
    prepared: PreparedChoreographyCheckpoint,
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

  private frontierCoherent(frontier: ChoreographyFrontier): boolean {
    try {
      createChoreographyFrontier(frontier);
      return true;
    } catch {
      return false;
    }
  }

  private outstandingCount(): number {
    return (this.active ? 1 : 0) + this.queue.length;
  }

  private truncateHistory(prefixLength: number): void {
    const length = Math.max(0, Math.min(prefixLength, this.accepted.length));
    this.accepted = this.accepted.slice(0, length);
    const last = this.accepted.at(-1);
    this.committed = last
      ? createChoreographyFrontier({
          scene: last.scene,
          semanticScene: last.semanticScene,
          viewport: last.viewport,
          layout: last.layout,
          certificateHeadSha256: last.presentation.certificateSha256,
        })
      : EMPTY_FRONTIER;
    this.provisional = this.committed;
    this.committedCaption = last?.event.patch.narration ?? "";
    this.visibleCaption = this.committedCaption;
    this.evidence = this.buildEvidence(this.accepted);
  }

  private buildEvidence(
    records: readonly AcceptedChoreographyRevision[],
  ): readonly ChoreographyEvidenceTraceEvent[] {
    if (records.length === 0) return createChoreographyEvidenceTrace();
    const replay = preflightChoreographyReplay(records);
    let trace: readonly ChoreographyEvidenceTraceEvent[] =
      createChoreographyEvidenceTrace();
    replay.checkpoints.forEach((prepared, index) => {
      for (const cue of prepared.plan.choreographyPlan.phase.cues) {
        trace = appendChoreographyCueStarted(trace, prepared, cue.cue);
      }
      trace = appendChoreographyFirstCuePresented(trace, prepared);
      trace = appendChoreographyCheckpointSettled(
        trace,
        prepared,
        replay.records[index],
      );
    });
    return trace;
  }

  private longestValidPrefix(
    records: readonly AcceptedChoreographyRevision[],
  ): ReturnType<typeof preflightChoreographyReplay> {
    let prefix = preflightChoreographyReplay([]);
    for (let length = 1; length <= records.length; length += 1) {
      try {
        prefix = preflightChoreographyReplay(records.slice(0, length));
      } catch {
        break;
      }
    }
    return prefix;
  }

  private lastSequenceForGeneration(generation: number): number {
    for (let index = this.accepted.length - 1; index >= 0; index -= 1) {
      const event = this.accepted[index].event;
      if (event.generation === generation) return event.sequence;
    }
    return 0;
  }

  private playbackOutcome(
    playback: ChoreographyPlayback,
  ): Promise<ChoreographyPlaybackOutcome> {
    return Promise.resolve(playback.finished as unknown)
      .then((outcome) => decodeChoreographyPlaybackOutcome(outcome))
      .catch((error: unknown) => ({
        status: "failed" as const,
        firstCuePresented: false,
        error: this.playbackFailureMessage(error),
      }));
  }

  private playbackFailureMessage(error: unknown): string {
    const message =
      error instanceof Error
        ? error.message
        : "Checkpoint playback rejected or returned an invalid outcome";
    return [...(message.trim() || "Checkpoint playback failed")]
      .slice(0, 512)
      .join("");
  }

  private cancelRendererMotion(): string | undefined {
    try {
      this.renderer.cancelMotion();
      return undefined;
    } catch (error) {
      return errorMessage(error);
    }
  }

  private armInterruptionDeadline(transition: ActiveCheckpoint): void {
    this.clearInterruptionDeadline();
    this.interruptionDeadline = globalThis.setTimeout(() => {
      this.interruptionDeadline = null;
      if (
        this.currentToken !== transition.token ||
        this.pendingInterruptToken !== transition.token ||
        this.active !== transition
      ) {
        return;
      }
      if (transition.source === "replay") {
        this.finishReplayFailure(
          transition.token,
          "Checkpoint cancellation did not settle before its deadline",
          transition.replayIndex ?? 0,
        );
      } else {
        this.failRenderer(
          transition.token,
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

  private reconcileRenderer(
    frontier: ChoreographyFrontier,
  ): string | undefined {
    const cancellationFailure = this.cancelRendererMotion();
    if (cancellationFailure) {
      return `renderer cancellation failed: ${cancellationFailure}`;
    }
    try {
      this.renderer.clear();
      if (frontier.scene.revision > 0 || frontier.scene.nodes.length > 0) {
        this.renderer.materializeScene(frontier.scene);
      }
      if (frontier.viewport) {
        this.renderer.materializeViewport(frontier.viewport);
      }
      return undefined;
    } catch (error) {
      return `renderer materialization failed: ${errorMessage(error)}`;
    }
  }

  /** Keep the logical prefix coherent, then prove or quarantine its canvas. */
  private restoreReplayPrefix(
    records: readonly AcceptedChoreographyRevision[],
    prefixLength: number,
  ): string | undefined {
    let retained: readonly AcceptedChoreographyRevision[];
    let validationFailure: string | undefined;
    try {
      retained = preflightChoreographyReplay(
        records.slice(0, prefixLength),
      ).records;
    } catch (error) {
      retained = [];
      validationFailure = `retained prefix is invalid: ${errorMessage(error)}`;
    }

    this.accepted = [...retained];
    this.truncateHistory(retained.length);
    const last = retained.at(-1);
    this.viewportInitialized = last !== undefined;
    this.initializedViewport = last?.viewport ?? null;
    const restorationFailure = this.reconcileRenderer(this.committed);
    this.rendererTrusted = restorationFailure === undefined;
    return (
      [validationFailure, restorationFailure].filter(Boolean).join("; ") ||
      undefined
    );
  }

  private failProtocol(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    const control = this.streamControl;
    if (!control || control.token !== token || control.terminal) return;
    control.terminal = "failed";
    control.controller.abort();
    this.runtimeFailure = failure(
      "invalid_stream_event",
      "The visual stream stopped. The last presented checkpoint is safe.",
      true,
    );
    this.narration = this.runtimeFailure.message;
    this.settlePhase();
    console.warn("[LiveScene] Rejected choreography event:", detail);
    this.publish();
  }

  private failRenderer(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    this.clearInterruptionDeadline();
    const control = this.streamControl;
    this.streamControl = null;
    control?.controller.abort();
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.replayPrefixLength = 0;
    if (this.active) this.active.evidence.closed = true;
    this.active = null;
    this.queue = [];
    this.provisional = this.committed;
    this.evidence = this.buildEvidence(this.accepted);
    this.sequence = this.lastSequenceForGeneration(this.generation);
    const restorationFailure = this.reconcileRenderer(this.committed);
    this.rendererTrusted = restorationFailure === undefined;
    this.phase = "failed";
    this.completion = undefined;
    this.runtimeFailure = failure(
      "renderer_failed",
      "The visible checkpoint no longer has a trustworthy frontier. Reset the board or replay the retained prefix.",
      false,
    );
    this.narration =
      "Checkpoint presentation integrity was lost. Reset or replay the retained prefix.";
    console.warn(
      "[LiveScene] Choreography renderer failure:",
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private finishReplayFailure(
    token: RuntimeToken,
    detail: string,
    acceptedPrefixLength: number,
  ): void {
    if (this.currentToken !== token) return;
    this.clearInterruptionDeadline();
    const active = this.active;
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.replayPrefixLength = 0;
    if (active) active.evidence.closed = true;
    this.active = null;
    this.queue = [];
    try {
      active?.playback.cancel();
    } catch {
      // The replay is already quarantined.
    }
    const restorationFailure = this.restoreReplayPrefix(
      this.accepted,
      acceptedPrefixLength,
    );
    try {
      this.evidence = this.buildEvidence(
        this.replayPresented.slice(0, this.accepted.length),
      );
    } catch {
      this.evidence = createChoreographyEvidenceTrace();
    }
    this.replayPresented = [];
    this.sequence = this.lastSequenceForGeneration(this.generation);
    this.phase = "failed";
    this.completion = undefined;
    this.runtimeFailure = failure(
      "replay_integrity_failed",
      "Replay could not establish a trustworthy frontier. Reset the board or replay the retained prefix.",
      false,
    );
    this.narration =
      "Replay integrity was lost. Reset or replay the retained prefix.";
    console.warn(
      "[LiveScene] Choreography replay failure:",
      restorationFailure ? `${detail}; ${restorationFailure}` : detail,
    );
    this.publish();
  }

  private onNetworkSettled(token: RuntimeToken): void {
    const control = this.streamControl;
    if (this.currentToken !== token || !control || control.token !== token) {
      return;
    }
    control.networkSettled = true;
    if (!control.terminal) {
      this.failProtocol(token, "Stream ended without a terminal event");
    }
  }

  private onNetworkError(token: RuntimeToken, error: unknown): void {
    const control = this.streamControl;
    if (this.currentToken !== token || !control || control.token !== token) {
      return;
    }
    control.networkSettled = true;
    if (
      control.terminal ||
      (control.controller.signal.aborted && abortError(error))
    ) {
      return;
    }
    control.terminal = "failed";
    control.controller.abort();
    this.runtimeFailure = failure(
      "stream_unavailable",
      "The visual stream stopped. The last presented checkpoint is safe.",
      true,
    );
    this.narration = this.runtimeFailure.message;
    this.settlePhase();
    this.publish();
  }

  private settlePhase(): void {
    const terminal = this.streamControl?.terminal;
    if (terminal === "completed") {
      this.phase =
        this.active || this.queue.length > 0 ? "completing" : "completed";
      if (this.phase === "completed") {
        this.narration = this.visibleCaption;
      }
    } else if (terminal === "failed") {
      this.phase =
        this.active || this.queue.length > 0 ? "completing" : "failed";
      if (this.phase === "failed" && this.runtimeFailure) {
        this.narration = this.runtimeFailure.message;
      }
    }
  }

  private requireControl(token: RuntimeToken): StreamControl {
    const control = this.streamControl;
    if (!control || control.token !== token) {
      throw new Error("Stream control token is stale");
    }
    return control;
  }

  private createToken(
    kind: RuntimeToken["kind"],
    generation: number,
  ): RuntimeToken {
    return Object.freeze({ id: ++this.tokenSequence, kind, generation });
  }

  private invalidateToken(abort: boolean): void {
    this.clearInterruptionDeadline();
    if (abort) this.streamControl?.controller.abort();
    this.currentToken = null;
    this.streamControl = null;
    this.pendingInterruptToken = null;
    this.replayPrefixLength = 0;
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

  private publishIfCurrent(token: RuntimeToken): void {
    if (this.currentToken === token) this.publish();
  }

  private publish(): void {
    if (
      this.phase === "completed" ||
      this.phase === "declined" ||
      this.phase === "failed" ||
      this.phase === "interrupted"
    ) {
      this.presentationTiming = settlePresentationMetrics(
        this.presentationTiming,
        this.now(),
      );
    }
    this.snapshot = this.buildSnapshot();
    for (const listener of [...this.listeners]) {
      try {
        listener();
      } catch (error) {
        console.error("[LiveScene] Snapshot subscriber failed:", error);
      }
    }
  }

  private buildSnapshot(): ChoreographyRuntimeSnapshot {
    const visibleAccepted =
      this.currentToken?.kind === "replay"
        ? this.accepted.slice(0, this.replayPrefixLength)
        : this.accepted;
    const choreography: ChoreographyRuntimeDetailSnapshot = Object.freeze({
      committedSemanticScene: this.committed.semanticScene,
      provisionalSemanticScene: this.provisional.semanticScene,
      committedViewport: this.committed.viewport,
      provisionalViewport: this.provisional.viewport,
      layout: this.layout,
      accepted: Object.freeze([...visibleAccepted]),
      evidence: this.evidence,
      committedCaption: this.committedCaption,
      visibleCaption: this.visibleCaption,
      rendererTrusted: this.rendererTrusted,
      ...(visibleAccepted.length > 0
        ? { commitFrontier: visibleAccepted.at(-1)!.presentation }
        : {}),
    });
    return Object.freeze({
      phase: this.phase,
      generation: this.generation,
      attempt: this.attempt,
      sequence: this.sequence,
      committedScene: this.committed.scene,
      provisionalScene: this.provisional.scene,
      accepted: Object.freeze([]) as readonly [],
      queuedPatchCount: this.queue.length,
      ...(this.active
        ? { activeRevision: this.active.prepared.target.scene.revision }
        : {}),
      narration: this.narration,
      ...(this.runtimeFailure ? { error: this.runtimeFailure } : {}),
      ...(this.completion ? { completion: this.completion } : {}),
      ...(this.presentationTiming.snapshot
        ? { presentationMetrics: this.presentationTiming.snapshot }
        : {}),
      ...(this.decline ? { decline: this.decline } : {}),
      choreography,
    });
  }

  private assertUsable(): void {
    if (this.disposed) {
      throw new Error("ChoreographyStreamRuntime has been disposed");
    }
  }
}
