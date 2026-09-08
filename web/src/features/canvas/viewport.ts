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

function freezePose(
  pose: Omit<ViewportPoseV1, "v"> & { readonly v?: number },
  width: number,
  height: number,
): ViewportPoseV1 {
  if (pose.v !== undefined && pose.v !== 1) {
    throw new RangeError("Viewport pose version must equal 1");
  }
  const values = [pose.x, pose.y, pose.width, pose.height];
  if (
    values.some(
      (value) => typeof value !== "number" || !Number.isFinite(value),
    )
  ) {
    throw new TypeError("Viewport coordinates and extents must be finite numbers");
  }
  if (pose.x < 0 || pose.y < 0 || pose.width <= 0 || pose.height <= 0) {
    throw new RangeError(
      "Viewport pose must have a nonnegative origin and positive extent",
    );
  }
  if (pose.x + pose.width > width || pose.y + pose.height > height) {
    throw new RangeError("Viewport pose must remain inside the logical canvas");
  }
  return Object.freeze({
    v: 1,
    x: pose.x,
    y: pose.y,
    width: pose.width,
    height: pose.height,
  });
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
    () => freezePose({ x: 0, y: 0, width, height }, width, height),
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
    (poseValue: ViewportPoseV1): ViewportPoseV1 => {
      const pose = freezePose(poseValue, width, height);
      const svg = svgRef.current;
      if (!svg) throw new Error("The SVG canvas is unavailable");
      svg.setAttribute("viewBox", viewBoxValue(pose));
      viewportPoseRef.current = pose;
      return pose;
    },
    [height, svgRef, width],
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
    const pose = freezePose(
      { x, y, width: poseWidth, height: poseHeight },
      width,
      height,
    );
    viewportPoseRef.current = pose;
    return pose;
  }, [height, svgRef, width]);

  const syncLegacyState = useCallback(
    (pose: ViewportPoseV1) => {
      panRef.current = { x: pose.x, y: pose.y };
      if (mountedRef.current) setZoomLevel(width / pose.width);
    },
    [width],
  );

  const cancelViewportAnimation = useCallback(
    (): ViewportPlaybackOutcome | null => {
      const active = activePlaybackRef.current;
      return active ? active.cancel() : null;
    },
    [],
  );

  const materializeViewport = useCallback(
    (poseValue: ViewportPoseV1): void => {
      cancelViewportAnimation();
      const pose = writePose(freezePose(poseValue, width, height));
      syncLegacyState(pose);
    },
    [cancelViewportAnimation, height, syncLegacyState, width, writePose],
  );

  const animateViewport = useCallback(
    (
      poseValue: ViewportPoseV1,
      options: ViewportPlaybackOptions,
    ): ViewportPlayback => {
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
        throw new RangeError("Viewport easing is not in the closed choreography vocabulary");
      }

      const target = freezePose(poseValue, width, height);
      cancelViewportAnimation();
      const start = readViewport();
      let settled: ViewportPlaybackOutcome | null = null;
      let resolveFinished!: (outcome: ViewportPlaybackOutcome) => void;
      const finished = new Promise<ViewportPlaybackOutcome>((resolve) => {
        resolveFinished = resolve;
      });
      let tween: gsap.core.Tween | null = null;

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
        if (activePlaybackRef.current === playback) activePlaybackRef.current = null;
        syncLegacyState(pose);
        resolveFinished(settled);
        return settled;
      };

      const playback: ManagedViewportPlayback = Object.freeze({
        finished,
        cancel: () => {
          if (settled) return settled;
          tween?.kill();
          const current = readViewport();
          return settle("cancelled", current);
        },
      });
      activePlaybackRef.current = playback;

      if (options.durationMs === 0) {
        try {
          const pose = writePose(target);
          settle("completed", pose);
        } catch (error) {
          settle("failed", viewportPoseRef.current, error);
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
            writePose(freezePose(animated, width, height));
          },
          onComplete: () => {
            const pose = writePose(target);
            settle("completed", pose);
          },
        });
      } catch (error) {
        try {
          writePose(start);
        } catch {
          // Keep the original animation failure as the public error.
        }
        settle("failed", viewportPoseRef.current, error);
      }
      return playback;
    },
    [
      cancelViewportAnimation,
      height,
      readViewport,
      syncLegacyState,
      width,
      writePose,
    ],
  );

  const applyViewBox = useCallback(
    (zoom: number, pan: { x: number; y: number }, animate = false) => {
      const pose = freezePose(
        {
          x: pan.x,
          y: pan.y,
          width: width / zoom,
          height: height / zoom,
        },
        width,
        height,
      );
      if (animate) {
        animateViewport(pose, {
          durationMs: DURATION.fast * 1_000,
          easing: "ease_in_out",
        });
        return;
      }
      materializeViewport(pose);
    },
    [animateViewport, height, materializeViewport, width],
  );

  const panTo = useCallback(
    (x: number, y: number, zoom?: number) => {
      if (interactionLocked) return;
      const nextZoom = zoom ?? zoomLevel;
      const poseWidth = width / nextZoom;
      const poseHeight = height / nextZoom;
      const nextPan = {
        x: Math.min(Math.max(0, x - poseWidth / 2), width - poseWidth),
        y: Math.min(Math.max(0, y - poseHeight / 2), height - poseHeight),
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
        (event.clientX - panStartRef.current.x) * (current.width / bounds.width);
      const deltaY =
        (event.clientY - panStartRef.current.y) * (current.height / bounds.height);
      const nextPan = {
        x: Math.min(
          Math.max(0, panStartRef.current.panX - deltaX),
          width - current.width,
        ),
        y: Math.min(
          Math.max(0, panStartRef.current.panY - deltaY),
          height - current.height,
        ),
      };
      panRef.current = nextPan;
      writePose({ ...current, x: nextPan.x, y: nextPan.y });
    },
    [height, interactionLocked, readViewport, svgRef, width, writePose],
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
        x: Math.min(Math.max(0, centerX - width / nextZoom / 2), width - width / nextZoom),
        y: Math.min(
          Math.max(0, centerY - height / nextZoom / 2),
          height - height / nextZoom,
        ),
      };
      panRef.current = nextPan;
      applyViewBox(nextZoom, nextPan, true);
      return nextZoom;
    });
  }, [applyViewBox, height, interactionLocked, width]);

  const zoomOut = useCallback(() => {
    if (interactionLocked) return;
    setZoomLevel((currentZoom) => {
      const nextZoom = Math.max(currentZoom - 0.25, 1);
      const poseWidth = width / nextZoom;
      const poseHeight = height / nextZoom;
      if (poseWidth > width || poseHeight > height) return currentZoom;
      const centerX = panRef.current.x + width / currentZoom / 2;
      const centerY = panRef.current.y + height / currentZoom / 2;
      const nextPan = {
        x: Math.min(Math.max(0, centerX - poseWidth / 2), width - poseWidth),
        y: Math.min(Math.max(0, centerY - poseHeight / 2), height - poseHeight),
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
    zoomIn,
    zoomLevel,
    zoomOut,
  };
}
