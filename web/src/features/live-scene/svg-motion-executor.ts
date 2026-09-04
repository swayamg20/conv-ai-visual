import { gsap } from "gsap";

import type { SVGPrimitiveRenderer } from "@/features/canvas/primitives";
import type {
  CanvasOperation,
  MotionPlayback,
  MotionPlaybackOutcome,
  MotionPlaybackOptions,
  SVGElementData,
} from "@/features/canvas/types";
import {
  animateColorPulse,
  animateDrawOn,
  DURATION,
  EASING,
  resolveCssColor,
  settleDrawOn,
} from "@/lib/gsap-setup";
import type { MotionPlan, MotionStep, SceneNode } from "@/lib/live-scene";

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

export interface SvgMotionExecutorContext {
  readonly elements: Map<string, SVGElementData>;
  getSvg(): SVGSVGElement | null;
  getRenderer(): SVGPrimitiveRenderer | null;
  getHighlightColor(): string;
  invalidate(): void;
}

export interface SvgMotionExecutor {
  play(plan: MotionPlan, options?: MotionPlaybackOptions): MotionPlayback;
  emphasize(id: string, color?: string): void;
  cancel(): void;
  dispose(): void;
}

function sceneNodeOperation(node: Exclude<SceneNode, { kind: "latex" }>): CanvasOperation {
  switch (node.kind) {
    case "line":
      return {
        action: "line",
        id: node.id,
        points: node.points.map(([x, y]) => [x, y]) as [number, number][],
        color: node.style.stroke,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "path":
      return {
        action: "path",
        id: node.id,
        points: node.points.map(([x, y]) => [x, y]) as [number, number][],
        color: node.style.stroke,
        fill: node.style.fill,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "rect":
      return {
        action: "rect",
        id: node.id,
        x: node.x,
        y: node.y,
        width: node.width,
        height: node.height,
        color: node.style.stroke,
        fill: node.style.fill === "none" ? undefined : node.style.fill,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "text":
      return {
        action: "text",
        id: node.id,
        x: node.x,
        y: node.y,
        text: node.text,
        color: node.style.color,
        font_size: node.style.fontSize,
        font_family: node.style.fontFamily,
      };
  }
}

function sceneNodePosition(node: SceneNode): { x: number; y: number } {
  return "x" in node ? { x: node.x, y: node.y } : { x: 0, y: 0 };
}

function createPathElement(
  node: Extract<SceneNode, { kind: "path" }>,
  domId: string
): SVGElement {
  const namespace = "http://www.w3.org/2000/svg";
  const group = document.createElementNS(namespace, "g");
  group.setAttribute("id", domId);
  group.setAttribute("data-element-id", domId);

  const path = document.createElementNS(namespace, "path");
  path.setAttribute(
    "d",
    `${node.points
      .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`)
      .join(" ")}${node.closed ? " Z" : ""}`
  );
  path.setAttribute("fill", node.style.fill);
  path.setAttribute("stroke", node.style.stroke);
  path.setAttribute("stroke-width", String(node.style.strokeWidth));
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  group.appendChild(path);
  return group;
}

function createSceneElement(
  context: SvgMotionExecutorContext,
  node: SceneNode,
  domId = node.id
): SVGElement | null {
  const renderer = context.getRenderer();
  if (!renderer) return null;

  if (node.kind === "latex") {
    return renderer.drawLatex({
      type: "latex",
      id: domId,
      latex: node.latex,
      x: node.x,
      y: node.y,
      font_size: node.style.fontSize,
      color: node.style.color,
    });
  }

  if (node.kind === "path") return createPathElement(node, domId);

  const element = renderer.draw({ ...sceneNodeOperation(node), id: domId });
  if (element && node.kind === "text") {
    element.querySelector("text")?.setAttribute("text-anchor", node.style.anchor);
  }
  return element;
}

function rememberElement(
  context: SvgMotionExecutorContext,
  node: SceneNode,
  element: SVGElement
): void {
  context.elements.set(node.id, {
    element,
    id: node.id,
    type: node.kind,
    ...sceneNodePosition(node),
    data: node,
  });
  context.invalidate();
}

function restoreElementSnapshot(element: SVGElement, snapshot: SVGElement): void {
  gsap.killTweensOf(element);
  gsap.set(element, { clearProps: "all" });
  for (const attribute of Array.from(element.attributes)) {
    element.removeAttribute(attribute.name);
  }
  for (const attribute of Array.from(snapshot.attributes)) {
    element.setAttribute(attribute.name, attribute.value);
  }
  element.replaceChildren(
    ...Array.from(snapshot.childNodes, (child) => child.cloneNode(true))
  );
}

function restoreElementPosition(
  element: SVGElement,
  parent: Node | null,
  nextSibling: Node | null
): void {
  if (!parent || element.parentNode === parent) return;
  parent.insertBefore(
    element,
    nextSibling?.parentNode === parent ? nextSibling : null
  );
}

function settleElementPresentation(element: SVGElement, node: SceneNode): void {
  element.removeAttribute("clip-path");
  element.style.removeProperty("clip-path");
  element
    .querySelectorAll<SVGElement>("*")
    .forEach((child) => {
      child.removeAttribute("clip-path");
      child.style.removeProperty("clip-path");
    });
  element.removeAttribute("transform");
  gsap.set(element, { opacity: node.style.opacity });
  gsap.set(element, { clearProps: "transform,transformOrigin" });
  element.style.removeProperty("transform");
  element.style.removeProperty("transform-origin");
}

function clearDrawResidue(element: SVGElement): void {
  element.querySelectorAll<SVGElement>("path").forEach((path) => {
    path.removeAttribute("stroke-dasharray");
    path.removeAttribute("stroke-dashoffset");
    path.style.removeProperty("stroke-dasharray");
    path.style.removeProperty("stroke-dashoffset");
  });
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
  step: MotionStep
): StartedMotion {
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");

  if (step.type === "remove") {
    const target = context.elements.get(step.id);
    if (!target) throw new Error(`Missing remove target: ${step.id}`);
    const snapshot = target.element.cloneNode(true) as SVGElement;
    const parent = target.element.parentNode;
    const nextSibling = target.element.nextSibling;
    let state: "pending" | "committed" | "rolledback" = "pending";
    const commit = () => {
      if (state === "committed") return;
      if (state === "rolledback") {
        throw new Error(`Cannot recommit rolled-back remove target: ${step.id}`);
      }
      target.element.remove();
      if (context.elements.get(step.id)?.element === target.element) {
        context.elements.delete(step.id);
      }
      context.invalidate();
      state = "committed";
    };
    const rollback = () => {
      if (state === "rolledback") return;
      restoreElementSnapshot(target.element, snapshot);
      restoreElementPosition(target.element, parent, nextSibling);
      context.elements.set(step.id, target);
      context.invalidate();
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
      const replacement = createSceneElement(context, node, node.id);
      if (!replacement) throw new Error(`Could not render update target: ${node.id}`);
      const snapshot = outgoing.cloneNode(true) as SVGElement;
      let state: "pending" | "committed" | "rolledback" = "pending";
      const commit = () => {
        if (state === "committed") return;
        if (state === "rolledback") {
          throw new Error(`Cannot recommit rolled-back update target: ${step.id}`);
        }
        settleElementPresentation(outgoing, node);
        rememberElement(context, node, outgoing);
        state = "committed";
      };
      const rollback = () => {
        if (state === "rolledback") return;
        restoreElementSnapshot(outgoing, snapshot);
        rememberElement(context, step.previous, outgoing);
        state = "rolledback";
      };
      try {
        outgoing.replaceChildren(...Array.from(replacement.childNodes));
        outgoing.removeAttribute("clip-path");
        gsap.set(outgoing, { opacity: node.style.opacity, scale: 0.98 });
        rememberElement(context, node, outgoing);
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

    const incoming = createSceneElement(context, node, `${node.id}--incoming`);
    if (!incoming) throw new Error(`Could not render update target: ${node.id}`);
    const outgoingSnapshot = outgoing.cloneNode(true) as SVGElement;
    const outgoingParent = outgoing.parentNode;
    const outgoingNextSibling = outgoing.nextSibling;
    let state: "pending" | "committed" | "rolledback" = "pending";
    const commit = () => {
      if (state === "committed") return;
      if (state === "rolledback") {
        throw new Error(`Cannot recommit rolled-back update target: ${step.id}`);
      }
      outgoing.remove();
      incoming.setAttribute("id", node.id);
      incoming.setAttribute("data-element-id", node.id);
      settleElementPresentation(incoming, node);
      rememberElement(context, node, incoming);
      state = "committed";
    };
    const rollback = () => {
      if (state === "rolledback") return;
      incoming.remove();
      restoreElementSnapshot(outgoing, outgoingSnapshot);
      restoreElementPosition(outgoing, outgoingParent, outgoingNextSibling);
      rememberElement(context, step.previous, outgoing);
      state = "rolledback";
    };
    try {
      outgoing.setAttribute("id", `${node.id}--outgoing`);
      outgoing.setAttribute("data-element-id", `${node.id}--outgoing`);
      incoming.setAttribute("id", node.id);
      incoming.setAttribute("data-element-id", node.id);
      gsap.set(incoming, { opacity: 0 });
      if (outgoingParent) {
        outgoingParent.insertBefore(incoming, outgoing.nextSibling);
      } else {
        svg.appendChild(incoming);
      }
      rememberElement(context, node, incoming);

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
  const element = createSceneElement(context, node);
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
      clearDrawResidue(element);
    }
    settleElementPresentation(element, node);
    rememberElement(context, node, element);
    state = "committed";
  };
  const rollback = () => {
    if (state === "rolledback") return;
    element.remove();
    if (context.elements.get(node.id)?.element === element) {
      context.elements.delete(node.id);
    }
    context.invalidate();
    state = "rolledback";
  };

  try {
    svg.appendChild(element);
    rememberElement(context, node, element);

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
      node.kind !== "latex"
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
        const started = executeMotionStep(context, step);
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
