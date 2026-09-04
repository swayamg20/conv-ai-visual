import { describe, expect, it, vi } from "vitest";

import type {
  MotionPlayback,
  MotionPlaybackOptions,
  MotionPlaybackOutcome,
} from "@/features/canvas/types";
import {
  PYTHAGOREAN_ROLE_ORDER,
  decodeSemanticScenePatchEvent,
  type MotionPlan,
  type PythagoreanRole,
  type SemanticScenePatchEvent,
} from "@/lib/live-scene";

import {
  decodeSceneStreamEvent,
  decodeSemanticSceneStreamEvent,
  type SemanticSceneStreamEvent,
  type SemanticSceneStreamRunInvocation,
  type SemanticSceneStreamRunner,
} from "./model-stream";
import {
  SceneStreamRuntime,
  SceneStreamRuntimeError,
  type SceneStreamRenderer,
  type SceneStreamRunInvocation,
  type SceneStreamRunner,
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
    this.cancel = vi.fn(() => this.cancelOutcome);
  }

  settle(outcome: MotionPlaybackOutcome): void {
    if (this.settled) return;
    this.settled = true;
    this.completion.resolve(outcome);
  }

  fail(error = new Error("presentation barrier failed")): void {
    if (this.settled) return;
    this.settled = true;
    this.completion.reject(error);
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
  readonly invocation: SemanticSceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function runnerHarness(): {
  readonly runStream: SemanticSceneStreamRunner;
  readonly runs: CapturedRun[];
} {
  const runs: CapturedRun[] = [];
  const runStream: SemanticSceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runStream, runs };
}

interface CapturedRawRun {
  readonly invocation: SceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function rawRunnerHarness(): {
  readonly runStream: SceneStreamRunner;
  readonly runs: CapturedRawRun[];
} {
  const runs: CapturedRawRun[] = [];
  const runStream: SceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runStream, runs };
}

function digest(seed: number): string {
  return seed.toString(16).padStart(64, "0");
}

function certificateDigest(ordinal: number): string {
  return digest(100 + ordinal);
}

function semanticPatch(options: {
  generation?: number;
  attempt?: number;
  sequence?: number;
  ordinal?: number;
  baseRevision?: number;
  componentId?: string;
  certificateSha256?: string;
  previousCertificateSha256?: string | null;
} = {}): SemanticScenePatchEvent {
  const generation = options.generation ?? 1;
  const attempt = options.attempt ?? 1;
  const ordinal = options.ordinal ?? 1;
  const sequence = options.sequence ?? ordinal;
  const baseRevision = options.baseRevision ?? ordinal - 1;
  const role = PYTHAGOREAN_ROLE_ORDER[ordinal - 1] as PythagoreanRole;
  const componentId = options.componentId ?? "areas";
  const atomId = `${componentId}__atom_${role}`;
  const nodeId = `${componentId}__${role}`;
  const narration = `Reveal ${role}.`;
  const previousCertificateSha256 =
    options.previousCertificateSha256 !== undefined
      ? options.previousCertificateSha256
      : ordinal === 1
        ? null
        : certificateDigest(ordinal - 1);
  const certificateSha256 =
    options.certificateSha256 ?? certificateDigest(ordinal);

  return decodeSemanticScenePatchEvent({
    type: "semantic_scene_patch",
    generation,
    attempt,
    sequence,
    baseRevision,
    resultRevision: baseRevision + 1,
    patch: {
      v: 1,
      patchId: atomId,
      narration,
      operations: [
        {
          op: "put",
          node: {
            id: nodeId,
            kind: "text",
            presentation: { enter: "fade", exit: "fade" },
            x: 400,
            y: 60 + ordinal * 36,
            text: role,
            style: {
              color: "hsl(var(--chalk))",
              fontSize: 24,
              opacity: 1,
              anchor: "middle",
            },
          },
        },
      ],
    },
    semantic: {
      beat: {
        v: 1,
        beatId: "beat-identity",
        narration,
        act: "derive",
        directive: {
          kind: "pythagorean_area_identity",
          id: componentId,
          revealThrough: "identity",
        },
      },
      atomId,
      componentId,
      role,
      atomOrdinal: ordinal,
      semanticBaseRevision: baseRevision,
      semanticResultRevision: baseRevision + 1,
      receipt: {
        issuer: "semantic_verifier",
        componentId,
        role,
        nodeId,
        obligationCodes: ["stable_id"],
        verified: true,
      },
      certificate: {
        body: {
          v: 1,
          issuer: "semantic_compiler",
          compilerVersion: "murmur.pythagorean_area_identity.v1",
          canonicalization: "murmur-json-v1",
          hashAlgorithm: "sha256",
          atomId,
          beatId: "beat-identity",
          beatSha256: digest(1),
          componentId,
          role,
          nodeId,
          atomOrdinal: ordinal,
          baseSemanticRevision: baseRevision,
          resultSemanticRevision: baseRevision + 1,
          baseSceneSha256: digest(10 + ordinal),
          resultSceneSha256: digest(20 + ordinal),
          patchSha256: digest(30 + ordinal),
          receiptSha256: digest(40 + ordinal),
          previousCertificateSha256,
        },
        certificateSha256,
      },
    },
  });
}

function started(generation = 1, baseRevision = 0): SemanticSceneStreamEvent {
  return decodeSemanticSceneStreamEvent({
    type: "scene_stream_started",
    generation,
    attempt: 1,
    baseRevision,
  });
}

function completed(options: {
  generation?: number;
  finalRevision: number;
  patchCount: number;
}): SemanticSceneStreamEvent {
  return decodeSemanticSceneStreamEvent({
    type: "scene_stream_completed",
    generation: options.generation ?? 1,
    finalRevision: options.finalRevision,
    patchCount: options.patchCount,
    firstPatchMs: 12,
    totalMs: 24,
    repaired: false,
  });
}

function failed(lastAcceptedRevision: number): SemanticSceneStreamEvent {
  return decodeSemanticSceneStreamEvent({
    type: "scene_stream_failed",
    generation: 1,
    attempt: 1,
    code: "provider_error",
    message: "The model stream ended early.",
    lastAcceptedRevision,
    retryable: true,
  });
}

function completePlayback(playback: ControlledPlayback, ordinal: number): void {
  playback.settle({
    status: "completed",
    appliedStepIds: [`areas__${PYTHAGOREAN_ROLE_ORDER[ordinal - 1]}`],
  });
}

function semanticSnapshot(runtime: SceneStreamRuntime) {
  const semantic = runtime.getSnapshot().semantic;
  expect(semantic).toBeDefined();
  if (!semantic) throw new Error("semantic runtime did not expose semantic state");
  return semantic;
}

async function startedRuntime() {
  const renderer = new ControlledRenderer();
  const harness = runnerHarness();
  const runtime = new SceneStreamRuntime({
    protocol: "semantic",
    renderer,
    runStream: harness.runStream,
    staggerMs: 0,
  });
  runtime.start("Explain the area identity");
  await flushMicrotasks();
  expect(harness.runs).toHaveLength(1);
  const run = harness.runs[0];
  run.invocation.onEvent(started());
  return { renderer, harness, runtime, run };
}

describe("SceneStreamRuntime semantic protocol", () => {
  it("keeps paired admitted state provisional until serialized post-presentation receipts commit", async () => {
    const { renderer, runtime, run } = await startedRuntime();

    expect(run.invocation.request).toMatchObject({
      prompt: "Explain the area identity",
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    });
    expect(Object.isFrozen(run.invocation.request)).toBe(true);
    expect(Object.isFrozen(run.invocation.request.baseScene)).toBe(true);
    expect(Object.isFrozen(run.invocation.request.baseSemanticScene)).toBe(true);

    run.invocation.onEvent(semanticPatch({ ordinal: 1 }));
    run.invocation.onEvent(semanticPatch({ ordinal: 2 }));

    expect(renderer.rendered).toHaveLength(1);
    expect(renderer.rendered[0].plan).toMatchObject({
      fromRevision: 0,
      toRevision: 1,
      steps: [{ type: "enter", id: "areas__triangle" }],
    });
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "streaming",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 2 },
      queuedPatchCount: 1,
      activeRevision: 1,
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0, components: [] },
      provisionalScene: {
        revision: 2,
        components: [{ revealedRoles: ["triangle", "square_a"] }],
      },
      accepted: [],
    });

    run.invocation.onEvent(completed({ finalRevision: 2, patchCount: 2 }));
    expect(runtime.getSnapshot().phase).toBe("completing");
    expect(semanticSnapshot(runtime).committedScene.revision).toBe(0);

    completePlayback(renderer.rendered[0].playback, 1);
    await flushMicrotasks();

    expect(renderer.rendered).toHaveLength(2);
    expect(runtime.getSnapshot().committedScene.revision).toBe(1);
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: {
        revision: 1,
        certificateHeadSha256: certificateDigest(1),
      },
      accepted: [
        {
          scene: { revision: 1 },
          semanticScene: { revision: 1 },
          event: { semantic: { atomId: "areas__atom_triangle" } },
          presentation: {
            type: "semantic_atom_presented",
            atomId: "areas__atom_triangle",
            nodeId: "areas__triangle",
            certificateSha256: certificateDigest(1),
            sceneRevision: 1,
            semanticRevision: 1,
            settlement: "completed",
            appliedStepIds: ["areas__triangle"],
          },
        },
      ],
      commitFrontier: { atomId: "areas__atom_triangle" },
    });

    completePlayback(renderer.rendered[1].playback, 2);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
      queuedPatchCount: 0,
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: {
        revision: 2,
        components: [{ revealedRoles: ["triangle", "square_a"] }],
        certificateHeadSha256: certificateDigest(2),
      },
      provisionalScene: { revision: 2 },
      accepted: [{}, {}],
      commitFrontier: {
        atomId: "areas__atom_square_a",
        settlement: "completed",
      },
    });
  });

  it("deep-freezes the paired ledger, original event, and browser presentation receipt", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    run.invocation.onEvent(completed({ finalRevision: 1, patchCount: 1 }));
    completePlayback(renderer.rendered[0].playback, 1);
    await flushMicrotasks();

    const snapshot = runtime.getSnapshot();
    const semantic = semanticSnapshot(runtime);
    const record = semantic.accepted[0];

    expect(Object.isFrozen(snapshot)).toBe(true);
    expect(Object.isFrozen(semantic)).toBe(true);
    expect(Object.isFrozen(semantic.accepted)).toBe(true);
    expect(Object.isFrozen(semantic.committedScene)).toBe(true);
    expect(Object.isFrozen(semantic.committedScene.components)).toBe(true);
    expect(Object.isFrozen(record)).toBe(true);
    expect(Object.isFrozen(record.semanticScene)).toBe(true);
    expect(Object.isFrozen(record.event)).toBe(true);
    expect(Object.isFrozen(record.event.semantic)).toBe(true);
    expect(Object.isFrozen(record.event.semantic.certificate)).toBe(true);
    expect(Object.isFrozen(record.presentation)).toBe(true);
    expect(Object.isFrozen(record.presentation.appliedStepIds)).toBe(true);
    expect(semantic.commitFrontier).toBe(record.presentation);
  });

  it("holds interruption at the async presentation barrier and commits one exact cancelled atom", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch({ ordinal: 1 }));
    run.invocation.onEvent(semanticPatch({ ordinal: 2 }));
    const playback = renderer.rendered[0].playback;
    playback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["areas__triangle"],
    };
    renderer.cancelMotion.mockClear();

    expect(runtime.interrupt()).toBe(true);
    expect(run.invocation.signal.aborted).toBe(true);
    expect(playback.cancel).toHaveBeenCalledOnce();
    expect(renderer.cancelMotion).not.toHaveBeenCalled();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupting",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 1 },
      queuedPatchCount: 0,
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0 },
      provisionalScene: { revision: 1 },
      accepted: [],
    });
    expect(() => runtime.start("Must wait for the receipt")).toThrow(
      SceneStreamRuntimeError
    );

    // Every callback after token invalidation is abandoned, even before the
    // presentation barrier resolves.
    run.invocation.onEvent(semanticPatch({ ordinal: 2 }));
    run.invocation.onEvent(completed({ finalRevision: 2, patchCount: 2 }));
    run.completion.resolve();
    await flushMicrotasks();
    expect(runtime.getSnapshot().phase).toBe("interrupting");
    expect(renderer.rendered).toHaveLength(1);

    playback.settle(playback.cancelOutcome);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: {
        revision: 1,
        components: [{ revealedRoles: ["triangle"] }],
        certificateHeadSha256: certificateDigest(1),
      },
      provisionalScene: { revision: 1 },
      accepted: [
        {
          presentation: {
            settlement: "cancelled",
            appliedStepIds: ["areas__triangle"],
          },
        },
      ],
      commitFrontier: {
        atomId: "areas__atom_triangle",
        settlement: "cancelled",
      },
    });
    expect(renderer.cancelMotion).not.toHaveBeenCalled();

    expect(runtime.start("Continue from the exact frontier")).toBe(2);
    await flushMicrotasks();
    expect(harness.runs).toHaveLength(2);
    expect(harness.runs[1].invocation.request).toMatchObject({
      generation: 2,
      baseScene: { revision: 1 },
      baseSemanticScene: {
        revision: 1,
        components: [{ revealedRoles: ["triangle"] }],
        certificateHeadSha256: certificateDigest(1),
      },
    });
  });

  it("makes repeated interruption idempotent while one presentation barrier is pending", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    const playback = renderer.rendered[0].playback;
    playback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["areas__triangle"],
    };

    expect(runtime.interrupt()).toBe(true);
    expect(runtime.interrupt()).toBe(true);
    expect(runtime.getSnapshot().phase).toBe("interrupting");
    expect(playback.cancel).toHaveBeenCalledOnce();

    playback.settle(playback.cancelOutcome);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
    });
    expect(semanticSnapshot(runtime).accepted).toHaveLength(1);
    expect(playback.cancel).toHaveBeenCalledOnce();
  });

  it("interrupts a connecting semantic request synchronously before the runner starts", async () => {
    const renderer = new ControlledRenderer();
    const harness = runnerHarness();
    const runtime = new SceneStreamRuntime({
      protocol: "semantic",
      renderer,
      runStream: harness.runStream,
    });

    runtime.start("Cancel before transport begins");
    expect(runtime.getSnapshot().phase).toBe("connecting");
    expect(runtime.interrupt()).toBe(true);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });
    expect(renderer.cancelMotion).not.toHaveBeenCalled();

    await flushMicrotasks();
    expect(harness.runs).toHaveLength(0);
  });

  it("keeps global renderer cancellation available to explicit reset and disposal", async () => {
    const resetCase = await startedRuntime();
    resetCase.run.invocation.onEvent(semanticPatch());
    const resetPlayback = resetCase.renderer.rendered[0].playback;

    resetCase.runtime.reset();

    expect(resetPlayback.cancel).toHaveBeenCalledOnce();
    expect(resetCase.renderer.cancelMotion).toHaveBeenCalledOnce();
    expect(resetCase.renderer.clear).toHaveBeenCalledOnce();
    expect(resetCase.runtime.getSnapshot()).toMatchObject({
      phase: "idle",
      generation: 0,
      committedScene: { revision: 0, nodes: [] },
      provisionalScene: { revision: 0, nodes: [] },
    });
    expect(semanticSnapshot(resetCase.runtime)).toMatchObject({
      committedScene: { revision: 0, components: [] },
      provisionalScene: { revision: 0, components: [] },
      accepted: [],
    });

    const disposeCase = await startedRuntime();
    disposeCase.run.invocation.onEvent(semanticPatch());
    const disposePlayback = disposeCase.renderer.rendered[0].playback;

    disposeCase.runtime.dispose();

    expect(disposePlayback.cancel).toHaveBeenCalledOnce();
    expect(disposeCase.renderer.cancelMotion).toHaveBeenCalledOnce();
    expect(() => disposeCase.runtime.start("Disposed runtime")).toThrow(
      /has been disposed/
    );
  });

  it("commits no atom when an interrupted presentation reports no exact node", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    const playback = renderer.rendered[0].playback;
    playback.cancelOutcome = { status: "cancelled", appliedStepIds: [] };

    runtime.interrupt();
    expect(runtime.getSnapshot().phase).toBe("interrupting");
    playback.settle(playback.cancelOutcome);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      sequence: 0,
      committedScene: { revision: 0, nodes: [] },
      provisionalScene: { revision: 0, nodes: [] },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0, components: [] },
      provisionalScene: { revision: 0, components: [] },
      accepted: [],
    });
    expect(semanticSnapshot(runtime).commitFrontier).toBeUndefined();

    runtime.start("Restart from the empty semantic prefix");
    await flushMicrotasks();
    expect(harness.runs[1].invocation.request).toMatchObject({
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    });
  });

  it.each([
    {
      name: "wrong node",
      appliedStepIds: ["other-node"],
    },
    {
      name: "extra node",
      appliedStepIds: ["areas__triangle", "other-node"],
    },
  ])(
    "fails closed after the interrupt barrier returns a $name receipt",
    async ({ appliedStepIds }) => {
      const { renderer, runtime, run } = await startedRuntime();
      run.invocation.onEvent(semanticPatch());
      const playback = renderer.rendered[0].playback;
      playback.cancelOutcome = { status: "cancelled", appliedStepIds };
      renderer.cancelMotion.mockClear();

      runtime.interrupt();
      expect(runtime.getSnapshot().phase).toBe("interrupting");
      expect(renderer.cancelMotion).not.toHaveBeenCalled();

      playback.settle(playback.cancelOutcome);
      await flushMicrotasks();

      expect(runtime.getSnapshot()).toMatchObject({
        phase: "failed",
        committedScene: { revision: 0, nodes: [] },
        provisionalScene: { revision: 0, nodes: [] },
        error: { code: "renderer_failed", retryable: false },
      });
      expect(semanticSnapshot(runtime)).toMatchObject({
        committedScene: { revision: 0, components: [] },
        provisionalScene: { revision: 0, components: [] },
        accepted: [],
      });
      expect(renderer.cancelMotion).not.toHaveBeenCalled();
    }
  );

  it("rejects an exact cancelled receipt without an explicit runtime interruption", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    const playback = renderer.rendered[0].playback;

    playback.settle({
      status: "cancelled",
      appliedStepIds: ["areas__triangle"],
    });
    await flushMicrotasks();

    expect(playback.cancel).not.toHaveBeenCalled();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0, nodes: [] },
      provisionalScene: { revision: 0, nodes: [] },
      error: { code: "renderer_failed", retryable: false },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0, components: [] },
      provisionalScene: { revision: 0, components: [] },
      accepted: [],
    });
    expect(semanticSnapshot(runtime).commitFrontier).toBeUndefined();

    let blockedStart: unknown;
    try {
      runtime.start("This must not branch from untrusted visible ink");
    } catch (error) {
      blockedStart = error;
    }
    expect(blockedStart).toBeInstanceOf(SceneStreamRuntimeError);
    expect(blockedStart).toMatchObject({ code: "runtime_reset_required" });
    expect(harness.runs).toHaveLength(1);

    runtime.reset();
    expect(renderer.clear).toHaveBeenCalledOnce();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "idle",
      committedScene: { revision: 0, nodes: [] },
    });
    expect(runtime.getSnapshot().error).toBeUndefined();
    expect(runtime.start("Start again after an explicit reset")).toBe(1);
    await flushMicrotasks();
    expect(harness.runs).toHaveLength(2);
    warning.mockRestore();
  });

  it.each([
    {
      name: "missing node",
      outcome: { status: "completed", appliedStepIds: [] } as const,
    },
    {
      name: "wrong node",
      outcome: { status: "completed", appliedStepIds: ["other-node"] } as const,
    },
    {
      name: "extra node",
      outcome: {
        status: "completed",
        appliedStepIds: ["areas__triangle", "other-node"],
      } as const,
    },
    {
      name: "failed presentation",
      outcome: {
        status: "failed",
        appliedStepIds: ["areas__triangle"],
      } as const,
    },
  ])("fails closed on a $name receipt", async ({ outcome }) => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    renderer.rendered[0].playback.settle(outcome);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0, nodes: [] },
      provisionalScene: { revision: 0, nodes: [] },
      error: { code: "renderer_failed", retryable: false },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 0, components: [] },
      provisionalScene: { revision: 0, components: [] },
      accepted: [],
    });
    expect(semanticSnapshot(runtime).commitFrontier).toBeUndefined();
    warning.mockRestore();
  });

  it("turns a rejected presentation promise into the same fail-closed branch", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch());
    renderer.rendered[0].playback.fail();
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      error: { code: "renderer_failed", retryable: false },
    });
    expect(semanticSnapshot(runtime).accepted).toEqual([]);
    warning.mockRestore();
  });

  it("drains already admitted semantic atoms in order after a terminal provider failure", async () => {
    const { renderer, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch({ ordinal: 1 }));
    run.invocation.onEvent(semanticPatch({ ordinal: 2 }));
    run.invocation.onEvent(failed(2));

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 2 },
      error: { code: "provider_error" },
    });
    expect(renderer.rendered).toHaveLength(1);

    completePlayback(renderer.rendered[0].playback, 1);
    await flushMicrotasks();
    expect(renderer.rendered).toHaveLength(2);
    completePlayback(renderer.rendered[1].playback, 2);
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
      error: { code: "provider_error" },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
      accepted: [{}, {}],
      commitFrontier: { atomId: "areas__atom_square_a" },
    });
  });

  it("rejects a raw patch delivered to the semantic discriminator", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, runtime, run } = await startedRuntime();
    const rawEvent = decodeSceneStreamEvent({
      type: "scene_patch",
      generation: 1,
      attempt: 1,
      sequence: 1,
      baseRevision: 0,
      resultRevision: 1,
      patch: {
        v: 1,
        patchId: "raw-patch",
        narration: "This is not a certified semantic atom.",
        operations: [
          {
            op: "put",
            node: {
              id: "raw-node",
              kind: "text",
              presentation: { enter: "fade", exit: "fade" },
              x: 400,
              y: 80,
              text: "raw",
              style: {
                color: "hsl(var(--chalk))",
                fontSize: 24,
                opacity: 1,
                anchor: "middle",
              },
            },
          },
        ],
      },
    });

    (run.invocation.onEvent as (event: unknown) => void)(rawEvent);

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      error: { code: "invalid_stream_event" },
    });
    expect(renderer.rendered).toHaveLength(0);
    expect(semanticSnapshot(runtime).accepted).toEqual([]);
    warning.mockRestore();
  });

  it("rejects a semantic patch delivered to the raw discriminator", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const renderer = new ControlledRenderer();
    const harness = rawRunnerHarness();
    const runtime = new SceneStreamRuntime({
      protocol: "raw",
      renderer,
      runStream: harness.runStream,
    });
    runtime.start("Keep the raw protocol isolated");
    await flushMicrotasks();
    const run = harness.runs[0];
    run.invocation.onEvent(
      decodeSceneStreamEvent({
        type: "scene_stream_started",
        generation: 1,
        attempt: 1,
        baseRevision: 0,
      })
    );

    (run.invocation.onEvent as (event: unknown) => void)(semanticPatch());

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      error: { code: "invalid_stream_event" },
    });
    expect(runtime.getSnapshot().semantic).toBeUndefined();
    expect(renderer.rendered).toHaveLength(0);
    warning.mockRestore();
  });

  it("retains 33 paired semantic atoms without applying the raw 32-record history cap", async () => {
    const renderer = new ControlledRenderer();
    const harness = runnerHarness();
    const runtime = new SceneStreamRuntime({
      protocol: "semantic",
      renderer,
      runStream: harness.runStream,
      staggerMs: 0,
    });
    const targetAtomCount = 33;
    let acceptedAtomCount = 0;

    for (let generation = 1; acceptedAtomCount < targetAtomCount; generation += 1) {
      runtime.start(`Semantic lecture batch ${generation}`);
      await flushMicrotasks();
      const run = harness.runs[generation - 1];
      run.invocation.onEvent(started(generation, acceptedAtomCount));
      const batchSize = Math.min(
        PYTHAGOREAN_ROLE_ORDER.length,
        targetAtomCount - acceptedAtomCount
      );
      const events: SemanticScenePatchEvent[] = [];

      for (let batchIndex = 0; batchIndex < batchSize; batchIndex += 1) {
        const ordinal = batchIndex + 1;
        const globalOrdinal = acceptedAtomCount + ordinal;
        const event = semanticPatch({
          generation,
          sequence: ordinal,
          ordinal,
          baseRevision: globalOrdinal - 1,
          componentId: `areas${generation}`,
          certificateSha256: digest(1_000 + globalOrdinal),
          previousCertificateSha256:
            globalOrdinal === 1 ? null : digest(999 + globalOrdinal),
        });
        events.push(event);
        run.invocation.onEvent(event);
      }
      run.invocation.onEvent(
        completed({
          generation,
          finalRevision: acceptedAtomCount + batchSize,
          patchCount: batchSize,
        })
      );

      for (const event of events) {
        const rendered = renderer.rendered[acceptedAtomCount];
        rendered.playback.settle({
          status: "completed",
          appliedStepIds: [event.semantic.receipt.nodeId],
        });
        acceptedAtomCount += 1;
        await flushMicrotasks();
      }
      run.completion.resolve();
      await flushMicrotasks();
    }

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: targetAtomCount },
      provisionalScene: { revision: targetAtomCount },
    });
    expect(runtime.getSnapshot().accepted).toHaveLength(targetAtomCount);
    expect(runtime.getSnapshot().accepted[0].scene.revision).toBe(1);
    expect(runtime.getSnapshot().accepted.at(-1)?.scene.revision).toBe(
      targetAtomCount
    );
    expect(semanticSnapshot(runtime).accepted).toHaveLength(targetAtomCount);
    expect(semanticSnapshot(runtime).accepted[0].semanticScene.revision).toBe(1);
    expect(
      semanticSnapshot(runtime).accepted.at(-1)?.semanticScene.revision
    ).toBe(targetAtomCount);
  });

  it("replays the paired semantic ledger exactly without invoking the runner", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    for (let ordinal = 1; ordinal <= 3; ordinal += 1) {
      run.invocation.onEvent(semanticPatch({ ordinal }));
    }
    run.invocation.onEvent(completed({ finalRevision: 3, patchCount: 3 }));
    for (let index = 0; index < 3; index += 1) {
      completePlayback(renderer.rendered[index].playback, index + 1);
      await flushMicrotasks();
    }

    const acceptedBefore = semanticSnapshot(runtime).accepted;
    const finalScene = runtime.getSnapshot().committedScene;
    const finalSemanticScene = semanticSnapshot(runtime).committedScene;
    const frontierBefore = semanticSnapshot(runtime).commitFrontier;
    const renderedBeforeReplay = renderer.rendered.length;

    const replay = runtime.replayAccepted();
    expect(runtime.getSnapshot().phase).toBe("replaying");
    expect(renderer.clear).toHaveBeenCalledOnce();
    expect(renderer.rendered).toHaveLength(renderedBeforeReplay + 1);
    expect(harness.runs).toHaveLength(1);
    expect(runtime.getSnapshot().accepted).toEqual([]);
    expect(semanticSnapshot(runtime).accepted).toEqual([]);
    expect(semanticSnapshot(runtime).commitFrontier).toBeUndefined();

    for (let index = 0; index < 3; index += 1) {
      const rendered = renderer.rendered[renderedBeforeReplay + index];
      expect(rendered.plan.steps).toMatchObject([
        {
          type: "enter",
          id: `areas__${PYTHAGOREAN_ROLE_ORDER[index]}`,
        },
      ]);
      completePlayback(rendered.playback, index + 1);
      await flushMicrotasks();

      expect(runtime.getSnapshot().accepted).toHaveLength(index + 1);
      expect(semanticSnapshot(runtime).accepted).toHaveLength(index + 1);
      expect(semanticSnapshot(runtime).commitFrontier).toMatchObject({
        atomId: `areas__atom_${PYTHAGOREAN_ROLE_ORDER[index]}`,
        settlement: "completed",
      });
    }
    await replay;

    expect(harness.runs).toHaveLength(1);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: finalScene,
      provisionalScene: finalScene,
    });
    expect(semanticSnapshot(runtime).committedScene).toEqual(finalSemanticScene);
    expect(semanticSnapshot(runtime).provisionalScene).toEqual(finalSemanticScene);
    expect(semanticSnapshot(runtime).accepted).toEqual(acceptedBefore);
    expect(semanticSnapshot(runtime).commitFrontier).toEqual(frontierBefore);
  });

  it("recovers a nonempty trusted prefix through replay before allowing another generation", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { renderer, harness, runtime, run } = await startedRuntime();
    run.invocation.onEvent(semanticPatch({ ordinal: 1 }));
    run.invocation.onEvent(semanticPatch({ ordinal: 2 }));

    completePlayback(renderer.rendered[0].playback, 1);
    await flushMicrotasks();
    expect(semanticSnapshot(runtime).accepted).toHaveLength(1);

    renderer.rendered[1].playback.settle({
      status: "completed",
      appliedStepIds: [],
    });
    await flushMicrotasks();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
      error: { code: "renderer_failed", retryable: false },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
      accepted: [{}],
      commitFrontier: { atomId: "areas__atom_triangle" },
    });

    let blockedStart: unknown;
    try {
      runtime.start("Do not trust the leftover visible second atom");
    } catch (error) {
      blockedStart = error;
    }
    expect(blockedStart).toBeInstanceOf(SceneStreamRuntimeError);
    expect(blockedStart).toMatchObject({ code: "runtime_reset_required" });
    expect(harness.runs).toHaveLength(1);

    const replayIndex = renderer.rendered.length;
    const replay = runtime.replayAccepted();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "replaying",
      accepted: [],
    });
    expect(runtime.getSnapshot().error).toBeUndefined();
    expect(semanticSnapshot(runtime).accepted).toEqual([]);
    expect(semanticSnapshot(runtime).commitFrontier).toBeUndefined();

    completePlayback(renderer.rendered[replayIndex].playback, 1);
    await replay;

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
      accepted: [{}],
    });
    expect(runtime.getSnapshot().error).toBeUndefined();
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
      accepted: [{}],
      commitFrontier: {
        atomId: "areas__atom_triangle",
        settlement: "completed",
      },
    });

    expect(runtime.start("Continue from the replayed trusted prefix")).toBe(2);
    await flushMicrotasks();
    expect(harness.runs).toHaveLength(2);
    expect(harness.runs[1].invocation.request).toMatchObject({
      generation: 2,
      baseScene: { revision: 1 },
      baseSemanticScene: {
        revision: 1,
        certificateHeadSha256: certificateDigest(1),
      },
    });
    warning.mockRestore();
  });

  it("waits for an interrupted replay receipt, then truncates to the exact paired prefix", async () => {
    const { renderer, harness, runtime, run } = await startedRuntime();
    for (let ordinal = 1; ordinal <= 3; ordinal += 1) {
      run.invocation.onEvent(semanticPatch({ ordinal }));
    }
    run.invocation.onEvent(completed({ finalRevision: 3, patchCount: 3 }));
    for (let index = 0; index < 3; index += 1) {
      completePlayback(renderer.rendered[index].playback, index + 1);
      await flushMicrotasks();
    }

    const acceptedBeforeReplay = semanticSnapshot(runtime).accepted;
    const replayStart = renderer.rendered.length;
    const replay = runtime.replayAccepted();
    completePlayback(renderer.rendered[replayStart].playback, 1);
    await flushMicrotasks();
    const interruptedPlayback = renderer.rendered[replayStart + 1].playback;
    interruptedPlayback.cancelOutcome = {
      status: "cancelled",
      appliedStepIds: ["areas__square_a"],
    };
    renderer.cancelMotion.mockClear();

    expect(runtime.interrupt()).toBe(true);
    expect(runtime.getSnapshot().phase).toBe("interrupting");
    expect(renderer.cancelMotion).not.toHaveBeenCalled();
    expect(() => runtime.start("Cannot branch before the replay receipt")).toThrow(
      SceneStreamRuntimeError
    );

    interruptedPlayback.settle(interruptedPlayback.cancelOutcome);
    await replay;

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 2 },
      provisionalScene: { revision: 2 },
    });
    expect(semanticSnapshot(runtime)).toMatchObject({
      committedScene: {
        revision: 2,
        components: [{ revealedRoles: ["triangle", "square_a"] }],
        certificateHeadSha256: certificateDigest(2),
      },
      provisionalScene: { revision: 2 },
      accepted: [{}, {}],
      commitFrontier: {
        atomId: "areas__atom_square_a",
        settlement: "cancelled",
      },
    });
    expect(semanticSnapshot(runtime).accepted[1].presentation).not.toBe(
      acceptedBeforeReplay[1].presentation
    );
    expect(renderer.cancelMotion).not.toHaveBeenCalled();

    runtime.start("Branch from the presented replay prefix");
    await flushMicrotasks();
    expect(harness.runs).toHaveLength(2);
    expect(harness.runs[1].invocation.request).toMatchObject({
      baseScene: { revision: 2 },
      baseSemanticScene: {
        revision: 2,
        components: [{ revealedRoles: ["triangle", "square_a"] }],
        certificateHeadSha256: certificateDigest(2),
      },
    });
  });
});
