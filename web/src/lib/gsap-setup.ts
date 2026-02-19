/**
 * GSAP Setup and Configuration
 *
 * Global GSAP configuration for math tutor animations.
 * Provides animation presets, draw-on effects, and utilities.
 */

import { gsap } from "gsap";

gsap.config({
  autoSleep: 60,
  force3D: true,
  nullTargetWarn: false,
});

/**
 * Easing function presets
 */
export const EASING = {
  teaching: "power2.inOut",
  smooth: "power2.out",
  smoothIn: "power2.in",
  bounce: "bounce.out",
  elastic: "elastic.out(1, 0.5)",
  back: "back.out(1.7)",
  linear: "none",
  snappy: "power3.out",
  gentle: "sine.inOut",
  draw: "power1.inOut",
} as const;

/**
 * Default animation durations (in seconds)
 */
export const DURATION = {
  instant: 0.15,
  fast: 0.3,
  normal: 0.5,
  slow: 0.8,
  verySlow: 1.2,
  draw: 0.6,
  drawSlow: 1.0,
} as const;

/**
 * Animation presets for common teaching animations
 */
export const ANIMATION_PRESETS = {
  fadeIn: {
    from: { opacity: 0 },
    to: { opacity: 1, duration: 0.4, ease: EASING.teaching },
  },
  slideUp: {
    from: { y: 20, opacity: 0 },
    to: { y: 0, opacity: 1, duration: 0.5, ease: "back.out(1.2)" },
  },
  scaleIn: {
    from: { scale: 0, opacity: 0 },
    to: { scale: 1, opacity: 1, duration: 0.6, ease: "back.out(1.7)" },
  },
  highlight: {
    to: {
      scale: 1.15,
      duration: 0.3,
      yoyo: true,
      repeat: 1,
      ease: EASING.teaching,
    },
  },
  bounceIn: {
    from: { scale: 0, opacity: 0 },
    to: { scale: 1, opacity: 1, duration: 0.8, ease: "bounce.out" },
  },
  elasticScale: {
    to: {
      scale: 1.2,
      duration: 0.6,
      ease: EASING.elastic,
      yoyo: true,
      repeat: 1,
    },
  },
};

/**
 * Create a GSAP timeline with common defaults
 */
export function createTimeline(config?: gsap.TimelineVars): gsap.core.Timeline {
  return gsap.timeline({
    defaults: {
      ease: EASING.teaching,
      duration: DURATION.normal,
    },
    ...config,
  });
}

// ============= Draw-On Animation (the big visual win) =============

/**
 * Animate SVG paths with a "draw-on" stroke effect.
 * Makes Rough.js shapes look like they're being hand-drawn in real-time.
 *
 * Works by setting strokeDasharray/strokeDashoffset on every <path> inside
 * the target group, then tweening offset from full-length to 0.
 */
export function animateDrawOn(
  group: SVGElement,
  duration: number = DURATION.draw,
  ease: string = EASING.draw,
  stagger: number = 0.08
): gsap.core.Timeline {
  const paths = group.querySelectorAll("path");
  const tl = gsap.timeline();

  if (paths.length === 0) {
    gsap.fromTo(group, { opacity: 0 }, { opacity: 1, duration: DURATION.fast, ease: EASING.smooth });
    return tl;
  }

  gsap.set(group, { opacity: 1 });

  paths.forEach((path) => {
    const length = path.getTotalLength();
    gsap.set(path, {
      strokeDasharray: length,
      strokeDashoffset: length,
    });
  });

  tl.to(Array.from(paths), {
    strokeDashoffset: 0,
    duration,
    ease,
    stagger,
  });

  // Reveal any fill after the stroke animation completes
  const filledPaths = Array.from(paths).filter(
    (p) => p.getAttribute("fill") && p.getAttribute("fill") !== "none"
  );
  if (filledPaths.length > 0) {
    filledPaths.forEach((p) => {
      const originalFill = p.getAttribute("fill")!;
      gsap.set(p, { fill: "transparent" });
      tl.to(p, { fill: originalFill, duration: DURATION.fast, ease: EASING.smooth }, "-=0.1");
    });
  }

  return tl;
}

/**
 * Animate a progressive path draw (for function plots).
 * Reveals points left-to-right as a growing polyline.
 */
export function animateProgressivePath(
  pathElement: SVGPathElement,
  duration: number = DURATION.verySlow,
  ease: string = EASING.draw
): gsap.core.Tween {
  const length = pathElement.getTotalLength();
  gsap.set(pathElement, {
    strokeDasharray: length,
    strokeDashoffset: length,
    opacity: 1,
  });
  return gsap.to(pathElement, {
    strokeDashoffset: 0,
    duration,
    ease,
  });
}

// ============= Highlight / Emphasis Effects =============

/**
 * Color pulse glow: briefly changes stroke color and adds a glow filter,
 * then returns to original. Great for "look at this" moments.
 */
export function animateColorPulse(
  group: SVGElement,
  highlightColor: string = "#ef4444",
  duration: number = 0.4,
  repeat: number = 1
): gsap.core.Timeline {
  const paths = group.querySelectorAll("path, line, circle, ellipse, rect");
  const tl = gsap.timeline();

  paths.forEach((el) => {
    const originalStroke = el.getAttribute("stroke") || "#000";
    tl.to(
      el,
      {
        attr: { stroke: highlightColor },
        duration: duration / 2,
        yoyo: true,
        repeat: repeat * 2 - 1,
        ease: EASING.teaching,
        onComplete: () => el.setAttribute("stroke", originalStroke),
      },
      0
    );
  });

  return tl;
}

/**
 * Cross-out effect: draws an animated strikethrough line over an element.
 * Useful for showing wrong answers or cancelled terms.
 */
export function animateCrossOut(
  svgRoot: SVGSVGElement,
  targetGroup: SVGElement,
  color: string = "#ef4444",
  duration: number = DURATION.fast
): SVGLineElement {
  const bbox = (targetGroup as SVGGraphicsElement).getBBox();
  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  line.setAttribute("x1", String(bbox.x - 4));
  line.setAttribute("y1", String(bbox.y + bbox.height / 2));
  line.setAttribute("x2", String(bbox.x + bbox.width + 4));
  line.setAttribute("y2", String(bbox.y + bbox.height / 2));
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "3");
  line.setAttribute("stroke-linecap", "round");
  svgRoot.appendChild(line);

  const len = line.getTotalLength?.() || bbox.width + 8;
  gsap.set(line, { strokeDasharray: len, strokeDashoffset: len });
  gsap.to(line, { strokeDashoffset: 0, duration, ease: EASING.snappy });

  gsap.to(targetGroup, { opacity: 0.35, duration, ease: EASING.smooth });

  return line;
}

// ============= Basic Utilities =============

export function animateFadeIn(
  target: gsap.TweenTarget,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.from(target, { opacity: 0, duration, ease: EASING.teaching });
}

export function animateSlideUp(
  target: gsap.TweenTarget,
  distance: number = 20,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.from(target, { y: distance, opacity: 0, duration, ease: EASING.back });
}

export function animateHighlight(
  target: gsap.TweenTarget,
  scale: number = 1.15,
  duration: number = DURATION.fast
): gsap.core.Tween {
  return gsap.to(target, {
    scale,
    duration,
    yoyo: true,
    repeat: 1,
    ease: EASING.teaching,
  });
}

export function animateMoveTo(
  target: gsap.TweenTarget,
  x: number,
  y: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, { x, y, duration, ease: EASING.teaching });
}

export function animateRotate(
  target: gsap.TweenTarget,
  rotation: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, { rotation, duration, ease: EASING.teaching });
}

export function animateScale(
  target: gsap.TweenTarget,
  scale: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, { scale, duration, ease: EASING.back });
}

export function animateFadeOut(
  target: gsap.TweenTarget,
  duration: number = DURATION.normal,
  onComplete?: () => void
): gsap.core.Tween {
  return gsap.to(target, { opacity: 0, duration, ease: EASING.teaching, onComplete });
}

export default gsap;
