import type { MotionPlayback, MotionPlaybackOutcome, SVGCanvasHandle } from "@/features/canvas/types";
import {
  createSceneState,
  materializeSceneTransition,
  planSceneTransition,
  type SceneState,
} from "@/lib/live-scene";
import {
  applyScenePatch,
  LIVE_SCENE_MAX_ACCEPTED_PATCHES,
  type ScenePatchEvent,
} from "@/lib/live-scene/patch";

import type {
  SceneStreamCompletedEvent,
  SceneStreamEvent,
  SceneStreamFailedEvent,
  SceneStreamRequest,
} from "./model-stream";

const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });
const MAX_PROMPT_LENGTH = 2_000;

export const LIVE_SCENE_MAX_PATCH_QUEUE = LIVE_SCENE_MAX_ACCEPTED_PATCHES;
export const LIVE_SCENE_MAX_ACCEPTED_HISTORY = 32;

export type SceneStreamRuntimePhase =
  | "idle"
  | "connecting"
  | "streaming"
  | "repairing"
  | "completing"
  | "completed"
  | "failed"
  | "interrupted"
  | "replaying";

export interface AcceptedSceneRevision {
  readonly scene: SceneState;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly patchId: string;
  readonly narration: string;
  readonly materialized: boolean;
}

export interface SceneStreamRuntimeFailure {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface SceneStreamCompletionMetrics {
  readonly firstPatchMs: number;
  readonly totalMs: number;
  readonly repaired: boolean;
}

export interface SceneStreamRuntimeSnapshot {
  readonly phase: SceneStreamRuntimePhase;
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly committedScene: SceneState;
  readonly provisionalScene: SceneState;
  readonly accepted: readonly AcceptedSceneRevision[];
  readonly queuedPatchCount: number;
  readonly activeRevision?: number;
  readonly narration: string;
  readonly error?: SceneStreamRuntimeFailure;
  readonly completion?: SceneStreamCompletionMetrics;
}

export type SceneStreamRenderer = Pick<
  SVGCanvasHandle,
  "playMotionPlan" | "cancelMotion" | "clear"
>;

export interface SceneStreamRunInvocation {
  readonly request: SceneStreamRequest;
  readonly signal: AbortSignal;
  readonly onEvent: (event: SceneStreamEvent) => void;
}

export type SceneStreamRunner = (
  invocation: SceneStreamRunInvocation
) => Promise<void>;

export interface SceneStreamRuntimeOptions {
  readonly renderer: SceneStreamRenderer;
  readonly runStream: SceneStreamRunner;
  readonly queueLimit?: number;
  readonly staggerMs?: number;
}

export type SceneStreamRuntimeErrorCode =
  | "runtime_busy"
  | "invalid_prompt"
  | "invalid_event"
  | "queue_overflow";

export class SceneStreamRuntimeError extends Error {
  readonly code: SceneStreamRuntimeErrorCode;

  constructor(code: SceneStreamRuntimeErrorCode, message: string) {
    super(message);
    this.name = "SceneStreamRuntimeError";
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
  terminal: "completed" | "failed" | undefined;
  networkSettled: boolean;
}

interface QueuedPatch {
  readonly event: ScenePatchEvent;
  readonly target: SceneState;
}

interface ActiveTransition {
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly previous: SceneState;
  readonly target: SceneState;
  readonly playback: MotionPlayback;
  readonly record: AcceptedSceneRevision;
  readonly replayIndex?: number;
}

function acceptedRecord(
  event: ScenePatchEvent,
  scene: SceneState,
  materialized: boolean
): AcceptedSceneRevision {
  return Object.freeze({
    scene,
    generation: event.generation,
    attempt: event.attempt,
    sequence: event.sequence,
    patchId: event.patch.patchId,
    narration: event.patch.narration,
    materialized,
  });
}

function runtimeFailure(
  code: string,
  message: string,
  retryable: boolean
): SceneStreamRuntimeFailure {
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

/** Framework-neutral, single-flight owner for one progressively authored board. */
export class SceneStreamRuntime {
  private readonly renderer: SceneStreamRenderer;
  private readonly runStream: SceneStreamRunner;
  private readonly queueLimit: number;
  private readonly staggerMs: number;
  private readonly listeners = new Set<() => void>();

  private tokenSequence = 0;
  private currentToken: RuntimeToken | null = null;
  private streamControl: StreamControl | null = null;
  private phase: SceneStreamRuntimePhase = "idle";
  private generation = 0;
  private attempt = 0;
  private sequence = 0;
  private committedScene = EMPTY_SCENE;
  private provisionalScene = EMPTY_SCENE;
  private accepted: AcceptedSceneRevision[] = [];
  private queue: QueuedPatch[] = [];
  private active: ActiveTransition | null = null;
  private patchIds = new Set<string>();
  private narration = "Ready for a visual explanation.";
  private failure: SceneStreamRuntimeFailure | undefined;
  private completion: SceneStreamCompletionMetrics | undefined;
  private snapshot: SceneStreamRuntimeSnapshot;
  private disposed = false;

  constructor(options: SceneStreamRuntimeOptions) {
    this.renderer = options.renderer;
    this.runStream = options.runStream;
    this.queueLimit = options.queueLimit ?? LIVE_SCENE_MAX_PATCH_QUEUE;
    this.staggerMs = options.staggerMs ?? 70;
    if (
      !Number.isSafeInteger(this.queueLimit) ||
      this.queueLimit < 1 ||
      this.queueLimit > LIVE_SCENE_MAX_PATCH_QUEUE
    ) {
      throw new RangeError(
        `queueLimit must be between 1 and ${LIVE_SCENE_MAX_PATCH_QUEUE}`
      );
    }
    if (!Number.isFinite(this.staggerMs) || this.staggerMs < 0) {
      throw new RangeError("staggerMs must be a finite non-negative number");
    }
    this.snapshot = this.buildSnapshot();
  }

  getSnapshot = (): SceneStreamRuntimeSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.assertUsable();
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Start a new generation from the exact committed scene. */
  start(promptValue: string): number {
    this.assertUsable();
    if (this.isBusy()) {
      throw new SceneStreamRuntimeError(
        "runtime_busy",
        "A scene generation or replay is still active"
      );
    }
    if (typeof promptValue !== "string") {
      throw new SceneStreamRuntimeError("invalid_prompt", "Prompt must be text");
    }
    const prompt = promptValue.trim();
    if (prompt.length === 0 || prompt.length > MAX_PROMPT_LENGTH) {
      throw new SceneStreamRuntimeError(
        "invalid_prompt",
        `Prompt must contain between 1 and ${MAX_PROMPT_LENGTH} characters`
      );
    }

    this.invalidateCurrentToken(true);
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
    this.provisionalScene = this.committedScene;
    this.phase = "connecting";
    this.narration = "Preparing the live board…";
    this.failure = undefined;
    this.completion = undefined;
    this.publish();

    const request = Object.freeze({
      prompt,
      generation,
      baseScene: this.committedScene,
    });
    const invocation: SceneStreamRunInvocation = Object.freeze({
      request,
      signal: controller.signal,
      onEvent: (event: SceneStreamEvent) => this.acceptStreamEvent(token, event),
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
        return this.runStream(invocation);
      })
      .then(() => this.onNetworkSettled(token))
      .catch((error: unknown) => this.onNetworkError(token, error));
    return generation;
  }

  /** Cancel one exact stream token and retain only materially visible work. */
  interrupt(): boolean {
    this.assertUsable();
    if (!this.isBusy()) return false;

    const active = this.active;
    this.currentToken = null;
    this.streamControl?.controller.abort();
    this.streamControl = null;
    this.queue = [];
    this.active = null;

    if (active) {
      const outcome = active.playback.cancel();
      const retained = this.authoritativeRetainedScene(
        active,
        this.retainedScene(active, outcome)
      );
      this.committedScene = retained;
      this.provisionalScene = retained;
      if (active.source === "stream") {
        this.appendAccepted(
          Object.freeze({
            ...active.record,
            scene: retained,
            materialized: outcome.status !== "completed",
          })
        );
      } else {
        this.reconcileInterruptedReplay(active, retained);
      }
    } else {
      this.provisionalScene = this.committedScene;
    }

    this.renderer.cancelMotion();
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.phase = "interrupted";
    this.narration = "Generation interrupted. The last visible board is safe.";
    this.failure = undefined;
    this.completion = undefined;
    this.publish();
    return true;
  }

  /** Clear all semantic and rendered state synchronously. */
  reset(): void {
    this.assertUsable();
    this.invalidateCurrentToken(true);
    this.queue = [];
    const active = this.active;
    this.active = null;
    active?.playback.cancel();
    this.renderer.cancelMotion();
    this.renderer.clear();
    this.phase = "idle";
    this.generation = 0;
    this.attempt = 0;
    this.sequence = 0;
    this.committedScene = EMPTY_SCENE;
    this.provisionalScene = EMPTY_SCENE;
    this.accepted = [];
    this.patchIds = new Set();
    this.narration = "Ready for a visual explanation.";
    this.failure = undefined;
    this.completion = undefined;
    this.publish();
  }

  /** Replay accepted snapshots through the renderer without invoking the model runner. */
  async replayAccepted(): Promise<void> {
    this.assertUsable();
    if (this.isBusy()) {
      throw new SceneStreamRuntimeError(
        "runtime_busy",
        "A scene generation or replay is still active"
      );
    }
    if (this.accepted.length === 0) return;

    this.invalidateCurrentToken(true);
    const records = [...this.accepted];
    const token = this.createToken("replay", this.generation);
    this.currentToken = token;
    this.renderer.cancelMotion();
    this.renderer.clear();
    this.committedScene = EMPTY_SCENE;
    this.provisionalScene = EMPTY_SCENE;
    this.phase = "replaying";
    this.narration = `Replaying ${records.length} accepted revision${records.length === 1 ? "" : "s"}.`;
    this.failure = undefined;
    this.publish();

    for (const [replayIndex, record] of records.entries()) {
      if (this.currentToken !== token) return;
      const target =
        record.scene.revision === this.committedScene.revision + 1
          ? record.scene
          : createSceneState({
              revision: this.committedScene.revision + 1,
              nodes: record.scene.nodes,
            });
      let playback: MotionPlayback;
      try {
        const plan = planSceneTransition(this.committedScene, target);
        playback = this.renderer.playMotionPlan(plan, { staggerMs: this.staggerMs });
      } catch {
        this.finishReplayFailure(
          token,
          "The accepted scene could not be replayed.",
          replayIndex
        );
        return;
      }

      const transition: ActiveTransition = {
        token,
        source: "replay",
        previous: this.committedScene,
        target,
        playback,
        record,
        replayIndex,
      };
      this.active = transition;
      this.publish();

      let outcome: MotionPlaybackOutcome;
      try {
        outcome = await playback.finished;
      } catch {
        outcome = { status: "failed", appliedStepIds: [] };
      }
      if (this.currentToken !== token || this.active !== transition) return;
      this.active = null;
      if (outcome.status !== "completed") {
        const authoritativeRetained = this.authoritativeRetainedScene(
          transition,
          this.retainedScene(transition, outcome)
        );
        this.committedScene = authoritativeRetained;
        this.provisionalScene = authoritativeRetained;
        this.reconcileInterruptedReplay(transition, authoritativeRetained);
        this.sequence = this.lastAcceptedSequence(this.generation);
        this.currentToken = null;
        this.renderer.cancelMotion();
        this.phase = outcome.status === "cancelled" ? "interrupted" : "failed";
        this.narration =
          outcome.status === "cancelled"
            ? "Replay interrupted."
            : "Replay stopped early. The visible board is now the safe branch.";
        if (outcome.status === "failed") {
          this.failure = runtimeFailure(
            "renderer_failed",
            "Replay stopped early. The visible board is now the safe branch.",
            true
          );
        }
        this.publish();
        return;
      }

      this.committedScene = target;
      this.provisionalScene = target;
      this.publish();
    }

    if (this.currentToken !== token) return;
    const authoritativeFinal = records[records.length - 1].scene;
    this.committedScene = authoritativeFinal;
    this.provisionalScene = authoritativeFinal;
    this.currentToken = null;
    this.phase = "completed";
    this.narration = "Replay reached the same accepted semantic state.";
    this.publish();
  }

  dispose(): void {
    if (this.disposed) return;
    this.invalidateCurrentToken(true);
    this.active?.playback.cancel();
    this.active = null;
    this.queue = [];
    this.renderer.cancelMotion();
    this.listeners.clear();
    this.disposed = true;
  }

  private acceptStreamEvent(token: RuntimeToken, event: SceneStreamEvent): void {
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
      this.failProtocol(token, "Scene event generation does not match the active request");
      return;
    }

    try {
      switch (event.type) {
        case "scene_stream_started":
          this.acceptStarted(token, event.attempt, event.baseRevision);
          break;
        case "scene_stream_repairing":
          this.acceptRepairing(
            token,
            event.fromAttempt,
            event.toAttempt,
            event.lastAcceptedRevision,
            event.message
          );
          break;
        case "scene_patch":
          this.acceptPatch(token, event);
          break;
        case "scene_stream_completed":
          this.acceptCompleted(token, event);
          break;
        case "scene_stream_failed":
          this.acceptFailed(token, event);
          break;
      }
    } catch (error) {
      this.failProtocol(
        token,
        error instanceof Error ? error.message : "Scene event validation failed"
      );
    }
  }

  private acceptStarted(token: RuntimeToken, attempt: number, baseRevision: number): void {
    if (this.attempt !== 0 || attempt !== 1 || baseRevision !== this.provisionalScene.revision) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Started event does not match the active generation boundary"
      );
    }
    this.attempt = 1;
    this.phase = "streaming";
    this.narration = "The model is authoring the first scene patch…";
    this.publishIfCurrent(token);
  }

  private acceptRepairing(
    token: RuntimeToken,
    fromAttempt: number,
    toAttempt: number,
    lastAcceptedRevision: number,
    message: string
  ): void {
    if (
      this.attempt !== 1 ||
      fromAttempt !== 1 ||
      toAttempt !== 2 ||
      lastAcceptedRevision !== this.provisionalScene.revision
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Repair event does not match the active attempt or provisional revision"
      );
    }
    this.attempt = 2;
    this.phase = "repairing";
    this.narration = message;
    this.publishIfCurrent(token);
  }

  private acceptPatch(token: RuntimeToken, event: ScenePatchEvent): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.sequence !== this.sequence + 1 ||
      event.baseRevision !== this.provisionalScene.revision
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Patch does not match the active attempt, sequence, or provisional revision"
      );
    }
    if (this.patchIds.has(event.patch.patchId)) {
      throw new SceneStreamRuntimeError("invalid_event", "Patch ID was already accepted");
    }
    if (this.queue.length >= this.queueLimit) {
      throw new SceneStreamRuntimeError(
        "queue_overflow",
        `Scene patch queue exceeds ${this.queueLimit} entries`
      );
    }

    const target = applyScenePatch(this.provisionalScene, event);
    this.provisionalScene = target;
    this.sequence = event.sequence;
    this.patchIds.add(event.patch.patchId);
    this.queue.push(Object.freeze({ event, target }));
    this.phase = "streaming";
    this.narration = event.patch.narration;
    this.publishIfCurrent(token);
    this.pump(token);
  }

  private acceptCompleted(token: RuntimeToken, event: SceneStreamCompletedEvent): void {
    if (
      this.attempt === 0 ||
      event.finalRevision !== this.provisionalScene.revision ||
      event.patchCount !== this.sequence ||
      event.repaired !== (this.attempt === 2)
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Completion event does not match the accepted patch ledger"
      );
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

  private acceptFailed(token: RuntimeToken, event: SceneStreamFailedEvent): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.lastAcceptedRevision !== this.provisionalScene.revision
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Failure event does not match the active attempt or accepted revision"
      );
    }
    const control = this.requireControl(token);
    control.terminal = "failed";
    control.controller.abort();
    this.failure = runtimeFailure(event.code, event.message, event.retryable);
    this.narration = event.message;
    this.settlePhase();
    this.publishIfCurrent(token);
  }

  private pump(token: RuntimeToken): void {
    if (this.currentToken !== token || this.active || this.queue.length === 0) return;
    const queued = this.queue.shift();
    if (!queued) return;
    if (queued.event.baseRevision !== this.committedScene.revision) {
      this.failRenderer(token, "Queued patch no longer follows the committed scene");
      return;
    }

    let playback: MotionPlayback;
    try {
      const plan = planSceneTransition(this.committedScene, queued.target);
      playback = this.renderer.playMotionPlan(plan, { staggerMs: this.staggerMs });
    } catch {
      this.failRenderer(token, "The visual board could not start this scene patch");
      return;
    }

    const transition: ActiveTransition = {
      token,
      source: "stream",
      previous: this.committedScene,
      target: queued.target,
      playback,
      record: acceptedRecord(queued.event, queued.target, false),
    };
    this.active = transition;
    this.publishIfCurrent(token);
    void playback.finished
      .then((outcome) => this.onPlaybackFinished(transition, outcome))
      .catch(() =>
        this.onPlaybackFinished(transition, {
          status: "failed",
          appliedStepIds: [],
        })
      );
  }

  private onPlaybackFinished(
    transition: ActiveTransition,
    outcome: MotionPlaybackOutcome
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      transition.source !== "stream"
    ) {
      return;
    }
    this.active = null;

    if (outcome.status === "completed") {
      this.committedScene = transition.target;
      this.appendAccepted(transition.record);
      this.publish();
      this.pump(transition.token);
      this.settlePhase();
      this.publishIfCurrent(transition.token);
      return;
    }

    const retained = this.retainedScene(transition, outcome);
    this.committedScene = retained;
    this.provisionalScene = retained;
    this.queue = [];
    this.appendAccepted(
      Object.freeze({
        ...transition.record,
        scene: retained,
        materialized: true,
      })
    );
    this.renderer.cancelMotion();
    this.sequence = transition.record.sequence;

    if (outcome.status === "cancelled") {
      this.invalidateCurrentToken(true);
      this.phase = "interrupted";
      this.narration = "Generation interrupted. The last visible board is safe.";
      this.failure = undefined;
    } else {
      this.failRenderer(transition.token, "The visual board stopped while applying a patch");
    }
    this.publish();
  }

  private retainedScene(
    transition: ActiveTransition,
    outcome: MotionPlaybackOutcome
  ): SceneState {
    if (outcome.status === "completed") return transition.target;
    try {
      return materializeSceneTransition(
        transition.previous,
        transition.target,
        outcome.appliedStepIds
      );
    } catch {
      return transition.previous;
    }
  }

  private authoritativeRetainedScene(
    transition: ActiveTransition,
    retained: SceneState
  ): SceneState {
    if (
      transition.source !== "replay" ||
      retained.revision === transition.record.scene.revision
    ) {
      return retained;
    }
    return createSceneState({
      revision: transition.record.scene.revision,
      nodes: retained.nodes,
    });
  }

  private appendAccepted(record: AcceptedSceneRevision): void {
    const previous = this.accepted[this.accepted.length - 1];
    let next: AcceptedSceneRevision[];
    if (previous?.scene.revision === record.scene.revision) {
      next = [...this.accepted.slice(0, -1), record];
    } else {
      next = [...this.accepted, record];
    }
    this.accepted = next.slice(-LIVE_SCENE_MAX_ACCEPTED_HISTORY);
  }

  private reconcileInterruptedReplay(
    transition: ActiveTransition,
    retained: SceneState
  ): void {
    if (transition.source !== "replay" || transition.replayIndex === undefined) return;
    const retainedRecord = Object.freeze({
      ...transition.record,
      scene: retained,
      materialized: retained !== transition.target || transition.record.materialized,
    });
    this.accepted = [
      ...this.accepted.slice(0, transition.replayIndex),
      retainedRecord,
    ];
  }

  private lastAcceptedSequence(generation: number): number {
    for (let index = this.accepted.length - 1; index >= 0; index -= 1) {
      const record = this.accepted[index];
      if (record.generation === generation) return record.sequence;
    }
    return 0;
  }

  private failProtocol(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    const control = this.streamControl;
    if (!control || control.token !== token || control.terminal) return;
    control.terminal = "failed";
    control.controller.abort();
    this.failure = runtimeFailure(
      "invalid_stream_event",
      "The visual stream stopped. Your last accepted board is safe.",
      true
    );
    this.narration = "The visual stream stopped. Your last accepted board is safe.";
    this.settlePhase();
    // Keep diagnostics out of the user-facing snapshot while retaining a useful console signal.
    console.warn("[LiveScene] Rejected stream event:", detail);
    this.publish();
  }

  private failRenderer(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    const control = this.streamControl;
    if (control?.token === token) {
      control.terminal = "failed";
      control.controller.abort();
    }
    this.queue = [];
    this.provisionalScene = this.committedScene;
    this.phase = "failed";
    this.failure = runtimeFailure(
      "renderer_failed",
      "The visual update stopped. Your last accepted board is safe.",
      true
    );
    this.narration = "The visual update stopped. Your last accepted board is safe.";
    console.warn("[LiveScene] Renderer failure:", detail);
    this.publish();
  }

  private finishReplayFailure(
    token: RuntimeToken,
    detail: string,
    acceptedPrefixLength: number
  ): void {
    if (this.currentToken !== token) return;
    this.currentToken = null;
    this.active = null;
    this.accepted = this.accepted.slice(0, acceptedPrefixLength);
    const lastAccepted = this.accepted[this.accepted.length - 1]?.scene ?? EMPTY_SCENE;
    this.committedScene = lastAccepted;
    this.provisionalScene = lastAccepted;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.renderer.cancelMotion();
    this.phase = "failed";
    this.failure = runtimeFailure(
      "renderer_failed",
      "Replay stopped early. The visible board is now the safe branch.",
      true
    );
    this.narration = "Replay stopped early. The visible board is now the safe branch.";
    console.warn("[LiveScene] Replay failure:", detail);
    this.publish();
  }

  private onNetworkSettled(token: RuntimeToken): void {
    const control = this.streamControl;
    if (this.currentToken !== token || !control || control.token !== token) return;
    control.networkSettled = true;
    if (!control.terminal) {
      this.failProtocol(token, "Scene stream ended without a terminal event");
    }
  }

  private onNetworkError(token: RuntimeToken, error: unknown): void {
    const control = this.streamControl;
    if (this.currentToken !== token || !control || control.token !== token) return;
    control.networkSettled = true;
    if (control.terminal || (control.controller.signal.aborted && abortError(error))) return;
    control.terminal = "failed";
    control.controller.abort();
    this.failure = runtimeFailure(
      "stream_unavailable",
      "The visual stream stopped. Your last accepted board is safe.",
      true
    );
    this.narration = "The visual stream stopped. Your last accepted board is safe.";
    this.settlePhase();
    this.publish();
  }

  private settlePhase(): void {
    const control = this.streamControl;
    if (control?.terminal === "completed") {
      this.phase = this.active || this.queue.length > 0 ? "completing" : "completed";
      if (this.phase === "completed") {
        this.narration = "The live visual explanation is complete.";
      }
    } else if (control?.terminal === "failed") {
      this.phase = this.active || this.queue.length > 0 ? "completing" : "failed";
    }
  }

  private requireControl(token: RuntimeToken): StreamControl {
    const control = this.streamControl;
    if (!control || control.token !== token) {
      throw new SceneStreamRuntimeError("invalid_event", "Stream control token is stale");
    }
    return control;
  }

  private createToken(kind: RuntimeToken["kind"], generation: number): RuntimeToken {
    return Object.freeze({ id: ++this.tokenSequence, kind, generation });
  }

  private invalidateCurrentToken(abort: boolean): void {
    if (abort) this.streamControl?.controller.abort();
    this.currentToken = null;
    this.streamControl = null;
  }

  private isBusy(): boolean {
    return (
      this.active !== null ||
      this.queue.length > 0 ||
      this.phase === "connecting" ||
      this.phase === "streaming" ||
      this.phase === "repairing" ||
      this.phase === "completing" ||
      this.phase === "replaying"
    );
  }

  private publishIfCurrent(token: RuntimeToken): void {
    if (this.currentToken === token) this.publish();
  }

  private publish(): void {
    this.snapshot = this.buildSnapshot();
    for (const listener of [...this.listeners]) {
      try {
        listener();
      } catch (error) {
        console.error("[LiveScene] Snapshot subscriber failed:", error);
      }
    }
  }

  private buildSnapshot(): SceneStreamRuntimeSnapshot {
    return Object.freeze({
      phase: this.phase,
      generation: this.generation,
      attempt: this.attempt,
      sequence: this.sequence,
      committedScene: this.committedScene,
      provisionalScene: this.provisionalScene,
      accepted: Object.freeze([...this.accepted]),
      queuedPatchCount: this.queue.length,
      ...(this.active ? { activeRevision: this.active.target.revision } : {}),
      narration: this.narration,
      ...(this.failure ? { error: this.failure } : {}),
      ...(this.completion ? { completion: this.completion } : {}),
    });
  }

  private assertUsable(): void {
    if (this.disposed) throw new Error("SceneStreamRuntime has been disposed");
  }
}
