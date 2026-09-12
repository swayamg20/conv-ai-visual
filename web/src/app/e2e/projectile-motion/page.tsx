import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProjectileMotionE2EClient } from "./client";
import {
  isProjectileMotionE2EEnabled,
  parseProjectileMotionE2EOptions,
} from "./options";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Projectile motion proof · Murmur",
  robots: { index: false, follow: false },
};

interface ProjectileMotionE2EPageProps {
  readonly searchParams: Promise<
    Record<string, string | readonly string[] | undefined>
  >;
}

export default async function ProjectileMotionE2EPage({
  searchParams,
}: ProjectileMotionE2EPageProps) {
  if (!isProjectileMotionE2EEnabled(process.env)) notFound();

  let options: ReturnType<typeof parseProjectileMotionE2EOptions>;
  try {
    options = parseProjectileMotionE2EOptions(await searchParams);
  } catch {
    notFound();
  }
  return (
    <div className="dark">
      <ProjectileMotionE2EClient {...options} />
    </div>
  );
}
