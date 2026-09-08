import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ChoreographyCaptureClient } from "./capture-client";
import { parseChoreographyCaptureOptions } from "./capture-options";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Live choreography capture · Murmur",
  robots: { index: false, follow: false },
};

interface ChoreographyCapturePageProps {
  readonly searchParams: Promise<
    Record<string, string | readonly string[] | undefined>
  >;
}

export default async function ChoreographyCapturePage({
  searchParams,
}: ChoreographyCapturePageProps) {
  if (process.env.MURMUR_E2E_MODE !== "1") notFound();

  let options: ReturnType<typeof parseChoreographyCaptureOptions>;
  try {
    options = parseChoreographyCaptureOptions(await searchParams);
  } catch {
    notFound();
  }

  return (
    <div className="dark">
      <ChoreographyCaptureClient options={options} />
    </div>
  );
}
