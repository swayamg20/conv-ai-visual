/**
 * GSAP Setup and Configuration
 *
 * Global GSAP configuration for math tutor animations.
 * Provides animation presets and utilities for consistent behavior.
 */

import { gsap } from "gsap";

// Global GSAP configuration for performance
gsap.config({
  autoSleep: 60,        // Sleep after 60 seconds of inactivity
  force3D: true,        // Force GPU acceleration
  nullTargetWarn: false // Don't warn about null targets
});

// Custom easing alias for teaching animations
// We use "power2.inOut" directly via EASING.teaching constant below

/**
 * Animation presets for common teaching animations
 */
export const ANIMATION_PRESETS = {
  // Fade in elements smoothly
  fadeIn: {
    from: { opacity: 0 },
    to: { opacity: 1, duration: 0.4, ease: "power2.inOut" }
  },

  // Slide up with fade for entering elements
  slideUp: {
    from: { y: 20, opacity: 0 },
    to: { y: 0, opacity: 1, duration: 0.5, ease: "back.out(1.2)" }
  },

  // Scale in from center (great for emphasis)
  scaleIn: {
    from: { scale: 0, opacity: 0 },
    to: { scale: 1, opacity: 1, duration: 0.6, ease: "back.out(1.7)" }
  },

  // Highlight pulse (scale up and back)
  highlight: {
    to: {
      scale: 1.15,
      duration: 0.3,
      yoyo: true,
      repeat: 1,
      ease: "power2.inOut"
    }
  },

  // Bounce in (fun, engaging)
  bounceIn: {
    from: { scale: 0, opacity: 0 },
    to: { scale: 1, opacity: 1, duration: 0.8, ease: "bounce.out" }
  },

  // Elastic scale (playful emphasis)
  elasticScale: {
    to: {
      scale: 1.2,
      duration: 0.6,
      ease: "elastic.out(1, 0.5)",
      yoyo: true,
      repeat: 1
    }
  }
};

/**
 * Easing function presets
 */
export const EASING = {
  teaching: "power2.inOut",
  smooth: "power2.out",
  bounce: "bounce.out",
  elastic: "elastic.out(1, 0.5)",
  back: "back.out(1.7)",
  linear: "none"
} as const;

/**
 * Default animation durations (in seconds)
 */
export const DURATION = {
  instant: 0.15,
  fast: 0.3,
  normal: 0.5,
  slow: 0.8,
  verySlow: 1.2
} as const;

/**
 * Utility: Create a GSAP timeline with common defaults
 */
export function createTimeline(config?: gsap.TimelineVars): gsap.core.Timeline {
  return gsap.timeline({
    defaults: {
      ease: EASING.teaching,
      duration: DURATION.normal
    },
    ...config
  });
}

/**
 * Utility: Animate element with fade in
 */
export function animateFadeIn(
  target: gsap.TweenTarget,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.from(target, {
    opacity: 0,
    duration,
    ease: EASING.teaching
  });
}

/**
 * Utility: Animate element with slide up
 */
export function animateSlideUp(
  target: gsap.TweenTarget,
  distance: number = 20,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.from(target, {
    y: distance,
    opacity: 0,
    duration,
    ease: EASING.back
  });
}

/**
 * Utility: Highlight element with pulse
 */
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
    ease: EASING.teaching
  });
}

/**
 * Utility: Move element to new position
 */
export function animateMoveTo(
  target: gsap.TweenTarget,
  x: number,
  y: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, {
    x,
    y,
    duration,
    ease: EASING.teaching
  });
}

/**
 * Utility: Rotate element
 */
export function animateRotate(
  target: gsap.TweenTarget,
  rotation: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, {
    rotation,
    duration,
    ease: EASING.teaching
  });
}

/**
 * Utility: Scale element
 */
export function animateScale(
  target: gsap.TweenTarget,
  scale: number,
  duration: number = DURATION.normal
): gsap.core.Tween {
  return gsap.to(target, {
    scale,
    duration,
    ease: EASING.back
  });
}

/**
 * Utility: Fade out and remove element
 */
export function animateFadeOut(
  target: gsap.TweenTarget,
  duration: number = DURATION.normal,
  onComplete?: () => void
): gsap.core.Tween {
  return gsap.to(target, {
    opacity: 0,
    duration,
    ease: EASING.teaching,
    onComplete
  });
}

export default gsap;
