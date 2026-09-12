import type { Metadata } from "next";

import { LiveSemanticStoryboard } from "@/features/live-scene/live-semantic-storyboard";

export const metadata: Metadata = {
  title: "Live semantic storyboard · Murmur",
  description:
    "Direct an interruptible visual explanation composed and verified live by Murmur.",
};

export default function SemanticStoryboardCanvasPage() {
  return <LiveSemanticStoryboard />;
}
