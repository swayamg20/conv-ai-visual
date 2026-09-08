import type { gsap } from "gsap";

import type {
  ChoreographyEasing,
  MotionStep,
  PlannedCheckpointChoreography,
  ScenePoint,
  ViewportPoseV1,
} from "@/lib/live-scene";

import {
  addCompatibleTransform,
  interpolateViewport,
} from "./choreography-interpolation";
import type {
  SvgNodeReconciler,
  SvgNodeReconcilerContext,
} from "./svg-node-reconciler";

const CLOSED_EASINGS: Readonly<Record<ChoreographyEasing, string>> =
  Object.freeze({
    linear: "none",
    ease_in: "power2.in",
    ease_out_quart: "power3.out",
    ease_out_quint: "power4.out",
    ease_in_out: "power2.inOut",
  });

type TimelineContext = Pick<SvgNodeReconcilerContext, "elements" | "getSvg"> & {
  renderViewportFrame(viewport: ViewportPoseV1): void;
};

interface AppendPhaseTweensOptions {
  readonly timeline: gsap.core.Timeline;
  readonly reconciler: SvgNodeReconciler;
  readonly context: TimelineContext;
  readonly plan: PlannedCheckpointChoreography;
  readonly duration: number;
  readonly onError: (error: unknown) => void;
}

interface PhaseSchedule {
  readonly exitDuration: number;
  readonly updateStart: number;
  readonly updateDuration: number;
  readonly enterStart: number;
  readonly enterDuration: number;
}

const EXIT_HANDOFF_FRACTION = 0.2;
const DEFERRED_ENTER_FRACTION = 0.75;

/** Keep mutually exclusive layouts apart while retained identities move. */
function phaseSchedule(
  steps: readonly MotionStep[],
  duration: number,
): PhaseSchedule {
  const hasEnter = steps.some((step) => step.type === "enter");
  const hasExit = steps.some((step) => step.type === "remove");
  const hasUpdate = steps.some((step) => step.type === "update");
  const needsExitHandoff = hasExit && (hasEnter || hasUpdate);
  const exitDuration = needsExitHandoff
    ? duration * EXIT_HANDOFF_FRACTION
    : duration;
  const updateStart = hasExit && hasUpdate ? exitDuration : 0;
  const enterStart = hasExit
    ? exitDuration
    : hasEnter && hasUpdate
      ? duration * DEFERRED_ENTER_FRACTION
      : 0;

  return {
    exitDuration,
    updateStart,
    updateDuration: duration - updateStart,
    enterStart,
    enterDuration: duration - enterStart,
  };
}

function replaceCanonicalContent(target: SVGElement, source: SVGElement): void {
  const identity = new Set(["id", "data-element-id"]);
  for (const attribute of Array.from(target.attributes)) {
    if (!identity.has(attribute.name)) target.removeAttribute(attribute.name);
  }
  for (const attribute of Array.from(source.attributes)) {
    if (!identity.has(attribute.name)) {
      target.setAttribute(attribute.name, attribute.value);
    }
  }
  target.replaceChildren(
    ...Array.from(source.childNodes, (child) => child.cloneNode(true)),
  );
}

function pathLength(points: readonly ScenePoint[], closed: boolean): number {
  let length = 0;
  for (let index = 1; index < points.length; index += 1) {
    length += Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    );
  }
  if (closed && points.length > 1) {
    length += Math.hypot(
      points[0][0] - points[points.length - 1][0],
      points[0][1] - points[points.length - 1][1],
    );
  }
  return length;
}

function addCrossfade(
  timeline: gsap.core.Timeline,
  reconciler: SvgNodeReconciler,
  element: SVGElement,
  step: Extract<MotionStep, { type: "update" }>,
  duration: number,
  ease: string,
  position: number,
): void {
  const canonical = reconciler.create(step.next, `${step.id}--canonical`);
  if (!canonical) {
    throw new Error(`Could not render crossfade target ${step.id}`);
  }
  const half = duration / 2;
  timeline.to(element, { opacity: 0, duration: half, ease }, position);
  timeline.call(
    () => replaceCanonicalContent(element, canonical),
    [],
    position + half,
  );
  timeline.to(
    element,
    { opacity: step.next.style.opacity, duration: half, ease },
    position + half,
  );
}

function addEnter(
  timeline: gsap.core.Timeline,
  reconciler: SvgNodeReconciler,
  context: TimelineContext,
  step: Extract<MotionStep, { type: "enter" }>,
  duration: number,
  ease: string,
  position: number,
): void {
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");
  const element = reconciler.create(step.node);
  if (!element) throw new Error(`Could not render enter target ${step.id}`);
  svg.appendChild(element);
  reconciler.remember(step.node, element);
  const opacity = step.node.style.opacity;

  if (step.effect === "none") {
    element.style.opacity = "0";
    timeline.call(
      () => {
        element.style.opacity = String(opacity);
      },
      [],
      position,
    );
    return;
  }

  if (step.effect === "scale") {
    const proxy = { progress: 0 };
    element.style.opacity = "0";
    element.style.transform = "scale(0.94)";
    element.style.transformOrigin = "center center";
    timeline.to(
      proxy,
      {
        progress: 1,
        duration,
        ease,
        onUpdate: () => {
          element.style.opacity = String(opacity * proxy.progress);
          element.style.transform = `scale(${0.94 + proxy.progress * 0.06})`;
        },
      },
      position,
    );
    return;
  }

  if (
    step.effect === "draw" &&
    (step.node.kind === "path" || step.node.kind === "line")
  ) {
    const paths = element.querySelectorAll<SVGPathElement>("path");
    const length = Math.max(
      1,
      pathLength(
        step.node.points,
        step.node.kind === "path" && step.node.closed,
      ),
    );
    paths.forEach((path) => {
      path.setAttribute("stroke-dasharray", String(length));
      path.setAttribute("stroke-dashoffset", String(length));
    });
    element.style.opacity = "0";
    const proxy = { progress: 0 };
    timeline.to(
      proxy,
      {
        progress: 1,
        duration,
        ease,
        onUpdate: () => {
          const remaining = length * (1 - proxy.progress);
          paths.forEach((path) =>
            path.setAttribute("stroke-dashoffset", String(remaining)),
          );
          element.style.opacity = String(opacity * proxy.progress);
        },
      },
      position,
    );
    return;
  }

  timeline.fromTo(
    element,
    { opacity: 0 },
    { opacity, duration, ease },
    position,
  );
}

function addExit(
  timeline: gsap.core.Timeline,
  element: SVGElement,
  step: Extract<MotionStep, { type: "remove" }>,
  duration: number,
  ease: string,
  position: number,
): void {
  if (step.effect === "none") {
    timeline.call(
      () => {
        element.style.opacity = "0";
      },
      [],
      position,
    );
    return;
  }
  timeline.to(element, { opacity: 0, duration, ease }, position);
}

function addEmphasis(
  timeline: gsap.core.Timeline,
  element: SVGElement,
  duration: number,
  ease: string,
  position: number,
): void {
  const proxy = { progress: 0 };
  timeline.to(
    proxy,
    {
      progress: 1,
      duration,
      ease,
      onUpdate: () => {
        const brightness = 1 + Math.sin(Math.PI * proxy.progress) * 0.28;
        element.style.filter = `brightness(${brightness})`;
      },
    },
    position,
  );
}

function addMotionStep(
  timeline: gsap.core.Timeline,
  reconciler: SvgNodeReconciler,
  context: TimelineContext,
  step: MotionStep,
  ease: string,
  onError: (error: unknown) => void,
  schedule: PhaseSchedule,
): void {
  if (step.type === "enter") {
    addEnter(
      timeline,
      reconciler,
      context,
      step,
      schedule.enterDuration,
      ease,
      schedule.enterStart,
    );
    return;
  }

  const data = context.elements.get(step.id);
  if (!data) throw new Error(`Missing ${step.type} target ${step.id}`);
  if (step.type === "remove") {
    addExit(timeline, data.element, step, schedule.exitDuration, ease, 0);
  } else if (step.transition === "crossfade") {
    addCrossfade(
      timeline,
      reconciler,
      data.element,
      step,
      schedule.updateDuration,
      ease,
      schedule.updateStart,
    );
  } else {
    addCompatibleTransform(
      timeline,
      data.element,
      step,
      schedule.updateDuration,
      ease,
      onError,
      schedule.updateStart,
    );
  }
}

/** Append one visual phase; the executor retains timeline lifecycle ownership. */
export function appendChoreographyPhaseTweens({
  timeline,
  reconciler,
  context,
  plan,
  duration,
  onError,
}: AppendPhaseTweensOptions): void {
  const ease = CLOSED_EASINGS[plan.choreographyPlan.phase.easing];
  const schedule = phaseSchedule(plan.motionPlan.steps, duration);
  for (const step of plan.motionPlan.steps) {
    addMotionStep(
      timeline,
      reconciler,
      context,
      step,
      ease,
      onError,
      schedule,
    );
  }

  const camera = { progress: 0 };
  const cameraStart = plan.motionPlan.steps.some(
    (step) => step.type === "update",
  )
    ? schedule.updateStart
    : 0;
  const cameraDuration = duration - cameraStart;
  timeline.to(
    camera,
    {
      progress: 1,
      duration: cameraDuration,
      ease,
      onUpdate: () => {
        try {
          context.renderViewportFrame(
            interpolateViewport(
              plan.baseViewport,
              plan.resultViewport,
              camera.progress,
            ),
          );
        } catch (error) {
          onError(error);
        }
      },
    },
    cameraStart,
  );

  const emphasized =
    plan.choreographyPlan.phase.cues.find((cue) => cue.cue === "emphasize")
      ?.targetIds ?? [];
  for (const id of emphasized) {
    const element = context.elements.get(id)?.element;
    if (!element) throw new Error(`Missing emphasis target ${id}`);
    addEmphasis(timeline, element, duration, ease, 0);
  }
}
