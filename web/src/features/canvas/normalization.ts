import { snap, snapPoints } from "@/lib/canvas-utils";

import type { CanvasOperation, TeachingStep } from "./types";

type ShapeBounds = { x: number; y: number; width: number; height: number };

const SHAPE_ACTIONS = new Set(["rect", "circle", "ellipse"]);

/** Snap a render operation to the canvas grid and enforce usable shape dimensions. */
export function normalizeOperation(operation: CanvasOperation): CanvasOperation {
  const normalized = { ...operation };
  if (normalized.x != null) normalized.x = snap(normalized.x);
  if (normalized.y != null) normalized.y = snap(normalized.y);
  if (normalized.width != null) normalized.width = Math.max(snap(normalized.width), 40);
  if (normalized.height != null) normalized.height = Math.max(snap(normalized.height), 40);
  if (normalized.points) normalized.points = snapPoints(normalized.points);
  return normalized;
}

/** Normalize a teaching sequence while preserving its declared step order. */
export function normalizeTeachingSteps(steps: TeachingStep[]): TeachingStep[] {
  let lastShape: ShapeBounds | null = null;

  return steps.map((step) => {
    const normalized = { ...step };

    if (normalized.x != null) normalized.x = snap(normalized.x);
    if (normalized.y != null) normalized.y = snap(normalized.y);
    if (normalized.width != null) {
      normalized.width = Math.max(snap(normalized.width), 40);
    }
    if (normalized.height != null) {
      normalized.height = Math.max(snap(normalized.height), 40);
    }
    if (normalized.points) normalized.points = snapPoints(normalized.points);

    if (SHAPE_ACTIONS.has(normalized.action) && normalized.x != null && normalized.y != null) {
      const width = normalized.width ?? (normalized.action === "circle" ? 60 : 100);
      const height = normalized.height ?? (normalized.action === "circle" ? width : 60);
      lastShape = { x: normalized.x, y: normalized.y, width, height };
    }

    if (normalized.action === "text" && lastShape && normalized.text) {
      const isShortLabel = normalized.text.length <= 20;
      const textX = normalized.x ?? 0;
      const textY = normalized.y ?? 0;
      const nearShape =
        Math.abs(textX - lastShape.x) < lastShape.width + 40 &&
        Math.abs(textY - lastShape.y) < lastShape.height + 40;

      if (isShortLabel && nearShape) {
        const fontSize = normalized.font_size ?? 16;
        const estimatedWidth = normalized.text.length * fontSize * 0.55;
        normalized.x = snap(lastShape.x + lastShape.width / 2 - estimatedWidth / 2);
        normalized.y = snap(lastShape.y + lastShape.height / 2 + fontSize / 3);
        normalized.font_size = Math.min(fontSize, lastShape.height * 0.4);
        normalized._centered = true;
      }

      if (isShortLabel && nearShape && textY > lastShape.y + lastShape.height - 10) {
        normalized.x = snap(
          lastShape.x +
            lastShape.width / 2 -
            (normalized.text.length * (normalized.font_size ?? 14) * 0.55) / 2
        );
        normalized.y = snap(lastShape.y + lastShape.height + 20);
      }
    }

    if (
      normalized.action !== "text" &&
      normalized.action !== "latex" &&
      !SHAPE_ACTIONS.has(normalized.action)
    ) {
      lastShape = null;
    }

    return normalized;
  });
}
