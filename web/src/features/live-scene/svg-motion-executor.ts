import { gsap } from "gsap";

import type {
  MotionPlayback,
  MotionPlaybackOutcome,
  MotionPlaybackOptions,
} from "@/features/canvas/types";
import {
  animateColorPulse,
  animateDrawOn,
  DURATION,
  EASING,
  resolveCssColor,
  settleDrawOn,
} from "@/lib/gsap-setup";
import type { MotionPlan, MotionStep } from "@/lib/live-scene";

import {
  createSvgNodeReconciler,
  type SvgNodeReconciler,
  type SvgNodeReconcilerContext,
} from "./svg-node-reconciler";

interface ManagedMotionPlayback {
  pause(): void;
  resume(): void;
  cancel(): MotionPlaybackOutcome;
}

interface StartedMotion {
  readonly animation: gsap.core.Animation | null;
  /** Materialize the target node with no transient animation state left behind. */
  commit(): void;
  /** Restore the exact pre-step node when rendering the step fails. */
  rollback(): void;
}

export type SvgPresentationBarrier = () => Promise<void>;

export interface SvgMotionExecutorOptions {
  /** Resolves after canonical DOM has crossed a browser presentation boundary. */
  readonly presentationBarrier?: SvgPresentationBarrier;
}

export interface SvgMotionExecutorContext extends SvgNodeReconcilerContext {
  getHighlightColor(): string;
}

export interface SvgMotionExecutor {
  play(plan: MotionPlan, options?: MotionPlaybackOptions): MotionPlayback;
  emphasize(id: string, color?: string): void;
  cancel(): void;
  dispose(): void;
}

function browserPresentationBarrier(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => resolve());
      });
      return;
    }
    setTimeout(resolve, 0);
  });
}

function executeMotionStep(
  context: SvgMotionExecutorContext,
  reconciler: SvgNodeReconciler,
  step: MotionStep
): StartedMotion {
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");

  if (step.type === "remove") {
    const target = context.elements.get(step.id);
    if (!target) throw new Error(`Missing remove target: ${step.id}`);
    const snapshot = reconciler.capture(target.element);
    let state: "pending" | "committed" | "rolledback" = "pending";
    const commit = () => {
      if (state === "committed") return;
      if (state === "rolledback") {
        throw new Error(`Cannot recommit rolled-back remove target: ${step.id}`);
      }
      target.element.remove();
      reconciler.forget(step.id, target.element);
      state = "committed";
    };
    const rollback = () => {
      if (state === "rolledback") return;
      reconciler.restore(snapshot);
      reconciler.remember(step.node, target.element);
      state = "rolledback";
    };
    if (step.effect === "none") {
      return { animation: null, commit, rollback };
    }
    try {
      const animation = gsap.to(target.element, {
        opacity: 0,
        duration: DURATION.fast,
        ease: EASING.smooth,
      });
      return { animation, commit, rollback };
    } catch (error) {
      rollback();
      throw error;
    }
  }

  const node = step.type === "enter" ? step.node : step.next;
  if (step.type === "update") {
    const outgoingData = context.elements.get(step.id);
    if (!outgoingData) throw new Error(`Missing update target: ${step.id}`);
    const outgoing = outgoingData.element;

    if (step.transition === "transform") {
      const replacement = reconciler.create(node, node.id);
      if (!replacement) throw new Error(`Could not render update target: ${node.id}`);
      const snapshot = reconciler.capture(outgoing);
      let state: "pending" | "committed" | "rolledback" = "pending";
      const commit = () => {
        if (state === "committed") return;
        if (state === "rolledback") {
          throw new Error(`Cannot recommit rolled-back update target: ${step.id}`);
        }
        reconciler.settle(outgoing, node);
        reconciler.remember(node, outgoing);
        state = "committed";
      };
      const rollback = () => {
        if (state === "rolledback") return;
        reconciler.restore(snapshot);
        reconciler.remember(step.previous, outgoing);
        state = "rolledback";
      };
      try {
        outgoing.replaceChildren(...Array.from(replacement.childNodes));
        outgoing.removeAttribute("clip-path");
        gsap.set(outgoing, { opacity: node.style.opacity, scale: 0.98 });
        reconciler.remember(node, outgoing);
        const animation = gsap.to(outgoing, {
          opacity: node.style.opacity,
          scale: 1,
          duration: DURATION.stateChange,
          ease: EASING.teaching,
        });
        return { animation, commit, rollback };
      } catch (error) {
        rollback();
        throw error;
      }
    }

    const incoming = reconciler.create(node, `${node.id}--incoming`);
    if (!incoming) throw new Error(`Could not render update target: ${node.id}`);
    const outgoingSnapshot = reconciler.capture(outgoing);
    let state: "pending" | "committed" | "rolledback" = "pending";
    const commit = () => {
      if (state === "committed") return;
      if (state === "rolledback") {
        throw new Error(`Cannot recommit rolled-back update target: ${step.id}`);
      }
      outgoing.remove();
      incoming.setAttribute("id", node.id);
      incoming.setAttribute("data-element-id", node.id);
      reconciler.settle(incoming, node);
      reconciler.remember(node, incoming);
      state = "committed";
    };
    const rollback = () => {
      if (state === "rolledback") return;
      incoming.remove();
      reconciler.restore(outgoingSnapshot);
      reconciler.remember(step.previous, outgoing);
      state = "rolledback";
    };
    try {
      outgoing.setAttribute("id", `${node.id}--outgoing`);
      outgoing.setAttribute("data-element-id", `${node.id}--outgoing`);
      incoming.setAttribute("id", node.id);
      incoming.setAttribute("data-element-id", node.id);
      gsap.set(incoming, { opacity: 0 });
      if (outgoingSnapshot.parent) {
        outgoingSnapshot.parent.insertBefore(incoming, outgoing.nextSibling);
      } else {
        svg.appendChild(incoming);
      }
      reconciler.remember(node, incoming);

      const timeline = gsap.timeline();
      timeline.to(outgoing, {
        opacity: 0,
        duration: DURATION.normal,
        ease: EASING.smooth,
      });
      timeline.to(
        incoming,
        {
          opacity: node.style.opacity,
          duration: DURATION.normal,
          ease: EASING.teaching,
        },
        "<"
      );
      return { animation: timeline, commit, rollback };
    } catch (error) {
      rollback();
      throw error;
    }
  }

  if (context.elements.has(node.id)) {
    throw new Error(`Duplicate enter target: ${node.id}`);
  }
  const element = reconciler.create(node);
  if (!element) throw new Error(`Could not render enter target: ${node.id}`);
  let animation: gsap.core.Animation | null = null;
  let drawAnimation: gsap.core.Timeline | null = null;
  let state: "pending" | "committed" | "rolledback" = "pending";
  const commit = () => {
    if (state === "committed") return;
    if (state === "rolledback") {
      throw new Error(`Cannot recommit rolled-back enter target: ${step.id}`);
    }
    if (drawAnimation) {
      settleDrawOn(drawAnimation);
    }
    reconciler.settle(element, node, Boolean(drawAnimation));
    reconciler.remember(node, element);
    state = "committed";
  };
  const rollback = () => {
    if (state === "rolledback") return;
    element.remove();
    reconciler.forget(node.id, element);
    state = "rolledback";
  };

  try {
    svg.appendChild(element);
    reconciler.remember(node, element);

    if (step.effect === "none") {
      animation = null;
    } else if (step.effect === "scale") {
      animation = gsap.fromTo(
        element,
        { opacity: 0, scale: 0.85, transformOrigin: "center center" },
        {
          opacity: node.style.opacity,
          scale: 1,
          duration: DURATION.normal,
          ease: EASING.back,
        }
      );
    } else if (
      step.effect === "draw" &&
      node.kind !== "text" &&
      node.kind !== "latex" &&
      node.kind !== "latex_token"
    ) {
      drawAnimation = animateDrawOn(element, DURATION.drawSlow, EASING.draw);
      animation = drawAnimation;
    } else {
      animation = gsap.fromTo(
        element,
        { opacity: 0 },
        {
          opacity: node.style.opacity,
          duration: DURATION.fast,
          ease: EASING.teaching,
        }
      );
    }
    return { animation, commit, rollback };
  } catch (error) {
    rollback();
    throw error;
  }
}

/** Own every timer and tween required to materialize one deterministic motion plan. */
export function createSvgMotionExecutor(
  context: SvgMotionExecutorContext,
  options: SvgMotionExecutorOptions = {}
): SvgMotionExecutor {
  const reconciler = createSvgNodeReconciler(context);
  const playbacks = new Set<ManagedMotionPlayback>();
  const emphasisAnimations = new Map<gsap.core.Animation, () => void>();
  let mutationEpoch = 0;
  const presentationBarrier =
    options.presentationBarrier ?? browserPresentationBarrier;

  const play = (
    plan: MotionPlan,
    playbackOptions: MotionPlaybackOptions = {}
  ): MotionPlayback => {
    if (playbacks.size > 0) {
      throw new Error(
        "A motion plan is still active or awaiting its presentation receipt"
      );
    }
    const playbackEpoch = ++mutationEpoch;
    const animations = new Set<gsap.core.Animation>();
    const activeMotions = new Map<string, StartedMotion>();
    const transactions: { readonly step: MotionStep; readonly motion: StartedMotion }[] = [];
    const appliedStepIds = new Set<string>();
    const staggerSeconds = Math.max(0, playbackOptions.staggerMs ?? 90) / 1000;
    let completedSteps = 0;
    let settlementStarted = false;
    let settledOutcome: MotionPlaybackOutcome | null = null;
    let resolveFinished!: (outcome: MotionPlaybackOutcome) => void;
    const finished = new Promise<MotionPlaybackOutcome>((resolve) => {
      resolveFinished = resolve;
    });

    let controller: ManagedMotionPlayback;
    const outcome = (
      status: MotionPlaybackOutcome["status"],
      error?: unknown
    ): MotionPlaybackOutcome =>
      Object.freeze({
        status,
        appliedStepIds: Object.freeze(
          plan.steps.filter((step) => appliedStepIds.has(step.id)).map((step) => step.id)
        ),
        ...(error === undefined
          ? {}
          : { error: error instanceof Error ? error.message : String(error) }),
      });

    const combineErrors = (primary: unknown, cleanupErrors: readonly unknown[]) => {
      if (cleanupErrors.length === 0) return primary;
      const message = [primary, ...cleanupErrors]
        .map((error) => (error instanceof Error ? error.message : String(error)))
        .join("; cleanup failed: ");
      return new Error(message);
    };

    const rollbackTransactions = (
      entries: readonly { readonly step: MotionStep; readonly motion: StartedMotion }[]
    ): unknown[] => {
      const cleanupErrors: unknown[] = [];
      for (const { step, motion } of [...entries].reverse()) {
        try {
          motion.rollback();
        } catch (error) {
          cleanupErrors.push(error);
        }
        activeMotions.delete(step.id);
        appliedStepIds.delete(step.id);
      }
      return cleanupErrors;
    };

    const stopAnimations = (): unknown[] => {
      const cleanupErrors: unknown[] = [];
      animations.forEach((animation) => {
        try {
          animation.kill();
        } catch (error) {
          cleanupErrors.push(error);
        }
      });
      animations.clear();
      return cleanupErrors;
    };

    const settle = (
      status: MotionPlaybackOutcome["status"],
      error?: unknown
    ): MotionPlaybackOutcome => {
      if (settledOutcome) return settledOutcome;
      settlementStarted = true;
      settledOutcome = outcome(status, error);
      void Promise.resolve()
        .then(() => presentationBarrier())
        .then(() => {
          transactions.length = 0;
          playbacks.delete(controller);
          resolveFinished(settledOutcome as MotionPlaybackOutcome);
        })
        .catch((barrierError: unknown) => {
          const cleanupErrors =
            mutationEpoch === playbackEpoch
              ? rollbackTransactions(transactions)
              : [];
          transactions.length = 0;
          appliedStepIds.clear();
          settledOutcome = outcome(
            "failed",
            combineErrors(barrierError, cleanupErrors)
          );
          playbacks.delete(controller);
          resolveFinished(settledOutcome);
        });
      return settledOutcome;
    };

    const fail = (error: unknown) => {
      if (settlementStarted) return;
      settlementStarted = true;
      const stopErrors = stopAnimations();
      const activeTransactions = transactions.filter(({ step }) =>
        activeMotions.has(step.id)
      );
      const cleanupErrors = [
        ...stopErrors,
        ...rollbackTransactions(activeTransactions),
      ];
      settle("failed", combineErrors(error, cleanupErrors));
    };

    const completeStep = () => {
      completedSteps += 1;
      if (completedSteps === plan.steps.length) settle("completed");
    };

    const startStep = (step: MotionStep) => {
      if (settlementStarted) return;
      try {
        const started = executeMotionStep(context, reconciler, step);
        activeMotions.set(step.id, started);
        transactions.push({ step, motion: started });

        if (!started.animation) {
          started.commit();
          appliedStepIds.add(step.id);
          activeMotions.delete(step.id);
          completeStep();
          return;
        }

        const wrapper = gsap.timeline({
          paused: true,
          onComplete: () => {
            animations.delete(wrapper);
            if (settlementStarted) return;
            try {
              started.commit();
              appliedStepIds.add(step.id);
              activeMotions.delete(step.id);
              completeStep();
            } catch (error) {
              fail(error);
            }
          },
        });
        wrapper.add(started.animation, 0);
        animations.add(wrapper);
        wrapper.play();
      } catch (error) {
        fail(error);
      }
    };

    controller = {
      pause: () => animations.forEach((animation) => animation.pause()),
      resume: () => animations.forEach((animation) => animation.resume()),
      cancel: () => {
        if (settledOutcome) return settledOutcome;
        settlementStarted = true;
        const stopErrors = stopAnimations();
        const activeTransactions = transactions.filter(({ step }) =>
          activeMotions.has(step.id)
        );
        if (stopErrors.length > 0) {
          const cleanupErrors = rollbackTransactions(activeTransactions);
          return settle(
            "failed",
            combineErrors(stopErrors[0], [
              ...stopErrors.slice(1),
              ...cleanupErrors,
            ])
          );
        }
        try {
          for (const { step, motion } of activeTransactions) {
            motion.commit();
            appliedStepIds.add(step.id);
          }
          activeMotions.clear();
          return settle("cancelled");
        } catch (error) {
          const cleanupErrors = rollbackTransactions(activeTransactions);
          return settle("failed", combineErrors(error, cleanupErrors));
        }
      },
    };
    playbacks.add(controller);

    if (plan.steps.length === 0) {
      settle("completed");
    } else {
      plan.steps.forEach((step, index) => {
        if (settlementStarted) return;
        const delay = index * staggerSeconds;
        if (delay === 0) {
          startStep(step);
          return;
        }
        let scheduled!: gsap.core.Tween;
        scheduled = gsap.delayedCall(delay, () => {
          animations.delete(scheduled);
          startStep(step);
        });
        animations.add(scheduled);
      });
    }

    return {
      finished,
      pause: controller.pause,
      resume: controller.resume,
      cancel: controller.cancel,
    };
  };

  const emphasize = (id: string, color?: string) => {
    const target = context.elements.get(id)?.element;
    if (!target) return;
    const strokedElements = Array.from(
      target.querySelectorAll<SVGElement>("path, line, circle, ellipse, rect")
    );
    const originalStrokes = strokedElements.map((element) =>
      element.getAttribute("stroke")
    );
    strokedElements.forEach((element, index) => {
      const originalStroke = originalStrokes[index];
      if (originalStroke && originalStroke !== "none") {
        element.setAttribute("stroke", resolveCssColor(originalStroke));
      }
    });
    const restore = () => {
      strokedElements.forEach((element, index) => {
        const stroke = originalStrokes[index];
        if (stroke === null) element.removeAttribute("stroke");
        else element.setAttribute("stroke", stroke);
      });
    };
    const animation = animateColorPulse(
      target,
      resolveCssColor(color ?? context.getHighlightColor()),
      0.4,
      2
    );
    emphasisAnimations.set(animation, restore);
    animation.eventCallback("onComplete", () => {
      restore();
      emphasisAnimations.delete(animation);
    });
  };

  const cancel = () => {
    playbacks.forEach((playback) => playback.cancel());
    mutationEpoch += 1;
    playbacks.clear();
    emphasisAnimations.forEach((restore, animation) => {
      animation.kill();
      restore();
    });
    emphasisAnimations.clear();
  };

  return { play, emphasize, cancel, dispose: cancel };
}
