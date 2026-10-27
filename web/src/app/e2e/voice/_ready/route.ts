import { createHash } from "crypto";

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const PROCESS_READY_DOMAIN = "murmur-relay-process-ready:v1:";
const NONCE_HEX_LENGTH = 64;
const HEX = /^[0-9a-f]{64}$/;

export function GET() {
  const nonce = process.env.VOICE_E2E_PROCESS_READY_NONCE;
  if (!nonce || !HEX.test(nonce)) {
    return NextResponse.json(
      { schema_version: 1, ok: false },
      { status: 404 },
    );
  }
  if (process.env.VOICE_E2E_NETWORK !== "relay-tls") {
    return NextResponse.json(
      { schema_version: 1, ok: false },
      { status: 404 },
    );
  }
  const digest = createHash("sha256")
    .update(PROCESS_READY_DOMAIN + nonce)
    .digest("hex");
  return NextResponse.json(
    { schema_version: 1, ok: true, process_ready_digest: digest },
    { headers: { "cache-control": "no-store" } },
  );
}
