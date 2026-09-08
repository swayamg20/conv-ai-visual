import type { ChoreographyLayout } from "@/lib/live-scene";

export interface ChoreographyCaptureOptions {
  readonly layout: ChoreographyLayout;
  readonly reducedMotion: boolean;
  readonly pace: ChoreographyCapturePace;
}

export const CHOREOGRAPHY_CAPTURE_PACES = ["auto", "step"] as const;
export type ChoreographyCapturePace =
  (typeof CHOREOGRAPHY_CAPTURE_PACES)[number];

type CaptureSearchParams = Readonly<
  Record<string, string | readonly string[] | undefined>
>;

function singleValue(
  value: string | readonly string[] | undefined,
  field: string,
): string | undefined {
  if (value !== undefined && typeof value !== "string") {
    throw new TypeError(`${field} must appear at most once`);
  }
  return value;
}

/** Decode only the closed capture controls; arbitrary lesson input is forbidden. */
export function parseChoreographyCaptureOptions(
  searchParams: CaptureSearchParams,
): ChoreographyCaptureOptions {
  const unknown = Object.keys(searchParams).filter(
    (key) => key !== "layout" && key !== "motion" && key !== "pace",
  );
  if (unknown.length > 0) {
    throw new TypeError(`unsupported capture option: ${unknown[0]}`);
  }

  const layout = singleValue(searchParams.layout, "layout") ?? "cinematic";
  if (layout !== "cinematic" && layout !== "compact") {
    throw new TypeError("layout must be cinematic or compact");
  }

  const motion = singleValue(searchParams.motion, "motion") ?? "real";
  if (motion !== "real" && motion !== "reduced") {
    throw new TypeError("motion must be real or reduced");
  }

  const pace = singleValue(searchParams.pace, "pace") ?? "auto";
  if (!CHOREOGRAPHY_CAPTURE_PACES.some((candidate) => candidate === pace)) {
    throw new TypeError("pace must be auto or step");
  }

  return Object.freeze({
    layout,
    reducedMotion: motion === "reduced",
    pace: pace as ChoreographyCapturePace,
  });
}
