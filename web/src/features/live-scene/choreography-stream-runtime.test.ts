import { describe, expect, it, vi } from "vitest";

import type {
  PlannedCheckpointChoreography,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";

import fixtureValue from "./fixtures/completing-the-square.v1.json";
import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import {
  decodeChoreographySceneStreamEvent,
  type ChoreographySceneCheckpointEvent,
  type ChoreographySceneStreamEvent,
  type ChoreographySceneStreamRunInvocation,
  type ChoreographySceneStreamRunner,
} from "./choreography-model-stream";
import type { AcceptedChoreographyRevision } from "./choreography-playback";
import type { ChoreographySceneStreamRenderer } from "./choreography-stream-runtime";
import { SceneStreamRuntime, SceneStreamRuntimeError } from "./stream-runtime";

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<Value>((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

const MAIN_EVENTS = fixtureValue.events.map((event) =>
  decodeChoreographySceneStreamEvent(event),
);
const MAIN_STARTED = MAIN_EVENTS[0];
const MAIN_CHECKPOINTS = MAIN_EVENTS.filter(
  (event): event is ChoreographySceneCheckpointEvent =>
    event.type === "choreography_scene_checkpoint",
);
const MAIN_COMPLETED = MAIN_EVENTS.at(-1)!;
const ADAPTIVE_EVENTS = fixtureValue.adaptiveTranscript.events.map((event) =>
  decodeChoreographySceneStreamEvent(event),
);

function completed(
  generation: number,
  finalRevision: number,
  patchCount: number,
): ChoreographySceneStreamEvent {
  return decodeChoreographySceneStreamEvent({
    type: "scene_stream_completed",
    generation,
    finalRevision,
    patchCount,
    firstPatchMs: 24,
    totalMs: 72,
    repaired: false,
  });
}

class ControlledPlayback implements ChoreographyPlayback {
  readonly firstCuePresented = Promise.resolve(false);
  readonly finished: Promise<ChoreographyPlaybackOutcome>;
  readonly cancel = vi.fn();
  private readonly terminal = deferred<ChoreographyPlaybackOutcome>();
  private settled = false;

  constructor() {
    this.finished = this.terminal.promise;
  }

  settle(outcome: ChoreographyPlaybackOutcome): void {
    if (this.settled) return;
    this.settled = true;
    this.terminal.resolve(outcome);
  }

  fail(error = new Error("presentation barrier rejected")): void {
    if (this.settled) return;
    this.settled = true;
    this.terminal.reject(error);
  }
}

interface RenderedCheckpoint {
  readonly plan: PlannedCheckpointChoreography;
  readonly observer: ChoreographyExecutorObserver | undefined;
  readonly playback: ControlledPlayback;
}

class ControlledRenderer implements ChoreographySceneStreamRenderer {
  readonly rendered: RenderedCheckpoint[] = [];
  readonly materializeScene = vi.fn((_scene: SceneState) => undefined);
  readonly materializeViewport = vi.fn(
    (_viewport: ViewportPoseV1) => undefined,
  );
  readonly cancelMotion = vi.fn();
  readonly clear = vi.fn();
  readonly playCheckpointChoreography = vi.fn(
    (
      plan: PlannedCheckpointChoreography,
      observer?: ChoreographyExecutorObserver,
    ): ChoreographyPlayback => {
      const playback = new ControlledPlayback();
      this.rendered.push({ plan, observer, playback });
      return playback;
    },
  );
}

interface CapturedRun {
  readonly invocation: ChoreographySceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function runnerHarness(): {
  readonly runStream: ChoreographySceneStreamRunner;
  readonly runs: CapturedRun[];
} {
  const runs: CapturedRun[] = [];
  const runStream: ChoreographySceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runStream, runs };
}

function createRuntime(
  options: {
    readonly layout?: "cinematic" | "compact";
    readonly queueLimit?: number;
    readonly now?: () => number;
  } = {},
) {
  const renderer = new ControlledRenderer();
  const runner = runnerHarness();
  const runtime = new SceneStreamRuntime({
    protocol: "choreography",
    renderer,
    runStream: runner.runStream,
    layout: options.layout ?? "cinematic",
    ...(options.queueLimit === undefined
      ? {}
      : { queueLimit: options.queueLimit }),
    ...(options.now === undefined ? {} : { now: options.now }),
  });
  return { runtime, renderer, runner };
}

function emit(
  run: CapturedRun,
  ...events: readonly ChoreographySceneStreamEvent[]
): void {
  for (const event of events) run.invocation.onEvent(event);
}

function emitCertifiedCues(rendered: RenderedCheckpoint): void {
  for (const cue of rendered.plan.choreographyPlan.phase.cues) {
    rendered.observer?.({ type: "cueStarted", cue: cue.cue });
  }
}

function settlePresented(
  rendered: RenderedCheckpoint,
  settlement: "completed" | "cancelled_to_checkpoint" = "completed",
): void {
  emitCertifiedCues(rendered);
  rendered.observer?.({ type: "firstCuePresented" });
  rendered.observer?.({ type: "checkpointSettled", settlement });
  rendered.playback.settle({
    status: settlement,
    firstCuePresented: true,
  });
}

async function startRuntime(
  runtime: SceneStreamRuntime,
  runner: ReturnType<typeof runnerHarness>,
  prompt = "Complete the square",
): Promise<CapturedRun> {
  runtime.start(prompt);
  await flushMicrotasks();
  return runner.runs.at(-1)!;
}

async function acceptMainPrefix(
  runtime: SceneStreamRuntime,
  renderer: ControlledRenderer,
  run: CapturedRun,
  count: number,
): Promise<void> {
  emit(
    run,
    MAIN_STARTED,
    ...MAIN_CHECKPOINTS.slice(0, count),
    completed(1, count, count),
  );
  for (let index = 0; index < count; index += 1) {
    settlePresented(renderer.rendered[index]);
    await flushMicrotasks();
  }
}

describe("choreography SceneStreamRuntime", () => {
  it("delegates a locked-layout V2 lane and commits only serialized post-paint checkpoints", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner, "  Complete the square  ");

    expect(run.invocation.request).toEqual({
      prompt: "Complete the square",
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    });

    emit(
      run,
      MAIN_STARTED,
      MAIN_CHECKPOINTS[0],
      MAIN_CHECKPOINTS[1],
      completed(1, 2, 2),
    );

    let snapshot = runtime.getSnapshot();
    expect(snapshot.phase).toBe("completing");
    expect(snapshot.committedScene.revision).toBe(0);
    expect(snapshot.provisionalScene.revision).toBe(2);
    expect(snapshot.choreography).toMatchObject({
      layout: "cinematic",
      committedCaption: "",
      visibleCaption: "",
      committedSemanticScene: { revision: 0 },
      provisionalSemanticScene: { revision: 2 },
    });
    expect(snapshot.choreography?.accepted).toHaveLength(0);
    expect(snapshot.activeRevision).toBe(1);
    expect(snapshot.queuedPatchCount).toBe(1);
    expect(renderer.rendered).toHaveLength(1);
    expect(renderer.materializeViewport).toHaveBeenCalledTimes(1);

    settlePresented(renderer.rendered[0]);
    await flushMicrotasks();
    snapshot = runtime.getSnapshot();
    expect(snapshot.committedScene.revision).toBe(1);
    expect(snapshot.choreography?.committedSemanticScene.revision).toBe(1);
    expect(snapshot.choreography?.visibleCaption).toBe(
      MAIN_CHECKPOINTS[0].patch.narration,
    );
    expect(snapshot.choreography?.accepted).toHaveLength(1);
    expect(renderer.rendered).toHaveLength(2);
    expect(renderer.materializeViewport).toHaveBeenCalledTimes(1);

    settlePresented(renderer.rendered[1]);
    await flushMicrotasks();
    snapshot = runtime.getSnapshot();
    expect(snapshot.phase).toBe("completed");
    expect(snapshot.committedScene.revision).toBe(2);
    expect(snapshot.choreography?.accepted).toHaveLength(2);
    expect(snapshot.choreography?.commitFrontier).toEqual(
      snapshot.choreography?.accepted[1].presentation,
    );
    expect(snapshot.completion).toEqual({
      firstPatchMs: 24,
      totalMs: 72,
      repaired: false,
    });

    const secondRun = await startRuntime(runtime, runner, "Explain the corner");
    expect(secondRun.invocation.request.baseScene).toEqual(
      snapshot.committedScene,
    );
    expect(secondRun.invocation.request.baseSemanticScene).toEqual(
      snapshot.choreography?.committedSemanticScene,
    );
  });

  it("cancels before first paint without moving the trusted frontier or retaining evidence", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0], MAIN_CHECKPOINTS[1]);
    const first = renderer.rendered[0];

    expect(runtime.interrupt()).toBe(true);
    expect(runtime.interrupt()).toBe(true);
    expect(first.playback.cancel).toHaveBeenCalledTimes(1);
    expect(run.invocation.signal.aborted).toBe(true);
    expect(runtime.getSnapshot().phase).toBe("interrupting");
    expect(runtime.getSnapshot().queuedPatchCount).toBe(0);

    first.playback.settle({
      status: "cancelled_before_presented",
      firstCuePresented: false,
    });
    await flushMicrotasks();
    const settled = runtime.getSnapshot();
    expect(settled.phase).toBe("interrupted");
    expect(settled.committedScene.revision).toBe(0);
    expect(settled.provisionalScene.revision).toBe(0);
    expect(settled.choreography?.accepted).toHaveLength(0);
    expect(settled.choreography?.evidence).toEqual([]);
    expect(settled.choreography?.visibleCaption).toBe("");

    first.observer?.({ type: "cueStarted", cue: "enter" });
    expect(runtime.getSnapshot()).toEqual(settled);
  });

  it("settles the whole target after first paint and records a cancelled checkpoint receipt", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    const first = renderer.rendered[0];
    emitCertifiedCues(first);
    first.observer?.({ type: "firstCuePresented" });

    expect(runtime.getSnapshot().committedScene.revision).toBe(0);
    expect(runtime.getSnapshot().choreography?.visibleCaption).toBe("");
    expect(runtime.interrupt()).toBe(true);
    expect(first.playback.cancel).toHaveBeenCalledTimes(1);

    first.observer?.({
      type: "checkpointSettled",
      settlement: "cancelled_to_checkpoint",
    });
    first.playback.settle({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
    await flushMicrotasks();

    const snapshot = runtime.getSnapshot();
    expect(snapshot.phase).toBe("interrupted");
    expect(snapshot.committedScene.revision).toBe(1);
    expect(snapshot.choreography?.accepted).toHaveLength(1);
    expect(snapshot.choreography?.accepted[0].presentation.settlement).toBe(
      "cancelled_to_checkpoint",
    );
    expect(snapshot.choreography?.visibleCaption).toBe(
      MAIN_CHECKPOINTS[0].patch.narration,
    );
    expect(snapshot.choreography?.evidence.at(-1)).toMatchObject({
      type: "checkpointSettled",
      settlement: "cancelled_to_checkpoint",
    });
  });

  it("quarantines contradictory executor evidence and ignores its stale callbacks", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    const first = renderer.rendered[0];
    emitCertifiedCues(first);
    first.observer?.({ type: "firstCuePresented" });
    first.playback.settle({ status: "completed", firstCuePresented: true });
    await flushMicrotasks();

    const failed = runtime.getSnapshot();
    expect(failed.phase).toBe("failed");
    expect(failed.error).toMatchObject({
      code: "renderer_failed",
      retryable: false,
    });
    expect(failed.committedScene.revision).toBe(0);
    expect(failed.choreography?.accepted).toHaveLength(0);
    expect(failed.choreography?.evidence).toEqual([]);
    expect(() => runtime.start("Try again")).toThrowError(
      expect.objectContaining<Partial<SceneStreamRuntimeError>>({
        code: "runtime_reset_required",
      }),
    );

    first.observer?.({
      type: "checkpointSettled",
      settlement: "completed",
    });
    expect(runtime.getSnapshot()).toEqual(failed);
  });

  it("rejects an unknown executor signal instead of treating it as settlement", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    const first = renderer.rendered[0];
    emitCertifiedCues(first);
    first.observer?.({ type: "firstCuePresented" });

    expect(() =>
      (first.observer as ((signal: unknown) => void) | undefined)?.({
        type: "unknown",
        settlement: "completed",
      }),
    ).toThrow("Executor signal is outside the closed vocabulary");

    first.playback.settle({ status: "completed", firstCuePresented: true });
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      error: { code: "renderer_failed", retryable: false },
      choreography: { accepted: [], evidence: [] },
    });
  });

  it("fails closed when the renderer returns a malformed playback handle", async () => {
    const { runtime, renderer, runner } = createRuntime();
    renderer.playCheckpointChoreography.mockReturnValueOnce(
      {} as unknown as ChoreographyPlayback,
    );
    const run = await startRuntime(runtime, runner);

    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      queuedPatchCount: 0,
      error: { code: "renderer_failed", retryable: false },
      choreography: { accepted: [], evidence: [] },
    });
    expect(runtime.getSnapshot().activeRevision).toBeUndefined();
  });

  it("fails closed when a valid-looking playback resolves a malformed outcome", async () => {
    const { runtime, renderer, runner } = createRuntime();
    renderer.playCheckpointChoreography.mockReturnValueOnce({
      cancel: vi.fn(),
      firstCuePresented: Promise.resolve(false),
      finished: Promise.resolve(null),
    } as unknown as ChoreographyPlayback);
    const run = await startRuntime(runtime, runner);

    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      queuedPatchCount: 0,
      error: { code: "renderer_failed", retryable: false },
      choreography: { accepted: [], evidence: [] },
    });
    expect(runtime.getSnapshot().activeRevision).toBeUndefined();
  });

  it("finishes renderer quarantine when cancellation cleanup throws", async () => {
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const { runtime, renderer, runner } = createRuntime();
      renderer.cancelMotion.mockImplementation(() => {
        throw new Error("cancel cleanup failed");
      });
      const run = await startRuntime(runtime, runner);
      emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
      const first = renderer.rendered[0];
      emitCertifiedCues(first);
      first.observer?.({ type: "firstCuePresented" });

      first.playback.settle({ status: "completed", firstCuePresented: true });
      await flushMicrotasks();

      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0 },
        queuedPatchCount: 0,
        error: { code: "renderer_failed", retryable: false },
        choreography: { rendererTrusted: false },
      });
      expect(runtime.getSnapshot().activeRevision).toBeUndefined();
      expect(() => runtime.start("Try again")).toThrowError(
        expect.objectContaining<Partial<SceneStreamRuntimeError>>({
          code: "runtime_reset_required",
        }),
      );
    } finally {
      warning.mockRestore();
    }
  });

  it("fails closed when interrupted playback misses its settlement deadline", async () => {
    vi.useFakeTimers();
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const { runtime, renderer, runner } = createRuntime();
      const run = await startRuntime(runtime, runner);
      emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);

      expect(runtime.interrupt()).toBe(true);
      expect(runtime.getSnapshot().phase).toBe("interrupting");
      await vi.advanceTimersByTimeAsync(2_001);

      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0 },
        queuedPatchCount: 0,
        error: { code: "renderer_failed", retryable: false },
        choreography: {
          accepted: [],
          evidence: [],
          rendererTrusted: true,
        },
      });
      expect(runtime.getSnapshot().activeRevision).toBeUndefined();
      expect(renderer.clear).toHaveBeenCalledTimes(1);
    } finally {
      warning.mockRestore();
      vi.useRealTimers();
    }
  });

  it("validates started, repairing, declined, failed, foreign, and missing terminal lifecycles", async () => {
    const repaired = createRuntime();
    const repairedRun = await startRuntime(repaired.runtime, repaired.runner);
    emit(
      repairedRun,
      MAIN_STARTED,
      decodeChoreographySceneStreamEvent({
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 0,
        message: "Repairing one bounded attempt.",
      }),
      decodeChoreographySceneStreamEvent({
        type: "choreography_scene_stream_declined",
        generation: 1,
        attempt: 2,
        finalRevision: 0,
        reasonCode: "unsupported_intent",
        message: "This lesson is outside the bounded fixture.",
      }),
    );
    expect(repaired.runtime.getSnapshot()).toMatchObject({
      phase: "declined",
      attempt: 2,
      decline: { reasonCode: "unsupported_intent" },
    });

    const failed = createRuntime();
    const failedRun = await startRuntime(failed.runtime, failed.runner);
    emit(
      failedRun,
      MAIN_STARTED,
      decodeChoreographySceneStreamEvent({
        type: "scene_stream_failed",
        generation: 1,
        attempt: 1,
        code: "model_timeout",
        message: "The model timed out.",
        lastAcceptedRevision: 0,
        retryable: true,
      }),
    );
    expect(failed.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      error: { code: "model_timeout", retryable: true },
    });

    const foreign = createRuntime();
    const foreignRun = await startRuntime(foreign.runtime, foreign.runner);
    foreignRun.invocation.onEvent({
      type: "scene_patch",
      generation: 1,
    } as unknown as ChoreographySceneStreamEvent);
    expect(foreign.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      error: { code: "invalid_stream_event", retryable: true },
    });

    const missingTerminal = createRuntime();
    const missingRun = await startRuntime(
      missingTerminal.runtime,
      missingTerminal.runner,
    );
    emit(missingRun, MAIN_STARTED);
    missingRun.completion.resolve();
    await flushMicrotasks();
    expect(missingTerminal.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      error: { code: "invalid_stream_event", retryable: true },
    });
  });

  it("rejects repair after an attempt has emitted a checkpoint", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    settlePresented(renderer.rendered[0]);
    await flushMicrotasks();

    emit(
      run,
      decodeChoreographySceneStreamEvent({
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 1,
        message: "Repairing after a partial attempt is forbidden.",
      }),
    );

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 1 },
      error: { code: "invalid_stream_event", retryable: true },
      choreography: { accepted: [{ scene: { revision: 1 } }] },
    });
  });

  it("rejects malformed generation, completion, duplicate, and bounded queue events", async () => {
    const wrongGeneration = createRuntime();
    const wrongGenerationRun = await startRuntime(
      wrongGeneration.runtime,
      wrongGeneration.runner,
    );
    wrongGenerationRun.invocation.onEvent({
      ...MAIN_STARTED,
      generation: 2,
    });
    expect(wrongGeneration.runtime.getSnapshot().error?.code).toBe(
      "invalid_stream_event",
    );

    const completion = createRuntime();
    const completionRun = await startRuntime(
      completion.runtime,
      completion.runner,
    );
    emit(completionRun, MAIN_STARTED, completed(1, 1, 1));
    expect(completion.runtime.getSnapshot().error?.code).toBe(
      "invalid_stream_event",
    );

    const duplicate = createRuntime();
    const duplicateRun = await startRuntime(
      duplicate.runtime,
      duplicate.runner,
    );
    emit(duplicateRun, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    duplicateRun.invocation.onEvent({
      ...MAIN_CHECKPOINTS[0],
      sequence: 2,
      baseRevision: 1,
      resultRevision: 2,
      semantic: {
        ...MAIN_CHECKPOINTS[0].semantic,
        semanticBaseRevision: 1,
        semanticResultRevision: 2,
      },
    });
    expect(duplicate.runtime.getSnapshot().error?.code).toBe(
      "invalid_stream_event",
    );

    const bounded = createRuntime({ queueLimit: 1 });
    const boundedRun = await startRuntime(bounded.runtime, bounded.runner);
    emit(
      boundedRun,
      MAIN_STARTED,
      MAIN_CHECKPOINTS[0],
      MAIN_CHECKPOINTS[1],
      MAIN_CHECKPOINTS[2],
    );
    expect(bounded.runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      error: { code: "invalid_stream_event", retryable: true },
    });
  });

  it.each(["interrupt", "reset", "dispose"] as const)(
    "prevents a same-tick runner call after %s",
    async (action) => {
      const { runtime, runner } = createRuntime();
      runtime.start("Complete the square");
      if (action === "interrupt") runtime.interrupt();
      else runtime[action]();
      await flushMicrotasks();
      expect(runner.runs).toHaveLength(0);
    },
  );

  it("preserves stored receipts while recording the fresh replay settlement", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0], MAIN_CHECKPOINTS[1]);

    settlePresented(renderer.rendered[0]);
    await flushMicrotasks();
    const second = renderer.rendered[1];
    emitCertifiedCues(second);
    second.observer?.({ type: "firstCuePresented" });
    runtime.interrupt();
    second.observer?.({
      type: "checkpointSettled",
      settlement: "cancelled_to_checkpoint",
    });
    second.playback.settle({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
    await flushMicrotasks();

    const stored = clone(runtime.getSnapshot().choreography?.accepted ?? []);
    expect(stored.map((record) => record.presentation.settlement)).toEqual([
      "completed",
      "cancelled_to_checkpoint",
    ]);
    const runnerCalls = runner.runs.length;
    const priorPlaybackCount = renderer.rendered.length;
    const replayPromise = runtime.replayAccepted();

    expect(runner.runs).toHaveLength(runnerCalls);
    expect(renderer.cancelMotion).toHaveBeenCalledTimes(1);
    expect(renderer.clear).toHaveBeenCalledTimes(1);
    expect(renderer.materializeScene).toHaveBeenCalledTimes(1);
    expect(renderer.materializeViewport).toHaveBeenCalledTimes(2);
    expect(renderer.rendered).toHaveLength(priorPlaybackCount + 1);

    settlePresented(renderer.rendered[priorPlaybackCount]);
    await flushMicrotasks();
    expect(renderer.rendered).toHaveLength(priorPlaybackCount + 2);
    settlePresented(renderer.rendered[priorPlaybackCount + 1]);
    await replayPromise;

    const replayed = runtime.getSnapshot();
    expect(replayed.phase).toBe("completed");
    expect(replayed.choreography?.accepted).toEqual(stored);
    expect(replayed.choreography?.accepted[1].presentation.settlement).toBe(
      "cancelled_to_checkpoint",
    );
    expect(replayed.choreography?.evidence.at(-1)).toMatchObject({
      type: "checkpointSettled",
      settlement: "completed",
    });
    expect(runner.runs).toHaveLength(runnerCalls);
  });

  it("preflights the whole replay ledger before any renderer call and truncates corruption", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    await acceptMainPrefix(runtime, renderer, run, 2);
    const accepted = runtime.getSnapshot().choreography?.accepted ?? [];
    const corrupt = clone(accepted[1]) as AcceptedChoreographyRevision;
    (corrupt.viewport as { x: number }).x += 1;
    const owner = runtime as unknown as {
      choreographyRuntime: {
        accepted: AcceptedChoreographyRevision[];
      };
    };
    owner.choreographyRuntime.accepted = [accepted[0], corrupt];

    const before = {
      play: renderer.playCheckpointChoreography.mock.calls.length,
      cancel: renderer.cancelMotion.mock.calls.length,
      clear: renderer.clear.mock.calls.length,
      scene: renderer.materializeScene.mock.calls.length,
      viewport: renderer.materializeViewport.mock.calls.length,
    };
    await runtime.replayAccepted();

    expect(renderer.playCheckpointChoreography.mock.calls.length).toBe(
      before.play,
    );
    expect(renderer.cancelMotion.mock.calls.length).toBe(before.cancel + 1);
    expect(renderer.clear.mock.calls.length).toBe(before.clear + 1);
    expect(renderer.materializeScene.mock.calls.length).toBe(before.scene + 1);
    expect(renderer.materializeViewport.mock.calls.length).toBe(
      before.viewport + 1,
    );
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      accepted[0].scene,
    );
    expect(renderer.materializeViewport).toHaveBeenLastCalledWith(
      accepted[0].viewport,
    );
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 1 },
      error: { code: "replay_integrity_failed", retryable: false },
      choreography: { accepted: [{ scene: { revision: 1 } }] },
    });
    expect(() => runtime.start("Continue")).toThrowError(
      expect.objectContaining<Partial<SceneStreamRuntimeError>>({
        code: "runtime_reset_required",
      }),
    );
  });

  it("quarantines malformed replay outcomes and restores the exact retained prefix", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    await acceptMainPrefix(runtime, renderer, run, 1);
    renderer.playCheckpointChoreography.mockReturnValueOnce({
      cancel: vi.fn(),
      firstCuePresented: Promise.resolve(false),
      finished: Promise.resolve(null),
    } as unknown as ChoreographyPlayback);
    const clearsBeforeReplay = renderer.clear.mock.calls.length;

    await runtime.replayAccepted();

    expect(renderer.clear.mock.calls.length).toBe(clearsBeforeReplay + 2);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      queuedPatchCount: 0,
      error: { code: "replay_integrity_failed", retryable: false },
      choreography: { accepted: [], evidence: [] },
    });
    expect(runtime.getSnapshot().activeRevision).toBeUndefined();
  });

  it("finishes replay quarantine when restoration cancellation throws", async () => {
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const { runtime, renderer, runner } = createRuntime();
      const run = await startRuntime(runtime, runner);
      await acceptMainPrefix(runtime, renderer, run, 1);
      renderer.playCheckpointChoreography.mockReturnValueOnce(
        {} as unknown as ChoreographyPlayback,
      );
      renderer.cancelMotion
        .mockImplementationOnce(() => undefined)
        .mockImplementation(() => {
          throw new Error("restore cancellation failed");
        });

      await runtime.replayAccepted();

      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0 },
        queuedPatchCount: 0,
        error: { code: "replay_integrity_failed", retryable: false },
        choreography: {
          accepted: [],
          evidence: [],
          rendererTrusted: false,
        },
      });
      expect(
        runtime.getSnapshot().choreography?.commitFrontier,
      ).toBeUndefined();
      expect(runtime.getSnapshot().activeRevision).toBeUndefined();
      expect(() => runtime.start("Try again")).toThrowError(
        expect.objectContaining<Partial<SceneStreamRuntimeError>>({
          code: "runtime_reset_required",
        }),
      );
    } finally {
      warning.mockRestore();
    }
  });

  it("truncates replay at the exact terminal interruption boundary", async () => {
    const beforePaint = createRuntime();
    const beforeRun = await startRuntime(
      beforePaint.runtime,
      beforePaint.runner,
    );
    await acceptMainPrefix(
      beforePaint.runtime,
      beforePaint.renderer,
      beforeRun,
      2,
    );
    const beforeReplayIndex = beforePaint.renderer.rendered.length;
    const beforeReplay = beforePaint.runtime.replayAccepted();
    settlePresented(beforePaint.renderer.rendered[beforeReplayIndex]);
    await flushMicrotasks();
    const beforePlayback = beforePaint.renderer.rendered[beforeReplayIndex + 1];
    beforePaint.runtime.interrupt();
    beforePlayback.playback.settle({
      status: "cancelled_before_presented",
      firstCuePresented: false,
    });
    await beforeReplay;
    expect(beforePaint.runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
      choreography: { accepted: [{ scene: { revision: 1 } }] },
    });

    const afterPaint = createRuntime();
    const afterRun = await startRuntime(afterPaint.runtime, afterPaint.runner);
    await acceptMainPrefix(
      afterPaint.runtime,
      afterPaint.renderer,
      afterRun,
      2,
    );
    const storedFirst = clone(
      afterPaint.runtime.getSnapshot().choreography!.accepted[0],
    );
    const afterReplayIndex = afterPaint.renderer.rendered.length;
    const afterReplay = afterPaint.runtime.replayAccepted();
    settlePresented(afterPaint.renderer.rendered[afterReplayIndex]);
    await flushMicrotasks();
    const afterPlayback = afterPaint.renderer.rendered[afterReplayIndex + 1];
    emitCertifiedCues(afterPlayback);
    afterPlayback.observer?.({ type: "firstCuePresented" });
    afterPaint.runtime.interrupt();
    afterPlayback.observer?.({
      type: "checkpointSettled",
      settlement: "cancelled_to_checkpoint",
    });
    afterPlayback.playback.settle({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
    await afterReplay;

    expect(afterPaint.runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 2 },
      choreography: {
        accepted: [storedFirst, { scene: { revision: 2 } }],
      },
    });
    expect(
      afterPaint.runtime.getSnapshot().choreography?.accepted[1].presentation
        .settlement,
    ).toBe("completed");
    expect(
      afterPaint.runtime.getSnapshot().choreography?.evidence.at(-1),
    ).toMatchObject({
      type: "checkpointSettled",
      settlement: "cancelled_to_checkpoint",
    });
  });

  it("supports the real three-generation adaptive path up to the non-pruning nine-checkpoint cap", async () => {
    const { runtime, renderer, runner } = createRuntime({ layout: "compact" });
    const firstRun = await startRuntime(runtime, runner);
    emit(
      firstRun,
      MAIN_STARTED,
      ...MAIN_CHECKPOINTS.slice(0, 5),
      completed(1, 5, 5),
    );
    for (let index = 0; index < 5; index += 1) {
      settlePresented(renderer.rendered[index]);
      await flushMicrotasks();
    }

    const clarificationRun = await startRuntime(runtime, runner, "Why nine?");
    emit(clarificationRun, ...ADAPTIVE_EVENTS.slice(0, 3));
    settlePresented(renderer.rendered[5]);
    await flushMicrotasks();

    const continuationRun = await startRuntime(runtime, runner, "Continue");
    emit(continuationRun, ...ADAPTIVE_EVENTS.slice(3));
    for (let index = 6; index < 9; index += 1) {
      settlePresented(renderer.rendered[index]);
      await flushMicrotasks();
    }

    const accepted = runtime.getSnapshot().choreography?.accepted ?? [];
    expect(runtime.getSnapshot().phase).toBe("completed");
    expect(runtime.getSnapshot().committedScene.revision).toBe(9);
    expect(runtime.getSnapshot().choreography?.layout).toBe("compact");
    expect(accepted.map((record) => record.event.sequence)).toEqual([
      1, 2, 3, 4, 5, 1, 1, 2, 3,
    ]);
    expect(
      accepted.map((record) => record.event.semantic.checkpointId),
    ).toEqual([
      "problem",
      "area_model",
      "split_linear_term",
      "rearrange_halves",
      "missing_corner",
      "corner_detail",
      "balance_and_complete",
      "factor_square",
      "solve_roots",
    ]);

    const overflowRun = await startRuntime(runtime, runner, "One more");
    overflowRun.invocation.onEvent({
      type: "scene_stream_started",
      generation: 4,
      attempt: 1,
      baseRevision: 9,
    });
    overflowRun.invocation.onEvent({
      ...ADAPTIVE_EVENTS[4],
      generation: 4,
      attempt: 1,
      sequence: 1,
      baseRevision: 9,
      resultRevision: 10,
      semantic: {
        ...(ADAPTIVE_EVENTS[4] as ChoreographySceneCheckpointEvent).semantic,
        semanticBaseRevision: 9,
        semanticResultRevision: 10,
      },
    } as ChoreographySceneCheckpointEvent);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 9 },
      error: { code: "invalid_stream_event", retryable: true },
    });
    expect(runtime.getSnapshot().choreography?.accepted).toHaveLength(9);
  });

  it("invalidates transport and playback callbacks across reset", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    const playback = renderer.rendered[0];
    playback.observer?.({
      type: "cueStarted",
      cue: playback.plan.choreographyPlan.phase.cues[0].cue,
    });
    runtime.reset();
    const reset = runtime.getSnapshot();

    settlePresented(playback);
    emit(run, MAIN_CHECKPOINTS[1], MAIN_COMPLETED);
    await flushMicrotasks();
    expect(runtime.getSnapshot()).toEqual(reset);
    expect(reset.phase).toBe("idle");
    expect(reset.committedScene.revision).toBe(0);
    expect(reset.choreography?.accepted).toEqual([]);
  });

  it("makes active observer signals inert after dispose", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await startRuntime(runtime, runner);
    emit(run, MAIN_STARTED, MAIN_CHECKPOINTS[0]);
    const playback = renderer.rendered[0];
    playback.observer?.({
      type: "cueStarted",
      cue: playback.plan.choreographyPlan.phase.cues[0].cue,
    });

    runtime.dispose();

    expect(() => emitCertifiedCues(playback)).not.toThrow();
    expect(() =>
      playback.observer?.({
        type: "checkpointSettled",
        settlement: "completed",
      }),
    ).not.toThrow();
  });

  it("quarantines reset when cancellation cleanup throws and permits retry", () => {
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const { runtime, renderer } = createRuntime();
      renderer.cancelMotion.mockImplementation(() => {
        throw new Error("cancel cleanup failed");
      });

      expect(() => runtime.reset()).not.toThrow();
      expect(renderer.clear).toHaveBeenCalledTimes(1);
      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0 },
        error: { code: "renderer_failed", retryable: false },
        choreography: {
          accepted: [],
          evidence: [],
          rendererTrusted: false,
        },
      });

      renderer.cancelMotion.mockReset();
      runtime.reset();
      expect(runtime.getSnapshot()).toMatchObject({
        phase: "idle",
        choreography: { rendererTrusted: true },
      });
    } finally {
      warning.mockRestore();
    }
  });

  it("retains the committed frontier when reset cannot clear and permits retry", async () => {
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const { runtime, renderer, runner } = createRuntime();
      const run = await startRuntime(runtime, runner);
      await acceptMainPrefix(runtime, renderer, run, 1);
      renderer.clear.mockImplementationOnce(() => {
        throw new Error("clear failed");
      });

      expect(() => runtime.reset()).not.toThrow();
      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 1 },
        error: { code: "renderer_failed", retryable: false },
        choreography: {
          accepted: [{ scene: { revision: 1 } }],
          rendererTrusted: false,
        },
      });

      renderer.clear.mockReset();
      runtime.reset();
      expect(runtime.getSnapshot()).toMatchObject({
        phase: "idle",
        committedScene: { revision: 0 },
        choreography: { accepted: [], evidence: [], rendererTrusted: true },
      });
    } finally {
      warning.mockRestore();
    }
  });
});
