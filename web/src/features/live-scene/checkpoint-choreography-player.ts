import type { SVGCanvasHandle } from "@/features/canvas/types";
import type {
  ChoreographyCueKindV2,
  ChoreographyPlan,
  PlannedCheckpointChoreography,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";

import type {
  ChoreographyExecutorObserver,
  ChoreographyExecutorSignal,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import { decodeChoreographyPlaybackOutcome } from "./choreography-playback";

export type CheckpointChoreographyRenderer = Pick<
  SVGCanvasHandle,
  | "playCheckpointChoreography"
  | "materializeScene"
  | "materializeViewport"
  | "cancelMotion"
  | "clear"
>;

export interface PlayableCheckpoint {
  readonly plan: PlannedCheckpointChoreography<ChoreographyPlan>;
  readonly base: { readonly viewport: ViewportPoseV1 };
  readonly bootstrappedViewport: boolean;
}

interface PlaybackSignals {
  cueIndex: number;
  firstCueCount: number;
  settlement: "completed" | "cancelled_to_checkpoint" | null;
  invalid: Error | null;
  closed: boolean;
}

export interface CheckpointPlaybackSession<
  Prepared extends PlayableCheckpoint = PlayableCheckpoint,
> {
  readonly prepared: Prepared;
  readonly playback: ChoreographyPlayback;
  readonly signals: PlaybackSignals;
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function playbackHandle(value: unknown): ChoreographyPlayback {
  if (typeof value !== "object" || value === null) {
    throw new TypeError("Renderer returned no checkpoint playback handle");
  }
  const candidate = value as Partial<ChoreographyPlayback>;
  if (
    typeof candidate.cancel !== "function" ||
    !candidate.finished ||
    typeof candidate.finished.then !== "function" ||
    !candidate.firstCuePresented ||
    typeof candidate.firstCuePresented.then !== "function"
  ) {
    throw new TypeError(
      "Renderer returned an invalid checkpoint playback handle",
    );
  }
  return candidate as ChoreographyPlayback;
}

/** Protocol-neutral owner of renderer evidence, viewport bootstrap, and rollback. */
export class CheckpointChoreographyPlayer {
  private viewportInitialized = false;
  private initializedViewport: ViewportPoseV1 | null = null;

  constructor(private readonly renderer: CheckpointChoreographyRenderer) {}

  start<Prepared extends PlayableCheckpoint>(
    prepared: Prepared,
    onFirstPresented: () => void,
  ): CheckpointPlaybackSession<Prepared> {
    this.bootstrapViewport(prepared);
    const signals: PlaybackSignals = {
      cueIndex: 0,
      firstCueCount: 0,
      settlement: null,
      invalid: null,
      closed: false,
    };
    const observer: ChoreographyExecutorObserver<ChoreographyCueKindV2> = (
      signal,
    ) => {
      if (signals.closed) return;
      try {
        this.acceptSignal(prepared, signals, signal);
      } catch (error) {
        signals.invalid =
          error instanceof Error ? error : new Error(String(error));
        throw signals.invalid;
      }
      if (signal.type === "firstCuePresented") onFirstPresented();
    };
    return Object.freeze({
      prepared,
      playback: playbackHandle(
        // The canvas handle keeps its legacy V1 default; its executor is generic.
        // Contain the additive V2 widening at this protocol-neutral boundary.
        (
          this.renderer.playCheckpointChoreography as unknown as (
            plan: PlannedCheckpointChoreography<ChoreographyPlan>,
            observer: ChoreographyExecutorObserver<ChoreographyCueKindV2>,
          ) => ChoreographyPlayback
        )(prepared.plan, observer),
      ),
      signals,
    });
  }

  close(session: CheckpointPlaybackSession): void {
    session.signals.closed = true;
  }

  validSettlement(
    session: CheckpointPlaybackSession,
    outcome: ChoreographyPlaybackOutcome,
    expected: "completed" | "cancelled_to_checkpoint",
  ): boolean {
    return (
      !session.signals.invalid &&
      outcome.status === expected &&
      outcome.firstCuePresented &&
      session.signals.firstCueCount === 1 &&
      session.signals.settlement === expected &&
      (expected === "cancelled_to_checkpoint" ||
        session.signals.cueIndex ===
          session.prepared.plan.choreographyPlan.phase.cues.length)
    );
  }

  cancelledBeforePresented(
    session: CheckpointPlaybackSession,
    outcome: ChoreographyPlaybackOutcome,
  ): boolean {
    return (
      outcome.status === "cancelled_before_presented" &&
      !outcome.firstCuePresented &&
      session.signals.firstCueCount === 0 &&
      session.signals.settlement === null &&
      !session.signals.invalid
    );
  }

  outcome(
    playback: ChoreographyPlayback,
  ): Promise<ChoreographyPlaybackOutcome> {
    return Promise.resolve(playback.finished as unknown)
      .then((value) => decodeChoreographyPlaybackOutcome(value))
      .catch((error: unknown) => ({
        status: "failed" as const,
        firstCuePresented: false,
        error: message(error),
      }));
  }

  restore(frontier: {
    readonly scene: SceneState;
    readonly viewport: ViewportPoseV1 | null;
  }): string | undefined {
    try {
      this.renderer.cancelMotion();
      this.renderer.clear();
      this.renderer.materializeScene(frontier.scene);
      if (frontier.viewport) {
        this.renderer.materializeViewport(frontier.viewport);
        this.viewportInitialized = true;
        this.initializedViewport = frontier.viewport;
      } else {
        this.viewportInitialized = false;
        this.initializedViewport = null;
      }
      return undefined;
    } catch (error) {
      return message(error);
    }
  }

  clear(): void {
    this.renderer.cancelMotion();
    this.renderer.clear();
    this.viewportInitialized = false;
    this.initializedViewport = null;
  }

  materializeEmpty(scene: SceneState): void {
    this.clear();
    this.renderer.materializeScene(scene);
  }

  cancel(playback?: ChoreographyPlayback): void {
    playback?.cancel();
  }

  cancelMotion(): void {
    this.renderer.cancelMotion();
  }

  private bootstrapViewport(prepared: PlayableCheckpoint): void {
    if (!prepared.bootstrappedViewport) return;
    if (!this.viewportInitialized) {
      this.renderer.materializeViewport(prepared.base.viewport);
      this.viewportInitialized = true;
      this.initializedViewport = prepared.base.viewport;
      return;
    }
    if (!same(this.initializedViewport, prepared.base.viewport)) {
      throw new Error("certified bootstrap viewport changed");
    }
  }

  private acceptSignal(
    prepared: PlayableCheckpoint,
    signals: PlaybackSignals,
    signal: ChoreographyExecutorSignal<ChoreographyCueKindV2>,
  ): void {
    if (signals.invalid) throw signals.invalid;
    if (signals.settlement) {
      throw new Error("a settled checkpoint emitted another signal");
    }
    if (signal.type === "cueStarted") {
      const expected =
        prepared.plan.choreographyPlan.phase.cues[signals.cueIndex]?.cue;
      if (signal.cue !== expected) {
        throw new Error("renderer cue order does not match the certified plan");
      }
      signals.cueIndex += 1;
      return;
    }
    if (signal.type === "firstCuePresented") {
      if (signals.cueIndex === 0 || signals.firstCueCount > 0) {
        throw new Error(
          "first presentation signal crossed an invalid boundary",
        );
      }
      signals.firstCueCount += 1;
      return;
    }
    signals.settlement = signal.settlement;
  }
}
