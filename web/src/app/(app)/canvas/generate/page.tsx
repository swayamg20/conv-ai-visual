import type { Metadata } from "next";

import { LiveParametricChoreography } from "@/features/live-scene/live-parametric-choreography";

export const metadata: Metadata = {
  title: "Verified visual lesson · Murmur",
  description: "Direct an interruptible visual lesson compiled and verified by Murmur.",
};

export default function GenerateCanvasPage() {
  return <LiveParametricChoreography />;
}
