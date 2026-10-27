import { describe, expect, it, vi } from "vitest";

vi.mock("next/server", () => ({
  NextResponse: {
    json: (body: unknown, init?: { status?: number }) =>
      ({ body, status: init?.status ?? 200 }) as unknown as ReturnType<
        typeof import("next/server")["NextResponse"]["json"]
      >,
  },
}));

import { GET } from "./route";
import { createHash } from "crypto";

const NONCE = "ab".repeat(32);

function digestOf(nonce: string) {
  return createHash("sha256").update("murmur-relay-process-ready:v1:" + nonce).digest("hex");
}

type ResponseLike = { body: Record<string, unknown>; status: number };

function responseOf(result: unknown): ResponseLike {
  return result as ResponseLike;
}

describe("voice e2e process ready route", () => {
  it("returns the exact digest in relay mode", () => {
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    vi.stubEnv("VOICE_E2E_PROCESS_READY_NONCE", NONCE);
    const response = responseOf(GET());
    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      schema_version: 1,
      ok: true,
      process_ready_digest: digestOf(NONCE),
    });
  });

  it("never exposes the nonce itself", () => {
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    vi.stubEnv("VOICE_E2E_PROCESS_READY_NONCE", NONCE);
    const response = responseOf(GET());
    expect(JSON.stringify(response.body)).not.toContain(NONCE);
  });

  it("404s without a nonce or in direct mode", () => {
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    vi.unstubAllEnvs();
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    let response = responseOf(GET());
    expect(response.status).toBe(404);
    vi.stubEnv("VOICE_E2E_PROCESS_READY_NONCE", NONCE);
    vi.stubEnv("VOICE_E2E_NETWORK", "direct");
    response = responseOf(GET());
    expect(response.status).toBe(404);
    vi.unstubAllEnvs();
  });

  it("404s on a malformed nonce", () => {
    vi.stubEnv("VOICE_E2E_NETWORK", "relay-tls");
    vi.stubEnv("VOICE_E2E_PROCESS_READY_NONCE", "XYZ");
    const response = responseOf(GET());
    expect(response.status).toBe(404);
    vi.unstubAllEnvs();
  });
});
