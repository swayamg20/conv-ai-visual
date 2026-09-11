import type { gsap } from "gsap";

import type {
  ChoreographyPlan,
  MotionStep,
  PathSceneNode,
  PlannedCheckpointChoreography,
  ScenePoint,
  TracePathCueV2,
} from "@/lib/live-scene";

import {
  createUniformTimePathSampler,
  pathBoundingBoxCenter,
  pathTranslationDelta,
} from "./choreography-interpolation";
import type {
  SvgNodeReconciler,
  SvgNodeReconcilerContext,
} from "./svg-node-reconciler";

type TraceTimelineContext = Pick<
  SvgNodeReconcilerContext,
  "elements" | "getSvg"
>;

export interface PreparedTracePathMotion {
  readonly cue: TracePathCueV2;
  readonly pathStep: Extract<MotionStep, { type: "enter" }> & {
    readonly node: PathSceneNode;
  };
  readonly markerStep:
    | (Extract<MotionStep, { type: "enter" }> & {
        readonly node: PathSceneNode;
      })
    | (Extract<MotionStep, { type: "update" }> & {
        readonly previous: PathSceneNode;
        readonly next: PathSceneNode;
      });
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function samePoint(left: ScenePoint, right: ScenePoint): boolean {
  const tolerance = 1e-9;
  return (
    Math.abs(left[0] - right[0]) <= tolerance &&
    Math.abs(left[1] - right[1]) <= tolerance
  );
}

/** Validate V2 ownership and geometry before any target DOM mutation. */
export function prepareTracePathMotion(
  plan: PlannedCheckpointChoreography<ChoreographyPlan>,
): PreparedTracePathMotion | null {
  if (plan.choreographyPlan.v !== 2) return null;
  const motionIds = (type: MotionStep["type"]): string[] =>
    plan.motionPlan.steps
      .filter((step) => step.type === type)
      .map((step) => step.id)
      .sort();
  const cueIds = (
    kind: "enter" | "exit" | "transform",
  ): readonly string[] =>
    plan.choreographyPlan.phase.cues.find((cue) => cue.cue === kind)
      ?.targetIds ?? [];
  if (
    !sameIds(cueIds("enter"), motionIds("enter")) ||
    !sameIds(cueIds("exit"), motionIds("remove"))
  ) {
    throw new Error("V2 lifecycle cues must exactly own enter and exit steps");
  }

  const trace = plan.choreographyPlan.phase.cues.find(
    (cue): cue is TracePathCueV2 => cue.cue === "trace_path",
  );
  if (!trace) {
    if (!sameIds(cueIds("transform"), motionIds("update"))) {
      throw new Error("V2 transform cues must exactly own update steps");
    }
    return null;
  }

  const transformIds = cueIds("transform");
  if (
    transformIds.includes(trace.pathId) ||
    transformIds.includes(trace.markerId)
  ) {
    throw new Error("Trace-owned nodes overlap normal transform targets");
  }

  const pathStep = plan.motionPlan.steps.find(
    (step) => step.id === trace.pathId,
  );
  if (
    !pathStep ||
    pathStep.type !== "enter" ||
    pathStep.node.kind !== "path" ||
    pathStep.effect !== "draw" ||
    pathStep.node.closed ||
    pathStep.node.points.length < 2
  ) {
    throw new Error("Trace path must be one entering open path");
  }

  const markerStep = plan.motionPlan.steps.find(
    (step) => step.id === trace.markerId,
  );
  if (
    !markerStep ||
    (markerStep.type !== "enter" && markerStep.type !== "update")
  ) {
    throw new Error("Trace marker must be one entering or retained update");
  }
  const markerTarget =
    markerStep.type === "enter" ? markerStep.node : markerStep.next;
  if (
    markerTarget.kind !== "path" ||
    !markerTarget.closed ||
    markerTarget.points.length < 3
  ) {
    throw new Error("Trace marker must be a closed path node");
  }

  const pathStart = pathStep.node.points[0];
  const pathEnd = pathStep.node.points.at(-1)!;
  if (!samePoint(pathBoundingBoxCenter(markerTarget), pathEnd)) {
    throw new Error("Trace marker target anchor must join the path endpoint");
  }

  if (markerStep.type === "update") {
    const translation =
      markerStep.previous.kind === "path" && markerStep.next.kind === "path"
        ? pathTranslationDelta(markerStep.previous, markerStep.next)
        : null;
    if (
      markerStep.transition !== "transform" ||
      markerStep.previous.kind !== "path" ||
      markerStep.next.kind !== "path" ||
      !markerStep.previous.closed ||
      translation === null ||
      !samePoint(pathBoundingBoxCenter(markerStep.previous), pathStart)
    ) {
      throw new Error("A retained trace marker must be a pure path translation");
    }
    const expectedDelta: ScenePoint = [
      pathEnd[0] - pathStart[0],
      pathEnd[1] - pathStart[1],
    ];
    if (!samePoint(translation, expectedDelta)) {
      throw new Error("Trace marker translation must join both path endpoints");
    }
  }

  const ownedUpdateIds = [
    ...transformIds,
    ...(markerStep.type === "update" ? [markerStep.id] : []),
  ].sort();
  if (!sameIds(motionIds("update"), ownedUpdateIds)) {
    throw new Error(
      "Normal transforms and the trace marker must exactly partition updates",
    );
  }

  return { cue: trace, pathStep, markerStep } as PreparedTracePathMotion;
}

function appendEnterElement(
  reconciler: SvgNodeReconciler,
  context: TraceTimelineContext,
  step: Extract<MotionStep, { type: "enter" }>,
): SVGElement {
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");
  const element = reconciler.create(step.node);
  if (!element) throw new Error(`Could not render enter target ${step.id}`);
  svg.appendChild(element);
  reconciler.remember(step.node, element);
  return element;
}

function setTranslation(element: SVGElement, x: number, y: number): void {
  if (Math.abs(x) <= 1e-9 && Math.abs(y) <= 1e-9) {
    element.removeAttribute("transform");
    return;
  }
  element.setAttribute("transform", `translate(${x} ${y})`);
}

/** Add the one linear, equal-sample-time trace owned by a V2 phase. */
export function appendTracePathTween(
  timeline: gsap.core.Timeline,
  reconciler: SvgNodeReconciler,
  context: TraceTimelineContext,
  trace: PreparedTracePathMotion,
  duration: number,
  onError: (error: unknown) => void,
): void {
  const pathElement = appendEnterElement(
    reconciler,
    context,
    trace.pathStep,
  );
  const markerElement =
    trace.markerStep.type === "enter"
      ? appendEnterElement(reconciler, context, trace.markerStep)
      : context.elements.get(trace.markerStep.id)?.element;
  if (!markerElement) {
    throw new Error(`Missing trace marker ${trace.markerStep.id}`);
  }
  const svg = context.getSvg();
  if (!svg) throw new Error("The SVG canvas is unavailable");
  svg.appendChild(markerElement);

  const sampler = createUniformTimePathSampler(trace.pathStep.node.points);
  const paths = Array.from(
    pathElement.querySelectorAll<SVGPathElement>("path"),
  );
  if (paths.length === 0) {
    throw new Error(`Trace path ${trace.pathStep.id} has no SVG path`);
  }
  paths.forEach((path) => {
    path.setAttribute("stroke-dasharray", String(sampler.totalLength));
    path.setAttribute("stroke-dashoffset", String(sampler.totalLength));
  });
  pathElement.style.opacity = "0";
  if (trace.markerStep.type === "enter") markerElement.style.opacity = "0";

  const markerOrigin =
    trace.markerStep.type === "enter"
      ? pathBoundingBoxCenter(trace.markerStep.node)
      : pathBoundingBoxCenter(trace.markerStep.previous);
  const markerOpacity =
    trace.markerStep.type === "enter"
      ? trace.markerStep.node.style.opacity
      : trace.markerStep.previous.style.opacity;
  const proxy = { progress: 0 };
  timeline.to(
    proxy,
    {
      progress: 1,
      duration,
      ease: "none",
      onUpdate: () => {
        try {
          const sample = sampler.sample(proxy.progress);
          paths.forEach((path) => {
            path.setAttribute(
              "stroke-dashoffset",
              String(sample.totalLength - sample.revealedLength),
            );
          });
          pathElement.style.opacity = String(trace.pathStep.node.style.opacity);
          markerElement.style.opacity = String(markerOpacity);
          setTranslation(
            markerElement,
            sample.point[0] - markerOrigin[0],
            sample.point[1] - markerOrigin[1],
          );
        } catch (error) {
          onError(error);
        }
      },
    },
    0,
  );
}
