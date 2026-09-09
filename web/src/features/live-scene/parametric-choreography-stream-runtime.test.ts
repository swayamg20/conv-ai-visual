import { describe, expect, it, vi } from "vitest";

import type {
  PlannedCheckpointChoreography,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";
import {
  decodeParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import type {
  ParametricChoreographySceneStreamRunInvocation,
  ParametricChoreographySceneStreamRunner,
} from "./parametric-choreography-model-stream";
import {
  ParametricChoreographyStreamRuntime,
  type ParametricChoreographyCommand,
  type ParametricChoreographyRenderer,
  type ParametricChoreographyRuntimeSnapshot,
} from "./parametric-choreography-stream-runtime";
import {
  createParametricAdaptiveCheckpointFixture,
  createParametricCheckpointFixture,
  createParametricLifecycleFixture,
} from "./parametric-choreography-test-fixture";

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  let reject!: (error?: unknown) => void;
  const promise = new Promise<Value>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

class ControlledPlayback implements ChoreographyPlayback {
  readonly firstCuePresented = Promise.resolve(false);
  readonly cancel = vi.fn();
  readonly finished: Promise<ChoreographyPlaybackOutcome>;
  private readonly terminal = deferred<ChoreographyPlaybackOutcome>();

  constructor() {
    this.finished = this.terminal.promise;
  }

  settle(outcome: ChoreographyPlaybackOutcome): void {
    this.terminal.resolve(outcome);
  }
}

interface RenderedCheckpoint {
  readonly plan: PlannedCheckpointChoreography;
  readonly observer?: ChoreographyExecutorObserver;
  readonly playback: ControlledPlayback;
}

class ControlledRenderer implements ParametricChoreographyRenderer {
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
  readonly invocation: ParametricChoreographySceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function harness() {
  const runs: CapturedRun[] = [];
  const runStream: ParametricChoreographySceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runs, runStream };
}

const REFLEX: ParametricChoreographyCommand = {
  routingMode: "reflex",
  problemText: "x squared plus six x equals seven",
  requestedRoute: { intent: "advance", targetStage: "solve" },
};

function createRuntime(
  options: { layout?: "cinematic" | "compact"; queueLimit?: number } = {},
) {
  const renderer = new ControlledRenderer();
  const runner = harness();
  const runtime = new ParametricChoreographyStreamRuntime({
    renderer,
    runStream: runner.runStream,
    layout: options.layout ?? "cinematic",
    ...(options.queueLimit ? { queueLimit: options.queueLimit } : {}),
  });
  return { runtime, renderer, runner };
}

async function start(
  runtime: ParametricChoreographyStreamRuntime,
  runner: ReturnType<typeof harness>,
  command = REFLEX,
): Promise<CapturedRun> {
  runtime.start(command);
  await flush();
  return runner.runs.at(-1)!;
}

function emit(
  run: CapturedRun,
  ...events: readonly ParametricChoreographySceneStreamEventV3[]
): void {
  for (const event of events) run.invocation.onEvent(event);
}

function settle(
  rendered: RenderedCheckpoint,
  status: "completed" | "cancelled_to_checkpoint" = "completed",
): void {
  for (const cue of rendered.plan.choreographyPlan.phase.cues) {
    rendered.observer?.({ type: "cueStarted", cue: cue.cue });
  }
  rendered.observer?.({ type: "firstCuePresented" });
  rendered.observer?.({ type: "checkpointSettled", settlement: status });
  rendered.playback.settle({ status, firstCuePresented: true });
}

function started(generation: number, baseRevision: number) {
  return decodeParametricChoreographySceneStreamEventV3({
    type: "scene_stream_started",
    generation,
    attempt: 1,
    baseRevision,
  });
}

function completed(
  generation: number,
  finalRevision: number,
  patchCount: number,
  repaired = false,
) {
  return decodeParametricChoreographySceneStreamEventV3({
    type: "scene_stream_completed",
    generation,
    finalRevision,
    patchCount,
    firstPatchMs: 12,
    totalMs: 24,
    repaired,
  });
}

function expectCommittedFrontierUnchanged(
  after: ParametricChoreographyRuntimeSnapshot,
  before: ParametricChoreographyRuntimeSnapshot,
): void {
  expect(after.committedScene).toEqual(before.committedScene);
  expect(after.provisionalScene).toEqual(before.committedScene);
  expect(after.committedSemanticScene).toEqual(before.committedSemanticScene);
  expect(after.provisionalSemanticScene).toEqual(
    before.committedSemanticScene,
  );
  expect(after.committedViewport).toEqual(before.committedViewport);
  expect(after.provisionalViewport).toEqual(before.committedViewport);
  expect(after.committedSemanticScene.certificateHeadSha256).toBe(
    before.committedSemanticScene.certificateHeadSha256,
  );
  expect(after.accepted).toEqual(before.accepted);
  expect(after.accepted.at(-1)?.event.patch.narration).toBe(
    before.accepted.at(-1)?.event.patch.narration,
  );
  expect(after.visibleCheckpointId).toBe(before.visibleCheckpointId);
  expect(after.sequence).toBe(0);
  expect(after.queuedCheckpointCount).toBe(0);
  expect(after.activeRevision).toBeUndefined();
}

async function acceptPrefix(
  runtime: ParametricChoreographyStreamRuntime,
  renderer: ControlledRenderer,
  runner: ReturnType<typeof harness>,
  count: number,
): Promise<CapturedRun> {
  const run = await start(runtime, runner);
  emit(run, ...createParametricLifecycleFixture(count));
  expect(runtime.getSnapshot().phase).toBe("completing");
  for (let index = 0; index < count; index += 1) {
    settle(renderer.rendered[index]);
    await flush();
  }
  run.completion.resolve();
  await flush();
  expect(runtime.getSnapshot().phase).toBe("completed");
  return run;
}

describe("ParametricChoreographyStreamRuntime", () => {
  it("sends the exact Reflex request and commits only after terminal playback", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);

    expect(run.invocation.request).toEqual({
      protocol: "parametric_choreography_v3",
      routingMode: "reflex",
      problemText: REFLEX.problemText,
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });

    emit(run, ...createParametricLifecycleFixture(1));
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 1 },
      activeRevision: 1,
    });

    settle(renderer.rendered[0]);
    await flush();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 1 },
      committedSemanticScene: { revision: 1 },
      visibleCheckpointId: "problem",
      rendererTrusted: true,
    });
  });

  it("continues from the exact scene, semantic, and certificate frontier", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const accepted = runtime.getSnapshot().accepted[0];

    const continuation = await start(runtime, runner, {
      routingMode: "director",
      problemText: REFLEX.problemText,
      prompt: "Explain the next visible idea.",
    });

    expect(continuation.invocation.request).toEqual({
      protocol: "parametric_choreography_v3",
      routingMode: "director",
      problemText: REFLEX.problemText,
      prompt: "Explain the next visible idea.",
      generation: 2,
      baseScene: accepted.scene,
      baseSemanticScene: accepted.semanticScene,
    });
    expect(
      continuation.invocation.request.baseSemanticScene.certificateHeadSha256,
    ).toBe(accepted.presentation.certificateSha256);
  });

  it("sends an exact Director request without a client route", async () => {
    const { runtime, runner } = createRuntime();
    const run = await start(runtime, runner, {
      routingMode: "director",
      problemText: "x² + 6x = 7",
      prompt: "Start with the area model.",
    });

    expect(run.invocation.request).toEqual({
      protocol: "parametric_choreography_v3",
      routingMode: "director",
      problemText: "x² + 6x = 7",
      prompt: "Start with the area model.",
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    });
    expect(run.invocation.request).not.toHaveProperty("requestedRoute");
  });

  it("accepts one repair boundary followed by attempt-two checkpoints", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner, {
      routingMode: "director",
      problemText: "x² + 6x = 7",
      prompt: "Start the solution.",
    });
    emit(
      run,
      started(1, 0),
      decodeParametricChoreographySceneStreamEventV3({
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 0,
        message: "Repairing once.",
      }),
      createParametricCheckpointFixture(0, 1, 1, 2),
      completed(1, 1, 1, true),
    );
    settle(renderer.rendered[0]);
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      attempt: 2,
      committedScene: { revision: 1 },
      completion: { repaired: true },
    });
  });

  it("accepts only unchanged pre-checkpoint decline and failure terminals", async () => {
    const declinedRuntime = createRuntime();
    const declinedRun = await start(
      declinedRuntime.runtime,
      declinedRuntime.runner,
    );
    emit(
      declinedRun,
      started(1, 0),
      decodeParametricChoreographySceneStreamEventV3({
        type: "parametric_choreography_scene_stream_declined",
        generation: 1,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "problem_unsupported",
        message: "Use a supported equation.",
      }),
    );
    expect(declinedRuntime.runtime.getSnapshot()).toMatchObject({
      phase: "declined",
      committedScene: { revision: 0 },
      decline: { reasonCode: "problem_unsupported" },
    });

    const failedRuntime = createRuntime();
    const failedRun = await start(failedRuntime.runtime, failedRuntime.runner);
    emit(
      failedRun,
      started(1, 0),
      decodeParametricChoreographySceneStreamEventV3({
        type: "parametric_choreography_scene_stream_failed",
        generation: 1,
        attempt: 1,
        code: "provider_timeout",
        message: "The Director timed out.",
        lastAcceptedRevision: 0,
        retryable: true,
      }),
    );
    expect(failedRuntime.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      error: { code: "provider_timeout", retryable: true },
    });
  });

  it("rejects checkpoints before started and duplicate sequences", async () => {
    const beforeStart = createRuntime();
    const beforeStartRun = await start(beforeStart.runtime, beforeStart.runner);
    emit(beforeStartRun, createParametricCheckpointFixture(0));
    expect(beforeStart.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      error: { code: "invalid_stream_event" },
    });

    const duplicate = createRuntime();
    const duplicateRun = await start(duplicate.runtime, duplicate.runner);
    const checkpoint = createParametricCheckpointFixture(0);
    emit(duplicateRun, started(1, 0), checkpoint, checkpoint);
    const active = duplicate.renderer.rendered[0];
    expect(active.playback.cancel).toHaveBeenCalledOnce();
    settle(active);
    await flush();
    expect(duplicate.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      sequence: 0,
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });
  });

  it("rejects duplicate patch identities and queue overflow", async () => {
    const duplicate = createRuntime();
    const duplicateRun = await start(duplicate.runtime, duplicate.runner);
    const first = createParametricCheckpointFixture(0);
    const forged = JSON.parse(
      JSON.stringify(createParametricCheckpointFixture(1)),
    ) as ParametricChoreographySceneStreamEventV3;
    if (forged.type !== "parametric_choreography_scene_checkpoint") {
      throw new Error("expected checkpoint fixture");
    }
    (forged.patch as { patchId: string }).patchId = first.patch.patchId;
    emit(duplicateRun, started(1, 0), first, forged);
    expect(duplicateRun.invocation.signal.aborted).toBe(true);
    expect(duplicate.renderer.rendered[0].playback.cancel).toHaveBeenCalledOnce();
    expect(duplicate.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });

    const limited = createRuntime({ queueLimit: 1 });
    const limitedRun = await start(limited.runtime, limited.runner);
    emit(
      limitedRun,
      started(1, 0),
      createParametricCheckpointFixture(0),
      createParametricCheckpointFixture(1),
      createParametricCheckpointFixture(2),
    );
    expect(limitedRun.invocation.signal.aborted).toBe(true);
    expect(limited.renderer.rendered[0].playback.cancel).toHaveBeenCalledOnce();
    expect(limited.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      queuedCheckpointCount: 0,
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
      error: { code: "invalid_stream_event" },
    });
  });

  it("bounds the retained semantic history at the eight main checkpoints plus one detour", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 5);

    const cornerRun = await start(runtime, runner, {
      routingMode: "reflex",
      problemText: REFLEX.problemText,
      requestedRoute: { intent: "clarify_corner" },
    });
    emit(
      cornerRun,
      started(2, 5),
      createParametricAdaptiveCheckpointFixture(0, 2, 1),
      completed(2, 6, 1),
    );
    settle(renderer.rendered[5]);
    await flush();

    const finishRun = await start(runtime, runner);
    emit(
      finishRun,
      started(3, 6),
      createParametricAdaptiveCheckpointFixture(1, 3, 1),
      createParametricAdaptiveCheckpointFixture(2, 3, 2),
      createParametricAdaptiveCheckpointFixture(3, 3, 3),
      completed(3, 9, 3),
    );
    for (let index = 6; index < 9; index += 1) {
      settle(renderer.rendered[index]);
      await flush();
    }
    expect(runtime.getSnapshot().accepted).toHaveLength(9);

    const overflowRun = await start(runtime, runner);
    emit(overflowRun, started(4, 9));
    const forged = JSON.parse(
      JSON.stringify(createParametricCheckpointFixture(0, 4, 1)),
    ) as Record<string, unknown>;
    forged.baseRevision = 9;
    forged.resultRevision = 10;
    const semantic = forged.semantic as Record<string, unknown>;
    semantic.semanticBaseRevision = 9;
    semantic.semanticResultRevision = 10;
    emit(
      overflowRun,
      forged as unknown as ParametricChoreographySceneStreamEventV3,
    );

    expect(overflowRun.invocation.signal.aborted).toBe(true);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 9 },
      error: { code: "invalid_stream_event" },
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(9);
  });

  it("stops at an in-flight checkpoint only after cancelled settlement", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);
    emit(run, started(1, 0), createParametricCheckpointFixture(0));

    expect(runtime.interrupt()).toBe(true);
    expect(run.invocation.signal.aborted).toBe(true);
    expect(renderer.rendered[0].playback.cancel).toHaveBeenCalledOnce();
    expect(runtime.getSnapshot().committedScene.revision).toBe(0);

    settle(renderer.rendered[0], "cancelled_to_checkpoint");
    await flush();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(1);
  });

  it("fails closed if an interrupted checkpoint does not settle in two seconds", async () => {
    vi.useFakeTimers();
    try {
      const { runtime, renderer, runner } = createRuntime();
      const run = await start(runtime, runner);
      emit(run, started(1, 0), createParametricCheckpointFixture(0));
      runtime.interrupt();

      await vi.advanceTimersByTimeAsync(2_001);

      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0 },
        provisionalScene: { revision: 0 },
        rendererTrusted: true,
        error: { code: "renderer_failed" },
      });
      expect(renderer.clear).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("restores the prior checkpoint when stopped before the next one is visible", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot();
    const run = await start(runtime, runner);
    emit(run, started(2, 1), createParametricCheckpointFixture(1, 2, 1));
    runtime.interrupt();
    renderer.rendered[1].playback.settle({
      status: "cancelled_before_presented",
      firstCuePresented: false,
    });
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: before.committedScene,
      committedSemanticScene: before.committedSemanticScene,
      accepted: before.accepted,
      rendererTrusted: true,
    });
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      before.committedScene,
    );
  });

  it.each([
    ["missing settlement", "none"],
    ["out-of-order cue", "order"],
    ["duplicate first-paint signal", "duplicate"],
  ] as const)("rejects %s renderer evidence", async (_label, mode) => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);
    emit(run, ...createParametricLifecycleFixture(1));
    const rendered = renderer.rendered[0];

    if (mode === "order") {
      expect(() =>
        rendered.observer?.({ type: "cueStarted", cue: "focus" }),
      ).toThrow(/cue order/);
    } else {
      for (const cue of rendered.plan.choreographyPlan.phase.cues) {
        rendered.observer?.({ type: "cueStarted", cue: cue.cue });
      }
      rendered.observer?.({ type: "firstCuePresented" });
      if (mode === "duplicate") {
        expect(() =>
          rendered.observer?.({ type: "firstCuePresented" }),
        ).toThrow(/first presentation/);
      }
    }
    rendered.playback.settle({
      status: "completed",
      firstCuePresented: mode !== "order",
    });
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      error: { code: "renderer_failed" },
    });
  });

  it("discards late chunks after reset and keeps a clean frontier", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);
    const events = createParametricLifecycleFixture(1);
    emit(run, events[0]);
    runtime.reset();
    emit(run, ...events.slice(1));
    run.completion.resolve();
    await flush();

    expect(run.invocation.signal.aborted).toBe(true);
    expect(renderer.rendered).toHaveLength(0);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "idle",
      generation: 0,
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });
  });

  it("restores the last accepted state after a transport or decode failure", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot().committedScene;
    const run = await start(runtime, runner);
    emit(run, started(2, 1));
    run.completion.reject(new Error("malformed SSE event"));
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: before,
      provisionalScene: before,
      error: { code: "invalid_stream_event", retryable: true },
    });
  });

  it("rolls back active provisional work when the transport fails", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot();
    const run = await start(runtime, runner);
    emit(run, started(2, 1), createParametricCheckpointFixture(1, 2, 1));
    const active = renderer.rendered[1];

    run.completion.reject(new Error("connection ended mid-checkpoint"));
    await flush();

    const failed = runtime.getSnapshot();
    expect(run.invocation.signal.aborted).toBe(true);
    expect(active.playback.cancel).toHaveBeenCalledOnce();
    expect(failed).toMatchObject({
      phase: "failed",
      rendererTrusted: true,
      error: { code: "invalid_stream_event", retryable: true },
    });
    expectCommittedFrontierUnchanged(failed, before);
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      before.committedScene,
    );
    expect(renderer.materializeViewport).toHaveBeenLastCalledWith(
      before.committedViewport,
    );

    settle(active);
    await flush();
    expectCommittedFrontierUnchanged(runtime.getSnapshot(), before);
  });

  it("restores the last accepted renderer frontier after playback failure", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot().committedScene;
    const run = await start(runtime, runner);
    emit(
      run,
      started(2, 1),
      createParametricCheckpointFixture(1, 2, 1),
      completed(2, 2, 1),
    );
    renderer.rendered[1].playback.settle({
      status: "failed",
      firstCuePresented: false,
      error: "paint barrier rejected",
    });
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: before,
      provisionalScene: before,
      rendererTrusted: true,
      error: { code: "renderer_failed", retryable: true },
    });
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(before);
  });

  it("preflights and replays the exact full ledger with zero network", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 8);
    const before = runtime.getSnapshot();
    const runCount = runner.runs.length;

    const replay = runtime.replayAccepted();
    for (let index = 8; index < 16; index += 1) {
      await flush();
      settle(renderer.rendered[index]);
    }
    await replay;

    expect(runner.runs).toHaveLength(runCount);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: before.committedScene,
      committedSemanticScene: before.committedSemanticScene,
      accepted: before.accepted,
      rendererTrusted: true,
    });
  });

  it("restores the full last accepted board when Replay playback fails", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 2);
    const before = runtime.getSnapshot();

    const replay = runtime.replayAccepted();
    await flush();
    settle(renderer.rendered[2]);
    await flush();
    renderer.rendered[3].playback.settle({
      status: "failed",
      firstCuePresented: false,
    });
    await replay;

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: before.committedScene,
      committedSemanticScene: before.committedSemanticScene,
      accepted: before.accepted,
      rendererTrusted: true,
      error: { code: "replay_integrity_failed" },
    });
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      before.committedScene,
    );
  });

  it("restores the full ledger when Replay throws before returning a playback", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 2);
    const before = runtime.getSnapshot();
    renderer.playCheckpointChoreography.mockImplementationOnce(() => {
      throw new Error("renderer unavailable");
    });

    await runtime.replayAccepted();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: before.committedScene,
      committedSemanticScene: before.committedSemanticScene,
      accepted: before.accepted,
      rendererTrusted: true,
      error: { code: "replay_integrity_failed" },
    });
  });

  it("interrupts Replay to the exact presented prefix", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 3);

    const replay = runtime.replayAccepted();
    await flush();
    settle(renderer.rendered[3]);
    await flush();
    expect(runtime.interrupt()).toBe(true);
    settle(renderer.rendered[4], "cancelled_to_checkpoint");
    await replay;

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 2 },
      committedSemanticScene: { revision: 2 },
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(2);
  });

  it("rejects a failed terminal after a checkpoint without committing active work", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot();
    const run = await start(runtime, runner);
    emit(run, started(2, 1), createParametricCheckpointFixture(1, 2, 1));
    const active = renderer.rendered[1];
    emit(
      run,
      decodeParametricChoreographySceneStreamEventV3({
        type: "parametric_choreography_scene_stream_failed",
        generation: 2,
        attempt: 1,
        code: "choreography_integrity_error",
        message: "Invalid suffix.",
        lastAcceptedRevision: 2,
        retryable: false,
      }),
    );

    expect(run.invocation.signal.aborted).toBe(true);
    expect(active.playback.cancel).toHaveBeenCalledOnce();
    const failed = runtime.getSnapshot();
    expect(failed).toMatchObject({
      phase: "failed",
      rendererTrusted: true,
      error: { code: "invalid_stream_event", retryable: true },
    });
    expectCommittedFrontierUnchanged(failed, before);
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      before.committedScene,
    );
    expect(renderer.materializeViewport).toHaveBeenLastCalledWith(
      before.committedViewport,
    );

    settle(active);
    await flush();
    expectCommittedFrontierUnchanged(runtime.getSnapshot(), before);
  });

  it("locks compact viewport selection across the accepted frontier", async () => {
    const { runtime, renderer, runner } = createRuntime({ layout: "compact" });
    await acceptPrefix(runtime, renderer, runner, 2);
    const first = createParametricCheckpointFixture(0);
    const second = createParametricCheckpointFixture(1);

    expect(renderer.materializeViewport).toHaveBeenNthCalledWith(
      1,
      first.semantic.presentation.baseViewports.compact,
    );
    expect(runtime.getSnapshot().committedViewport).toEqual(
      second.semantic.presentation.resultViewports.compact,
    );
    expect(
      runtime
        .getSnapshot()
        .accepted.every((entry) => entry.layout === "compact"),
    ).toBe(true);
  });

  it("ignores every event after a terminal and aborts active work on dispose", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);
    const events = createParametricLifecycleFixture(1);
    emit(run, ...events, createParametricCheckpointFixture(1));
    expect(renderer.rendered).toHaveLength(1);
    expect(runtime.getSnapshot().sequence).toBe(1);

    runtime.dispose();
    expect(run.invocation.signal.aborted).toBe(true);
    expect(renderer.rendered[0].playback.cancel).toHaveBeenCalledOnce();
    emit(run, createParametricCheckpointFixture(1));
    expect(renderer.rendered).toHaveLength(1);
    expect(() => runtime.subscribe(() => undefined)).toThrow(/disposed/);
  });

  it("rejects a late event from a different generation without touching accepted state", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptPrefix(runtime, renderer, runner, 1);
    const before = runtime.getSnapshot().committedScene;
    const run = await start(runtime, runner);
    emit(run, started(1, 1));
    await flush();

    expect(run.invocation.signal.aborted).toBe(true);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: before,
      provisionalScene: before,
    });
  });
});
