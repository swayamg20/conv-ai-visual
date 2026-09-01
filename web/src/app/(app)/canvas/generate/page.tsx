import type { Metadata } from "next";

import { LiveModelScene } from "@/features/live-scene/live-model-scene";

export const metadata: Metadata = {
  title: "Live visual explanation · Murmur",
  description: "Generate an interruptible visual explanation as model patches arrive.",
};

export default function GenerateCanvasPage() {
  return <LiveModelScene />;
}
