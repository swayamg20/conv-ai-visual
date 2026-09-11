import { gsap } from "gsap";

import type {
  ChoreographyPlaybackRate,
  SVGElementData,
} from "@/features/canvas/types";
import {
  createSceneState,
  type ChoreographyCueKind,
  type ChoreographyCueKindV2,
  type ChoreographyPlan,
  type ChoreographyPlanV2,
  type PlannedCheckpointChoreography,
  type SceneNode,
  type SceneState,
  type ViewportPoseV1,
} from "@/lib/live-scene";

import {
  appendChoreographyPhaseTweens,
  validateChoreographyPhaseMotion,
} from "./choreography-timeline";
import {
  createSvgNodeReconciler,
  type SvgNodeReconcilerContext,
} from "./svg-node-reconciler";

export type ChoreographyPlaybackStatus =
  | "completed"
  | "cancelled_before_presented"
  | "cancelled_to_checkpoint"
  | "failed";

export interface ChoreographyPlaybackOutcome {
  readonly status: ChoreographyPlaybackStatus;
  readonly firstCuePresented: boolean;
  readonly error?: string;
}

export interface ChoreographyPlayback {
  /** Resolves true only after the first visible mutation crosses its paint barrier. */
  readonly firstCuePresented: Promise<boolean>;
  /** Resolves after the terminal scene and viewport have crossed their paint barrier. */
  readonly finished: Promise<ChoreographyPlaybackOutcome>;
  cancel(): void;
}

type CueKindForPlan<Plan extends ChoreographyPlan> =
  Plan extends ChoreographyPlanV2
    ? ChoreographyCueKindV2
    : ChoreographyCueKind;

export type ChoreographyExecutorSignal<
  Cue extends ChoreographyCueKindV2 = ChoreographyCueKind,
> =
  | {
      readonly type: "cueStarted";
      readonly cue: Cue;
    }
  | { readonly type: "firstCuePresented" }
  | {
      readonly type: "checkpointSettled";
      readonly settlement: "completed" | "cancelled_to_checkpoint";
    };

/** Receives only closed choreography vocabulary, never model-authored text or IDs. */
export type ChoreographyExecutorObserver<
  Cue extends ChoreographyCueKindV2 = ChoreographyCueKind,
> = (
  signal: ChoreographyExecutorSignal<Cue>,
) => void;

export type ChoreographyPresentationBarrier = () => Promise<void>;

export interface ChoreographyExecutorOptions {
  readonly presentationBarrier?: ChoreographyPresentationBarrier;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
}

export interface ChoreographyExecutorContext extends SvgNodeReconcilerContext {
  readViewport(): ViewportPoseV1;
  /** Paint one camera sample without synchronizing React-facing viewport state. */
  renderViewportFrame(viewport: ViewportPoseV1): void;
  materializeViewport(viewport: ViewportPoseV1): void;
}

export interface ChoreographyExecutor {
  play<Plan extends ChoreographyPlan>(
    plan: PlannedCheckpointChoreography<Plan>,
    observer?: ChoreographyExecutorObserver<CueKindForPlan<Plan>>,
  ): ChoreographyPlayback;
  cancel(): void;
  dispose(): void;
}

interface Deferred<Value> {
  readonly promise: Promise<Value>;
  resolve(value: Value): void;
}

function deferred<Value>(): Deferred<Value> {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function browserPresentationBarrier(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame !== "function") {
      setTimeout(resolve, 0);
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

function sameViewport(left: ViewportPoseV1, right: ViewportPoseV1): boolean {
  return (
    left.v === right.v &&
    left.x === right.x &&
    left.y === right.y &&
    left.width === right.width &&
    left.height === right.height
  );
}

function structuralNode(value: unknown, id: string): SceneNode {
  try {
    return createSceneState({
      revision: 0,
      nodes: [value as SceneNode],
    }).nodes[0];
  } catch (error) {
    throw new Error(
      `Invalid reconciler metadata for ${id}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

function sameNode(left: SceneNode, right: SceneNode): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function expectedBaseNodes(
  plan: PlannedCheckpointChoreography<ChoreographyPlan>,
): Map<string, SceneNode> {
  const expected = new Map(
    plan.targetScene.nodes.map((node) => [node.id, node]),
  );
  for (const step of plan.motionPlan.steps) {
    if (step.type === "enter") expected.delete(step.id);
    else if (step.type === "update") expected.set(step.id, step.previous);
    else expected.set(step.id, step.node);
  }
  return expected;
}

function validateAndReadBaseScene(
  context: ChoreographyExecutorContext,
  plan: PlannedCheckpointChoreography<ChoreographyPlan>,
): SceneState {
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");
  if (!context.getRenderer()) {
    throw new Error("The SVG renderer is unavailable");
  }
  if (!sameViewport(context.readViewport(), plan.baseViewport)) {
    throw new Error(
      "The rendered viewport does not match the certified base viewport",
    );
  }
  if (
    plan.motionPlan.toRevision !== plan.targetScene.revision ||
    plan.motionPlan.toRevision !== plan.motionPlan.fromRevision + 1
  ) {
    throw new Error("The motion plan revisions do not join the target scene");
  }

  const expected = expectedBaseNodes(plan);
  const actualIds = [...context.elements.keys()];
  if (
    actualIds.length !== expected.size ||
    actualIds.some((id) => !expected.has(id))
  ) {
    throw new Error(
      "The reconciler element set does not match the motion-plan base scene",
    );
  }

  const nodes = actualIds.map((id) => {
    const data = context.elements.get(id) as SVGElementData;
    const node = structuralNode(data.data, id);
    const expectedNode = expected.get(id) as SceneNode;
    if (!sameNode(node, expectedNode)) {
      throw new Error(`The reconciler metadata does not match base node ${id}`);
    }
    if (
      data.element.parentNode !== svg ||
      data.element.getAttribute("id") !== id ||
      data.element.getAttribute("data-element-id") !== id
    ) {
      throw new Error(
        `The reconciler DOM identity does not match base node ${id}`,
      );
    }
    return node;
  });

  return createSceneState({ revision: plan.motionPlan.fromRevision, nodes });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function combinedError(primary: unknown, cleanup?: unknown): Error {
  const primaryMessage = errorMessage(primary);
  return cleanup === undefined
    ? new Error(primaryMessage)
    : new Error(`${primaryMessage}; rollback failed: ${errorMessage(cleanup)}`);
}

function validatedPlaybackRate(value: unknown): ChoreographyPlaybackRate {
  const rate = value === undefined ? 1 : value;
  if (rate !== 1 && rate !== 16) {
    throw new RangeError("playbackRate must be exactly 1 or 16");
  }
  return rate;
}

/** Execute one already-verified checkpoint as one owned, interruption-safe timeline. */
export function createChoreographyExecutor(
  context: ChoreographyExecutorContext,
  options: ChoreographyExecutorOptions = {},
): ChoreographyExecutor {
  const reconciler = createSvgNodeReconciler(context);
  const presentationBarrier =
    options.presentationBarrier ?? browserPresentationBarrier;
  const playbackRate = validatedPlaybackRate(options.playbackRate);
  let active: ChoreographyPlayback | null = null;
  let disposed = false;

  const play = <Plan extends ChoreographyPlan>(
    plan: PlannedCheckpointChoreography<Plan>,
    observer?: ChoreographyExecutorObserver<CueKindForPlan<Plan>>,
  ): ChoreographyPlayback => {
    if (disposed) throw new Error("The choreography executor is disposed");
    if (active) throw new Error("A checkpoint choreography is already active");

    const firstCue = deferred<boolean>();
    const terminal = deferred<ChoreographyPlaybackOutcome>();
    const timeline = gsap.timeline({ paused: true });
    let baseScene: SceneState | null = null;
    let firstCuePresented = false;
    let firstCueSettled = false;
    let phaseFinished = false;
    let settling = false;
    let terminalOutcome: ChoreographyPlaybackOutcome | null = null;
    let barrierEpoch = 0;

    const resolveFirstCue = (presented: boolean) => {
      if (firstCueSettled) return;
      firstCueSettled = true;
      firstCue.resolve(presented);
    };

    const finish = (
      status: ChoreographyPlaybackStatus,
      error?: unknown,
    ): void => {
      if (terminalOutcome) return;
      terminalOutcome = Object.freeze({
        status,
        firstCuePresented,
        ...(error === undefined ? {} : { error: errorMessage(error) }),
      });
      if (!firstCueSettled) resolveFirstCue(firstCuePresented);
      if (active === playback) active = null;
      terminal.resolve(terminalOutcome);
    };

    const materializeExact = (
      scene: SceneState,
      viewport: ViewportPoseV1,
    ): void => {
      reconciler.reconcile(scene);
      context.materializeViewport(viewport);
      if (!sameViewport(context.readViewport(), viewport)) {
        throw new Error(
          "Viewport materialization did not produce the certified pose",
        );
      }
      const svg = context.getSvg();
      if (!svg) throw new Error("The SVG canvas is unavailable");
      if (
        svg.querySelector(
          "[id$='--incoming'], [id$='--outgoing'], [data-element-id$='--incoming'], [data-element-id$='--outgoing']",
        )
      ) {
        throw new Error(
          "Transient choreography nodes remain after reconciliation",
        );
      }
      for (const node of scene.nodes) {
        const element = context.elements.get(node.id)?.element;
        if (!element) throw new Error(`Missing reconciled target ${node.id}`);
        if (
          element.hasAttribute("transform") ||
          element.hasAttribute("filter") ||
          element.querySelector(
            "[stroke-dasharray], [stroke-dashoffset], [filter], [transform]",
          )
        ) {
          throw new Error(
            `Transient presentation residue remains on ${node.id}`,
          );
        }
      }
    };

    const rollback = (): void => {
      if (!baseScene) return;
      materializeExact(baseScene, plan.baseViewport);
    };

    const fail = (error: unknown): void => {
      if (terminalOutcome || settling) return;
      settling = true;
      barrierEpoch += 1;
      timeline.kill();
      let failure = error;
      try {
        rollback();
      } catch (cleanupError) {
        failure = combinedError(error, cleanupError);
      }
      resolveFirstCue(firstCuePresented);
      finish("failed", failure);
    };

    const settle = (
      scene: SceneState,
      viewport: ViewportPoseV1,
      status: Exclude<ChoreographyPlaybackStatus, "failed">,
    ): void => {
      if (terminalOutcome || settling) return;
      settling = true;
      const epoch = ++barrierEpoch;
      timeline.kill();
      try {
        materializeExact(scene, viewport);
      } catch (error) {
        settling = false;
        fail(error);
        return;
      }
      void Promise.resolve()
        .then(() => presentationBarrier())
        .then(() => {
          if (terminalOutcome || epoch !== barrierEpoch) return;
          if (status === "cancelled_before_presented") resolveFirstCue(false);
          else {
            observer?.({ type: "checkpointSettled", settlement: status });
          }
          finish(status);
        })
        .catch((error: unknown) => {
          if (terminalOutcome || epoch !== barrierEpoch) return;
          settling = false;
          fail(error);
        });
    };

    const completeIfReady = (): void => {
      if (phaseFinished && firstCuePresented && !settling && !terminalOutcome) {
        settle(plan.targetScene, plan.resultViewport, "completed");
      }
    };

    const crossFirstPresentationBoundary = (): void => {
      if (settling || terminalOutcome) return;
      const epoch = ++barrierEpoch;
      void Promise.resolve()
        .then(() => {
          if (settling || terminalOutcome || epoch !== barrierEpoch) return;
          return presentationBarrier();
        })
        .then(() => {
          if (settling || terminalOutcome || epoch !== barrierEpoch) return;
          firstCuePresented = true;
          resolveFirstCue(true);
          observer?.({ type: "firstCuePresented" });
          completeIfReady();
        })
        .catch((error: unknown) => {
          if (settling || terminalOutcome || epoch !== barrierEpoch) return;
          fail(error);
        });
    };

    const cancel = (): void => {
      if (terminalOutcome || settling) return;
      if (firstCuePresented) {
        settle(
          plan.targetScene,
          plan.resultViewport,
          "cancelled_to_checkpoint",
        );
      } else if (baseScene) {
        settle(baseScene, plan.baseViewport, "cancelled_before_presented");
      }
    };

    const playback: ChoreographyPlayback = Object.freeze({
      firstCuePresented: firstCue.promise,
      finished: terminal.promise,
      cancel,
    });
    active = playback;

    try {
      baseScene = validateAndReadBaseScene(context, plan);
      validateChoreographyPhaseMotion(plan);
      const phase = plan.choreographyPlan.phase;
      const duration = options.reducedMotion
        ? 0
        : phase.durationMs / 1_000 / playbackRate;
      const holdDuration = phase.holdAfterMs / 1_000 / playbackRate;
      const emitCueStarts = () => {
        for (const cue of phase.cues) {
          observer?.({
            type: "cueStarted",
            cue: cue.cue as CueKindForPlan<Plan>,
          });
        }
      };

      if (options.reducedMotion) {
        emitCueStarts();
        materializeExact(plan.targetScene, plan.resultViewport);
        crossFirstPresentationBoundary();
      } else {
        timeline.call(
          () => {
            try {
              emitCueStarts();
            } catch (error) {
              fail(error);
            }
          },
          [],
          0,
        );
        appendChoreographyPhaseTweens({
          timeline,
          reconciler,
          context,
          plan,
          duration,
          onError: fail,
        });
        // Start presentation acknowledgement only after the timeline has
        // rendered a visible sample. A callback at t=0 can run while enters
        // are still fully transparent even when the DOM node already exists.
        timeline.call(
          crossFirstPresentationBoundary,
          [],
          Math.min(duration, 1 / 60),
        );
      }

      if (holdDuration > 0) {
        timeline.to({}, { duration: holdDuration });
      }
      timeline.eventCallback("onComplete", () => {
        phaseFinished = true;
        completeIfReady();
      });
      if (options.reducedMotion && holdDuration === 0) {
        phaseFinished = true;
        completeIfReady();
      } else {
        timeline.play(0);
        if (!options.reducedMotion) {
          // GSAP normally renders on the next ticker. Render through the first
          // presentation marker now so the barrier cannot race a hidden enter.
          timeline.totalTime(Math.min(duration, 1 / 60) + 0.000001, false);
        }
      }
    } catch (error) {
      fail(error);
    }

    return playback;
  };

  return Object.freeze({
    play,
    cancel: () => active?.cancel(),
    dispose: () => {
      disposed = true;
      active?.cancel();
    },
  });
}
