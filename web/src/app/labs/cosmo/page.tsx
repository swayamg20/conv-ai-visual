import type { Metadata } from "next";

import { CosmoLab } from "@/features/cosmo/cosmo-lab";

export const metadata: Metadata = {
  title: "Cosmo voice lab · Murmur",
  description: "Talk through an idea with Murmur, powered by Cosmo.",
  robots: { index: false, follow: false },
};

export default function CosmoLabPage() {
  return <CosmoLab />;
}
