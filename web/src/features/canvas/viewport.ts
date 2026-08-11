import { gsap } from "gsap";
import { useCallback, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import { DURATION, EASING } from "@/lib/gsap-setup";

interface CanvasViewportOptions {
  svgRef: RefObject<SVGSVGElement | null>;
  width: number;
  height: number;
}

export function useCanvasViewport({ svgRef, width, height }: CanvasViewportOptions) {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef({ x: 0, y: 0 });
  const isPanningRef = useRef(false);
  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

  const applyViewBox = useCallback(
    (zoom: number, pan: { x: number; y: number }, animate = false) => {
      const svg = svgRef.current;
      if (!svg) return;
      const viewBox = `${pan.x} ${pan.y} ${width / zoom} ${height / zoom}`;
      if (animate) {
        gsap.to(svg, {
          attr: { viewBox },
          duration: DURATION.fast,
          ease: EASING.smooth,
        });
        return;
      }
      svg.setAttribute("viewBox", viewBox);
    },
    [height, svgRef, width]
  );

  const panTo = useCallback(
    (x: number, y: number, zoom?: number) => {
      const nextZoom = zoom ?? zoomLevel;
      panRef.current = {
        x: x - width / nextZoom / 2,
        y: y - height / nextZoom / 2,
      };
      applyViewBox(nextZoom, panRef.current, true);
    },
    [applyViewBox, height, width, zoomLevel]
  );

  const handlePointerDown = useCallback((event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    isPanningRef.current = true;
    setIsPanning(true);
    panStartRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      const svg = svgRef.current;
      if (!isPanningRef.current || !svg) return;
      const bounds = svg.getBoundingClientRect();
      const deltaX =
        (event.clientX - panStartRef.current.x) * (width / zoomLevel / bounds.width);
      const deltaY =
        (event.clientY - panStartRef.current.y) * (height / zoomLevel / bounds.height);
      panRef.current = {
        x: panStartRef.current.panX - deltaX,
        y: panStartRef.current.panY - deltaY,
      };
      applyViewBox(zoomLevel, panRef.current);
    },
    [applyViewBox, height, svgRef, width, zoomLevel]
  );

  const handlePointerUp = useCallback(() => {
    isPanningRef.current = false;
    setIsPanning(false);
  }, []);

  const zoomIn = useCallback(() => {
    setZoomLevel((currentZoom) => {
      const nextZoom = Math.min(currentZoom + 0.25, 3);
      const centerX = panRef.current.x + width / currentZoom / 2;
      const centerY = panRef.current.y + height / currentZoom / 2;
      panRef.current = {
        x: centerX - width / nextZoom / 2,
        y: centerY - height / nextZoom / 2,
      };
      applyViewBox(nextZoom, panRef.current, true);
      return nextZoom;
    });
  }, [applyViewBox, height, width]);

  const zoomOut = useCallback(() => {
    setZoomLevel((currentZoom) => {
      const nextZoom = Math.max(currentZoom - 0.25, 0.5);
      const centerX = panRef.current.x + width / currentZoom / 2;
      const centerY = panRef.current.y + height / currentZoom / 2;
      panRef.current = {
        x: centerX - width / nextZoom / 2,
        y: centerY - height / nextZoom / 2,
      };
      applyViewBox(nextZoom, panRef.current, true);
      return nextZoom;
    });
  }, [applyViewBox, height, width]);

  const resetZoom = useCallback(() => {
    setZoomLevel(1);
    panRef.current = { x: 0, y: 0 };
    applyViewBox(1, panRef.current, true);
  }, [applyViewBox]);

  return {
    applyViewBox,
    handlePointerDown,
    handlePointerMove,
    handlePointerUp,
    isPanning,
    panRef,
    panTo,
    resetZoom,
    zoomIn,
    zoomLevel,
    zoomOut,
  };
}
