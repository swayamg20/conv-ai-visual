import type { Metadata } from "next";

import { PythagorasDemo } from "@/features/live-scene/pythagoras-demo";

export const metadata: Metadata = {
  title: "Live Scene Lab · Murmur",
  description: "An interruptible, deterministic visual-teaching prototype.",
};

export default function LiveSceneDemoPage() {
  return <PythagorasDemo />;
}
