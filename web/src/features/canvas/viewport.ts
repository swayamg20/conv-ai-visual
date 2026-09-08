import { gsap } from "gsap";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import type {
  ViewportPlayback,
  ViewportPlaybackOptions,
  ViewportPlaybackOutcome,
} from "@/features/canvas/types";
import {
  MAX_CHOREOGRAPHY_PLAN_MS,
  type ChoreographyEasing,
  type ViewportPoseV1,
} from "@/lib/live-scene/choreography";
import { DURATION } from "@/lib/gsap-setup";

interface CanvasViewportOptions {
  svgRef: RefObject<SVGSVGElement | null>;
  width: number;
  height: number;
  interactionLocked?: boolean;
}

interface ManagedViewportPlayback extends ViewportPlayback {
  cancel(): ViewportPlaybackOutcome;
}

const VIEWPORT_EASING: Record<ChoreographyEasing, string> = {
  linear: "none",
  ease_in: "power2.in",
  ease_out_quart: "power4.out",
  ease_out_quint: "power5.out",
  ease_in_out: "power2.inOut",
};

function viewportError(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "Viewport animation failed";
}

function freezeRenderedPose(
  pose: Omit<ViewportPoseV1, "v"> & { readonly v?: number },
): ViewportPoseV1 {
  if (pose.v !== undefined && pose.v !== 1) {
    throw new RangeError("Viewport pose version must equal 1");
  }
  const values = [pose.x, pose.y, pose.width, pose.height];
  if (
    values.some((value) => typeof value !== "number" || !Number.isFinite(value))
  ) {
    throw new TypeError(
      "Viewport coordinates and extents must be finite numbers",
    );
  }
  if (pose.width <= 0 || pose.height <= 0) {
    throw new RangeError("Viewport pose must have positive extents");
  }
  return Object.freeze({
    v: 1,
    x: pose.x,
    y: pose.y,
    width: pose.width,
    height: pose.height,
  });
}

function freezeCertifiedPose(
  pose: Omit<ViewportPoseV1, "v"> & { readonly v?: number },
  width: number,
  height: number,
): ViewportPoseV1 {
  const frozen = freezeRenderedPose(pose);
  if (frozen.x < 0 || frozen.y < 0) {
    throw new RangeError(
      "Certified viewport pose must have a nonnegative origin",
    );
  }
  if (frozen.x + frozen.width > width || frozen.y + frozen.height > height) {
    throw new RangeError("Viewport pose must remain inside the logical canvas");
  }
  return frozen;
}

function validatePlaybackOptions(options: ViewportPlaybackOptions): void {
  if (
    typeof options.durationMs !== "number" ||
    !Number.isFinite(options.durationMs) ||
    options.durationMs < 0 ||
    options.durationMs > MAX_CHOREOGRAPHY_PLAN_MS
  ) {
    throw new RangeError(
      `Viewport durationMs must be between 0 and ${MAX_CHOREOGRAPHY_PLAN_MS}`,
    );
  }
  if (!Object.hasOwn(VIEWPORT_EASING, options.easing)) {
    throw new RangeError(
      "Viewport easing is not in the closed choreography vocabulary",
    );
  }
}

function viewBoxValue(pose: ViewportPoseV1): string {
  return `${pose.x} ${pose.y} ${pose.width} ${pose.height}`;
}

export function useCanvasViewport({
  svgRef,
  width,
  height,
  interactionLocked = false,
}: CanvasViewportOptions) {
  const defaultPose = useMemo(
    () => freezeCertifiedPose({ x: 0, y: 0, width, height }, width, height),
    [height, width],
  );
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef({ x: 0, y: 0 });
  const viewportPoseRef = useRef<ViewportPoseV1>(defaultPose);
  const activePlaybackRef = useRef<ManagedViewportPlayback | null>(null);
  const mountedRef = useRef(true);
  const isPanningRef = useRef(false);
  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

  const writePose = useCallback(
    (
      poseValue: ViewportPoseV1,
      policy: "certified" | "manual",
    ): ViewportPoseV1 => {
      const pose =
        policy === "certified"
          ? freezeCertifiedPose(poseValue, width, height)
          : freezeRenderedPose(poseValue);
      const svg = svgRef.current;
      if (!svg) throw new Error("The SVG canvas is unavailable");
      svg.setAttribute("viewBox", viewBoxValue(pose));
      viewportPoseRef.current = pose;
      return pose;
    },
    [height, svgRef, width],
  );

  const renderViewportFrame = useCallback(
    (poseValue: ViewportPoseV1): void => {
      writePose(poseValue, "certified");
    },
    [writePose],
  );

  const writeManualViewportFrame = useCallback(
    (poseValue: ViewportPoseV1): void => {
      writePose(poseValue, "manual");
    },
    [writePose],
  );

  const readViewport = useCallback((): ViewportPoseV1 => {
    const svg = svgRef.current;
    const serialized = svg?.getAttribute("viewBox")?.trim();
    if (!serialized) return viewportPoseRef.current;
    const values = serialized.split(/[\s,]+/u).map(Number);
    if (values.length !== 4) {
      throw new Error("The SVG canvas has an invalid viewBox");
    }
    const [x, y, poseWidth, poseHeight] = values;
    const pose = freezeRenderedPose({
      x,
      y,
      width: poseWidth,
      height: poseHeight,
    });
    viewportPoseRef.current = pose;
    return pose;
  }, [svgRef]);

  const syncLegacyState = useCallback(
    (pose: ViewportPoseV1) => {
      panRef.current = { x: pose.x, y: pose.y };
      if (mountedRef.current) setZoomLevel(width / pose.width);
    },
    [width],
  );

  const cancelViewportAnimation =
    useCallback((): ViewportPlaybackOutcome | null => {
      const active = activePlaybackRef.current;
      return active ? active.cancel() : null;
    }, []);

  const materializeViewport = useCallback(
    (poseValue: ViewportPoseV1): void => {
      const pose = freezeCertifiedPose(poseValue, width, height);
      cancelViewportAnimation();
      renderViewportFrame(pose);
      syncLegacyState(pose);
    },
    [
      cancelViewportAnimation,
      height,
      syncLegacyState,
      width,
      renderViewportFrame,
    ],
  );

  const startViewportAnimation = useCallback(
    (
      target: ViewportPoseV1,
      options: ViewportPlaybackOptions,
      policy: "certified" | "manual",
    ): ViewportPlayback => {
      validatePlaybackOptions(options);
      const start =
        policy === "certified"
          ? freezeCertifiedPose(readViewport(), width, height)
          : freezeRenderedPose(readViewport());
      cancelViewportAnimation();
      const writeFrame =
        policy === "certified" ? renderViewportFrame : writeManualViewportFrame;
      let settled: ViewportPlaybackOutcome | null = null;
      let resolveFinished!: (outcome: ViewportPlaybackOutcome) => void;
      const finished = new Promise<ViewportPlaybackOutcome>((resolve) => {
        resolveFinished = resolve;
      });
      let tween: gsap.core.Tween | null = null;
      let playback!: ManagedViewportPlayback;

      const settle = (
        status: ViewportPlaybackOutcome["status"],
        pose: ViewportPoseV1,
        error?: unknown,
      ): ViewportPlaybackOutcome => {
        if (settled) return settled;
        settled = Object.freeze({
          status,
          pose,
          ...(error === undefined ? {} : { error: viewportError(error) }),
        });
        if (activePlaybackRef.current === playback) {
          activePlaybackRef.current = null;
        }
        syncLegacyState(pose);
        resolveFinished(settled);
        return settled;
      };

      const fail = (error: unknown): ViewportPlaybackOutcome => {
        if (settled) return settled;
        tween?.kill();
        let pose = viewportPoseRef.current;
        let failure = error;
        try {
          writeFrame(start);
          pose = start;
        } catch (rollbackError) {
          failure = new Error(
            `${viewportError(error)}; rollback failed: ${viewportError(rollbackError)}`,
          );
        }
        return settle("failed", pose, failure);
      };

      playback = Object.freeze({
        finished,
        cancel: () => {
          if (settled) return settled;
          try {
            tween?.kill();
            const current = readViewport();
            return settle("cancelled", current);
          } catch (error) {
            return fail(error);
          }
        },
      });
      activePlaybackRef.current = playback;

      if (options.durationMs === 0) {
        try {
          writeFrame(target);
          settle("completed", target);
        } catch (error) {
          fail(error);
        }
        return playback;
      }

      const animated = {
        x: start.x,
        y: start.y,
        width: start.width,
        height: start.height,
      };
      try {
        tween = gsap.to(animated, {
          x: target.x,
          y: target.y,
          width: target.width,
          height: target.height,
          duration: options.durationMs / 1_000,
          ease: VIEWPORT_EASING[options.easing],
          onUpdate: () => {
            try {
              writeFrame(freezeRenderedPose(animated));
            } catch (error) {
              fail(error);
            }
          },
          onComplete: () => {
            try {
              writeFrame(target);
              settle("completed", target);
            } catch (error) {
              fail(error);
            }
          },
        });
      } catch (error) {
        fail(error);
      }
      return playback;
    },
    [
      cancelViewportAnimation,
      height,
      readViewport,
      syncLegacyState,
      width,
      writeManualViewportFrame,
      renderViewportFrame,
    ],
  );

  const animateViewport = useCallback(
    (
      poseValue: ViewportPoseV1,
      options: ViewportPlaybackOptions,
    ): ViewportPlayback => {
      const target = freezeCertifiedPose(poseValue, width, height);
      return startViewportAnimation(target, options, "certified");
    },
    [height, startViewportAnimation, width],
  );

  const animateManualViewport = useCallback(
    (
      poseValue: ViewportPoseV1,
      options: ViewportPlaybackOptions,
    ): ViewportPlayback => {
      const target = freezeRenderedPose(poseValue);
      return startViewportAnimation(target, options, "manual");
    },
    [startViewportAnimation],
  );

  const applyViewBox = useCallback(
    (zoom: number, pan: { x: number; y: number }, animate = false) => {
      if (!Number.isFinite(zoom) || zoom <= 0) {
        throw new RangeError(
          "Manual viewport zoom must be a positive finite number",
        );
      }
      const pose = freezeRenderedPose({
        x: pan.x,
        y: pan.y,
        width: width / zoom,
        height: height / zoom,
      });
      if (animate) {
        animateManualViewport(pose, {
          durationMs: DURATION.fast * 1_000,
          easing: "ease_in_out",
        });
        return;
      }
      cancelViewportAnimation();
      writeManualViewportFrame(pose);
      syncLegacyState(pose);
    },
    [
      animateManualViewport,
      cancelViewportAnimation,
      height,
      syncLegacyState,
      width,
      writeManualViewportFrame,
    ],
  );

  const panTo = useCallback(
    (x: number, y: number, zoom?: number) => {
      if (interactionLocked) return;
      const nextZoom = zoom ?? zoomLevel;
      const nextPan = {
        x: x - width / nextZoom / 2,
        y: y - height / nextZoom / 2,
      };
      panRef.current = nextPan;
      applyViewBox(nextZoom, nextPan, true);
    },
    [applyViewBox, height, interactionLocked, width, zoomLevel],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      if (interactionLocked || event.button !== 0) return;
      cancelViewportAnimation();
      isPanningRef.current = true;
      setIsPanning(true);
      panStartRef.current = {
        x: event.clientX,
        y: event.clientY,
        panX: panRef.current.x,
        panY: panRef.current.y,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [cancelViewportAnimation, interactionLocked],
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      const svg = svgRef.current;
      if (interactionLocked || !isPanningRef.current || !svg) return;
      const bounds = svg.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const current = readViewport();
      const deltaX =
        (event.clientX - panStartRef.current.x) *
        (current.width / bounds.width);
      const deltaY =
        (event.clientY - panStartRef.current.y) *
        (current.height / bounds.height);
      const nextPan = {
        x: panStartRef.current.panX - deltaX,
        y: panStartRef.current.panY - deltaY,
      };
      panRef.current = nextPan;
      writeManualViewportFrame({ ...current, x: nextPan.x, y: nextPan.y });
    },
    [interactionLocked, readViewport, svgRef, writeManualViewportFrame],
  );

  const handlePointerUp = useCallback(() => {
    isPanningRef.current = false;
    setIsPanning(false);
  }, []);

  const zoomIn = useCallback(() => {
    if (interactionLocked) return;
    setZoomLevel((currentZoom) => {
      const nextZoom = Math.min(currentZoom + 0.25, 3);
      const centerX = panRef.current.x + width / currentZoom / 2;
      const centerY = panRef.current.y + height / currentZoom / 2;
      const nextPan = {
        x: centerX - width / nextZoom / 2,
        y: centerY - height / nextZoom / 2,
      };
      panRef.current = nextPan;
      applyViewBox(nextZoom, nextPan, true);
      return nextZoom;
    });
  }, [applyViewBox, height, interactionLocked, width]);

  const zoomOut = useCallback(() => {
    if (interactionLocked) return;
    setZoomLevel((currentZoom) => {
      const nextZoom = Math.max(currentZoom - 0.25, 0.5);
      const poseWidth = width / nextZoom;
      const poseHeight = height / nextZoom;
      const centerX = panRef.current.x + width / currentZoom / 2;
      const centerY = panRef.current.y + height / currentZoom / 2;
      const nextPan = {
        x: centerX - poseWidth / 2,
        y: centerY - poseHeight / 2,
      };
      panRef.current = nextPan;
      applyViewBox(nextZoom, nextPan, true);
      return nextZoom;
    });
  }, [applyViewBox, height, interactionLocked, width]);

  const resetViewport = useCallback(
    (pose = defaultPose) => {
      materializeViewport(pose);
    },
    [defaultPose, materializeViewport],
  );

  const resetZoom = useCallback(() => {
    if (interactionLocked) return;
    setZoomLevel(1);
    panRef.current = { x: 0, y: 0 };
    applyViewBox(1, panRef.current, true);
  }, [applyViewBox, interactionLocked]);

  useEffect(() => {
    viewportPoseRef.current = defaultPose;
  }, [defaultPose]);

  useEffect(() => {
    if (!interactionLocked) return;
    isPanningRef.current = false;
    const frame = window.requestAnimationFrame(() => setIsPanning(false));
    return () => window.cancelAnimationFrame(frame);
  }, [interactionLocked]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activePlaybackRef.current?.cancel();
      activePlaybackRef.current = null;
    };
  }, []);

  return {
    animateViewport,
    applyViewBox,
    cancelViewportAnimation,
    handlePointerDown,
    handlePointerMove,
    handlePointerUp,
    isPanning,
    materializeViewport,
    panRef,
    panTo,
    readViewport,
    resetViewport,
    resetZoom,
    renderViewportFrame,
    zoomIn,
    zoomLevel,
    zoomOut,
  };
}
