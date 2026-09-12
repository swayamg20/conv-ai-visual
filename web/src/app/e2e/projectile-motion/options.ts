import type { ChoreographyPlaybackRate } from "@/features/canvas/types";
import type { ChoreographyLayout } from "@/lib/live-scene";

export interface ProjectileMotionE2EOptions {
  readonly layout: ChoreographyLayout;
  readonly reducedMotion: boolean;
  readonly flow: "main" | "adaptive";
  readonly playbackRate: ChoreographyPlaybackRate;
  readonly keyframeProof: boolean;
}

type SearchParams = Readonly<
  Record<string, string | readonly string[] | undefined>
>;

interface ProjectileMotionE2EEnvironment {
  readonly MURMUR_E2E_MODE?: string;
  readonly NODE_ENV?: string;
}

/** Keep the provider-free proof route inaccessible in every production build. */
export function isProjectileMotionE2EEnabled(
  environment: ProjectileMotionE2EEnvironment,
): boolean {
  return (
    environment.MURMUR_E2E_MODE === "1" &&
    (environment.NODE_ENV === "development" || environment.NODE_ENV === "test")
  );
}

function single(
  value: string | readonly string[] | undefined,
  field: string,
): string | undefined {
  if (value !== undefined && typeof value !== "string") {
    throw new TypeError(`${field} must appear at most once`);
  }
  return value;
}

/** Decode only the closed controls exercised by the Gate 1.7 browser proof. */
export function parseProjectileMotionE2EOptions(
  searchParams: SearchParams,
): ProjectileMotionE2EOptions {
  const unknown = Object.keys(searchParams).filter(
    (key) =>
      key !== "layout" &&
      key !== "motion" &&
      key !== "flow" &&
      key !== "proof" &&
      key !== "speed",
  );
  if (unknown.length > 0) {
    throw new TypeError(
      `unsupported projectile-motion e2e option: ${unknown[0]}`,
    );
  }

  const layout = single(searchParams.layout, "layout") ?? "cinematic";
  if (layout !== "cinematic" && layout !== "compact") {
    throw new TypeError("layout must be cinematic or compact");
  }
  const motion = single(searchParams.motion, "motion") ?? "real";
  if (motion !== "real" && motion !== "reduced") {
    throw new TypeError("motion must be real or reduced");
  }
  const flow = single(searchParams.flow, "flow") ?? "main";
  if (flow !== "main" && flow !== "adaptive") {
    throw new TypeError("flow must be main or adaptive");
  }
  const proof = single(searchParams.proof, "proof") ?? "none";
  if (proof !== "none" && proof !== "keyframes") {
    throw new TypeError("proof must be none or keyframes");
  }
  const speed = single(searchParams.speed, "speed") ?? "accelerated";
  if (speed !== "accelerated" && speed !== "normal") {
    throw new TypeError("speed must be accelerated or normal");
  }

  return Object.freeze({
    layout,
    reducedMotion: motion === "reduced",
    flow,
    playbackRate: speed === "normal" ? 1 : 16,
    keyframeProof: proof === "keyframes",
  });
}
