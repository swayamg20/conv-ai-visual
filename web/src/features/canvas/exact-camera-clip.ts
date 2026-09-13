import type { ViewportPoseV1 } from "@/lib/live-scene/choreography";

export interface RenderedSvgViewport {
  readonly borderBoxWidth: number;
  readonly borderBoxHeight: number;
  readonly clientLeft: number;
  readonly clientTop: number;
  readonly clientWidth: number;
  readonly clientHeight: number;
}

export interface ExactCameraClipInsets {
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
  readonly left: number;
}

const CLIP_PRECISION = 6;

function positiveFinite(value: number, label: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new RangeError(`${label} must be a positive finite number`);
  }
  return value;
}

function nonnegativeFinite(value: number, label: string): number {
  if (!Number.isFinite(value) || value < 0) {
    throw new RangeError(`${label} must be a nonnegative finite number`);
  }
  return value;
}

function normalizedInset(value: number): number {
  const rounded = Number(value.toFixed(CLIP_PRECISION));
  return Object.is(rounded, -0) ? 0 : rounded;
}

/**
 * Compute the letterbox/pillarbox matte around an xMidYMid meet projection.
 * The camera origin does not affect the matte; only its declared aspect ratio does.
 */
export function computeExactCameraClipInsets(
  rendered: RenderedSvgViewport,
  pose: Pick<ViewportPoseV1, "width" | "height">,
): ExactCameraClipInsets {
  const borderBoxWidth = positiveFinite(
    rendered.borderBoxWidth,
    "Rendered SVG border-box width",
  );
  const borderBoxHeight = positiveFinite(
    rendered.borderBoxHeight,
    "Rendered SVG border-box height",
  );
  const clientLeft = nonnegativeFinite(rendered.clientLeft, "SVG clientLeft");
  const clientTop = nonnegativeFinite(rendered.clientTop, "SVG clientTop");
  const clientWidth = positiveFinite(rendered.clientWidth, "SVG clientWidth");
  const clientHeight = positiveFinite(
    rendered.clientHeight,
    "SVG clientHeight",
  );
  const borderRight = borderBoxWidth - clientLeft - clientWidth;
  const borderBottom = borderBoxHeight - clientTop - clientHeight;
  nonnegativeFinite(borderRight, "SVG right border");
  nonnegativeFinite(borderBottom, "SVG bottom border");
  const cameraWidth = positiveFinite(pose.width, "Camera width");
  const cameraHeight = positiveFinite(pose.height, "Camera height");
  const scale = Math.min(
    clientWidth / cameraWidth,
    clientHeight / cameraHeight,
  );
  const horizontal = normalizedInset(
    Math.max(0, (clientWidth - cameraWidth * scale) / 2),
  );
  const vertical = normalizedInset(
    Math.max(0, (clientHeight - cameraHeight * scale) / 2),
  );
  return Object.freeze({
    top: normalizedInset(clientTop + vertical),
    right: normalizedInset(borderRight + horizontal),
    bottom: normalizedInset(borderBottom + vertical),
    left: normalizedInset(clientLeft + horizontal),
  });
}

function cssPixels(value: number): string {
  return `${normalizedInset(value)}px`;
}

/** Serialize all four edges so the browser cannot collapse away evidence. */
export function exactCameraClipPath(insets: ExactCameraClipInsets): string {
  return `inset(${cssPixels(insets.top)} ${cssPixels(insets.right)} ${cssPixels(
    insets.bottom,
  )} ${cssPixels(insets.left)})`;
}
