import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

describe("GET /healthz", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns the stable public readiness contract", async () => {
    vi.stubEnv("MURMUR_RELEASE_SHA", "");
    vi.stubEnv("NEXT_PUBLIC_VOICE_RUNTIME", "disabled");
    const response = GET();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      service: "murmur-web",
      status: "ok",
      voice_experience: "disabled",
    });
  });

  it("exposes the configured non-secret release revision", async () => {
    vi.stubEnv("MURMUR_RELEASE_SHA", "4eac28e95115a8266f525b4856663f2d18ece02f");
    vi.stubEnv("NEXT_PUBLIC_VOICE_RUNTIME", "disabled");

    const response = GET();

    await expect(response.json()).resolves.toEqual({
      release_sha: "4eac28e95115a8266f525b4856663f2d18ece02f",
      service: "murmur-web",
      status: "ok",
      voice_experience: "disabled",
    });
  });

  it("never reports disabled unless the exact product gate is selected", async () => {
    vi.stubEnv("NEXT_PUBLIC_VOICE_RUNTIME", "voice_v2");

    const response = GET();

    await expect(response.json()).resolves.toMatchObject({
      voice_experience: "enabled",
    });
  });
});
