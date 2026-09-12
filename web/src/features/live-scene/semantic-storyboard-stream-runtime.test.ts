import { describe, expect, it, vi } from "vitest";

import type {
  PlannedCheckpointChoreography,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";
import type {
  PairedProjectileComparisonSpecV1,
  SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import type { SemanticStoryboardSceneStreamEventV1 } from "@/lib/live-scene/semantic-storyboard-stream";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import type { SemanticStoryboardSceneStreamRunner } from "./semantic-storyboard-model-stream";
import { createSemanticStoryboardFixtureRunner } from "./semantic-storyboard-scene-stream-fixture";
import {
  SemanticStoryboardStreamRuntime,
  SemanticStoryboardRuntimeError,
  type SemanticStoryboardRenderer,
  type SemanticStoryboardRuntimePhase,
} from "./semantic-storyboard-stream-runtime";

const COMPLEMENTARY_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  anglesDeg: [30, 60],
} as const satisfies PairedProjectileComparisonSpecV1);
const UNEQUAL_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  anglesDeg: [30, 45],
} as const satisfies PairedProjectileComparisonSpecV1);

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  const promise = new Promise<Value>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

async function tick(): Promise<void> {
  await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
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

class ControlledRenderer implements SemanticStoryboardRenderer {
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

interface StreamCall {
  readonly request: SemanticStoryboardRequestV1;
  readonly signal: AbortSignal;
  readonly events: SemanticStoryboardSceneStreamEventV1[];
}

function instrumentedRunner(options: {
  readonly eventDelayMs: number;
  readonly chunkDelayMs?: number;
}) {
  const delegate = createSemanticStoryboardFixtureRunner(options);
  const calls: StreamCall[] = [];
  const runStream: SemanticStoryboardSceneStreamRunner = vi.fn(
    async (invocation) => {
      const call: StreamCall = {
        request: invocation.request,
        signal: invocation.signal,
        events: [],
      };
      calls.push(call);
      await delegate({
        ...invocation,
        onEvent: (event) => {
          call.events.push(event);
          invocation.onEvent(event);
        },
      });
    },
  );
  return { calls, runStream };
}

interface RenderCursor {
  value: number;
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

async function waitFor(
  predicate: () => boolean,
  message: string,
): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    if (predicate()) return;
    await tick();
  }
  throw new Error(`timed out waiting for ${message}`);
}

async function settleUntil(
  runtime: SemanticStoryboardStreamRuntime,
  renderer: ControlledRenderer,
  cursor: RenderCursor,
  phases: readonly SemanticStoryboardRuntimePhase[],
): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    while (cursor.value < renderer.rendered.length) {
      settle(renderer.rendered[cursor.value]);
      cursor.value += 1;
      await flush();
    }
    if (phases.includes(runtime.getSnapshot().phase)) return;
    await tick();
  }
  throw new Error(`runtime did not settle into ${phases.join(" or ")}`);
}

function createRuntime(eventDelayMs = 0) {
  const renderer = new ControlledRenderer();
  const runner = instrumentedRunner({ eventDelayMs, chunkDelayMs: 0 });
  const runtime = new SemanticStoryboardStreamRuntime({
    renderer,
    runStream: runner.runStream,
    layout: "cinematic",
  });
  return { runtime, renderer, runner, cursor: { value: 0 } };
}

async function runToTerminal(
  runtime: SemanticStoryboardStreamRuntime,
  renderer: ControlledRenderer,
  cursor: RenderCursor,
  command: Parameters<SemanticStoryboardStreamRuntime["start"]>[0],
  phases: readonly SemanticStoryboardRuntimePhase[] = ["completed"],
): Promise<void> {
  runtime.start(command);
  await settleUntil(runtime, renderer, cursor, phases);
}

function acceptedIds(runtime: SemanticStoryboardStreamRuntime): string[] {
  return runtime
    .getSnapshot()
    .accepted.map(
      (accepted) => accepted.event.transition.checkpoint.checkpointId,
    );
}

describe("SemanticStoryboardStreamRuntime with provider-free fixtures", () => {
  it("runs two different model programs through one runtime and replays locally", async () => {
    const { runtime, renderer, runner, cursor } = createRuntime();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    const anchor = runtime.getSnapshot();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "director",
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: "Show the mathematical reason for the equal ranges first.",
    });

    expect(runner.calls[1].request).toMatchObject({
      generation: 2,
      routingMode: "director",
      baseScene: anchor.committedScene,
      baseSemanticScene: anchor.committedSemanticScene,
    });
    const mathIds = acceptedIds(runtime);
    expect(mathIds).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-reveal-range-formula",
      "storyboard-checkpoint-reveal-complementary-angles",
      "storyboard-checkpoint-relate-equal-range",
    ]);

    runtime.reset();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "director",
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: "Begin with the higher arc, then compare height and time.",
    });
    const motionSnapshot = runtime.getSnapshot();
    const motionIds = acceptedIds(runtime);
    expect(motionIds).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-trace-higher-angle",
      "storyboard-checkpoint-trace-lower-angle",
      "storyboard-checkpoint-relate-higher-apex",
      "storyboard-checkpoint-relate-longer-flight",
    ]);
    expect(motionIds).not.toEqual(mathIds);

    const callsBeforeReplay = runner.calls.length;
    const replay = runtime.replayAccepted();
    await settleUntil(runtime, renderer, cursor, ["completed"]);
    await replay;
    expect(runner.calls).toHaveLength(callsBeforeReplay);
    expect(runtime.getSnapshot()).toMatchObject({
      committedScene: motionSnapshot.committedScene,
      committedSemanticScene: motionSnapshot.committedSemanticScene,
      accepted: motionSnapshot.accepted,
      rendererTrusted: true,
    });
  });

  it("retains a malformed-tail prefix and keeps a sole decline mutation-free", async () => {
    const { runtime, renderer, runner, cursor } = createRuntime();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    const anchor = runtime.getSnapshot();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "director",
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt:
        "Show one formula, then stop safely if later output is malformed.",
    });
    expect(runner.calls.at(-1)?.events.at(-1)).toMatchObject({
      type: "semantic_storyboard_scene_stream_completed",
      reasonCode: "accepted_prefix",
      acceptedPrefixCause: "invalid_model_stream",
    });
    expect(acceptedIds(runtime)).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-reveal-range-formula",
    ]);

    runtime.reset();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    const beforeDecline = runtime.getSnapshot();
    await runToTerminal(
      runtime,
      renderer,
      cursor,
      {
        routingMode: "director",
        problemSpec: COMPLEMENTARY_PROBLEM,
        prompt: "Do nothing if this request has no supported forward step.",
      },
      ["declined"],
    );
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "declined",
      committedScene: beforeDecline.committedScene,
      committedSemanticScene: beforeDecline.committedSemanticScene,
      accepted: beforeDecline.accepted,
      decline: { reasonCode: "unsupported_intent" },
    });
    expect(anchor.accepted).toHaveLength(1);
  });

  it("settles only the in-flight beat when interrupted and ignores the tail", async () => {
    const { runtime, renderer, runner, cursor } = createRuntime(4);
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    const renderedBefore = renderer.rendered.length;
    runtime.start({
      routingMode: "director",
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: "Begin with the higher arc, then compare height and time.",
    });
    await waitFor(
      () => renderer.rendered.length > renderedBefore,
      "the first Director playback",
    );
    const active = renderer.rendered.at(-1)!;
    expect(runtime.interrupt()).toBe(true);
    expect(active.playback.cancel).toHaveBeenCalledOnce();
    settle(active, "cancelled_to_checkpoint");
    cursor.value = renderer.rendered.length;
    await waitFor(
      () => runtime.getSnapshot().phase === "interrupted",
      "interruption settlement",
    );
    const settled = structuredClone(runtime.getSnapshot());
    expect(acceptedIds(runtime)).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-trace-higher-angle",
    ]);
    expect(runner.calls.at(-1)?.signal.aborted).toBe(true);

    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 30));
    expect(runtime.getSnapshot()).toEqual(settled);
  });

  it("rejects a mismatched Director problem before invoking the runner", async () => {
    const { runtime, renderer, runner, cursor } = createRuntime();
    await runToTerminal(runtime, renderer, cursor, {
      routingMode: "reflex",
      problemSpec: COMPLEMENTARY_PROBLEM,
    });
    const callsBefore = runner.calls.length;
    let rejected: unknown;
    try {
      runtime.start({
        routingMode: "director",
        problemSpec: UNEQUAL_PROBLEM,
        prompt: "Explain a different problem.",
      });
    } catch (error) {
      rejected = error;
    }
    expect(rejected).toBeInstanceOf(SemanticStoryboardRuntimeError);
    expect(rejected).toMatchObject({ code: "invalid_command" });
    expect(runner.calls).toHaveLength(callsBefore);
  });
});
