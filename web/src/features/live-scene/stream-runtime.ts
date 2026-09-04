import type { MotionPlayback, MotionPlaybackOutcome, SVGCanvasHandle } from "@/features/canvas/types";
import {
  applySemanticScenePatch,
  createSemanticSceneState,
  createSceneState,
  decodeSemanticScenePatchEvent,
  materializeSceneTransition,
  planSceneTransition,
  type MotionPlan,
  type SceneState,
  type SemanticScenePatchEvent,
  type SemanticSceneState,
} from "@/lib/live-scene";
import {
  applyScenePatch,
  LIVE_SCENE_MAX_ACCEPTED_PATCHES,
  LIVE_SCENE_MAX_NODES,
  type ScenePatchEvent,
} from "@/lib/live-scene/patch";

import type {
  SemanticSceneDeclineReason,
  SemanticSceneStreamDeclinedEvent,
  SceneStreamCompletedEvent,
  SceneStreamEvent,
  SceneStreamFailedEvent,
  SceneStreamRequest,
  SemanticSceneStreamEvent,
  SemanticSceneStreamRequest,
  SemanticSceneStreamRunner,
} from "./model-stream";

const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });
const EMPTY_SEMANTIC_SCENE = createSemanticSceneState({
  revision: 0,
  components: [],
});
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
  | "declined"
  | "failed"
  | "interrupting"
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

/**
 * Deterministic, in-memory acknowledgement that one semantic atom crossed the
 * renderer's post-paint barrier. It is not proof of compiler geometry or
 * client authenticity.
 */
export interface SemanticPresentationReceipt {
  readonly type: "semantic_atom_presented";
  readonly atomId: string;
  readonly nodeId: string;
  readonly certificateSha256: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly settlement: "completed" | "cancelled";
  readonly appliedStepIds: readonly [string];
}

export interface AcceptedSemanticRevision {
  readonly scene: SceneState;
  readonly semanticScene: SemanticSceneState;
  readonly event: SemanticScenePatchEvent;
  readonly presentation: SemanticPresentationReceipt;
}

export interface SemanticSceneStreamRuntimeSnapshot {
  readonly committedScene: SemanticSceneState;
  readonly provisionalScene: SemanticSceneState;
  readonly accepted: readonly AcceptedSemanticRevision[];
  readonly commitFrontier?: SemanticPresentationReceipt;
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

export interface SemanticSceneStreamDecline {
  readonly reasonCode: SemanticSceneDeclineReason;
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
  readonly decline?: SemanticSceneStreamDecline;
  readonly semantic?: SemanticSceneStreamRuntimeSnapshot;
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

interface CommonSceneStreamRuntimeOptions {
  readonly renderer: SceneStreamRenderer;
  readonly queueLimit?: number;
  readonly staggerMs?: number;
}

export type SceneStreamRuntimeOptions =
  | (CommonSceneStreamRuntimeOptions & {
      readonly protocol?: "raw";
      readonly runStream: SceneStreamRunner;
    })
  | (CommonSceneStreamRuntimeOptions & {
      readonly protocol: "semantic";
      readonly runStream: SemanticSceneStreamRunner;
    });

export type SceneStreamRuntimeErrorCode =
  | "runtime_busy"
  | "runtime_reset_required"
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
  terminal: "completed" | "declined" | "failed" | undefined;
  networkSettled: boolean;
}

interface RawQueuedPatch {
  readonly kind: "raw";
  readonly event: ScenePatchEvent;
  readonly target: SceneState;
}

interface SemanticQueuedPatch {
  readonly kind: "semantic";
  readonly event: SemanticScenePatchEvent;
  readonly target: SceneState;
  readonly semanticTarget: SemanticSceneState;
  readonly plan: MotionPlan;
}

type QueuedPatch = RawQueuedPatch | SemanticQueuedPatch;

interface RawActiveTransition {
  readonly kind: "raw";
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly previous: SceneState;
  readonly target: SceneState;
  readonly playback: MotionPlayback;
  readonly record: AcceptedSceneRevision;
  readonly replayIndex?: number;
}

interface SemanticActiveTransition {
  readonly kind: "semantic";
  readonly token: RuntimeToken;
  readonly source: "stream" | "replay";
  readonly target: SceneState;
  readonly semanticTarget: SemanticSceneState;
  readonly playback: MotionPlayback;
  readonly event: SemanticScenePatchEvent;
  readonly replayIndex?: number;
}

type ActiveTransition = RawActiveTransition | SemanticActiveTransition;

function acceptedRecord(
  event: ScenePatchEvent | SemanticScenePatchEvent,
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

function committedTransitionWork(
  transition: RawActiveTransition,
  retained: SceneState
): boolean {
  return retained.revision === transition.target.revision;
}

type SemanticPresentationEvaluation =
  | {
      readonly kind: "presented";
      readonly receipt: SemanticPresentationReceipt;
    }
  | { readonly kind: "not_presented" }
  | { readonly kind: "invalid" };

function evaluateSemanticPresentation(
  transition: SemanticActiveTransition,
  outcome: MotionPlaybackOutcome
): SemanticPresentationEvaluation {
  const expectedNodeId = transition.event.semantic.receipt.nodeId;
  const exactTarget =
    outcome.appliedStepIds.length === 1 &&
    outcome.appliedStepIds[0] === expectedNodeId;

  if (
    exactTarget &&
    (outcome.status === "completed" || outcome.status === "cancelled")
  ) {
    const appliedStepIds = Object.freeze([expectedNodeId]) as readonly [string];
    return Object.freeze({
      kind: "presented",
      receipt: Object.freeze({
        type: "semantic_atom_presented",
        atomId: transition.event.semantic.atomId,
        nodeId: expectedNodeId,
        certificateSha256:
          transition.event.semantic.certificate.certificateSha256,
        sceneRevision: transition.target.revision,
        semanticRevision: transition.semanticTarget.revision,
        settlement: outcome.status,
        appliedStepIds,
      }),
    });
  }

  if (outcome.status === "cancelled" && outcome.appliedStepIds.length === 0) {
    return Object.freeze({ kind: "not_presented" });
  }
  return Object.freeze({ kind: "invalid" });
}

function sameCanonicalValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/** Framework-neutral, single-flight owner for one progressively authored board. */
export class SceneStreamRuntime {
  private readonly protocol: "raw" | "semantic";
  private readonly renderer: SceneStreamRenderer;
  private readonly runRawStream: SceneStreamRunner | undefined;
  private readonly runSemanticStream: SemanticSceneStreamRunner | undefined;
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
  private committedSemanticScene = EMPTY_SEMANTIC_SCENE;
  private provisionalSemanticScene = EMPTY_SEMANTIC_SCENE;
  private accepted: AcceptedSceneRevision[] = [];
  private acceptedSemantic: AcceptedSemanticRevision[] = [];
  private queue: QueuedPatch[] = [];
  private active: ActiveTransition | null = null;
  private pendingInterruptToken: RuntimeToken | null = null;
  private semanticReplayPrefixLength = 0;
  private patchIds = new Set<string>();
  private narration = "Ready for a visual explanation.";
  private failure: SceneStreamRuntimeFailure | undefined;
  private completion: SceneStreamCompletionMetrics | undefined;
  private decline: SemanticSceneStreamDecline | undefined;
  private snapshot: SceneStreamRuntimeSnapshot;
  private disposed = false;

  constructor(options: SceneStreamRuntimeOptions) {
    this.protocol = options.protocol ?? "raw";
    this.renderer = options.renderer;
    if (options.protocol === "semantic") {
      this.runSemanticStream = options.runStream;
      this.runRawStream = undefined;
    } else {
      this.runRawStream = options.runStream;
      this.runSemanticStream = undefined;
    }
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
    if (
      this.protocol === "semantic" &&
      this.phase === "failed" &&
      this.failure?.retryable === false
    ) {
      throw new SceneStreamRuntimeError(
        "runtime_reset_required",
        "Reset the board or successfully replay its accepted prefix before starting another semantic generation"
      );
    }
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
    this.semanticReplayPrefixLength = 0;
    this.provisionalScene = this.committedScene;
    if (this.protocol === "semantic") {
      this.provisionalSemanticScene = this.committedSemanticScene;
    }
    this.phase = "connecting";
    this.narration = "Preparing the live board…";
    this.failure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();

    const run =
      this.protocol === "semantic"
        ? () => {
            const request: SemanticSceneStreamRequest = Object.freeze({
              prompt,
              generation,
              baseScene: this.committedScene,
              baseSemanticScene: this.committedSemanticScene,
            });
            return this.runSemanticStream!(
              Object.freeze({
                request,
                signal: controller.signal,
                onEvent: (event: SemanticSceneStreamEvent) =>
                  this.acceptSemanticStreamEvent(token, event),
              })
            );
          }
        : () => {
            const request: SceneStreamRequest = Object.freeze({
              prompt,
              generation,
              baseScene: this.committedScene,
            });
            const invocation: SceneStreamRunInvocation = Object.freeze({
              request,
              signal: controller.signal,
              onEvent: (event: SceneStreamEvent) =>
                this.acceptStreamEvent(token, event),
            });
            return this.runRawStream!(invocation);
          };
    void Promise.resolve()
      .then(() => {
        if (
          this.disposed ||
          this.currentToken !== token ||
          controller.signal.aborted
        ) {
          return;
        }
        return run();
      })
      .then(() => this.onNetworkSettled(token))
      .catch((error: unknown) => this.onNetworkError(token, error));
    return generation;
  }

  /** Cancel one exact stream token and retain only materially visible work. */
  interrupt(): boolean {
    this.assertUsable();
    if (!this.isBusy()) return false;
    if (this.protocol === "semantic") {
      return this.interruptSemantic();
    }

    const active = this.active;
    this.currentToken = null;
    this.streamControl?.controller.abort();
    this.streamControl = null;
    this.queue = [];
    this.active = null;

    if (active?.kind === "raw") {
      const outcome = active.playback.cancel();
      const materialized = this.retainedScene(active, outcome);
      const committed = committedTransitionWork(active, materialized);
      const retained = committed
        ? this.authoritativeRetainedScene(active, materialized)
        : materialized;
      this.committedScene = retained;
      this.provisionalScene = retained;
      if (active.source === "stream") {
        if (committed) {
          this.appendAccepted(
            Object.freeze({
              ...active.record,
              scene: retained,
              materialized: outcome.status !== "completed",
            })
          );
        }
      } else {
        this.reconcileInterruptedReplay(active, retained, committed);
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
    this.committedSemanticScene = EMPTY_SEMANTIC_SCENE;
    this.provisionalSemanticScene = EMPTY_SEMANTIC_SCENE;
    this.accepted = [];
    this.acceptedSemantic = [];
    this.pendingInterruptToken = null;
    this.semanticReplayPrefixLength = 0;
    this.patchIds = new Set();
    this.narration = "Ready for a visual explanation.";
    this.failure = undefined;
    this.completion = undefined;
    this.decline = undefined;
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
    if (this.protocol === "semantic") {
      await this.replaySemanticAccepted();
      return;
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
    this.completion = undefined;
    this.decline = undefined;
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

      const transition: RawActiveTransition = {
        kind: "raw",
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
        const materialized = this.retainedScene(transition, outcome);
        const committed = committedTransitionWork(transition, materialized);
        const authoritativeRetained = committed
          ? this.authoritativeRetainedScene(transition, materialized)
          : materialized;
        this.committedScene = authoritativeRetained;
        this.provisionalScene = authoritativeRetained;
        this.reconcileInterruptedReplay(transition, authoritativeRetained, committed);
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

  private async replaySemanticAccepted(): Promise<void> {
    if (this.acceptedSemantic.length === 0) return;
    if (this.accepted.length !== this.acceptedSemantic.length) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Paired semantic acceptance ledgers are out of sync"
      );
    }

    this.invalidateCurrentToken(true);
    const records = [...this.acceptedSemantic];
    const token = this.createToken("replay", this.generation);
    this.currentToken = token;
    this.renderer.cancelMotion();
    this.renderer.clear();
    this.committedScene = EMPTY_SCENE;
    this.provisionalScene = EMPTY_SCENE;
    this.committedSemanticScene = EMPTY_SEMANTIC_SCENE;
    this.provisionalSemanticScene = EMPTY_SEMANTIC_SCENE;
    this.sequence = 0;
    this.phase = "replaying";
    this.narration = `Replaying ${records.length} accepted semantic atom${records.length === 1 ? "" : "s"}.`;
    this.failure = undefined;
    this.completion = undefined;
    this.decline = undefined;
    this.publish();

    for (const [replayIndex, record] of records.entries()) {
      if (this.currentToken !== token) return;

      let applied: ReturnType<typeof applySemanticScenePatch>;
      try {
        applied = applySemanticScenePatch(
          this.committedScene,
          this.committedSemanticScene,
          record.event
        );
        if (
          !sameCanonicalValue(applied.scene, record.scene) ||
          !sameCanonicalValue(applied.semanticScene, record.semanticScene) ||
          !this.semanticRecordMatchesEvent(record)
        ) {
          throw new Error(
            "Stored semantic atom no longer reproduces its paired accepted state"
          );
        }
      } catch (error) {
        this.finishSemanticReplayFailure(
          token,
          error instanceof Error
            ? error.message
            : "Stored semantic atom could not be re-applied",
          replayIndex
        );
        return;
      }

      let playback: MotionPlayback;
      try {
        playback = this.renderer.playMotionPlan(applied.plan, {
          staggerMs: this.staggerMs,
        });
      } catch {
        this.finishSemanticReplayFailure(
          token,
          "The accepted semantic atom could not start replay",
          replayIndex
        );
        return;
      }

      const transition: SemanticActiveTransition = {
        kind: "semantic",
        token,
        source: "replay",
        target: applied.scene,
        semanticTarget: applied.semanticScene,
        playback,
        event: record.event,
        replayIndex,
      };
      this.active = transition;
      this.provisionalScene = transition.target;
      this.provisionalSemanticScene = transition.semanticTarget;
      this.publish();

      let outcome: MotionPlaybackOutcome;
      try {
        outcome = await playback.finished;
      } catch {
        outcome = { status: "failed", appliedStepIds: [] };
      }
      if (this.currentToken !== token || this.active !== transition) return;
      if (this.pendingInterruptToken === token) {
        this.finishSemanticInterruption(transition, outcome);
        return;
      }

      this.active = null;
      const presentation = evaluateSemanticPresentation(transition, outcome);
      if (
        presentation.kind !== "presented" ||
        presentation.receipt.settlement !== "completed"
      ) {
        this.finishSemanticReplayFailure(
          token,
          presentation.kind === "not_presented"
            ? "Semantic replay did not present its target node"
            : presentation.kind === "invalid"
              ? "Semantic replay returned an invalid presentation receipt"
              : "Semantic replay was cancelled without an explicit interruption",
          replayIndex
        );
        return;
      }

      try {
        this.replaceSemanticAccepted(
          replayIndex,
          transition,
          presentation.receipt
        );
      } catch (error) {
        this.finishSemanticReplayFailure(
          token,
          error instanceof Error
            ? error.message
            : "Semantic replay receipt could not replace the stored frontier",
          replayIndex
        );
        return;
      }
      this.semanticReplayPrefixLength = replayIndex + 1;
      this.committedScene = transition.target;
      this.provisionalScene = transition.target;
      this.committedSemanticScene = transition.semanticTarget;
      this.provisionalSemanticScene = transition.semanticTarget;
      this.sequence = record.event.sequence;
      this.publish();
    }

    if (this.currentToken !== token) return;
    const authoritativeFinal =
      this.acceptedSemantic[this.acceptedSemantic.length - 1];
    this.committedScene = authoritativeFinal.scene;
    this.provisionalScene = authoritativeFinal.scene;
    this.committedSemanticScene = authoritativeFinal.semanticScene;
    this.provisionalSemanticScene = authoritativeFinal.semanticScene;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.currentToken = null;
    this.semanticReplayPrefixLength = 0;
    this.phase = "completed";
    this.narration = "Replay reached the same accepted semantic frontier.";
    this.publish();
  }

  private semanticRecordMatchesEvent(record: AcceptedSemanticRevision): boolean {
    const semantic = record.event.semantic;
    return (
      record.presentation.type === "semantic_atom_presented" &&
      record.presentation.atomId === semantic.atomId &&
      record.presentation.nodeId === semantic.receipt.nodeId &&
      record.presentation.certificateSha256 ===
        semantic.certificate.certificateSha256 &&
      record.presentation.sceneRevision === record.scene.revision &&
      record.presentation.semanticRevision === record.semanticScene.revision &&
      record.presentation.appliedStepIds.length === 1 &&
      record.presentation.appliedStepIds[0] === semantic.receipt.nodeId
    );
  }

  private finishSemanticReplayFailure(
    token: RuntimeToken,
    detail: string,
    acceptedPrefixLength: number
  ): void {
    if (this.currentToken !== token) return;
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.semanticReplayPrefixLength = 0;
    this.active = null;
    this.queue = [];
    this.truncateSemanticHistory(acceptedPrefixLength);
    const lastAccepted =
      this.acceptedSemantic[this.acceptedSemantic.length - 1];
    this.committedScene = lastAccepted?.scene ?? EMPTY_SCENE;
    this.provisionalScene = this.committedScene;
    this.committedSemanticScene =
      lastAccepted?.semanticScene ?? EMPTY_SEMANTIC_SCENE;
    this.provisionalSemanticScene = this.committedSemanticScene;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.phase = "failed";
    this.completion = undefined;
    this.failure = runtimeFailure(
      "renderer_failed",
      "Replay could not establish a trustworthy semantic frontier. Reset it or replay the accepted prefix before continuing.",
      false
    );
    this.narration =
      "Replay integrity was lost. Reset or replay the accepted prefix before continuing.";
    console.warn("[LiveScene] Semantic replay failure:", detail);
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
        default:
          throw new SceneStreamRuntimeError(
            "invalid_event",
            "Semantic scene events are not accepted by the raw runtime"
          );
      }
    } catch (error) {
      this.failProtocol(
        token,
        error instanceof Error ? error.message : "Scene event validation failed"
      );
    }
  }

  private acceptSemanticStreamEvent(
    token: RuntimeToken,
    event: SemanticSceneStreamEvent
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
      this.failProtocol(
        token,
        "Semantic scene event generation does not match the active request"
      );
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
        case "semantic_scene_patch":
          this.acceptSemanticPatch(token, event);
          break;
        case "scene_stream_completed":
          this.acceptCompleted(token, event);
          break;
        case "semantic_scene_stream_declined":
          this.acceptSemanticDeclined(token, event);
          break;
        case "scene_stream_failed":
          this.acceptFailed(token, event);
          break;
        default:
          throw new SceneStreamRuntimeError(
            "invalid_event",
            "Raw scene patches are not accepted by the semantic runtime"
          );
      }
    } catch (error) {
      this.failProtocol(
        token,
        error instanceof Error
          ? error.message
          : "Semantic scene event validation failed"
      );
    }
  }

  private acceptStarted(token: RuntimeToken, attempt: number, baseRevision: number): void {
    if (
      this.attempt !== 0 ||
      attempt !== 1 ||
      baseRevision !== this.provisionalScene.revision ||
      !this.pairedProvisionalRevisionsAgree()
    ) {
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
      lastAcceptedRevision !== this.provisionalScene.revision ||
      !this.pairedProvisionalRevisionsAgree()
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
    this.queue.push(Object.freeze({ kind: "raw", event, target }));
    this.phase = "streaming";
    this.narration = event.patch.narration;
    this.publishIfCurrent(token);
    this.pump(token);
  }

  private acceptSemanticPatch(
    token: RuntimeToken,
    eventValue: SemanticScenePatchEvent
  ): void {
    if (
      this.attempt === 0 ||
      eventValue.attempt !== this.attempt ||
      eventValue.sequence !== this.sequence + 1 ||
      eventValue.baseRevision !== this.provisionalScene.revision ||
      eventValue.semantic.semanticBaseRevision !==
        this.provisionalSemanticScene.revision
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Semantic atom does not match the active attempt, sequence, or paired provisional revision"
      );
    }
    if (this.patchIds.has(eventValue.patch.patchId)) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Semantic patch ID was already accepted"
      );
    }
    if (this.queue.length >= this.queueLimit) {
      throw new SceneStreamRuntimeError(
        "queue_overflow",
        `Semantic atom queue exceeds ${this.queueLimit} entries`
      );
    }

    const event = decodeSemanticScenePatchEvent(eventValue);
    const applied = applySemanticScenePatch(
      this.provisionalScene,
      this.provisionalSemanticScene,
      event
    );
    this.provisionalScene = applied.scene;
    this.provisionalSemanticScene = applied.semanticScene;
    this.sequence = event.sequence;
    this.patchIds.add(event.patch.patchId);
    this.queue.push(
      Object.freeze({
        kind: "semantic",
        event,
        target: applied.scene,
        semanticTarget: applied.semanticScene,
        plan: applied.plan,
      })
    );
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
      event.repaired !== (this.attempt === 2) ||
      !this.pairedProvisionalRevisionsAgree()
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

  private acceptSemanticDeclined(
    token: RuntimeToken,
    event: SemanticSceneStreamDeclinedEvent
  ): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.finalRevision !== this.provisionalScene.revision ||
      this.sequence !== 0 ||
      this.active !== null ||
      this.queue.length !== 0 ||
      !this.pairedProvisionalRevisionsAgree()
    ) {
      throw new SceneStreamRuntimeError(
        "invalid_event",
        "Declined event does not match an unchanged semantic generation boundary"
      );
    }
    const control = this.requireControl(token);
    control.terminal = "declined";
    this.phase = "declined";
    this.narration = event.message;
    this.failure = undefined;
    this.completion = undefined;
    this.decline = Object.freeze({ reasonCode: event.reasonCode });
    this.publishIfCurrent(token);
  }

  private acceptFailed(token: RuntimeToken, event: SceneStreamFailedEvent): void {
    if (
      this.attempt === 0 ||
      event.attempt !== this.attempt ||
      event.lastAcceptedRevision !== this.provisionalScene.revision ||
      !this.pairedProvisionalRevisionsAgree()
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
    if (queued.kind === "semantic") {
      this.pumpSemantic(token, queued);
      return;
    }
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

    const transition: RawActiveTransition = {
      kind: "raw",
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

  private pumpSemantic(token: RuntimeToken, queued: SemanticQueuedPatch): void {
    if (
      queued.event.baseRevision !== this.committedScene.revision ||
      queued.event.semantic.semanticBaseRevision !==
        this.committedSemanticScene.revision
    ) {
      this.failSemanticRenderer(
        token,
        "Queued semantic atom no longer follows the paired committed frontier"
      );
      return;
    }

    let playback: MotionPlayback;
    try {
      playback = this.renderer.playMotionPlan(queued.plan, {
        staggerMs: this.staggerMs,
      });
    } catch {
      this.failSemanticRenderer(
        token,
        "The visual board could not start this semantic atom"
      );
      return;
    }

    const transition: SemanticActiveTransition = {
      kind: "semantic",
      token,
      source: "stream",
      target: queued.target,
      semanticTarget: queued.semanticTarget,
      playback,
      event: queued.event,
    };
    this.active = transition;
    this.publishIfCurrent(token);
    void playback.finished
      .then((outcome) => this.onSemanticPlaybackFinished(transition, outcome))
      .catch(() =>
        this.onSemanticPlaybackFinished(transition, {
          status: "failed",
          appliedStepIds: [],
        })
      );
  }

  private onPlaybackFinished(
    transition: RawActiveTransition,
    outcome: MotionPlaybackOutcome
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      transition.source !== "stream" ||
      transition.kind !== "raw"
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
    if (committedTransitionWork(transition, retained)) {
      this.appendAccepted(
        Object.freeze({
          ...transition.record,
          scene: retained,
          materialized: true,
        })
      );
    }
    this.renderer.cancelMotion();
    this.sequence = this.lastAcceptedSequence(this.generation);

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

  private onSemanticPlaybackFinished(
    transition: SemanticActiveTransition,
    outcome: MotionPlaybackOutcome
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      transition.source !== "stream"
    ) {
      return;
    }

    if (this.pendingInterruptToken === transition.token) {
      this.finishSemanticInterruption(transition, outcome);
      return;
    }

    this.active = null;
    const presentation = evaluateSemanticPresentation(transition, outcome);
    if (
      presentation.kind !== "presented" ||
      presentation.receipt.settlement !== "completed"
    ) {
      this.failSemanticRenderer(
        transition.token,
        presentation.kind === "not_presented"
          ? "Semantic playback ended without presenting its target node"
          : presentation.kind === "invalid"
            ? "Semantic playback returned an invalid presentation receipt"
            : "Semantic playback was cancelled without an explicit interruption"
      );
      return;
    }

    try {
      this.appendSemanticAccepted(transition, presentation.receipt);
    } catch (error) {
      this.failSemanticRenderer(
        transition.token,
        error instanceof Error
          ? error.message
          : "Semantic acceptance history could not advance"
      );
      return;
    }
    this.committedScene = transition.target;
    this.committedSemanticScene = transition.semanticTarget;
    this.publish();
    this.pump(transition.token);
    this.settlePhase();
    this.publishIfCurrent(transition.token);
  }

  private interruptSemantic(): boolean {
    if (this.pendingInterruptToken) return true;

    const active = this.active;
    const control = this.streamControl;
    this.streamControl = null;
    control?.controller.abort();
    this.queue = [];
    this.failure = undefined;
    this.completion = undefined;

    if (active?.kind === "semantic") {
      this.pendingInterruptToken = active.token;
      this.provisionalScene = active.target;
      this.provisionalSemanticScene = active.semanticTarget;
      this.phase = "interrupting";
      this.narration = "Settling the visible semantic atom…";
      this.publish();
      try {
        // The synchronous outcome is intentionally ignored. Only `finished`
        // crosses the renderer's post-paint presentation barrier.
        active.playback.cancel();
      } catch (error) {
        console.warn("[LiveScene] Semantic playback cancellation threw:", error);
      }
      return true;
    }

    if (this.currentToken?.kind === "replay") {
      this.truncateSemanticHistory(this.semanticReplayPrefixLength);
    }
    this.currentToken = null;
    this.active = null;
    this.semanticReplayPrefixLength = 0;
    this.provisionalScene = this.committedScene;
    this.provisionalSemanticScene = this.committedSemanticScene;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.phase = "interrupted";
    this.narration = "Generation interrupted. The last presented atom is safe.";
    this.publish();
    return true;
  }

  private finishSemanticInterruption(
    transition: SemanticActiveTransition,
    outcome: MotionPlaybackOutcome
  ): void {
    if (
      this.currentToken !== transition.token ||
      this.active !== transition ||
      this.pendingInterruptToken !== transition.token
    ) {
      return;
    }

    const presentation = evaluateSemanticPresentation(transition, outcome);
    let invalid = presentation.kind === "invalid";
    if (presentation.kind === "presented") {
      if (transition.source === "stream") {
        try {
          this.appendSemanticAccepted(transition, presentation.receipt);
        } catch (error) {
          invalid = true;
          console.warn("[LiveScene] Semantic frontier could not advance:", error);
        }
      } else {
        try {
          const replayIndex = transition.replayIndex ?? -1;
          this.replaceSemanticAccepted(
            replayIndex,
            transition,
            presentation.receipt
          );
          this.semanticReplayPrefixLength = replayIndex + 1;
          this.truncateSemanticHistory(this.semanticReplayPrefixLength);
        } catch (error) {
          invalid = true;
          console.warn(
            "[LiveScene] Semantic replay frontier could not advance:",
            error
          );
        }
      }
      if (!invalid) {
        this.committedScene = transition.target;
        this.committedSemanticScene = transition.semanticTarget;
      }
    } else if (transition.source === "replay") {
      this.truncateSemanticHistory(transition.replayIndex ?? 0);
    }

    this.active = null;
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.semanticReplayPrefixLength = 0;
    this.queue = [];
    this.provisionalScene = this.committedScene;
    this.provisionalSemanticScene = this.committedSemanticScene;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.completion = undefined;

    if (invalid) {
      this.phase = "failed";
      this.failure = runtimeFailure(
        "renderer_failed",
        "The visible board no longer has a trustworthy semantic frontier. Reset it or replay the accepted prefix before continuing.",
        false
      );
      this.narration =
        "Presentation integrity was lost. Reset or replay the accepted prefix before continuing.";
    } else {
      this.phase = "interrupted";
      this.failure = undefined;
      this.narration =
        "Generation interrupted. The last presented semantic atom is safe.";
    }
    this.publish();
  }

  private appendSemanticAccepted(
    transition: SemanticActiveTransition,
    presentation: SemanticPresentationReceipt
  ): void {
    if (this.accepted.length !== this.acceptedSemantic.length) {
      throw new Error("Paired semantic acceptance ledgers are out of sync");
    }
    if (this.acceptedSemantic.length >= LIVE_SCENE_MAX_NODES) {
      throw new Error(
        `Semantic acceptance history exceeds ${LIVE_SCENE_MAX_NODES} atoms`
      );
    }

    const rawRecord = acceptedRecord(
      transition.event,
      transition.target,
      presentation.settlement === "cancelled"
    );
    const semanticRecord: AcceptedSemanticRevision = Object.freeze({
      scene: transition.target,
      semanticScene: transition.semanticTarget,
      event: transition.event,
      presentation,
    });
    this.accepted = [...this.accepted, rawRecord];
    this.acceptedSemantic = [...this.acceptedSemantic, semanticRecord];
  }

  private replaceSemanticAccepted(
    index: number,
    transition: SemanticActiveTransition,
    presentation: SemanticPresentationReceipt
  ): void {
    if (
      index < 0 ||
      index >= this.acceptedSemantic.length ||
      this.accepted.length !== this.acceptedSemantic.length
    ) {
      throw new Error("Semantic replay index is outside the paired ledger");
    }
    const rawRecord = acceptedRecord(
      transition.event,
      transition.target,
      presentation.settlement === "cancelled"
    );
    const semanticRecord: AcceptedSemanticRevision = Object.freeze({
      scene: transition.target,
      semanticScene: transition.semanticTarget,
      event: transition.event,
      presentation,
    });
    this.accepted = this.accepted.map((record, recordIndex) =>
      recordIndex === index ? rawRecord : record
    );
    this.acceptedSemantic = this.acceptedSemantic.map((record, recordIndex) =>
      recordIndex === index ? semanticRecord : record
    );
  }

  private truncateSemanticHistory(prefixLength: number): void {
    const safeLength = Math.max(
      0,
      Math.min(prefixLength, this.acceptedSemantic.length)
    );
    this.accepted = this.accepted.slice(0, safeLength);
    this.acceptedSemantic = this.acceptedSemantic.slice(0, safeLength);
  }

  private retainedScene(
    transition: RawActiveTransition,
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
    transition: RawActiveTransition,
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
    transition: RawActiveTransition,
    retained: SceneState,
    committed: boolean
  ): void {
    if (transition.source !== "replay" || transition.replayIndex === undefined) return;
    if (!committed) {
      this.accepted = this.accepted.slice(0, transition.replayIndex);
      return;
    }
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

  private failSemanticRenderer(token: RuntimeToken, detail: string): void {
    if (this.currentToken !== token) return;
    const control = this.streamControl;
    this.streamControl = null;
    control?.controller.abort();
    this.currentToken = null;
    this.pendingInterruptToken = null;
    this.semanticReplayPrefixLength = 0;
    this.active = null;
    this.queue = [];
    this.provisionalScene = this.committedScene;
    this.provisionalSemanticScene = this.committedSemanticScene;
    this.sequence = this.lastAcceptedSequence(this.generation);
    this.phase = "failed";
    this.completion = undefined;
    this.failure = runtimeFailure(
      "renderer_failed",
      "The visible board no longer has a trustworthy semantic frontier. Reset it or replay the accepted prefix before continuing.",
      false
    );
    this.narration =
      "Presentation integrity was lost. Reset or replay the accepted prefix before continuing.";
    console.warn("[LiveScene] Semantic renderer failure:", detail);
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

  private pairedProvisionalRevisionsAgree(): boolean {
    return (
      this.protocol === "raw" ||
      this.provisionalScene.revision === this.provisionalSemanticScene.revision
    );
  }

  private createToken(kind: RuntimeToken["kind"], generation: number): RuntimeToken {
    return Object.freeze({ id: ++this.tokenSequence, kind, generation });
  }

  private invalidateCurrentToken(abort: boolean): void {
    if (abort) this.streamControl?.controller.abort();
    this.currentToken = null;
    this.streamControl = null;
    this.pendingInterruptToken = null;
    this.semanticReplayPrefixLength = 0;
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
    const replayingSemanticPrefix =
      this.protocol === "semantic" && this.currentToken?.kind === "replay";
    const visibleAccepted = replayingSemanticPrefix
      ? this.accepted.slice(0, this.semanticReplayPrefixLength)
      : this.accepted;
    const visibleSemanticAccepted = replayingSemanticPrefix
      ? this.acceptedSemantic.slice(0, this.semanticReplayPrefixLength)
      : this.acceptedSemantic;
    const semantic =
      this.protocol === "semantic"
        ? Object.freeze({
            committedScene: this.committedSemanticScene,
            provisionalScene: this.provisionalSemanticScene,
            accepted: Object.freeze([...visibleSemanticAccepted]),
            ...(visibleSemanticAccepted.length > 0
              ? {
                  commitFrontier:
                    visibleSemanticAccepted[visibleSemanticAccepted.length - 1]
                      .presentation,
                }
              : {}),
          })
        : undefined;
    return Object.freeze({
      phase: this.phase,
      generation: this.generation,
      attempt: this.attempt,
      sequence: this.sequence,
      committedScene: this.committedScene,
      provisionalScene: this.provisionalScene,
      accepted: Object.freeze([...visibleAccepted]),
      queuedPatchCount: this.queue.length,
      ...(this.active ? { activeRevision: this.active.target.revision } : {}),
      narration: this.narration,
      ...(this.failure ? { error: this.failure } : {}),
      ...(this.completion ? { completion: this.completion } : {}),
      ...(this.decline ? { decline: this.decline } : {}),
      ...(semantic ? { semantic } : {}),
    });
  }

  private assertUsable(): void {
    if (this.disposed) throw new Error("SceneStreamRuntime has been disposed");
  }
}
