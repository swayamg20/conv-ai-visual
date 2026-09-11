import { describe, expect, it, vi } from "vitest";

import type {
  ChoreographyCueKindV2,
  ChoreographyPlan,
  PlannedCheckpointChoreography,
  SceneState,
  ViewportPoseV1,
} from "@/lib/live-scene";
import {
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionClarificationTopic,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
  type ProjectileMotionStateV1,
} from "@/lib/live-scene/projectile-motion";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
  ChoreographyPlaybackOutcome,
} from "./choreography-executor";
import type {
  ProjectileChoreographySceneStreamRunInvocation,
  ProjectileChoreographySceneStreamRunner,
} from "./projectile-choreography-model-stream";
import {
  MAX_RETAINED_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  ProjectileChoreographyStreamRuntime,
  type ProjectileChoreographyCommand,
  type ProjectileChoreographyRenderer,
  type ProjectileChoreographyRuntimeSnapshot,
} from "./projectile-choreography-stream-runtime";

const PROBLEM_HASHES = Object.freeze({
  "20:30": "f2ac3f33e3a48dacdd0e256937330b6f36d39451968aa3882d92f3ce3c436491",
  "20:45": "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa",
  "20:60": "b6859d7baf2204ebc98de07b974b02c39521a27ea1c5b1eb475e3daa6a083b59",
  "25:30": "6014ed0114f009bfd6e50acd99ccdccd42f28a286465d9dc9a2987cf196ca85e",
  "25:45": "e947b64e547b77b7e7899b1f8936138d49d8f5a027b38e9878f4e991a34665c3",
  "25:60": "98add4003547fa75b0f8debc391ab4e8a17f2b8cc656f0f9b61b838f3fe82d6f",
  "30:30": "aa4172502435083f997288d04ccbde34d0fa446430d37b75700a2d4b8b0f5bfc",
  "30:45": "205df15d13df182f0a62c47c7c850888797d11fd52774db7d490d46c2fca80bb",
  "30:60": "3366d188d60fbfe6a523a63f9ee0ece7bee84eed709e056026d33f5b8859e8c3",
} satisfies Readonly<Record<string, string>>);

interface EventOptions {
  readonly generation: number;
  readonly sequence: number;
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly action: "advance" | "clarify" | "retarget";
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly route: ProjectileMotionRouteV1;
  readonly beatId: string;
  readonly beatBaseProblem: ProjectileMotionProblemSpecV1 | null;
  readonly baseComponent: ProjectileMotionStateV1 | null;
  readonly resultComponent: ProjectileMotionStateV1;
  readonly baseRevision: number;
  readonly baseHead: string | null;
}

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function problem(
  speedMps: 20 | 25 | 30 = 20,
  angleDeg: 30 | 45 | 60 = 45,
): ProjectileMotionProblemSpecV1 {
  return { v: 1, speedMps, angleDeg };
}

function problemHash(value: ProjectileMotionProblemSpecV1): string {
  return PROBLEM_HASHES[`${value.speedMps}:${value.angleDeg}`];
}

function component(
  problemSpec: ProjectileMotionProblemSpecV1,
  lastMainCheckpoint: ProjectileMotionStateV1["lastMainCheckpoint"],
  clarifiedTopics: readonly ProjectileMotionClarificationTopic[] = [],
  activeClarification: ProjectileMotionClarificationTopic | null = null,
): ProjectileMotionStateV1 {
  return {
    kind: "projectile_motion",
    id: "lesson",
    problemSpec,
    lastMainCheckpoint,
    clarifiedTopics,
    activeClarification,
  };
}

function head(revision: number): string {
  return ((revision % 9) + 1).toString().repeat(64);
}

function checkpointEvent(
  options: EventOptions,
): ProjectileChoreographySceneCheckpointEventV1 {
  const resultRevision = options.baseRevision + 1;
  const resultHead = head(resultRevision);
  const baseProblem = options.baseComponent?.problemSpec ?? null;
  const resultProblem = options.resultComponent.problemSpec;
  const baseProblemSpecSha256 =
    baseProblem === null ? null : problemHash(baseProblem);
  const resultProblemSpecSha256 = problemHash(resultProblem);
  const target = "lesson__equation";
  const narration = `${options.checkpointId} at ${resultProblem.speedMps} m/s and ${resultProblem.angleDeg} degrees.`;
  const viewport = { v: 1, x: 0, y: 0, width: 800, height: 600 };
  const presentation = {
    v: 1,
    checkpointId: options.checkpointId,
    checkpointNarration: narration,
    baseViewports: { cinematic: viewport, compact: viewport },
    resultViewports: { cinematic: viewport, compact: viewport },
    transientFree: true,
  };
  const beat = {
    v: 1,
    beatId: options.beatId,
    componentKind: "projectile_motion",
    componentId: "lesson",
    baseProblemSpec: options.beatBaseProblem,
    resultProblemSpec: resultProblem,
    route: options.route,
  };
  const patch = {
    v: 1,
    patchId: `lesson__cp_${options.checkpointId}`,
    narration,
    operations: [
      {
        op: "put",
        node: {
          id: target,
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 400,
          y: 80,
          text: narration,
          style: {
            color: "hsl(var(--chalk))",
            fontSize: 32,
            opacity: 1,
            anchor: "middle",
          },
        },
      },
    ],
  };
  const receipt = {
    issuer: "projectile_motion_verifier",
    componentKind: "projectile_motion",
    componentId: "lesson",
    action: options.action,
    checkpointId: options.checkpointId,
    clarificationTopic: options.clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
    operationTargets: [target],
    obligationCodes: [...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS],
    verified: true,
  };
  const choreography = {
    v: 2,
    phase: {
      cues: [
        {
          cue: options.baseRevision === 0 ? "enter" : "transform",
          targetIds: [target],
        },
      ],
      durationMs: 800,
      easing: "ease_out_quart",
      holdAfterMs: 500,
    },
  };
  const certificate = {
    body: {
      v: 1,
      issuer: "projectile_motion_compiler",
      compilerVersion: PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
      canonicalization: "murmur-json-v1",
      hashAlgorithm: "sha256",
      beatId: options.beatId,
      routedBeatSha256: "a".repeat(64),
      componentKind: "projectile_motion",
      componentId: "lesson",
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseProblemSpecSha256,
      resultProblemSpecSha256,
      baseLowLevelRevision: options.baseRevision,
      resultLowLevelRevision: resultRevision,
      baseSemanticRevision: options.baseRevision,
      resultSemanticRevision: resultRevision,
      baseLowLevelSceneSha256: "b".repeat(64),
      resultLowLevelSceneSha256: "c".repeat(64),
      baseSemanticSceneSha256: "d".repeat(64),
      resultSemanticSceneSha256: "e".repeat(64),
      patchSha256: "f".repeat(64),
      receiptSha256: "0".repeat(64),
      presentationCheckpoint: clone(presentation),
      choreographySha256: "1".repeat(64),
      previousCertificateSha256: options.baseHead,
    },
    certificateSha256: resultHead,
  };
  const decoded = decodeProjectileChoreographySceneStreamEventV1({
    type: "projectile_choreography_scene_checkpoint",
    generation: options.generation,
    attempt: 1,
    sequence: options.sequence,
    baseRevision: options.baseRevision,
    resultRevision,
    patch,
    semantic: {
      baseProblemSpec: baseProblem,
      resultProblemSpec: resultProblem,
      beat,
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseComponent: options.baseComponent,
      resultComponent: options.resultComponent,
      semanticBaseRevision: options.baseRevision,
      semanticResultRevision: resultRevision,
      semanticBaseCertificateSha256: options.baseHead,
      semanticResultCertificateSha256: resultHead,
      receipt,
      presentation,
      choreography,
      certificate,
    },
  });
  if (decoded.type !== "projectile_choreography_scene_checkpoint") {
    throw new Error("expected a projectile checkpoint fixture");
  }
  return decoded;
}

function mainSuffix(
  generation = 1,
  value = problem(),
): readonly ProjectileChoreographySceneCheckpointEventV1[] {
  let baseComponent: ProjectileMotionStateV1 | null = null;
  let baseHead: string | null = null;
  return PROJECTILE_MOTION_MAIN_CHECKPOINTS.map((checkpointId, index) => {
    const resultComponent = component(value, checkpointId);
    const event = checkpointEvent({
      generation,
      sequence: index + 1,
      checkpointId,
      action: "advance",
      clarificationTopic: null,
      route: { intent: "advance", targetStage: "solve" },
      beatId: `beat_main_${generation}`,
      beatBaseProblem: null,
      baseComponent,
      resultComponent,
      baseRevision: index,
      baseHead,
    });
    baseComponent = resultComponent;
    baseHead = event.semantic.semanticResultCertificateSha256;
    return event;
  });
}

function started(generation: number, baseRevision: number) {
  return decodeProjectileChoreographySceneStreamEventV1({
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
) {
  return decodeProjectileChoreographySceneStreamEventV1({
    type: "scene_stream_completed",
    generation,
    finalRevision,
    patchCount,
    firstPatchMs: 12,
    totalMs: 24,
    repaired: false,
  });
}

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
  readonly plan: PlannedCheckpointChoreography<ChoreographyPlan>;
  readonly observer?: ChoreographyExecutorObserver<ChoreographyCueKindV2>;
  readonly playback: ControlledPlayback;
}

class ControlledRenderer implements ProjectileChoreographyRenderer {
  readonly rendered: RenderedCheckpoint[] = [];
  readonly materializeScene = vi.fn((_scene: SceneState) => undefined);
  readonly materializeViewport = vi.fn(
    (_viewport: ViewportPoseV1) => undefined,
  );
  readonly cancelMotion = vi.fn();
  readonly clear = vi.fn();
  readonly playCheckpointChoreography = vi.fn(
    (
      plan: PlannedCheckpointChoreography<ChoreographyPlan>,
      observer?: ChoreographyExecutorObserver<ChoreographyCueKindV2>,
    ): ChoreographyPlayback => {
      const playback = new ControlledPlayback();
      this.rendered.push({ plan, observer, playback });
      return playback;
    },
  );
}

interface CapturedRun {
  readonly invocation: ProjectileChoreographySceneStreamRunInvocation;
  readonly completion: ReturnType<typeof deferred<void>>;
}

function harness() {
  const runs: CapturedRun[] = [];
  const runStream: ProjectileChoreographySceneStreamRunner = (invocation) => {
    const completion = deferred<void>();
    runs.push({ invocation, completion });
    return completion.promise;
  };
  return { runs, runStream };
}

const REFLEX: ProjectileChoreographyCommand = {
  routingMode: "reflex",
  problemSpec: problem(),
  requestedRoute: { intent: "advance", targetStage: "solve" },
};

function createRuntime(
  options: { layout?: "cinematic" | "compact"; queueLimit?: number } = {},
) {
  const renderer = new ControlledRenderer();
  const runner = harness();
  const runtime = new ProjectileChoreographyStreamRuntime({
    renderer,
    runStream: runner.runStream,
    layout: options.layout ?? "cinematic",
    ...(options.queueLimit ? { queueLimit: options.queueLimit } : {}),
  });
  return { runtime, renderer, runner };
}

async function start(
  runtime: ProjectileChoreographyStreamRuntime,
  runner: ReturnType<typeof harness>,
  command = REFLEX,
): Promise<CapturedRun> {
  runtime.start(command);
  await flush();
  return runner.runs.at(-1)!;
}

function emit(
  run: CapturedRun,
  ...events: readonly ProjectileChoreographySceneStreamEventV1[]
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

async function acceptStream(
  runtime: ProjectileChoreographyStreamRuntime,
  renderer: ControlledRenderer,
  runner: ReturnType<typeof harness>,
  command: ProjectileChoreographyCommand,
  events: readonly ProjectileChoreographySceneCheckpointEventV1[],
): Promise<CapturedRun> {
  const firstRendered = renderer.rendered.length;
  const run = await start(runtime, runner, command);
  const generation = run.invocation.request.generation;
  const baseRevision = run.invocation.request.baseScene.revision;
  emit(
    run,
    started(generation, baseRevision),
    ...events,
    completed(generation, baseRevision + events.length, events.length),
  );
  for (let index = 0; index < events.length; index += 1) {
    await flush();
    settle(renderer.rendered[firstRendered + index]);
  }
  run.completion.resolve();
  await flush();
  return run;
}

function currentComponent(
  snapshot: ProjectileChoreographyRuntimeSnapshot,
): ProjectileMotionStateV1 {
  const current = snapshot.committedSemanticScene.components[0];
  if (!current) throw new Error("expected an accepted projectile component");
  return current;
}

function sidecarEvent(options: {
  readonly snapshot: ProjectileChoreographyRuntimeSnapshot;
  readonly generation: number;
  readonly action: "clarify" | "retarget";
  readonly topic?: ProjectileMotionClarificationTopic;
  readonly targetProblem?: ProjectileMotionProblemSpecV1;
}): ProjectileChoreographySceneCheckpointEventV1 {
  const base = currentComponent(options.snapshot);
  if (options.action === "clarify") {
    const topic = options.topic!;
    const topics = PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter(
      (candidate) => base.clarifiedTopics.includes(candidate) || candidate === topic,
    );
    return checkpointEvent({
      generation: options.generation,
      sequence: 1,
      checkpointId:
        topic === "horizontal_velocity"
          ? "horizontal_velocity_detail"
          : topic === "apex_acceleration"
            ? "apex_acceleration_detail"
            : "flight_symmetry_detail",
      action: "clarify",
      clarificationTopic: topic,
      route: { intent: "clarify", topic },
      beatId: `beat_clarify_${options.generation}`,
      beatBaseProblem: base.problemSpec,
      baseComponent: base,
      resultComponent: component(
        base.problemSpec,
        base.lastMainCheckpoint,
        topics,
        topic,
      ),
      baseRevision: options.snapshot.committedScene.revision,
      baseHead:
        options.snapshot.committedSemanticScene.certificateHeadSha256 ?? null,
    });
  }
  const targetProblem = options.targetProblem!;
  return checkpointEvent({
    generation: options.generation,
    sequence: 1,
    checkpointId: "parameters_retargeted",
    action: "retarget",
    clarificationTopic: null,
    route: { intent: "retarget", targetProblemSpec: targetProblem },
    beatId: `beat_retarget_${options.generation}`,
    beatBaseProblem: base.problemSpec,
    baseComponent: base,
    resultComponent: component(
      targetProblem,
      base.lastMainCheckpoint,
      base.clarifiedTopics,
      base.activeClarification,
    ),
    baseRevision: options.snapshot.committedScene.revision,
    baseHead:
      options.snapshot.committedSemanticScene.certificateHeadSha256 ?? null,
  });
}

function expectFrontierUnchanged(
  after: ProjectileChoreographyRuntimeSnapshot,
  before: ProjectileChoreographyRuntimeSnapshot,
): void {
  expect(after.committedScene).toEqual(before.committedScene);
  expect(after.provisionalScene).toEqual(before.committedScene);
  expect(after.committedSemanticScene).toEqual(before.committedSemanticScene);
  expect(after.provisionalSemanticScene).toEqual(before.committedSemanticScene);
  expect(after.committedViewport).toEqual(before.committedViewport);
  expect(after.accepted).toEqual(before.accepted);
}

describe("ProjectileChoreographyStreamRuntime", () => {
  it("streams all six main checkpoints and commits only after paint settlement", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const run = await start(runtime, runner);
    const checkpoints = mainSuffix();

    expect(run.invocation.request).toEqual({
      protocol: "projectile_choreography_v1",
      routingMode: "reflex",
      problemSpec: problem(),
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });

    emit(run, started(1, 0), ...checkpoints, completed(1, 6, 6));
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completing",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 6 },
      activeRevision: 1,
    });

    for (let index = 0; index < checkpoints.length; index += 1) {
      settle(renderer.rendered[index]);
      await flush();
    }
    run.completion.resolve();
    await flush();

    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: { revision: 6 },
      committedSemanticScene: { revision: 6 },
      visibleCheckpointId: "summary",
      rendererTrusted: true,
    });
    expect(
      runtime.getSnapshot().accepted.map((record) =>
        record.event.semantic.checkpointId,
      ),
    ).toEqual(PROJECTILE_MOTION_MAIN_CHECKPOINTS);
  });

  it("interrupts before paint without mutation and after paint at the checkpoint", async () => {
    const beforePaint = createRuntime();
    const beforeRun = await start(beforePaint.runtime, beforePaint.runner);
    emit(beforeRun, started(1, 0), mainSuffix()[0]);
    expect(beforePaint.runtime.interrupt()).toBe(true);
    expect(beforeRun.invocation.signal.aborted).toBe(true);
    beforePaint.renderer.rendered[0].playback.settle({
      status: "cancelled_before_presented",
      firstCuePresented: false,
    });
    await flush();
    expect(beforePaint.runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 0 },
      provisionalScene: { revision: 0 },
      accepted: [],
    });

    const afterPaint = createRuntime();
    const afterRun = await start(afterPaint.runtime, afterPaint.runner);
    emit(afterRun, started(1, 0), mainSuffix()[0]);
    expect(afterPaint.runtime.interrupt()).toBe(true);
    settle(afterPaint.renderer.rendered[0], "cancelled_to_checkpoint");
    await flush();
    expect(afterPaint.runtime.getSnapshot()).toMatchObject({
      phase: "interrupted",
      committedScene: { revision: 1 },
      provisionalScene: { revision: 1 },
      visibleCheckpointId: "setup",
    });
    expect(afterPaint.runtime.getSnapshot().accepted).toHaveLength(1);
  });

  it("adds a clarification then retargets in place from exact request frontiers", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptStream(runtime, renderer, runner, REFLEX, mainSuffix());

    const clarification = sidecarEvent({
      snapshot: runtime.getSnapshot(),
      generation: 2,
      action: "clarify",
      topic: "flight_symmetry",
    });
    const clarifyRun = await acceptStream(
      runtime,
      renderer,
      runner,
      {
        routingMode: "reflex",
        problemSpec: problem(),
        requestedRoute: { intent: "clarify", topic: "flight_symmetry" },
      },
      [clarification],
    );
    expect(clarifyRun.invocation.request).toMatchObject({
      generation: 2,
      problemSpec: problem(),
      requestedRoute: { intent: "clarify", topic: "flight_symmetry" },
      baseScene: { revision: 6 },
      baseSemanticScene: { revision: 6 },
    });
    expect(currentComponent(runtime.getSnapshot())).toMatchObject({
      lastMainCheckpoint: "summary",
      clarifiedTopics: ["flight_symmetry"],
      activeClarification: "flight_symmetry",
    });

    const targetProblem = problem(30, 60);
    const retarget = sidecarEvent({
      snapshot: runtime.getSnapshot(),
      generation: 3,
      action: "retarget",
      targetProblem,
    });
    const retargetRun = await acceptStream(
      runtime,
      renderer,
      runner,
      {
        routingMode: "reflex",
        problemSpec: problem(),
        requestedRoute: { intent: "retarget", targetProblemSpec: targetProblem },
      },
      [retarget],
    );
    expect(retargetRun.invocation.request.problemSpec).toEqual(problem());
    expect(retargetRun.invocation.request).toMatchObject({
      generation: 3,
      requestedRoute: { intent: "retarget", targetProblemSpec: targetProblem },
      baseScene: { revision: 7 },
      baseSemanticScene: { revision: 7 },
    });
    expect(currentComponent(runtime.getSnapshot())).toMatchObject({
      problemSpec: targetProblem,
      lastMainCheckpoint: "summary",
      clarifiedTopics: ["flight_symmetry"],
      activeClarification: "flight_symmetry",
    });
  });

  it("suppresses every late event owned by a stale generation token", async () => {
    const { runtime, renderer, runner } = createRuntime();
    const staleRun = await start(runtime, runner);
    runtime.interrupt();
    const currentRun = await start(runtime, runner);

    emit(staleRun, started(1, 0), mainSuffix()[0], completed(1, 1, 1));
    staleRun.completion.resolve();
    await flush();
    expect(renderer.rendered).toHaveLength(0);
    expect(runtime.getSnapshot()).toMatchObject({
      generation: 2,
      phase: "connecting",
      sequence: 0,
      committedScene: { revision: 0 },
    });

    emit(
      currentRun,
      started(2, 0),
      decodeProjectileChoreographySceneStreamEventV1({
        type: "projectile_choreography_scene_stream_declined",
        generation: 2,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "no_forward_progress",
        message: "The accepted projectile board is already at that frontier.",
      }),
    );
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "declined",
      decline: { reasonCode: "no_forward_progress" },
      committedScene: { revision: 0 },
    });
  });

  it("atomically restores the accepted frontier after an illegal post-checkpoint failure", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptStream(runtime, renderer, runner, REFLEX, mainSuffix().slice(0, 1));
    const before = runtime.getSnapshot();
    const base = currentComponent(before);
    const run = await start(runtime, runner);
    const next = checkpointEvent({
      generation: 2,
      sequence: 1,
      checkpointId: "decompose_velocity",
      action: "advance",
      clarificationTopic: null,
      route: { intent: "advance", targetStage: "solve" },
      beatId: "beat_continue_2",
      beatBaseProblem: base.problemSpec,
      baseComponent: base,
      resultComponent: component(base.problemSpec, "decompose_velocity"),
      baseRevision: 1,
      baseHead: before.committedSemanticScene.certificateHeadSha256 ?? null,
    });
    emit(run, started(2, 1), next);
    const active = renderer.rendered.at(-1)!;
    emit(
      run,
      decodeProjectileChoreographySceneStreamEventV1({
        type: "projectile_choreography_scene_stream_failed",
        generation: 2,
        attempt: 1,
        code: "choreography_integrity_error",
        message: "The projectile suffix failed integrity verification.",
        lastAcceptedRevision: 2,
        retryable: false,
      }),
    );
    expect(run.invocation.signal.aborted).toBe(true);
    expect(active.playback.cancel).toHaveBeenCalledOnce();
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      error: { code: "invalid_stream_event", retryable: true },
      rendererTrusted: true,
    });
    expectFrontierUnchanged(runtime.getSnapshot(), before);
    expect(renderer.materializeScene).toHaveBeenLastCalledWith(
      before.committedScene,
    );

    settle(active);
    await flush();
    expectFrontierUnchanged(runtime.getSnapshot(), before);
  });

  it("preflights and replays all six checkpoints with zero network", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptStream(runtime, renderer, runner, REFLEX, mainSuffix());
    const before = runtime.getSnapshot();
    const runCount = runner.runs.length;

    const replay = runtime.replayAccepted();
    for (let index = 6; index < 12; index += 1) {
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
      visibleCheckpointId: "summary",
      rendererTrusted: true,
    });
  });

  it("retains six main, three clarifications, and all eight alternate problems before requiring Reset", async () => {
    const { runtime, renderer, runner } = createRuntime();
    await acceptStream(runtime, renderer, runner, REFLEX, mainSuffix());

    for (const [index, topic] of PROJECTILE_MOTION_CLARIFICATION_TOPICS.entries()) {
      const generation = index + 2;
      const value = sidecarEvent({
        snapshot: runtime.getSnapshot(),
        generation,
        action: "clarify",
        topic,
      });
      const currentProblem = currentComponent(runtime.getSnapshot()).problemSpec;
      await acceptStream(
        runtime,
        renderer,
        runner,
        {
          routingMode: "reflex",
          problemSpec: currentProblem,
          requestedRoute: { intent: "clarify", topic },
        },
        [value],
      );
    }

    const alternateProblems = [
      problem(20, 30),
      problem(20, 60),
      problem(25, 30),
      problem(25, 45),
      problem(25, 60),
      problem(30, 30),
      problem(30, 45),
      problem(30, 60),
    ] as const;
    for (const [index, targetProblem] of alternateProblems.entries()) {
      const generation = index + 5;
      const currentProblem = currentComponent(
        runtime.getSnapshot(),
      ).problemSpec;
      const retarget = sidecarEvent({
        snapshot: runtime.getSnapshot(),
        generation,
        action: "retarget",
        targetProblem,
      });
      await acceptStream(
        runtime,
        renderer,
        runner,
        {
          routingMode: "reflex",
          problemSpec: currentProblem,
          requestedRoute: { intent: "retarget", targetProblemSpec: targetProblem },
        },
        [retarget],
      );
    }

    expect(MAX_RETAINED_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS).toBe(17);
    const exhausted = runtime.getSnapshot();
    expect(exhausted.accepted).toHaveLength(17);
    expect(currentComponent(exhausted).problemSpec).toEqual(problem(30, 60));
    const networkCalls = runner.runs.length;

    const replayStart = renderer.rendered.length;
    const replay = runtime.replayAccepted();
    for (let index = 0; index < exhausted.accepted.length; index += 1) {
      await flush();
      settle(renderer.rendered[replayStart + index]);
    }
    await replay;
    expect(runner.runs).toHaveLength(networkCalls);
    expect(runtime.getSnapshot()).toMatchObject({
      phase: "completed",
      committedScene: exhausted.committedScene,
      committedSemanticScene: exhausted.committedSemanticScene,
      accepted: exhausted.accepted,
    });
    const afterReplay = runtime.getSnapshot();

    expect(() =>
      runtime.start({
        routingMode: "reflex",
        problemSpec: problem(30, 60),
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: problem(),
        },
      }),
    ).toThrowError(
      "Reset the projectile board before continuing; its certified checkpoint history is full.",
    );
    expect(runner.runs).toHaveLength(networkCalls);
    expect(runtime.getSnapshot()).toBe(afterReplay);
  });

  it("surfaces closed decline/failure messages without algebra-specific copy", async () => {
    const declined = createRuntime();
    const declineRun = await start(declined.runtime, declined.runner);
    emit(
      declineRun,
      started(1, 0),
      decodeProjectileChoreographySceneStreamEventV1({
        type: "projectile_choreography_scene_stream_declined",
        generation: 1,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "problem_conflict",
        message: "Choose one supported launch problem before continuing.",
      }),
    );
    expect(declined.runtime.getSnapshot()).toMatchObject({
      phase: "declined",
      narration: "Choose one supported launch problem before continuing.",
      decline: { reasonCode: "problem_conflict" },
    });

    const failed = createRuntime();
    const failureRun = await start(failed.runtime, failed.runner);
    emit(
      failureRun,
      started(1, 0),
      decodeProjectileChoreographySceneStreamEventV1({
        type: "projectile_choreography_scene_stream_failed",
        generation: 1,
        attempt: 1,
        code: "provider_timeout",
        message: "The projectile Director timed out.",
        lastAcceptedRevision: 0,
        retryable: true,
      }),
    );
    expect(failed.runtime.getSnapshot()).toMatchObject({
      phase: "failed",
      narration: "The projectile Director timed out.",
      error: { code: "provider_timeout", retryable: true },
    });
  });
});
