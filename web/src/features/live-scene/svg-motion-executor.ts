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
} from "@/lib/gsap-setup";
import type { MotionPlan, MotionStep, SceneNode } from "@/lib/live-scene";

interface ManagedMotionPlayback {
  pause(): void;
  resume(): void;
  cancel(): MotionPlaybackOutcome;
}

interface StartedMotion {
  readonly animation: gsap.core.Animation | null;
  readonly appliedOnStart?: boolean;
  readonly complete?: () => void;
  readonly cancel?: () => void;
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

function resolveCssColor(color: string): string {
  let expanded = color;
  for (let depth = 0; depth < 4 && expanded.includes("var("); depth += 1) {
    expanded = expanded.replace(
      /var\((--[\w-]+)(?:,\s*([^)]*))?\)/g,
      (_match, property: string, fallback = "") =>
        getComputedStyle(document.documentElement).getPropertyValue(property).trim() ||
        fallback.trim()
    );
  }
  const probe = document.createElement("span");
  probe.style.color = expanded;
  probe.style.display = "none";
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  return resolved && !resolved.includes("var(") ? resolved : expanded;
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
    const complete = () => {
      target.element.remove();
      if (context.elements.get(step.id)?.element === target.element) {
        context.elements.delete(step.id);
      }
      context.invalidate();
    };
    const cancel = () => {
      gsap.set(target.element, { opacity: step.node.style.opacity });
      context.elements.set(step.id, target);
      context.invalidate();
    };
    if (step.effect === "none") {
      return { animation: null, complete };
    }
    return {
      animation: gsap.to(target.element, {
        opacity: 0,
        duration: DURATION.fast,
        ease: EASING.smooth,
      }),
      complete,
      cancel,
    };
  }

  const node = step.type === "enter" ? step.node : step.next;
  if (step.type === "update") {
    const outgoingData = context.elements.get(step.id);
    if (!outgoingData) throw new Error(`Missing update target: ${step.id}`);
    const outgoing = outgoingData.element;

    if (step.transition === "transform") {
      const replacement = createSceneElement(context, node, node.id);
      if (!replacement) throw new Error(`Could not render update target: ${node.id}`);
      outgoing.replaceChildren(...Array.from(replacement.childNodes));
      outgoing.removeAttribute("clip-path");
      gsap.set(outgoing, { opacity: node.style.opacity, scale: 0.98 });
      rememberElement(context, node, outgoing);
      const finishTransform = () => {
        gsap.set(outgoing, { opacity: node.style.opacity, scale: 1 });
      };
      return {
        appliedOnStart: true,
        animation: gsap.to(outgoing, {
          opacity: node.style.opacity,
          scale: 1,
          duration: DURATION.stateChange,
          ease: EASING.teaching,
        }),
        complete: finishTransform,
        cancel: finishTransform,
      };
    }

    const incoming = createSceneElement(context, node, `${node.id}--incoming`);
    if (!incoming) throw new Error(`Could not render update target: ${node.id}`);

    outgoing.setAttribute("id", `${node.id}--outgoing`);
    outgoing.setAttribute("data-element-id", `${node.id}--outgoing`);
    incoming.setAttribute("id", node.id);
    incoming.setAttribute("data-element-id", node.id);
    gsap.set(incoming, { opacity: 0 });
    svg.appendChild(incoming);
    rememberElement(context, node, incoming);

    const complete = () => {
      outgoing.remove();
      gsap.set(incoming, { opacity: node.style.opacity });
      context.invalidate();
    };
    const cancel = () => {
      incoming.remove();
      outgoing.setAttribute("id", step.previous.id);
      outgoing.setAttribute("data-element-id", step.previous.id);
      gsap.set(outgoing, { opacity: step.previous.style.opacity });
      rememberElement(context, step.previous, outgoing);
    };
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
    return { animation: timeline, complete, cancel };
  }

  if (context.elements.has(node.id)) {
    throw new Error(`Duplicate enter target: ${node.id}`);
  }
  const element = createSceneElement(context, node);
  if (!element) throw new Error(`Could not render enter target: ${node.id}`);
  svg.appendChild(element);
  rememberElement(context, node, element);

  if (step.effect === "none") {
    gsap.set(element, { opacity: node.style.opacity });
    return { animation: null, appliedOnStart: true };
  }
  if (step.effect === "scale") {
    return {
      appliedOnStart: true,
      animation: gsap.fromTo(
        element,
        { opacity: 0, scale: 0.85, transformOrigin: "center center" },
        {
          opacity: node.style.opacity,
          scale: 1,
          duration: DURATION.normal,
          ease: EASING.back,
        }
      ),
      complete: () => {
        gsap.set(element, { opacity: node.style.opacity, scale: 1 });
      },
    };
  }
  if (step.effect === "draw" && node.kind !== "text" && node.kind !== "latex") {
    return {
      appliedOnStart: true,
      animation: animateDrawOn(element, DURATION.drawSlow, EASING.draw),
      complete: () => {
        gsap.set(element, { opacity: node.style.opacity });
      },
    };
  }
  return {
    appliedOnStart: true,
    animation: gsap.fromTo(
      element,
      { opacity: 0 },
      {
        opacity: node.style.opacity,
        duration: DURATION.fast,
        ease: EASING.teaching,
      }
    ),
    complete: () => {
      gsap.set(element, { opacity: node.style.opacity });
    },
  };
}

/** Own every timer and tween required to materialize one deterministic motion plan. */
export function createSvgMotionExecutor(
  context: SvgMotionExecutorContext
): SvgMotionExecutor {
  const playbacks = new Set<ManagedMotionPlayback>();
  const emphasisAnimations = new Map<gsap.core.Animation, () => void>();

  const play = (
    plan: MotionPlan,
    options: MotionPlaybackOptions = {}
  ): MotionPlayback => {
    const animations = new Set<gsap.core.Animation>();
    const cancellers = new Set<() => void>();
    const appliedStepIds = new Set<string>();
    const staggerSeconds = Math.max(0, options.staggerMs ?? 90) / 1000;
    let completedSteps = 0;
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

    const settle = (
      status: MotionPlaybackOutcome["status"],
      error?: unknown
    ): MotionPlaybackOutcome => {
      if (settledOutcome) return settledOutcome;
      settledOutcome = outcome(status, error);
      playbacks.delete(controller);
      resolveFinished(settledOutcome);
      return settledOutcome;
    };

    const cancelActiveWork = () => {
      animations.forEach((animation) => animation.kill());
      animations.clear();
      cancellers.forEach((cancel) => cancel());
      cancellers.clear();
    };

    const fail = (error: unknown) => {
      if (settledOutcome) return;
      cancelActiveWork();
      settle("failed", error);
    };

    const completeStep = () => {
      completedSteps += 1;
      if (completedSteps === plan.steps.length) settle("completed");
    };

    const startStep = (step: MotionStep) => {
      if (settledOutcome) return;
      try {
        const started = executeMotionStep(context, step);
        if (started.appliedOnStart) appliedStepIds.add(step.id);
        if (started.cancel) cancellers.add(started.cancel);

        if (!started.animation) {
          started.complete?.();
          appliedStepIds.add(step.id);
          if (started.cancel) cancellers.delete(started.cancel);
          completeStep();
          return;
        }

        const wrapper = gsap.timeline({
          paused: true,
          onComplete: () => {
            animations.delete(wrapper);
            try {
              started.complete?.();
              appliedStepIds.add(step.id);
              if (started.cancel) cancellers.delete(started.cancel);
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
        cancelActiveWork();
        return settle("cancelled");
      },
    };
    playbacks.add(controller);

    if (plan.steps.length === 0) {
      settle("completed");
    } else {
      plan.steps.forEach((step, index) => {
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
    playbacks.clear();
    emphasisAnimations.forEach((restore, animation) => {
      animation.kill();
      restore();
    });
    emphasisAnimations.clear();
  };

  return { play, emphasize, cancel, dispose: cancel };
}
