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

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import type { SemanticStoryboardSceneStreamRunner } from "./semantic-storyboard-model-stream";
import {
  createSemanticStoryboardFixtureBatch,
  createSemanticStoryboardFixtureRunner,
} from "./semantic-storyboard-scene-stream-fixture";
import {
  SemanticStoryboardSessionController,
  SemanticStoryboardSessionError,
} from "./semantic-storyboard-session-controller";
import {
  SemanticStoryboardStreamRuntime,
  type SemanticStoryboardRenderer,
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

const HIGHER_ARC_PROMPT =
  "Begin with the higher arc, then compare height and time.";
const UNEQUAL_PROMPT =
  "Use the range formula to show that these distances differ.";
const CONTINUE_PROMPT = "Continue with exactly one new useful visual beat.";
const PREFIX_PROMPT =
  "Show one formula, then stop safely if later output is malformed.";
const ABSTAIN_PROMPT =
  "Do nothing if this request has no supported forward step.";

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  const promise = new Promise<Value>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
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
}

interface Harness {
  readonly runtime: SemanticStoryboardStreamRuntime;
  readonly controller: SemanticStoryboardSessionController;
  readonly renderer: ControlledRenderer;
  readonly calls: StreamCall[];
  readonly tasks: Array<() => void>;
  readonly cursor: { value: number };
}

function createHarness(
  delegate?: SemanticStoryboardSceneStreamRunner,
): Harness {
  const renderer = new ControlledRenderer();
  const calls: StreamCall[] = [];
  const tasks: Array<() => void> = [];
  const fixture =
    delegate ??
    createSemanticStoryboardFixtureRunner({ eventDelayMs: 0, chunkDelayMs: 0 });
  const runStream: SemanticStoryboardSceneStreamRunner = vi.fn(
    async (invocation) => {
      calls.push({ request: invocation.request, signal: invocation.signal });
      await fixture(invocation);
    },
  );
  const runtime = new SemanticStoryboardStreamRuntime({
    renderer,
    runStream,
    layout: "cinematic",
  });
  const controller = new SemanticStoryboardSessionController({
    runtime,
    enqueue: (task) => tasks.push(task),
  });
  return {
    runtime,
    controller,
    renderer,
    calls,
    tasks,
    cursor: { value: 0 },
  };
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
  description: string,
): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    if (predicate()) return;
    await tick();
  }
  throw new Error(`timed out waiting for ${description}`);
}

async function settleAvailable(harness: Harness): Promise<void> {
  while (harness.cursor.value < harness.renderer.rendered.length) {
    settle(harness.renderer.rendered[harness.cursor.value]);
    harness.cursor.value += 1;
    await tick();
  }
}

async function settleUntil(
  harness: Harness,
  statuses: readonly string[],
): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    await settleAvailable(harness);
    if (statuses.includes(harness.controller.getSnapshot().status)) return;
    await tick();
  }
  throw new Error(`session did not settle into ${statuses.join(" or ")}`);
}

async function reachHandoff(
  harness: Harness,
  prompt = HIGHER_ARC_PROMPT,
  problemSpec = COMPLEMENTARY_PROBLEM,
): Promise<void> {
  harness.controller.startFresh({ problemSpec, prompt });
  await waitFor(
    () => harness.renderer.rendered.length > harness.cursor.value,
    "anchor playback",
  );
  settle(harness.renderer.rendered[harness.cursor.value]);
  harness.cursor.value += 1;
  await waitFor(
    () => harness.controller.getSnapshot().status === "director_handoff",
    "Director handoff",
  );
}

async function dispatchHandoff(harness: Harness): Promise<void> {
  const task = harness.tasks.shift();
  if (!task) throw new Error("Director handoff was not queued");
  task();
  await waitFor(() => harness.calls.length === 2, "Director dispatch");
}

async function completeFresh(
  harness: Harness,
  prompt = HIGHER_ARC_PROMPT,
  problemSpec = COMPLEMENTARY_PROBLEM,
): Promise<void> {
  await reachHandoff(harness, prompt, problemSpec);
  await dispatchHandoff(harness);
  await settleUntil(harness, ["paused", "declined"]);
}

describe("SemanticStoryboardSessionController", () => {
  it("waits for anchor post-paint settlement, then dispatches Director generation 2 from that exact frontier", async () => {
    const harness = createHarness();
    harness.controller.startFresh({
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: HIGHER_ARC_PROMPT,
    });
    await waitFor(
      () => harness.renderer.rendered.length === 1,
      "anchor playback",
    );
    await waitFor(
      () => harness.runtime.getSnapshot().completion !== undefined,
      "anchor completion wire event",
    );

    expect(harness.runtime.getSnapshot().phase).toBe("completing");
    expect(harness.calls).toHaveLength(1);
    expect(harness.calls[0].request.routingMode).toBe("reflex");
    expect(harness.tasks).toHaveLength(0);
    expect(harness.controller.getSnapshot()).toMatchObject({
      status: "anchoring",
      pendingDirector: true,
      progress: { settledBeatCount: 0, frontierStatus: "live" },
    });

    settle(harness.renderer.rendered[0]);
    harness.cursor.value = 1;
    await waitFor(
      () => harness.controller.getSnapshot().status === "director_handoff",
      "queued handoff",
    );
    const anchor = harness.runtime.getSnapshot();
    expect(harness.calls).toHaveLength(1);
    expect(harness.tasks).toHaveLength(1);

    await dispatchHandoff(harness);
    expect(harness.calls[1].request).toEqual({
      protocol: "projectile_comparison_storyboard_v1",
      routingMode: "director",
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: HIGHER_ARC_PROMPT,
      generation: 2,
      baseScene: anchor.committedScene,
      baseSemanticScene: anchor.committedSemanticScene,
    });
    harness.controller.dispose();
  });

  it("cancels a queued handoff and an in-flight anchor without ever dispatching Director", async () => {
    const queued = createHarness();
    await reachHandoff(queued);
    expect(queued.controller.interrupt()).toBe(true);
    queued.tasks.shift()?.();
    await tick();
    expect(queued.calls).toHaveLength(1);
    expect(queued.controller.getSnapshot()).toMatchObject({
      status: "paused",
      pendingDirector: false,
    });

    const painting = createHarness();
    painting.controller.startFresh({
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: HIGHER_ARC_PROMPT,
    });
    await waitFor(
      () => painting.renderer.rendered.length === 1,
      "active anchor",
    );
    const anchor = painting.renderer.rendered[0];
    expect(painting.controller.interrupt()).toBe(true);
    expect(anchor.playback.cancel).toHaveBeenCalledOnce();
    settle(anchor, "cancelled_to_checkpoint");
    painting.cursor.value = 1;
    await waitFor(
      () => painting.controller.getSnapshot().status === "paused",
      "settled anchor interruption",
    );
    await tick();
    expect(painting.calls).toHaveLength(1);
    expect(painting.tasks).toHaveLength(0);
  });

  it("continues directly from the committed problem and frontier without wiping", async () => {
    const harness = createHarness();
    await completeFresh(harness);
    const before = harness.runtime.getSnapshot();
    const clearCount = harness.renderer.clear.mock.calls.length;
    const generation = harness.controller.continueWithPrompt(CONTINUE_PROMPT);
    expect(generation).toBe(3);
    await waitFor(() => harness.calls.length === 3, "follow-up dispatch");
    expect(harness.calls[2].request).toMatchObject({
      routingMode: "director",
      generation: 3,
      problemSpec: before.committedSemanticScene.components[0].problemSpec,
      baseScene: before.committedScene,
      baseSemanticScene: before.committedSemanticScene,
    });
    expect(harness.renderer.clear).toHaveBeenCalledTimes(clearCount);
    await settleUntil(harness, ["paused"]);
    expect(harness.controller.getSnapshot().progress.settledBeatCount).toBe(5);
  });

  it("replays the exact accepted frontier with a live indicator and zero runner calls", async () => {
    const harness = createHarness();
    await completeFresh(harness);
    const before = structuredClone(harness.runtime.getSnapshot());
    const callCount = harness.calls.length;
    const replay = harness.controller.replayAccepted();
    expect(harness.controller.getSnapshot()).toMatchObject({
      status: "replaying",
      pendingDirector: false,
      progress: { frontierStatus: "live", settledBeatCount: 4 },
    });
    await settleUntil(harness, ["paused"]);
    await replay;
    expect(harness.calls).toHaveLength(callCount);
    expect(harness.runtime.getSnapshot()).toMatchObject({
      committedScene: before.committedScene,
      committedSemanticScene: before.committedSemanticScene,
      accepted: before.accepted,
      rendererTrusted: true,
    });
  });

  it.each(["anchor", "handoff", "director", "replay"] as const)(
    "makes reset during %s stale-safe",
    async (stage) => {
      const harness = createHarness();
      let staleTask: (() => void) | undefined;
      let stalePlayback: RenderedCheckpoint | undefined;
      let pendingReplay: Promise<void> | undefined;

      if (stage === "anchor") {
        harness.controller.startFresh({
          problemSpec: COMPLEMENTARY_PROBLEM,
          prompt: HIGHER_ARC_PROMPT,
        });
        await waitFor(
          () => harness.renderer.rendered.length === 1,
          "anchor playback",
        );
        stalePlayback = harness.renderer.rendered[0];
      } else {
        await reachHandoff(harness);
        if (stage === "handoff") {
          staleTask = harness.tasks.shift();
        } else {
          await dispatchHandoff(harness);
          await waitFor(
            () => harness.renderer.rendered.length > harness.cursor.value,
            "Director playback",
          );
          if (stage === "director") {
            stalePlayback = harness.renderer.rendered[harness.cursor.value];
          } else {
            await settleUntil(harness, ["paused"]);
            pendingReplay = harness.controller.replayAccepted();
            stalePlayback = harness.renderer.rendered.at(-1);
          }
        }
      }

      const callCount = harness.calls.length;
      harness.controller.reset();
      staleTask?.();
      if (stalePlayback) settle(stalePlayback);
      await pendingReplay;
      await tick();
      expect(harness.calls).toHaveLength(callCount);
      expect(harness.controller.getSnapshot()).toMatchObject({
        status: "ready",
        pendingDirector: false,
        problemSpec: null,
        progress: { settledBeatCount: 0, frontierStatus: "paused" },
        runtime: {
          generation: 0,
          committedScene: { revision: 0, nodes: [] },
          accepted: [],
        },
      });
    },
  );

  it("prevents a stale A handoff from dispatching after reset and fresh B", async () => {
    const harness = createHarness();
    await reachHandoff(harness);
    const staleA = harness.tasks.shift();
    harness.controller.reset();

    harness.controller.startFresh({
      problemSpec: UNEQUAL_PROBLEM,
      prompt: UNEQUAL_PROMPT,
    });
    await waitFor(
      () => harness.renderer.rendered.length > harness.cursor.value,
      "B anchor playback",
    );
    settle(harness.renderer.rendered[harness.cursor.value]);
    harness.cursor.value += 1;
    await waitFor(() => harness.tasks.length === 1, "B handoff");

    staleA?.();
    await tick();
    expect(harness.calls).toHaveLength(2);
    harness.tasks.shift()?.();
    await waitFor(() => harness.calls.length === 3, "B Director dispatch");
    expect(harness.calls[2].request).toMatchObject({
      routingMode: "director",
      problemSpec: UNEQUAL_PROBLEM,
      prompt: UNEQUAL_PROMPT,
    });
  });

  it.each([
    { problemSpec: COMPLEMENTARY_PROBLEM, prompt: null },
    { problemSpec: COMPLEMENTARY_PROBLEM, prompt: "\u3000\ufeff\u202f" },
    { problemSpec: COMPLEMENTARY_PROBLEM, prompt: "x".repeat(2_001) },
    {
      problemSpec: { ...COMPLEMENTARY_PROBLEM, speedMps: 19 },
      prompt: HIGHER_ARC_PROMPT,
    },
  ])("prevalidates invalid fresh input with zero call or mutation", (input) => {
    const harness = createHarness();
    const before = harness.controller.getSnapshot();
    expect(() => harness.controller.startFresh(input)).toThrow();
    expect(harness.calls).toHaveLength(0);
    expect(harness.controller.getSnapshot()).toEqual(before);
  });

  it("surfaces an accepted prefix as a paused, counted, replayable frontier", async () => {
    const harness = createHarness();
    await completeFresh(harness, PREFIX_PROMPT);
    expect(harness.controller.getSnapshot()).toMatchObject({
      status: "paused",
      progress: {
        settledBeatCount: 1,
        frontierStatus: "paused",
        recentCertifiedLabels: ["Range formula revealed"],
      },
      controls: { canContinue: true, canReplay: true },
      runtime: {
        completion: {
          metadata: {
            reasonCode: "accepted_prefix",
            detailCode: "invalid_model_stream",
          },
        },
      },
    });
  });

  it("keeps a sole abstention mutation-free with Continue and Replay available", async () => {
    const harness = createHarness();
    await reachHandoff(harness, ABSTAIN_PROMPT);
    const anchor = structuredClone(harness.runtime.getSnapshot());
    await dispatchHandoff(harness);
    await settleUntil(harness, ["declined"]);
    expect(harness.runtime.getSnapshot()).toMatchObject({
      committedScene: anchor.committedScene,
      committedSemanticScene: anchor.committedSemanticScene,
      accepted: anchor.accepted,
    });
    expect(harness.controller.getSnapshot()).toMatchObject({
      status: "declined",
      progress: { settledBeatCount: 0, recentCertifiedLabels: [] },
      controls: { canContinue: true, canReplay: true },
    });
  });

  it("shows only the latest two model-record labels and moves live to paused", async () => {
    const harness = createHarness();
    await reachHandoff(harness);
    expect(harness.controller.getSnapshot().progress).toMatchObject({
      settledBeatCount: 0,
      frontierStatus: "live",
      recentCertifiedLabels: [],
    });
    await dispatchHandoff(harness);
    await waitFor(
      () => harness.renderer.rendered.length > harness.cursor.value,
      "first model beat",
    );
    settle(harness.renderer.rendered[harness.cursor.value]);
    harness.cursor.value += 1;
    await waitFor(
      () => harness.controller.getSnapshot().progress.settledBeatCount === 1,
      "first accepted model beat",
    );
    expect(harness.controller.getSnapshot()).toMatchObject({
      status: "directing",
      progress: {
        frontierStatus: "live",
        recentCertifiedLabels: ["Higher trajectory traced"],
      },
    });
    await settleUntil(harness, ["paused"]);
    expect(harness.controller.getSnapshot().progress).toEqual({
      kind: "open",
      settledBeatCount: 4,
      frontierStatus: "paused",
      recentCertifiedLabels: [
        "Higher apex connected",
        "Longer flight connected",
      ],
      progressAriaLabel: "4 certified beats settled; frontier paused",
    });
  });

  it("rejects duplicate fresh and continuation actions during handoff without dispatch", async () => {
    const harness = createHarness();
    await reachHandoff(harness);
    expect(() =>
      harness.controller.startFresh({
        problemSpec: COMPLEMENTARY_PROBLEM,
        prompt: HIGHER_ARC_PROMPT,
      }),
    ).toThrow(SemanticStoryboardSessionError);
    expect(() =>
      harness.controller.continueWithPrompt(CONTINUE_PROMPT),
    ).toThrowError(expect.objectContaining({ code: "session_busy" }));
    expect(harness.calls).toHaveLength(1);
    expect(harness.tasks).toHaveLength(1);
  });

  it("clears a queued handoff before Replay and makes disposal inert", async () => {
    const replaying = createHarness();
    await reachHandoff(replaying);
    const staleReplayHandoff = replaying.tasks.shift();
    const replay = replaying.controller.replayAccepted();
    expect(replaying.controller.getSnapshot().pendingDirector).toBe(false);
    staleReplayHandoff?.();
    await settleUntil(replaying, ["paused"]);
    await replay;
    expect(replaying.calls).toHaveLength(1);

    const disposed = createHarness();
    await reachHandoff(disposed);
    const staleDisposeHandoff = disposed.tasks.shift();
    disposed.controller.dispose();
    staleDisposeHandoff?.();
    await tick();
    expect(disposed.calls).toHaveLength(1);
    expect(() =>
      disposed.controller.continueWithPrompt(CONTINUE_PROMPT),
    ).toThrow("disposed");
  });

  it("fails closed when a completed Reflex stream is not an exact anchor terminal", async () => {
    const invalidAnchorRunner: SemanticStoryboardSceneStreamRunner = async (
      invocation,
    ) => {
      const batch = createSemanticStoryboardFixtureBatch({
        ...invocation.request,
        problemSpec: UNEQUAL_PROBLEM,
      });
      for (const event of batch.events) {
        invocation.onEvent(event);
      }
    };
    const harness = createHarness(invalidAnchorRunner);
    harness.controller.startFresh({
      problemSpec: COMPLEMENTARY_PROBLEM,
      prompt: HIGHER_ARC_PROMPT,
    });
    await waitFor(
      () => harness.renderer.rendered.length === 1,
      "invalid anchor playback",
    );
    settle(harness.renderer.rendered[0]);
    harness.cursor.value = 1;
    await waitFor(
      () => harness.controller.getSnapshot().status === "failed",
      "invalid anchor rejection",
    );
    expect(harness.controller.getSnapshot()).toMatchObject({
      pendingDirector: false,
      orchestrationError: { code: "invalid_anchor" },
      controls: {
        canContinue: false,
        canReplay: false,
        canReset: true,
      },
    });
    const renderCount = harness.renderer.rendered.length;
    expect(() => harness.controller.replayAccepted()).toThrowError(
      expect.objectContaining({ code: "session_reset_required" }),
    );
    expect(harness.calls).toHaveLength(1);
    expect(harness.renderer.rendered).toHaveLength(renderCount);
    expect(harness.tasks).toHaveLength(0);
  });
});
