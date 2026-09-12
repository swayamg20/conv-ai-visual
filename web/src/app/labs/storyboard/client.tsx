"use client";

import {
  LiveSemanticStoryboard,
  type LiveSemanticStoryboardProps,
} from "@/features/live-scene/live-semantic-storyboard";
import { createSemanticStoryboardFixtureRunner } from "@/features/live-scene/semantic-storyboard-scene-stream-fixture";

const runProviderFreeStoryboard = createSemanticStoryboardFixtureRunner();

interface SemanticStoryboardLabClientProps {
  readonly playbackRate?: LiveSemanticStoryboardProps["playbackRate"];
}

/** Keep generated fixture JSON and its catalog outside the product route bundle. */
export function SemanticStoryboardLabClient({
  playbackRate = 1,
}: SemanticStoryboardLabClientProps) {
  return (
    <LiveSemanticStoryboard
      backHref="/"
      playbackRate={playbackRate}
      runStream={runProviderFreeStoryboard}
    />
  );
}
