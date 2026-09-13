import {
  decodePairedProjectileComparisonSpecV1,
  decodeSemanticStoryboardDirectorPromptV1,
  storyboardHasForwardCapacity,
  type AcceptedSemanticStoryboardRecordV1,
  type PairedProjectileComparisonSpecV1,
} from "@/lib/live-scene/semantic-storyboard";

import {
  SemanticStoryboardStreamRuntime,
  type SemanticStoryboardRuntimeSnapshot,
} from "./semantic-storyboard-stream-runtime";

export type SemanticStoryboardSessionStatus =
  | "ready"
  | "anchoring"
  | "director_handoff"
  | "directing"
  | "interrupting"
  | "replaying"
  | "paused"
  | "declined"
  | "failed";

export type SemanticStoryboardSessionRoute = "reflex" | "director";

export interface SemanticStoryboardOpenProgress {
  readonly kind: "open";
  readonly settledBeatCount: number;
  readonly frontierStatus: "live" | "paused";
  readonly recentCertifiedLabels: readonly string[];
  readonly progressAriaLabel: string;
}

export interface SemanticStoryboardSessionControls {
  readonly canStartFresh: boolean;
  readonly canContinue: boolean;
  readonly canInterrupt: boolean;
  readonly canReplay: boolean;
  readonly canReset: boolean;
}

export type SemanticStoryboardOrchestrationErrorCode =
  "invalid_anchor" | "director_handoff_failed";

export interface SemanticStoryboardOrchestrationError {
  readonly code: SemanticStoryboardOrchestrationErrorCode;
  readonly message: string;
}

export interface SemanticStoryboardSessionSnapshot {
  readonly runtime: SemanticStoryboardRuntimeSnapshot;
  readonly status: SemanticStoryboardSessionStatus;
  readonly lastRoute: SemanticStoryboardSessionRoute | null;
  readonly pendingDirector: boolean;
  readonly problemSpec: PairedProjectileComparisonSpecV1 | null;
  readonly progress: SemanticStoryboardOpenProgress;
  readonly controls: SemanticStoryboardSessionControls;
  readonly orchestrationError: SemanticStoryboardOrchestrationError | null;
}

export interface SemanticStoryboardFreshSessionInput {
  readonly problemSpec: unknown;
  readonly prompt: unknown;
}

export interface SemanticStoryboardSessionControllerOptions {
  readonly runtime: SemanticStoryboardStreamRuntime;
  /** Must enqueue rather than synchronously invoke the task. */
  readonly enqueue?: (task: () => void) => void;
}

export type SemanticStoryboardSessionErrorCode =
  "session_busy" | "session_reset_required" | "storyboard_unavailable";

export class SemanticStoryboardSessionError extends Error {
  readonly code: SemanticStoryboardSessionErrorCode;

  constructor(code: SemanticStoryboardSessionErrorCode, message: string) {
    super(message);
    this.name = "SemanticStoryboardSessionError";
    this.code = code;
  }
}

interface PendingDirector {
  readonly epoch: number;
  readonly anchorGeneration: number;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly prompt: string;
}

const ACTIVE_STATUSES = new Set<SemanticStoryboardSessionStatus>([
  "anchoring",
  "director_handoff",
  "directing",
  "interrupting",
  "replaying",
]);

const EXTENDING_STATUSES = new Set<SemanticStoryboardSessionStatus>([
  "anchoring",
  "director_handoff",
  "directing",
  "interrupting",
  "replaying",
]);

const RECORD_LABELS = Object.freeze({
  reveal: Object.freeze({
    range_formula: "Range formula revealed",
    complementary_angles: "Complementary angles revealed",
  }),
  trace: Object.freeze({
    lower_angle: "Lower trajectory traced",
    higher_angle: "Higher trajectory traced",
  }),
  relate: Object.freeze({
    equal_range: "Equal range connected",
    unequal_range: "Unequal range connected",
    higher_apex: "Higher apex connected",
    longer_flight: "Longer flight connected",
  }),
});

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Turn one closed semantic record into stable, presentation-only copy. */
export function semanticStoryboardRecordLabel(
  record: AcceptedSemanticStoryboardRecordV1,
): string {
  if (record.act === "reveal") return RECORD_LABELS.reveal[record.conceptId];
  if (record.act === "trace") return RECORD_LABELS.trace[record.trajectoryId];
  return RECORD_LABELS.relate[record.claimId];
}

function acceptedModelRecords(
  runtime: SemanticStoryboardRuntimeSnapshot,
): readonly AcceptedSemanticStoryboardRecordV1[] {
  return runtime.accepted.flatMap((accepted) => {
    const checkpoint = accepted.event.transition.checkpoint;
    return checkpoint.checkpointOrigin === "model_record" && checkpoint.beat
      ? [checkpoint.beat.record]
      : [];
  });
}

function currentProblem(
  runtime: SemanticStoryboardRuntimeSnapshot,
): PairedProjectileComparisonSpecV1 | null {
  return runtime.committedSemanticScene.components[0]?.problemSpec ?? null;
}

function requiresReset(runtime: SemanticStoryboardRuntimeSnapshot): boolean {
  return (
    runtime.phase === "failed" &&
    (!runtime.rendererTrusted || runtime.error?.retryable === false)
  );
}

function isExactSettledAnchor(
  runtime: SemanticStoryboardRuntimeSnapshot,
  pending: PendingDirector,
): boolean {
  const accepted = runtime.accepted[0];
  const checkpoint = accepted?.event.transition.checkpoint;
  const component = runtime.committedSemanticScene.components[0];
  return (
    runtime.phase === "completed" &&
    runtime.generation === pending.anchorGeneration &&
    runtime.rendererTrusted &&
    runtime.completion?.metadata?.reasonCode === "anchor" &&
    runtime.completion.metadata.detailCode === null &&
    runtime.accepted.length === 1 &&
    checkpoint?.checkpointOrigin === "anchor" &&
    checkpoint.beat === null &&
    runtime.committedScene.revision === 1 &&
    runtime.committedSemanticScene.revision === 1 &&
    runtime.provisionalScene.revision === 1 &&
    runtime.provisionalSemanticScene.revision === 1 &&
    runtime.committedSemanticScene.components.length === 1 &&
    component?.acceptedRecords.length === 0 &&
    same(component?.problemSpec, pending.problemSpec)
  );
}

/**
 * Coordinates the two-generation storyboard interaction without owning any
 * transport, playback, certification, or renderer state.
 */
export class SemanticStoryboardSessionController {
  private readonly runtime: SemanticStoryboardStreamRuntime;
  private readonly enqueue: (task: () => void) => void;
  private readonly listeners = new Set<() => void>();
  private readonly unsubscribeRuntime: () => void;

  private epoch = 0;
  private pending: PendingDirector | null = null;
  private handoffScheduled = false;
  private lastRoute: SemanticStoryboardSessionRoute | null = null;
  private problemSpec: PairedProjectileComparisonSpecV1 | null = null;
  private orchestrationError: SemanticStoryboardOrchestrationError | null =
    null;
  private disposed = false;
  private snapshot: SemanticStoryboardSessionSnapshot;

  constructor(options: SemanticStoryboardSessionControllerOptions) {
    this.runtime = options.runtime;
    this.enqueue =
      options.enqueue ?? ((task) => globalThis.queueMicrotask(task));
    this.snapshot = this.buildSnapshot();
    this.unsubscribeRuntime = this.runtime.subscribe(this.onRuntimeChange);
  }

  getSnapshot = (): SemanticStoryboardSessionSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.assertUsable();
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  startFresh(input: SemanticStoryboardFreshSessionInput): number {
    this.assertUsable();
    const problemSpec = decodePairedProjectileComparisonSpecV1(
      input.problemSpec,
    );
    const prompt = decodeSemanticStoryboardDirectorPromptV1(input.prompt);
    if (!this.snapshot.controls.canStartFresh) {
      this.rejectUnavailable("A storyboard session is already active.");
    }

    const previous = {
      pending: this.pending,
      handoffScheduled: this.handoffScheduled,
      lastRoute: this.lastRoute,
      problemSpec: this.problemSpec,
      orchestrationError: this.orchestrationError,
    };
    const epoch = ++this.epoch;
    const anchorGeneration = this.runtime.getSnapshot().generation + 1;
    this.pending = Object.freeze({
      epoch,
      anchorGeneration,
      problemSpec,
      prompt,
    });
    this.handoffScheduled = false;
    this.lastRoute = "reflex";
    this.problemSpec = problemSpec;
    this.orchestrationError = null;
    try {
      const generation = this.runtime.start({
        routingMode: "reflex",
        problemSpec,
      });
      if (generation !== anchorGeneration) {
        throw new Error("storyboard anchor generation changed unexpectedly");
      }
      return generation;
    } catch (error) {
      this.pending = previous.pending;
      this.handoffScheduled = previous.handoffScheduled;
      this.lastRoute = previous.lastRoute;
      this.problemSpec = previous.problemSpec;
      this.orchestrationError = previous.orchestrationError;
      this.publish();
      throw error;
    }
  }

  continueWithPrompt(promptValue: unknown): number {
    this.assertUsable();
    const prompt = decodeSemanticStoryboardDirectorPromptV1(promptValue);
    if (ACTIVE_STATUSES.has(this.snapshot.status)) {
      throw new SemanticStoryboardSessionError(
        "session_busy",
        "Wait for the current storyboard action to settle.",
      );
    }
    if (requiresReset(this.runtime.getSnapshot())) {
      throw new SemanticStoryboardSessionError(
        "session_reset_required",
        "Reset the storyboard before continuing.",
      );
    }
    const component =
      this.runtime.getSnapshot().committedSemanticScene.components[0];
    if (
      !component ||
      this.orchestrationError ||
      !storyboardHasForwardCapacity(
        component.problemSpec,
        component.acceptedRecords,
      )
    ) {
      throw new SemanticStoryboardSessionError(
        "storyboard_unavailable",
        "This storyboard has no verified continuation frontier.",
      );
    }

    const previousRoute = this.lastRoute;
    const previousProblem = this.problemSpec;
    const previousError = this.orchestrationError;
    this.invalidatePendingDirector();
    this.lastRoute = "director";
    this.problemSpec = component.problemSpec;
    this.orchestrationError = null;
    try {
      return this.runtime.start({
        routingMode: "director",
        problemSpec: component.problemSpec,
        prompt,
      });
    } catch (error) {
      this.lastRoute = previousRoute;
      this.problemSpec = previousProblem;
      this.orchestrationError = previousError;
      this.publish();
      throw error;
    }
  }

  interrupt(): boolean {
    this.assertUsable();
    const cancelledHandoff = this.pending !== null;
    this.invalidatePendingDirector();
    const interrupted = this.runtime.interrupt();
    if (cancelledHandoff && !interrupted) this.publish();
    return cancelledHandoff || interrupted;
  }

  replayAccepted(): Promise<void> {
    this.assertUsable();
    if (requiresReset(this.runtime.getSnapshot()) || this.orchestrationError) {
      throw new SemanticStoryboardSessionError(
        "session_reset_required",
        "Reset the storyboard before replaying it.",
      );
    }
    const cancelledHandoff = this.pending !== null;
    this.invalidatePendingDirector();
    if (cancelledHandoff) this.publish();
    return this.runtime.replayAccepted();
  }

  reset(): void {
    this.assertUsable();
    this.invalidatePendingDirector();
    this.lastRoute = null;
    this.problemSpec = null;
    this.orchestrationError = null;
    this.runtime.reset();
  }

  dispose(): void {
    if (this.disposed) return;
    this.invalidatePendingDirector();
    this.unsubscribeRuntime();
    this.listeners.clear();
    this.disposed = true;
    this.runtime.dispose();
  }

  private readonly onRuntimeChange = (): void => {
    if (this.disposed) return;
    const runtime = this.runtime.getSnapshot();
    const pending = this.pending;
    if (pending) {
      if (runtime.generation > pending.anchorGeneration) {
        this.invalidatePendingDirector();
      } else if (runtime.generation === pending.anchorGeneration) {
        if (runtime.phase === "completed") {
          if (isExactSettledAnchor(runtime, pending)) {
            this.scheduleDirectorHandoff(pending);
          } else {
            this.failInvalidAnchor();
          }
        } else if (runtime.phase === "declined") {
          this.failInvalidAnchor();
        } else if (
          runtime.phase === "failed" ||
          runtime.phase === "interrupted" ||
          runtime.phase === "idle"
        ) {
          this.invalidatePendingDirector();
        }
      }
    }
    this.publish();
  };

  private scheduleDirectorHandoff(pending: PendingDirector): void {
    if (this.handoffScheduled) return;
    this.handoffScheduled = true;
    this.enqueue(() => this.dispatchPendingDirector(pending));
  }

  private dispatchPendingDirector(pending: PendingDirector): void {
    if (
      this.disposed ||
      this.pending !== pending ||
      this.epoch !== pending.epoch
    ) {
      return;
    }
    const runtime = this.runtime.getSnapshot();
    if (!isExactSettledAnchor(runtime, pending)) {
      if (
        runtime.generation === pending.anchorGeneration &&
        runtime.phase === "completed"
      ) {
        this.failInvalidAnchor();
        this.publish();
      }
      return;
    }

    this.pending = null;
    this.handoffScheduled = false;
    this.lastRoute = "director";
    try {
      this.runtime.start({
        routingMode: "director",
        problemSpec: pending.problemSpec,
        prompt: pending.prompt,
      });
    } catch (error) {
      this.orchestrationError = Object.freeze({
        code: "director_handoff_failed",
        message: `The storyboard anchor settled, but its continuation could not start: ${message(error)}`,
      });
      this.publish();
    }
  }

  private failInvalidAnchor(): void {
    this.invalidatePendingDirector();
    this.orchestrationError = Object.freeze({
      code: "invalid_anchor",
      message:
        "The opening frame did not settle at its certified anchor. Reset the storyboard before continuing.",
    });
  }

  private invalidatePendingDirector(): void {
    this.epoch += 1;
    this.pending = null;
    this.handoffScheduled = false;
  }

  private rejectUnavailable(messageText: string): never {
    if (requiresReset(this.runtime.getSnapshot()) || this.orchestrationError) {
      throw new SemanticStoryboardSessionError(
        "session_reset_required",
        "Reset the storyboard before starting again.",
      );
    }
    throw new SemanticStoryboardSessionError("session_busy", messageText);
  }

  private buildSnapshot(): SemanticStoryboardSessionSnapshot {
    const runtime = this.runtime.getSnapshot();
    const status = this.status(runtime);
    const records = acceptedModelRecords(runtime);
    const labels = Object.freeze(
      records.slice(-2).map(semanticStoryboardRecordLabel),
    );
    const component = runtime.committedSemanticScene.components[0];
    const busy = ACTIVE_STATUSES.has(status);
    const resetRequired =
      requiresReset(runtime) || Boolean(this.orchestrationError);
    const hasForwardCapacity = component
      ? storyboardHasForwardCapacity(
          component.problemSpec,
          component.acceptedRecords,
        )
      : false;
    const progress = Object.freeze({
      kind: "open",
      settledBeatCount: records.length,
      frontierStatus: EXTENDING_STATUSES.has(status) ? "live" : "paused",
      recentCertifiedLabels: labels,
      progressAriaLabel: `${records.length} certified ${records.length === 1 ? "beat" : "beats"} settled; frontier ${EXTENDING_STATUSES.has(status) ? "live" : "paused"}`,
    } as const satisfies SemanticStoryboardOpenProgress);
    const controls = Object.freeze({
      canStartFresh:
        !busy &&
        !resetRequired &&
        runtime.accepted.length === 0 &&
        currentProblem(runtime) === null,
      canContinue:
        !busy &&
        !resetRequired &&
        runtime.rendererTrusted &&
        Boolean(component) &&
        hasForwardCapacity,
      canInterrupt:
        status === "anchoring" ||
        status === "director_handoff" ||
        status === "directing" ||
        status === "replaying",
      canReplay:
        !busy &&
        !resetRequired &&
        runtime.rendererTrusted &&
        runtime.accepted.length > 0,
      canReset:
        runtime.generation > 0 ||
        runtime.accepted.length > 0 ||
        this.problemSpec !== null ||
        this.orchestrationError !== null,
    });
    return Object.freeze({
      runtime,
      status,
      lastRoute: this.lastRoute,
      pendingDirector: this.pending !== null,
      problemSpec: this.problemSpec ?? currentProblem(runtime),
      progress,
      controls,
      orchestrationError: this.orchestrationError,
    });
  }

  private status(
    runtime: SemanticStoryboardRuntimeSnapshot,
  ): SemanticStoryboardSessionStatus {
    if (this.orchestrationError) return "failed";
    if (runtime.phase === "replaying") return "replaying";
    if (runtime.phase === "interrupting") return "interrupting";
    if (this.pending && this.handoffScheduled) return "director_handoff";
    if (
      runtime.phase === "connecting" ||
      runtime.phase === "streaming" ||
      runtime.phase === "repairing" ||
      runtime.phase === "completing"
    ) {
      return this.lastRoute === "reflex" ? "anchoring" : "directing";
    }
    if (runtime.phase === "declined") return "declined";
    if (runtime.phase === "failed") return "failed";
    if (runtime.phase === "idle") return "ready";
    return "paused";
  }

  private publish(): void {
    this.snapshot = this.buildSnapshot();
    for (const listener of [...this.listeners]) {
      try {
        listener();
      } catch (error) {
        console.error(
          "[LiveScene] Storyboard session subscriber failed:",
          error,
        );
      }
    }
  }

  private assertUsable(): void {
    if (this.disposed) {
      throw new Error("SemanticStoryboardSessionController was disposed");
    }
  }
}
