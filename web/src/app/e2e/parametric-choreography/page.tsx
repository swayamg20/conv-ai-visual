import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ParametricChoreographyE2EClient } from "./client";
import {
  isParametricChoreographyE2EEnabled,
  parseParametricChoreographyE2EOptions,
} from "./options";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Parametric choreography proof · Murmur",
  robots: { index: false, follow: false },
};

interface ParametricChoreographyE2EPageProps {
  readonly searchParams: Promise<
    Record<string, string | readonly string[] | undefined>
  >;
}

export default async function ParametricChoreographyE2EPage({
  searchParams,
}: ParametricChoreographyE2EPageProps) {
  if (!isParametricChoreographyE2EEnabled(process.env)) notFound();

  let options: ReturnType<typeof parseParametricChoreographyE2EOptions>;
  try {
    options = parseParametricChoreographyE2EOptions(await searchParams);
  } catch {
    notFound();
  }
  return (
    <div className="dark">
      <ParametricChoreographyE2EClient {...options} />
    </div>
  );
}
