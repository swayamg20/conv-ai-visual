import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SemanticStoryboardLabClient } from "./client";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Live semantic storyboard lab · Murmur",
  description:
    "Development-only qualification of Murmur's interruptible semantic storyboard.",
  robots: { index: false, follow: false },
};

export default function SemanticStoryboardLabPage() {
  if (
    process.env.NODE_ENV !== "development" ||
    process.env.MURMUR_SCENE_LAB !== "1"
  ) {
    notFound();
  }

  return (
    <SemanticStoryboardLabClient
      playbackRate={process.env.MURMUR_E2E_MODE === "1" ? 16 : 1}
    />
  );
}
