import type { Metadata } from "next";

import { LiveProjectileChoreography } from "@/features/live-scene/live-projectile-choreography";

export const metadata: Metadata = {
  title: "Projectile motion studio · Murmur",
  description:
    "Explore an interruptible, verified projectile-motion lesson composed live by Murmur.",
};

export default function ProjectileCanvasPage() {
  return <LiveProjectileChoreography />;
}
