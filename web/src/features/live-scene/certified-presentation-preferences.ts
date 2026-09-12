"use client";

import { useEffect, useState } from "react";

import type { ChoreographyLayout } from "@/lib/live-scene";

export const CERTIFIED_COMPACT_LAYOUT_QUERY = "(max-width: 699px)";
export const CERTIFIED_REDUCED_MOTION_QUERY =
  "(prefers-reduced-motion: reduce)";

export interface CertifiedPresentationPreferenceOverrides {
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
}

export interface CertifiedPresentationPreferences {
  readonly layout: ChoreographyLayout;
  readonly reducedMotion: boolean;
}

function mediaMatches(query: string): boolean {
  return (
    typeof globalThis.matchMedia === "function" &&
    globalThis.matchMedia(query).matches
  );
}

function resolvePreferences(
  overrides: CertifiedPresentationPreferenceOverrides,
): CertifiedPresentationPreferences {
  return Object.freeze({
    layout:
      overrides.layout ??
      (mediaMatches(CERTIFIED_COMPACT_LAYOUT_QUERY) ? "compact" : "cinematic"),
    reducedMotion:
      overrides.reducedMotion ?? mediaMatches(CERTIFIED_REDUCED_MOTION_QUERY),
  });
}

/**
 * Resolve the browser presentation contract once per mount.
 *
 * Certified viewports are layout-bound, so later resize or media-query changes
 * must not mutate an accepted session. Fully explicit overrides resolve during
 * the first render for deterministic E2E surfaces; browser-derived preferences
 * resolve after mount to keep server and hydration output aligned.
 */
export function useCertifiedPresentationPreferences(
  overrides: CertifiedPresentationPreferenceOverrides = {},
): CertifiedPresentationPreferences | null {
  const [initialOverrides] = useState(() => Object.freeze({ ...overrides }));
  const [preferences, setPreferences] =
    useState<CertifiedPresentationPreferences | null>(() =>
      initialOverrides.layout !== undefined &&
      initialOverrides.reducedMotion !== undefined
        ? resolvePreferences(initialOverrides)
        : null,
    );

  useEffect(() => {
    if (preferences) return;
    let active = true;
    queueMicrotask(() => {
      if (active) setPreferences(resolvePreferences(initialOverrides));
    });
    return () => {
      active = false;
    };
  }, [initialOverrides, preferences]);

  return preferences;
}
