import { describe, expect, it, vi } from "vitest";

import type {
  MotionPlan,
  SceneNode,
  SceneState,
} from "@/lib/live-scene";
import type {
  MotionPlayback,
  MotionPlaybackOutcome,
  MotionPlaybackOptions,
} from "@/features/canvas/types";

import {
  decodeSceneStreamEvent,
  type SceneStreamEvent,
} from "./model-stream";
import {
  SceneStreamRuntime,
  type SceneStreamRenderer,
  type SceneStreamRunner,
  type SceneStreamRunInvocation,
} from "./stream-runtime";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolveValue, rejectValue) => {
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

class ControlledPlayback implements MotionPlayback {
  readonly finished: Promise<MotionPlaybackOutcome>;
  readonly pause = vi.fn();
  readonly resume = vi.fn();
  readonly cancel: ReturnType<typeof vi.fn<() => MotionPlaybackOutcome>>;
  private readonly completion = deferred<MotionPlaybackOutcome>();
  private settled = false;
  cancelOutcome: MotionPlaybackOutcome = {
    status: "cancelled",
    appliedStepIds: [],
  };

  constructor() {
    this.finished = this.completion.promise;
    this.cancel = vi.fn(() => {
      this.settle(this.cancelOutcome);
      return this.cancelOutcome;
    });
  }

  settle(outcome: MotionPlaybackOutcome): void {
    if (this.settled) return;
    this.settled = true;
    this.completion.resolve(outcome);
  }
}

interface RenderedPlayback {
  readonly plan: MotionPlan;
  readonly options?: MotionPlaybackOptions;
  readonly playback: ControlledPlayback;
}

class ControlledRenderer implements SceneStreamRenderer {
  readonly rendered: RenderedPlayback[] = [];
  readonly cancelMotion = vi.fn();
  readonly clear = vi.fn();
  readonly playMotionPlan = vi.fn(
    (plan: MotionPlan, options?: MotionPlaybackOptions): MotionPlayback => {
      const playback = new ControlledPlayback();
      this.rendered.push({ plan, options, playback });
      return playback;
    }
  );
}

interface CapturedRun {
  readonly invocation: SceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function runnerHarness(): { readonly runStream: SceneStreamRunner; readonly runs: CapturedRun[] } {
  const runs: CapturedRun[] = [];
  const runStream: SceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runStream, runs };
}

function textNode(id: string, text = id): SceneNode {
  return {
    id,
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x: 400,
    y: 80,
    text,
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 28,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function started(generation = 1, baseRevision = 0): SceneStreamEvent {
  return decodeSceneStreamEvent({
    type: "scene_stream_started",
    generation,
    attempt: 1,
    baseRevision,
  });
}

function patch(options: {
  generation?: number;
  attempt?: number;
  sequence?: number;
  baseRevision?: number;
  patchId?: string;
  nodes?: readonly SceneNode[];
  operations?: readonly unknown[];
} = {}): SceneStreamEvent {
  const generation = options.generation ?? 1;
  const attempt = options.attempt ?? 1;
  const sequence = options.sequence ?? 1;
  const baseRevision = options.baseRevision ?? sequence - 1;
  const patchId = options.patchId ?? `patch-${sequence}`;
  const nodes = options.nodes ?? [textNode(`node-${sequence}`)];
  return decodeSceneStreamEvent({
    type: "scene_patch",
    generation,
    attempt,
    sequence,
    baseRevision,
    resultRevision: baseRevision + 1,
    patch: {
      v: 1,
      patchId,
      narration: `Apply scene patch ${sequence}.`,
      operations:
        options.operations ?? nodes.map((node) => ({ op: "put", node })),
    },
  });
}

function repairing(lastAcceptedRevision: number): SceneStreamEvent {
  return decodeSceneStreamEvent({
    type: "scene_stream_repairing",
    generation: 1,
    fromAttempt: 1,
    toAttempt: 2,
    lastAcceptedRevision,
    message: "Repairing the next visual patch.",
  });
}

function completed(options: {
  generation?: number;
  finalRevision: number;
  patchCount: number;
  repaired?: boolean;
}): SceneStreamEvent {
  return decodeSceneStreamEvent({
    type: "scene_stream_completed",
    generation: options.generation ?? 1,
    finalRevision: options.finalRevision,
    patchCount: options.patchCount,
    firstPatchMs: 40,
    totalMs: 90,
    repaired: options.repaired ?? false,
  });
}

function failed(lastAcceptedRevision: number, attempt = 1): SceneStreamEvent {
  return decodeSceneStreamEvent({
    type: "scene_stream_failed",
    generation: 1,
    attempt,
    code: "provider_error",
    message: "The visual generator is temporarily unavailable.",
    lastAcceptedRevision,
    retryable: true,
  });
}

async function startedRuntime(options: { queueLimit?: number } = {}) {
  const renderer = new ControlledRenderer();
  const harness = runnerHarness();
  const runtime = new SceneStreamRuntime({
    renderer,
    runStream: harness.runStream,
    staggerMs: 0,
    ...options,
  });
  runtime.start("Explain the theorem");
  await flushMicrotasks();
  expect(harness.runs).toHaveLength(1);
  harness.runs[0].invocation.onEvent(started());
  return { renderer, harness, runtime, run: harness.runs[0] };
}

function completePlayback(playback: ControlledPlayback, appliedStepIds: readonly string[]) {
  playback.settle({ status: "completed", appliedStepIds });
}

describe("SceneStreamRuntime", () => {
  it("publishes immutable committed/provisional snapshots and serializes playback", async () => {
    const renderer = new ControlledRenderer();
    const harness = runnerHarness();
    const runtime = new SceneStreamRuntime({
      renderer,
      runStream: harness.runStream,
      staggerMs: 0,
    });
    const snapshots: SceneState[] = [];
    const unsubscribe = runtime.subscribe(() => {
      snapshots.push(runtime.getSnapshot().committedScene);
    });

    expect(Object.isFrozen(runtime.getSnapshot())).toBe(true);
    expect(Object.isFrozen(runtime.getSnapshot().accepted)).toBe(true);
    expect(runtime.start("Explain Pythagoras")).toBe(1);
    await flushMicrotasks();
    const run = harness.runs[0];
    run.invocation.onEvent(started());
    run.invocation.onEvent(patch({ sequence: 1 }));

    expect(renderer.rendered).toHaveLength(1);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "streaming",
      generation: 1,
      attempt: 1,
      sequence: 1,
      queuedPatchCount: 0,
      activeRevision: 1,
      committedScene: { revision: 0 },
      provisionalScene: { revision: 1 },
    });

    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    run.invocation.onEvent(completed({ finalRevision: 2, patchCount: 2 }));
    expect(renderer.rendered).toHaveLength(1);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      queuedPatchCount: 1,
      provisionalScene: { revision: 2 },
      completion: { firstPatchMs: 40, totalMs: 90, repaired: false },
    });

    completePlayback(renderer.rendered[0].playback, ["node-1"]);
    await flushMicrotasks();
    expect(renderer.rendered).toHaveLength(2);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      committedScene: { revision: 1 },
      activeRevision: 2,
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(1);

    completePlayback(renderer.rendered[1].playback, ["node-2"]);
    await flushMicrotasks();
    run.completion.resolve();
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
      queuedPatchCount: 0,
    });
    expect(runtime.getSnapshot().accepted.map((record) => record.scene.revision)).toEqual([
      1, 2,
    ]);
    expect(runtime.getSnapshot().accepted.every(Object.isFrozen)).toBe(true);
    expect(snapshots.length).toBeGreaterThan(4);
    expect(runtime.interrupt()).toBe(false);
    unsubscribe();
  });

  it("does not invoke a runner after same-tick interruption, reset, or disposal", async () => {
    for (const stop of ["interrupt", "reset", "dispose"] as const) {
      const renderer = new ControlledRenderer();
      const harness = runnerHarness();
      const runtime = new SceneStreamRuntime({ renderer, runStream: harness.runStream });
      runtime.start(`Stop with ${stop}`);

      if (stop === "interrupt") runtime.interrupt();
      else if (stop === "reset") runtime.reset();
      else runtime.dispose();

      await flushMicrotasks();
      expect(harness.runs, stop).toHaveLength(0);
    }
  });

  it("isolates throwing snapshot subscribers from lifecycle and other subscribers", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const renderer = new ControlledRenderer();
    const harness = runnerHarness();
    const runtime = new SceneStreamRuntime({ renderer, runStream: harness.runStream });
    const healthySubscriber = vi.fn();
    runtime.subscribe(() => {
      throw new Error("subscriber failed");
    });
    runtime.subscribe(healthySubscriber);

    expect(() => runtime.start("Keep the lifecycle alive")).not.toThrow();
    await flushMicrotasks();
    expect(harness.runs).toHaveLength(1);
    harness.runs[0].invocation.onEvent(started());

    expect(runtime.getSnapshot().phase).toBe("streaming");
    expect(healthySubscriber).toHaveBeenCalledTimes(2);
    expect(errorLog).toHaveBeenCalledTimes(2);
    errorLog.mockRestore();
  });

  it("fails closed on mismatched generation, attempt, sequence, base revision, or patch ID", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const cases: Array<{
      readonly name: string;
      readonly prepare?: (run: CapturedRun) => void;
      readonly event: SceneStreamEvent;
      readonly phase?: "failed" | "completing";
    }> = [
      { name: "generation", event: started(2) },
      { name: "attempt", event: patch({ attempt: 2 }) },
      { name: "sequence", event: patch({ sequence: 2, baseRevision: 0 }) },
      { name: "base revision", event: patch({ sequence: 1, baseRevision: 1 }) },
      {
        name: "duplicate patch ID",
        prepare: (run) => run.invocation.onEvent(patch()),
        phase: "completing",
        event: patch({
          sequence: 2,
          baseRevision: 1,
          patchId: "patch-1",
        }),
      },
    ];

    for (const scenario of cases) {
      const renderer = new ControlledRenderer();
      const harness = runnerHarness();
      const runtime = new SceneStreamRuntime({ renderer, runStream: harness.runStream });
      runtime.start(`Check ${scenario.name}`);
      await flushMicrotasks();
      const run = harness.runs[0];
      if (scenario.name !== "generation") run.invocation.onEvent(started());
      scenario.prepare?.(run);
      run.invocation.onEvent(scenario.event);

      expect(runtime.getSnapshot(), scenario.name).toMatchObject({
        phase: scenario.phase ?? "failed",
        committedScene: { revision: 0 },
        error: {
          code: "invalid_stream_event",
          message: "The visual stream stopped. Your last accepted board is safe.",
        },
      });
      expect(run.invocation.signal.aborted, scenario.name).toBe(true);
      runtime.dispose();
    }
    expect(warning).toHaveBeenCalledTimes(cases.length);
    warning.mockRestore();
  });

  it("accepts one authoritative repair transition and resumes from its provisional revision", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(patch());
    run.invocation.onEvent(repairing(1));
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "repairing",
      attempt: 2,
      sequence: 1,
      provisionalScene: { revision: 1 },
    });

    run.invocation.onEvent(
      patch({ attempt: 2, sequence: 2, baseRevision: 1, patchId: "repair-2" })
    );
    run.invocation.onEvent(
      completed({ finalRevision: 2, patchCount: 2, repaired: true })
    );
    completePlayback(renderer.rendered[0].playback, ["node-1"]);
    await flushMicrotasks();
    completePlayback(renderer.rendered[1].playback, ["node-2"]);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      attempt: 2,
      committedScene: { revision: 2 },
      completion: { repaired: true },
    });
    expect(runtime.getSnapshot().accepted.map((record) => record.attempt)).toEqual([1, 2]);
  });

  it("drains already accepted patches after a terminal model failure", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(patch());
    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    run.invocation.onEvent(failed(2));

    expect(run.invocation.signal.aborted).toBe(true);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 2 },
      queuedPatchCount: 1,
      error: { code: "provider_error", retryable: true },
    });

    completePlayback(renderer.rendered[0].playback, ["node-1"]);
    await flushMicrotasks();
    completePlayback(renderer.rendered[1].playback, ["node-2"]);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
      queuedPatchCount: 0,
      error: { code: "provider_error" },
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(2);
    expect(runtime.start("Try a new generation")).toBe(2);
  });

  it("interrupts the exact stream, materializes applied steps, and ignores late work", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(
      patch({ nodes: [textNode("visible-node"), textNode("pending-node")] })
    );
    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    const playback = renderer.rendered[0].playback;
    playback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["visible-node"],
    };

    expect(runtime.interrupt()).toBe(true);
    const interrupted = runtime.getSnapshot();
    expect(run.invocation.signal.aborted).toBe(true);
    expect(playback.cancel).toHaveBeenCalledOnce();
    expect(renderer.cancelMotion).toHaveBeenCalled();
    expect(interrupted).toMatchObject({
      phase: "interrupted",
      sequence: 1,
      queuedPatchCount: 0,
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
    });
    expect(interrupted.committedScene.nodes.map((node) => node.id)).toEqual([
      "visible-node",
    ]);
    expect(interrupted.accepted).toMatchObject([
      { sequence: 1, materialized: true, scene: { revision: 1 } },
    ]);

    run.invocation.onEvent(completed({ finalRevision: 2, patchCount: 2 }));
    run.completion.resolve();
    playback.settle({ status: "completed", appliedStepIds: ["visible-node", "pending-node"] });
    await flushMicrotasks();
    expect(runtime.getSnapshot()).toBe(interrupted);
    expect(renderer.rendered).toHaveLength(1);
    expect(runtime.interrupt()).toBe(false);
  });

  it("materializes only executor-confirmed replacements and removals", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(
      patch({
        nodes: [
          textNode("keep-node", "Keep"),
          textNode("replace-node", "Before"),
          textNode("remove-node", "Remove"),
        ],
      })
    );
    run.invocation.onEvent(completed({ finalRevision: 1, patchCount: 1 }));
    completePlayback(renderer.rendered[0].playback, [
      "keep-node",
      "replace-node",
      "remove-node",
    ]);
    await flushMicrotasks();

    runtime.start("Change the existing explanation");
    await flushMicrotasks();
    const secondRun = harness.runs[1];
    secondRun.invocation.onEvent(started(2, 1));
    secondRun.invocation.onEvent(
      patch({
        generation: 2,
        baseRevision: 1,
        patchId: "generation-2-patch-1",
        operations: [
          { op: "put", node: textNode("replace-node", "After") },
          { op: "remove", id: "remove-node" },
          { op: "put", node: textNode("pending-enter", "Pending") },
        ],
      })
    );
    const playback = renderer.rendered[1].playback;
    playback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["replace-node", "remove-node"],
    };

    runtime.interrupt();

    expect(runtime.getSnapshot().committedScene).toMatchObject({ revision: 2 });
    expect(runtime.getSnapshot().committedScene.nodes.map((node) => node.id)).toEqual([
      "keep-node",
      "replace-node",
    ]);
    expect(
      runtime.getSnapshot().committedScene.nodes.find((node) => node.id === "replace-node")
    ).toMatchObject({ kind: "text", text: "After" });
    expect(runtime.getSnapshot().accepted.at(-1)).toMatchObject({
      generation: 2,
      sequence: 1,
      materialized: true,
    });
  });

  it("resets synchronously and rejects every callback from the invalidated token", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(patch());
    runtime.reset();

    expect(run.invocation.signal.aborted).toBe(true);
    expect(renderer.rendered[0].playback.cancel).toHaveBeenCalledOnce();
    expect(renderer.clear).toHaveBeenCalledOnce();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "idle",
      generation: 0,
      attempt: 0,
      sequence: 0,
      committedScene: { revision: 0, nodes: [] },
      provisionalScene: { revision: 0, nodes: [] },
      accepted: [],
    });

    const resetSnapshot = runtime.getSnapshot();
    run.invocation.onEvent(failed(1));
    run.completion.reject(new Error("late network failure"));
    await flushMicrotasks();
    expect(runtime.getSnapshot()).toBe(resetSnapshot);
    expect(runtime.start("Start cleanly again")).toBe(1);
  });

  it("replays the accepted ledger sequentially with zero additional model calls", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(patch());
    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    run.invocation.onEvent(completed({ finalRevision: 2, patchCount: 2 }));
    completePlayback(renderer.rendered[0].playback, ["node-1"]);
    await flushMicrotasks();
    completePlayback(renderer.rendered[1].playback, ["node-2"]);
    await flushMicrotasks();
    const acceptedBefore = runtime.getSnapshot().accepted;
    const finalScene = runtime.getSnapshot().committedScene;

    const replay = runtime.replayAccepted();
    expect(runtime.getSnapshot().phase).toBe("replaying");
    expect(renderer.clear).toHaveBeenCalledOnce();
    expect(renderer.rendered).toHaveLength(3);
    expect(harness.runs).toHaveLength(1);

    completePlayback(renderer.rendered[2].playback, ["node-1"]);
    await flushMicrotasks();
    expect(renderer.rendered).toHaveLength(4);
    completePlayback(renderer.rendered[3].playback, ["node-2"]);
    await replay;

    expect(harness.runs).toHaveLength(1);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
    });
    expect(runtime.getSnapshot().committedScene).toEqual(finalScene);
    expect(runtime.getSnapshot().accepted).toEqual(acceptedBefore);
    expect(renderer.rendered.map(({ plan }) => [plan.fromRevision, plan.toRevision])).toEqual([
      [0, 1],
      [1, 2],
      [0, 1],
      [1, 2],
    ]);
  });

  it("truncates abandoned replay revisions before branching from the visible scene", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(patch());
    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    run.invocation.onEvent(patch({ sequence: 3, baseRevision: 2 }));
    run.invocation.onEvent(completed({ finalRevision: 3, patchCount: 3 }));
    for (let index = 0; index < 3; index += 1) {
      completePlayback(renderer.rendered[index].playback, [`node-${index + 1}`]);
      await flushMicrotasks();
    }
    expect(runtime.getSnapshot().accepted.map((record) => record.scene.revision)).toEqual([
      1, 2, 3,
    ]);

    const replay = runtime.replayAccepted();
    const replayPlayback = renderer.rendered[3].playback;
    replayPlayback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["node-1"],
    };
    runtime.interrupt();
    await replay;

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
    });
    expect(runtime.getSnapshot().accepted.map((record) => record.scene.revision)).toEqual([1]);

    expect(runtime.start("Branch from the visible replay state")).toBe(2);
    await flushMicrotasks();
    const branchRun = harness.runs[1];
    expect(branchRun.invocation.request.baseScene.revision).toBe(1);
    branchRun.invocation.onEvent(started(2, 1));
    branchRun.invocation.onEvent(
      patch({
        generation: 2,
        baseRevision: 1,
        patchId: "branch-patch-1",
        nodes: [textNode("branch-node")],
      })
    );
    branchRun.invocation.onEvent(
      completed({ generation: 2, finalRevision: 2, patchCount: 1 })
    );
    completePlayback(renderer.rendered[4].playback, ["branch-node"]);
    await flushMicrotasks();

    expect(runtime.getSnapshot().accepted.map((record) => record.scene.revision)).toEqual([
      1, 2,
    ]);
    const branchReplay = runtime.replayAccepted();
    completePlayback(renderer.rendered[5].playback, ["node-1"]);
    await flushMicrotasks();
    completePlayback(renderer.rendered[6].playback, ["branch-node"]);
    await branchReplay;
    expect(runtime.getSnapshot().committedScene.revision).toBe(2);
    expect(harness.runs).toHaveLength(2);
  });

  it("bounds the waiting queue and preserves patches accepted before overflow", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, runtime, run } = await startedRuntime({ queueLimit: 1 });
    run.invocation.onEvent(patch());
    run.invocation.onEvent(patch({ sequence: 2, baseRevision: 1 }));
    run.invocation.onEvent(patch({ sequence: 3, baseRevision: 2 }));

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      provisionalScene: { revision: 2 },
      queuedPatchCount: 1,
      error: { code: "invalid_stream_event" },
    });
    expect(run.invocation.signal.aborted).toBe(true);
    completePlayback(renderer.rendered[0].playback, ["node-1"]);
    await flushMicrotasks();
    completePlayback(renderer.rendered[1].playback, ["node-2"]);
    await flushMicrotasks();
    expect(runtime.getSnapshot().committedScene.revision).toBe(2);
    expect(runtime.getSnapshot().phase).toBe("failed");
    expect(runtime.getSnapshot().accepted).toHaveLength(2);
    warning.mockRestore();
  });

  it("fails a stream that settles without a terminal lifecycle event", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { runtime, run } = await startedRuntime();
    run.completion.resolve();
    await flushMicrotasks();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      error: { code: "invalid_stream_event" },
      committedScene: { revision: 0 },
    });
    expect(run.invocation.signal.aborted).toBe(true);
    warning.mockRestore();
  });
});
