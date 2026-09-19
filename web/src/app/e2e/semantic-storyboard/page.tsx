import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SemanticStoryboardE2EClient } from "./client";
import {
  isSemanticStoryboardE2EEnabled,
  parseSemanticStoryboardE2EOptions,
} from "./options";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Semantic storyboard proof · Murmur",
  robots: { index: false, follow: false },
};

interface SemanticStoryboardE2EPageProps {
  readonly searchParams: Promise<
    Record<string, string | readonly string[] | undefined>
  >;
}

export default async function SemanticStoryboardE2EPage({
  searchParams,
}: SemanticStoryboardE2EPageProps) {
  if (!isSemanticStoryboardE2EEnabled(process.env)) notFound();

  let options: ReturnType<typeof parseSemanticStoryboardE2EOptions>;
  try {
    options = parseSemanticStoryboardE2EOptions(await searchParams);
  } catch {
    notFound();
  }

  return (
    <div className="dark">
      <SemanticStoryboardE2EClient {...options} />
    </div>
  );
}
