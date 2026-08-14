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
  PIPECAT_VOICE_EVENT_PROTOCOL,
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

function pipecatResponse(
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    runtime: "pipecat_smallwebrtc_v1",
    profile_id: "pipecat-direct-cascade-v1",
    event_protocol: PIPECAT_VOICE_EVENT_PROTOCOL,
    expires_at: "2099-01-01T00:00:00Z",
    session_id: request.session_id,
    agent_id: "90bd1253-90a6-459a-bf37-365bc3039a76",
    voice_call_id: request.voice_call_id,
    trace_id: "025bcf26-dcab-4f8c-bb44-af298875f638",
    webrtc_url:
      "https://voice.example.test/api/voice/pipecat/signal/opaque-token-1",
    peer_reservation_id: "peer-reservation-1",
    ice_servers: [
      {
        urls: ["stun:stun.example.test:3478"],
        username: null,
        credential: null,
        credentialType: "password",
      },
      {
        urls: ["turns:turn.example.test:5349?transport=tcp"],
        username: "turn-user",
        credential: "turn-password",
        credentialType: "password",
      },
    ],
    ...overrides,
  };
}

describe("Voice V2 session API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.getAuthHeaders
      .mockReset()
      .mockResolvedValue({ Authorization: "Bearer firebase-token" });
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
        credentials: "omit",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      })
    );
  });

  it("decodes and deeply freezes the strict Pipecat browser assignment", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(pipecatResponse()), { status: 200 })
    );

    const bootstrap = await bootstrapVoiceSession(request);

    expect(bootstrap).toMatchObject({
      runtime: "pipecat_smallwebrtc_v1",
      session_id: request.session_id,
      voice_call_id: request.voice_call_id,
      event_protocol: PIPECAT_VOICE_EVENT_PROTOCOL,
    });
    if (bootstrap.runtime !== "pipecat_smallwebrtc_v1") {
      throw new Error("Expected a Pipecat assignment");
    }
    expect(Object.isFrozen(bootstrap)).toBe(true);
    expect(Object.isFrozen(bootstrap.ice_servers)).toBe(true);
    expect(Object.isFrozen(bootstrap.ice_servers[0])).toBe(true);
    expect(Object.isFrozen(bootstrap.ice_servers[0]?.urls)).toBe(true);
    expect(bootstrap.ice_servers).toEqual([
      {
        urls: ["stun:stun.example.test:3478"],
        username: null,
        credential: null,
        credentialType: "password",
      },
      {
        urls: ["turns:turn.example.test:5349?transport=tcp"],
        username: "turn-user",
        credential: "turn-password",
        credentialType: "password",
      },
    ]);
  });

  it("uses an injected auth provider for both bootstrap and release", async () => {
    const authHeaderProvider = vi.fn(async () => ({
      Authorization: "Bearer injected-test-token",
    }));
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(response()), { status: 200 })
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await bootstrapVoiceSession(request, { authHeaderProvider });
    await endVoiceSession(request, { authHeaderProvider });

    expect(authHeaderProvider).toHaveBeenCalledTimes(2);
    expect(mocks.getAuthHeaders).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "https://api.example.test/api/voice/session",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer injected-test-token",
        }),
      })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://api.example.test/api/voice/session/end",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer injected-test-token",
        }),
        keepalive: true,
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

  it.each([
    "wss://voice.example.test",
    "wss://voice.example.test/",
    "ws://localhost:7880/",
  ])("accepts a strict LiveKit ws/wss origin: %s", async (serverUrl) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(response({ server_url: serverUrl })),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).resolves.toMatchObject({
      runtime: "livekit_v2",
      server_url: serverUrl,
    });
  });

  it.each([
    "wss://voice.example.test/room",
    "wss://voice.example.test?secret=1",
    "wss://voice.example.test#secret",
    "wss://user:secret@voice.example.test",
    " wss://voice.example.test",
    "wss://voice.example.test\\room",
  ])("rejects a non-origin LiveKit server URL: %s", async (serverUrl) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(response({ server_url: serverUrl })),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
  });

  it.each([
    " signed.jwt.token",
    "signed.jwt.token ",
    "signed.jwt.\u0000token",
    "signed.jwt.\ntoken",
  ])("rejects an unsafe LiveKit participant token", async (participantToken) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(response({ participant_token: participantToken })),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
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

  it("rejects missing, mixed-runtime, and nested unknown Pipecat keys", async () => {
    const missingKey = pipecatResponse();
    delete missingKey.peer_reservation_id;
    const nestedUnknownKey = pipecatResponse({
      ice_servers: [
        {
          urls: ["stun:stun.example.test:3478"],
          username: null,
          credential: null,
          credentialType: "password",
          api_secret: "must-not-pass",
        },
      ],
    });
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(missingKey), { status: 200 })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(pipecatResponse({ server_url: "wss://mixed.example.test" })),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(nestedUnknownKey), { status: 200 })
      );

    await expect(bootstrapVoiceSession(request)).rejects.toThrow(
      "incompatible schema"
    );
    await expect(bootstrapVoiceSession(request)).rejects.toThrow(
      "incompatible schema"
    );
    await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
  });

  it.each([
    "https://voice.example.test/api/voice/pipecat/signal/token",
    "http://localhost:7860/api/voice/pipecat/signal/token",
    "http://127.0.0.1:7860/api/voice/pipecat/signal/token",
    "http://[::1]:7860/api/voice/pipecat/signal/token",
  ])("accepts a safe Pipecat signaling URL: %s", async (webrtcUrl) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(pipecatResponse({ webrtc_url: webrtcUrl })),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).resolves.toMatchObject({
      runtime: "pipecat_smallwebrtc_v1",
      webrtc_url: webrtcUrl,
    });
  });

  it("accepts loopback Pipecat signaling without an ICE server", async () => {
    const webrtcUrl =
      "http://localhost:7860/api/voice/pipecat/signal/direct-token";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(
          pipecatResponse({ webrtc_url: webrtcUrl, ice_servers: [] })
        ),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).resolves.toMatchObject({
      runtime: "pipecat_smallwebrtc_v1",
      webrtc_url: webrtcUrl,
      ice_servers: [],
    });
  });

  it.each([
    { label: "empty ICE", iceServers: [] },
    {
      label: "STUN only",
      iceServers: [
        {
          urls: ["stun:stun.example.test:3478"],
          username: null,
          credential: null,
          credentialType: "password",
        },
      ],
    },
  ])(
    "rejects public Pipecat signaling with $label",
    async ({ iceServers }) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(
          JSON.stringify(pipecatResponse({ ice_servers: iceServers })),
          { status: 200 }
        )
      );

      await expect(bootstrapVoiceSession(request)).rejects.toThrow(
        "invalid fields"
      );
    }
  );

  it.each([
    "turn:turn.example.test:3478?transport=udp",
    "turns:turn.example.test:5349?transport=tcp",
  ])("accepts public Pipecat signaling with TURN: %s", async (turnUrl) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(
          pipecatResponse({
            ice_servers: [
              {
                urls: [turnUrl],
                username: "turn-user",
                credential: "turn-password",
                credentialType: "password",
              },
            ],
          })
        ),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).resolves.toMatchObject({
      runtime: "pipecat_smallwebrtc_v1",
    });
  });

  it.each([
    "http://voice.example.test/api/voice/pipecat/signal/token",
    "ws://voice.example.test/api/voice/pipecat/signal/token",
    "https://user:secret@voice.example.test/api/voice/pipecat/signal/token",
    "https://voice.example.test/api/voice/pipecat/signal/token?secret=1",
    "https://voice.example.test/api/voice/pipecat/signal/token#secret",
    "https://voice.example.test/",
  ])("rejects an unsafe Pipecat signaling URL: %s", async (webrtcUrl) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(pipecatResponse({ webrtc_url: webrtcUrl })),
        { status: 200 }
      )
    );

    await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
  });

  it("rejects the wrong Pipecat event protocol and unsafe ICE credential shapes", async () => {
    const unsafeIceResponses = [
      pipecatResponse({ event_protocol: "murmur.voice.v2.events" }),
      pipecatResponse({
        ice_servers: [
          {
            urls: ["turn:turn.example.test:3478"],
            username: "turn-user",
            credential: "turn-password",
            credentialType: "oauth",
          },
        ],
      }),
      pipecatResponse({
        ice_servers: [
          {
            urls: ["turn:turn.example.test:3478"],
            username: null,
            credential: null,
            credentialType: "password",
          },
        ],
      }),
      pipecatResponse({
        ice_servers: [
          {
            urls: ["stun:stun.example.test:3478"],
            username: "turn-user",
            credential: "turn-password",
            credentialType: "password",
          },
        ],
      }),
    ];
    const fetchMock = vi.spyOn(globalThis, "fetch");
    for (const unsafeResponse of unsafeIceResponses) {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(unsafeResponse), { status: 200 })
      );
    }

    for (const _unsafeResponse of unsafeIceResponses) {
      await expect(bootstrapVoiceSession(request)).rejects.toThrow("invalid fields");
    }
  });

  it("rejects a Pipecat assignment whose call identity differs from the request", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(
          pipecatResponse({
            voice_call_id: "b041809d-b90e-45b2-b8ec-53f6fdaf1b42",
          })
        ),
        { status: 200 }
      )
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
        credentials: "omit",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      }
    );
  });
});
