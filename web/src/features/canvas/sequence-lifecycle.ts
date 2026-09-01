import type { gsap } from "gsap";

export type SDLSequenceEndReason = "completed" | "interrupted";

export interface SDLStepTimelineEntry {
  readonly tl: gsap.core.Timeline;
  started: boolean;
}

export type SDLStepTimelineMap = Map<string, SDLStepTimelineEntry>;

/** Stop every tracked sequence before replacing or unmounting the owning canvas. */
export function killStepTimelines(stepTimelines: SDLStepTimelineMap): void {
  stepTimelines.forEach(({ tl }) => tl.kill());
  stepTimelines.clear();
}

/**
 * Release one SDL sequence according to why it ended.
 *
 * Normal completion preserves the legacy fallback that plays a step whose audio
 * never started. An interrupted sequence instead kills both running and queued
 * timelines so no future visual command can be dispatched.
 */
export function endStepTimelineSequence(
  stepTimelines: SDLStepTimelineMap,
  sequenceId: string,
  reason: SDLSequenceEndReason
): void {
  const prefix = `${sequenceId}:`;
  for (const [key, entry] of stepTimelines) {
    if (!key.startsWith(prefix)) continue;

    if (reason === "interrupted") {
      entry.tl.kill();
    } else if (!entry.started) {
      entry.tl.play();
    }
    stepTimelines.delete(key);
  }
}
