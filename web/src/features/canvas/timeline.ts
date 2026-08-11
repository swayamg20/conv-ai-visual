import { gsap } from "gsap";

import {
  animateColorPulse,
  animateCrossOut,
  createTimeline,
  DURATION,
  EASING,
} from "@/lib/gsap-setup";

import type {
  CanvasOperation,
  CanvasPalette,
  LatexOperation,
  TeachingStep,
} from "./types";

export interface TeachingTimelineContext {
  render: (operations: CanvasOperation[]) => void;
  renderLatex: (operation: LatexOperation) => void;
  clearScene: () => void;
  getElement: (id: string) => SVGElement | null;
  getSvg: () => SVGSVGElement | null;
  generateId: () => string;
  palette: CanvasPalette;
}

/** Translate a declarative teaching step into the operation consumed by the renderer. */
export function operationForTeachingStep(
  step: TeachingStep,
  generateId: () => string
): CanvasOperation {
  return {
    action: step.action,
    id: step.target_id || step.label || `${step.action}_${generateId()}`,
    x: step.x,
    y: step.y,
    width: step.width,
    height: step.height,
    color: step.color,
    fill: step.fill,
    stroke_width: step.stroke_width,
    points: step.points,
    text: step.text,
    font_size: step.font_size,
    font_family: step.font_family,
    roughness: step.roughness,
    animate_style: step.animate_style ?? "draw",
    _centered: step._centered,
  };
}

/** Build a paused GSAP timeline; queueing and playback remain the component's responsibility. */
export function createTeachingTimeline(
  steps: TeachingStep[],
  context: TeachingTimelineContext
): gsap.core.Timeline {
  const timeline = createTimeline({ paused: true });

  for (const step of steps) {
    switch (step.action) {
      case "clear":
        timeline.add(context.clearScene);
        timeline.to({}, { duration: DURATION.fast + 0.1 });
        break;

      case "draw":
        if (step.element) {
          const operation = {
            ...step.element,
            animate_style: step.animate_style ?? "draw",
          };
          timeline.add(() => context.render([operation]), "+=0.15");
        }
        break;

      case "animate":
        if (step.target_id && step.properties) {
          const targetId = step.target_id;
          const properties = step.properties;
          const duration = step.duration ?? DURATION.normal;
          timeline.add(() => {
            const target = context.getElement(targetId);
            if (target) gsap.to(target, { ...properties, duration });
          }, ">");
        }
        break;

      case "latex":
        if (step.latex && step.x !== undefined && step.y !== undefined) {
          const operation: LatexOperation = {
            type: "latex",
            id: step.target_id || `latex_${context.generateId()}`,
            latex: step.latex,
            x: step.x,
            y: step.y,
            font_size: step.font_size ?? 20,
            color: step.color ?? context.palette.stroke,
          };
          timeline.add(() => context.renderLatex(operation), "+=0.15");
        }
        break;

      case "text": {
        const operation: CanvasOperation = {
          action: "text",
          id: step.target_id || `text_${context.generateId()}`,
          text: step.text ?? step.speech_cue ?? "",
          x: step.x ?? 0,
          y: step.y ?? 0,
          color: step.color ?? context.palette.stroke,
          font_size: step.font_size ?? 16,
          font_family: step.font_family,
          animate_style: step.animate_style ?? "draw",
          _centered: step._centered,
        };
        timeline.add(() => context.render([operation]), "+=0.15");
        break;
      }

      case "highlight":
        if (step.target_id) {
          const targetId = step.target_id;
          const duration = step.duration ?? 0.3;
          const color = step.highlight_color;
          timeline.add(() => {
            const target = context.getElement(targetId);
            if (!target) return;
            if (color) {
              animateColorPulse(target, color, duration, 2);
              return;
            }
            gsap.set(target, { transformOrigin: "center center" });
            gsap.to(target, {
              scale: 1.15,
              duration,
              yoyo: true,
              repeat: 1,
              ease: EASING.teaching,
            });
          }, ">");
        }
        break;

      case "crossout":
        if (step.target_id) {
          const targetId = step.target_id;
          const color = step.color ?? context.palette.error;
          timeline.add(() => {
            const target = context.getElement(targetId);
            const svg = context.getSvg();
            if (target && svg) animateCrossOut(svg, target, color, DURATION.fast);
          }, ">");
        }
        break;

      case "pause":
        timeline.to({}, { duration: step.duration ?? 0.5 });
        break;

      default: {
        const operation = operationForTeachingStep(step, context.generateId);
        timeline.add(() => context.render([operation]), "+=0.15");
        break;
      }
    }
  }

  return timeline;
}
