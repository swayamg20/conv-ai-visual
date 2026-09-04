import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { LiveSceneLab } from "@/features/live-scene/live-scene-lab";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Verified visual acts lab · Murmur",
  description:
    "Development-only comparison of compiler-verified visual acts and raw scene authoring.",
};

export default function LiveSceneLabPage() {
  if (
    process.env.NODE_ENV !== "development" ||
    process.env.MURMUR_SCENE_LAB !== "1"
  ) {
    notFound();
  }

  return <LiveSceneLab />;
}
