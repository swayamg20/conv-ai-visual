import { notFound } from "next/navigation";

import { VoiceE2EClient } from "./voice-e2e-client";

export const dynamic = "force-dynamic";

const E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76";
const E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578";
const E2E_API_ORIGINS = new Set([
  "http://127.0.0.1:8100",
  "http://127.0.0.1:8101",
]);

export default function VoiceE2EPage() {
  const apiUrl = process.env.VOICE_E2E_API_URL ?? "http://127.0.0.1:8100";
  if (
    process.env.MURMUR_E2E_MODE !== "1" ||
    process.env.VOICE_E2E_WEB_URL !== "http://127.0.0.1:3100" ||
    !E2E_API_ORIGINS.has(apiUrl)
  ) {
    notFound();
  }

  return (
    <VoiceE2EClient
      agentId={E2E_AGENT_ID}
      apiUrl={apiUrl}
      sessionId={E2E_SESSION_ID}
    />
  );
}
