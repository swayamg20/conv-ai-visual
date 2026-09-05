import type { Metadata } from "next";

import { LiveModelScene } from "@/features/live-scene/live-model-scene";

export const metadata: Metadata = {
  title: "Verified visual lesson · Murmur",
  description: "Direct an interruptible visual lesson compiled and verified by Murmur.",
};

export default function GenerateCanvasPage() {
  return <LiveModelScene />;
}
