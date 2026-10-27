import { getAuthHeaders } from "@/lib/firebase";

import { CosmoLabError } from "./contracts";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function mintCosmoToken(signal: AbortSignal): Promise<string> {
  const headers = await getAuthHeaders();
  if (!headers.Authorization)
    throw new CosmoLabError("Sign in to Murmur before connecting.");
  const response = await fetch(`${API_BASE}/api/voice/cosmo/token`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: "{}",
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401)
      throw new CosmoLabError("Your sign-in expired. Sign in again.");
    if (response.status === 503) {
      const body = await response.json().catch(() => null);
      const message = body?.detail ?? body?.error;
      const known = [
        "Enable MURMUR_COSMO_ENABLED to use the Cosmo lab.",
        "Set COSMO_API_KEY on the Murmur server, then restart it.",
        "Cosmo rejected token minting. Check the server key and its user_tokens:mint scope.",
      ];
      if (known.includes(message)) throw new CosmoLabError(message);
    }
    throw new CosmoLabError(
      "The voice service could not start. Check the backend and try again.",
    );
  }
  const body: unknown = await response.json();
  if (
    typeof body !== "object" ||
    body === null ||
    !("jwt" in body) ||
    typeof body.jwt !== "string" ||
    !body.jwt
  ) {
    throw new CosmoLabError("The voice service returned an invalid token.");
  }
  return body.jwt;
}
