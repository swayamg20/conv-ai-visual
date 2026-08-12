import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getAuthHeaders: vi.fn<() => Promise<Record<string, string>>>(),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: mocks.getAuthHeaders,
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "https://api.example.test",
}));

import {
  bootstrapVoiceSession,
  endVoiceSession,
  VOICE_V2_EVENT_TOPIC,
  VoiceSessionApiError,
} from "./session-api";

const request = {
  session_id: "a4f4328e-185e-4c65-b3f7-101e04a37578",
  voice_call_id: "68d16729-f21c-49fd-88b5-6d202710bc2d",
};

function response(overrides: Record<string, unknown> = {}) {
  return {
    runtime: "livekit_v2",
    trace_id: "025bcf26-dcab-4f8c-bb44-af298875f638",
    profile_id: "cascade-v1",
    server_url: "wss://voice.example.test",
    room_name: "room-1",
    participant_token: "signed.jwt.token",
    participant_identity: "user-1",
    agent_participant_identity: "agent-worker-1",
    session_id: request.session_id,
    agent_id: "90bd1253-90a6-459a-bf37-365bc3039a76",
    voice_call_id: request.voice_call_id,
    dispatch_id: "dispatch-1",
    worker_name: "murmur-worker",
    event_topic: VOICE_V2_EVENT_TOPIC,
    expires_at: "2099-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("Voice V2 session API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.getAuthHeaders.mockResolvedValue({ Authorization: "Bearer firebase-token" });
  });

  it("posts the strict retry-stable call identity with Firebase auth", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(response()), { status: 200 }));

    const bootstrap = await bootstrapVoiceSession(request);

    expect(bootstrap).toMatchObject({
      runtime: "livekit_v2",
      session_id: request.session_id,
      voice_call_id: request.voice_call_id,
      event_topic: VOICE_V2_EVENT_TOPIC,
    });
    expect(Object.isFrozen(bootstrap)).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/voice/session",
      expect.objectContaining({
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer firebase-token",
        },
        body: JSON.stringify(request),
      })
    );
  });

  it("does not reject a server-valid token because the device clock is ahead", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2100-01-01T00:00:00Z"));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(response()), { status: 200 })
    );

    await expect(bootstrapVoiceSession(request)).resolves.toMatchObject({
      participant_token: "signed.jwt.token",
      expires_at: "2099-01-01T00:00:00Z",
    });
  });

  it("fails before the network when no authenticated user is available", async () => {
    mocks.getAuthHeaders.mockResolvedValue({});
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(bootstrapVoiceSession(request)).rejects.toMatchObject({
      name: "VoiceSessionApiError",
      status: 401,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed on unknown response fields and identity mismatch", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(response({ api_secret: "must-not-pass" })), {
          status: 200,
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(
            response({ session_id: "b041809d-b90e-45b2-b8ec-53f6fdaf1b42" })
          ),
          { status: 200 }
        )
      );

    await expect(bootstrapVoiceSession(request)).rejects.toBeInstanceOf(
      VoiceSessionApiError
    );
    await expect(bootstrapVoiceSession(request)).rejects.toThrow(
      "identity does not match"
    );
  });

  it("preserves safe backend errors without accepting an invalid success body", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Voice runtime unavailable" }), {
          status: 503,
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(response({ server_url: "https://not-websocket.test" })), {
          status: 200,
        })
      );

    await expect(bootstrapVoiceSession(request)).rejects.toMatchObject({
      message: "Voice runtime unavailable",
      status: 503,
    });
    await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
  });

  it("rejects non-canonical bootstrap UUIDs before the network", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(
      bootstrapVoiceSession({ ...request, session_id: "session-1" })
    ).rejects.toThrow("valid session and call IDs");
    await expect(
      bootstrapVoiceSession({
        ...request,
        voice_call_id: request.voice_call_id.toUpperCase(),
      })
    ).rejects.toThrow("valid session and call IDs");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("releases an assigned call with Firebase auth and keepalive", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));

    await endVoiceSession(request);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/voice/session/end",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer firebase-token",
        },
        body: JSON.stringify(request),
        signal: undefined,
        keepalive: true,
      }
    );
  });
});
