import { describe, expect, it, vi } from "vitest";

import {
  decodeVoiceEvent,
  REQUIRED_VOICE_READY_COMPONENTS,
  type EventOf,
  type VoiceEvent,
} from "./events";
import type { PipecatSignalingPort } from "./pipecat-signaling-api";
import type {
  LiveKitVoiceSessionBootstrap,
  PipecatBrowserVoiceAssignment,
  VoiceSessionBootstrap,
} from "./session-api";
import {
  createVoiceTransportLoader,
  type VoiceTransportCallbacks,
  type VoiceTransportModuleImporters,
} from "./voice-transport";

const liveKitAssignment = Object.freeze({
  runtime: "livekit_v2",
  trace_id: "025bcf26-dcab-4f8c-bb44-af298875f638",
  profile_id: "cascade-v1",
  server_url: "wss://voice.example.test",
  room_name: "room-1",
  participant_token: "signed.jwt.token",
  participant_identity: "user-1",
  agent_participant_identity: "agent-worker-1",
  session_id: "a4f4328e-185e-4c65-b3f7-101e04a37578",
  agent_id: "90bd1253-90a6-459a-bf37-365bc3039a76",
  voice_call_id: "25b7aed8-4342-4def-9638-430309391c5c",
  dispatch_id: "dispatch-1",
  worker_name: "murmur-worker",
  event_topic: "murmur.voice.v2.events",
  expires_at: "2099-01-01T00:00:00Z",
} satisfies LiveKitVoiceSessionBootstrap);

const pipecatAssignment = Object.freeze({
  runtime: "pipecat_smallwebrtc_v1",
  profile_id: "cascade-v1",
  event_protocol: "rtvi-murmur-v2",
  expires_at: "2099-01-01T00:00:00Z",
  session_id: "a4f4328e-185e-4c65-b3f7-101e04a37578",
  agent_id: "90bd1253-90a6-459a-bf37-365bc3039a76",
  voice_call_id: "25b7aed8-4342-4def-9638-430309391c5c",
  trace_id: "025bcf26-dcab-4f8c-bb44-af298875f638",
  webrtc_url: "https://voice.example.test/signal/opaque-token",
  peer_reservation_id: "reservation-1",
  ice_servers: Object.freeze([
    Object.freeze({
      urls: Object.freeze(["stun:stun.example.test:3478"]),
      username: null,
      credential: null,
      credentialType: "password" as const,
    }),
    Object.freeze({
      urls: Object.freeze(["turns:turn.example.test:5349?transport=tcp"]),
      username: "turn-user",
      credential: "turn-password",
      credentialType: "password" as const,
    }),
  ]),
} satisfies PipecatBrowserVoiceAssignment);

function wireReady(
  assignment: VoiceSessionBootstrap,
  overrides: Readonly<Record<string, unknown>> = {},
): Record<string, unknown> {
  return {
    schema_version: 1,
    event_id: "ready-1",
    event_type: "agent_ready",
    trace_id: assignment.trace_id,
    voice_call_id: assignment.voice_call_id,
    session_id: assignment.session_id,
    producer_id: "voice-worker-1",
    producer_sequence: 1,
    emitted_at: "2026-08-14T08:00:00Z",
    payload: {
      profile_id: assignment.profile_id,
      required_components: REQUIRED_VOICE_READY_COMPONENTS,
      ready_components: REQUIRED_VOICE_READY_COMPONENTS,
    },
    ...overrides,
  };
}

function canonicalReady(
  assignment: VoiceSessionBootstrap,
): EventOf<"agent_ready"> {
  const decoded = decodeVoiceEvent(wireReady(assignment));
  if (!decoded.ok || decoded.event.event_type !== "agent_ready") {
    throw new Error("Expected a canonical Ready fixture");
  }
  return decoded.event;
}

function createCallbacks() {
  return {
    isCurrent: vi.fn<() => boolean>(() => true),
    onConnected: vi.fn<() => void>(),
    onDisconnected: vi.fn<() => void>(),
    onAgentDisconnected: vi.fn<() => void>(),
    onFreshCallRequired: vi.fn<() => void>(),
    onEvent: vi.fn<(event: VoiceEvent) => void>(),
    onInvalidEvent: vi.fn<VoiceTransportCallbacks["onInvalidEvent"]>(),
    onTransportError: vi.fn<VoiceTransportCallbacks["onTransportError"]>(),
    onMicrophoneUnavailable:
      vi.fn<VoiceTransportCallbacks["onMicrophoneUnavailable"]>(),
    onAudioPlaybackBlockedChange:
      vi.fn<VoiceTransportCallbacks["onAudioPlaybackBlockedChange"]>(),
    onLocalMicrophoneTrack:
      vi.fn<NonNullable<VoiceTransportCallbacks["onLocalMicrophoneTrack"]>>(),
    onLocalMicrophoneDiagnostic:
      vi.fn<
        NonNullable<VoiceTransportCallbacks["onLocalMicrophoneDiagnostic"]>
      >(),
  } satisfies VoiceTransportCallbacks;
}

interface LiveKitCallbackView {
  readonly onReconnecting: (attempt: number) => void;
  readonly onReconnected: () => void;
  readonly onTransportInput: (input: unknown) => void;
  readonly onInvalidEventChannel: () => void;
  readonly onLocalMicrophoneTrack: (track: MediaStreamTrack | null) => void;
  readonly onLocalMicrophonePublication: (
    track: MediaStreamTrack,
    observation: {
      readonly trackId: string;
      readonly observedAtMs: number;
      readonly mediaStreamTrackEnabled: boolean;
      readonly livekitMuted: boolean;
      readonly readyState: MediaStreamTrackState;
    },
  ) => void;
}

interface PipecatCallbackView {
  readonly onFreshCallRequired: () => void;
  readonly onEvent: (event: VoiceEvent) => void;
  readonly onLocalMicrophoneTrack: (
    track: MediaStreamTrack | null,
    observation: {
      readonly direction: "local" | "remote";
      readonly trackId: string;
      readonly observedAtMs: number;
      readonly enabled: boolean;
      readonly muted: boolean;
      readonly readyState: MediaStreamTrackState;
    } | null,
  ) => void;
}

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function createImporterHarness() {
  let liveKitOptions: unknown;
  let pipecatOptions: unknown;
  let liveKitInstance: FakeLiveKitTransport | null = null;
  let pipecatInstance: FakePipecatTransport | null = null;

  class FakeLiveKitTransport {
    readonly primeAudioPlayback = vi.fn();
    readonly connect = vi.fn(async (_assignment?: unknown) => undefined);
    readonly activateMicrophoneAfterReady = vi.fn(async () => undefined);
    readonly setMicrophoneEnabled = vi.fn(
      async (_enabled?: boolean) => undefined,
    );
    readonly setTtsEnabled = vi.fn((_enabled?: boolean) => undefined);
    readonly resumeAudio = vi.fn(async () => undefined);
    readonly disconnect = vi.fn<() => Promise<void>>(async () => undefined);

    constructor(options: unknown) {
      liveKitOptions = options;
      liveKitInstance = this;
    }
  }

  class FakePipecatTransport {
    readonly primeAudioPlayback = vi.fn();
    readonly connect = vi.fn(async (_connection?: unknown) => undefined);
    readonly acceptCanonicalReady = vi.fn(
      async (_event?: unknown) => undefined,
    );
    readonly setMicrophoneEnabled = vi.fn(
      async (_enabled?: boolean) => undefined,
    );
    readonly setTtsEnabled = vi.fn((_enabled?: boolean) => undefined);
    readonly resumeAudio = vi.fn(async () => undefined);
    readonly disconnect = vi.fn<() => Promise<void>>(async () => undefined);

    constructor(options: unknown) {
      pipecatOptions = options;
      pipecatInstance = this;
    }
  }

  const signalingPort: PipecatSignalingPort = {
    offer: vi.fn(async () => ({
      sdp: "v=0\r\n",
      type: "answer" as const,
      pc_id: "peer-1",
    })),
    patchCandidates: vi.fn(async () => undefined),
    deletePeer: vi.fn(async () => undefined),
  };
  const createPipecatSignalingPort = vi.fn(
    (_url: string, _options?: unknown) => signalingPort,
  );
  const loadLiveKit = vi.fn(async () => ({
    LiveKitVoiceTransport: FakeLiveKitTransport,
  }));
  const loadPipecat = vi.fn(async () => ({
    PipecatVoiceTransport: FakePipecatTransport,
    createPipecatSignalingPort,
  }));
  const importers = {
    loadLiveKit,
    loadPipecat,
  } as VoiceTransportModuleImporters;

  return {
    importers,
    loadLiveKit,
    loadPipecat,
    signalingPort,
    createPipecatSignalingPort,
    get liveKitOptions() {
      return liveKitOptions as {
        readonly voiceCallId: string;
        readonly ttsEnabled: boolean;
        readonly callbacks: LiveKitCallbackView;
      };
    },
    get pipecatOptions() {
      return pipecatOptions as {
        readonly outputEnabled: boolean;
        readonly signalingPortFactory: (url: string) => PipecatSignalingPort;
        readonly callbacks: PipecatCallbackView;
      };
    },
    get liveKitInstance() {
      if (!liveKitInstance) throw new Error("Expected a LiveKit adapter");
      return liveKitInstance;
    },
    get pipecatInstance() {
      if (!pipecatInstance) throw new Error("Expected a Pipecat adapter");
      return pipecatInstance;
    },
  };
}

describe("runtime-neutral voice transport loader", () => {
  it("loads only LiveKit, binds its assignment, and preserves diagnostics", async () => {
    const harness = createImporterHarness();
    const callbacks = createCallbacks();
    const loader = createVoiceTransportLoader(harness.importers);

    const transport = await loader(liveKitAssignment, {
      ttsEnabled: false,
      callbacks,
    });

    expect(transport.runtime).toBe("livekit_v2");
    expect(harness.loadLiveKit).toHaveBeenCalledOnce();
    expect(harness.loadPipecat).not.toHaveBeenCalled();
    expect(harness.liveKitOptions).toMatchObject({
      voiceCallId: liveKitAssignment.voice_call_id,
      ttsEnabled: false,
    });

    transport.primeAudioPlayback();
    const firstConnect = transport.connect();
    const secondConnect = transport.connect();
    expect(secondConnect).toBe(firstConnect);
    await firstConnect;
    expect(harness.liveKitInstance.primeAudioPlayback).toHaveBeenCalledOnce();
    expect(harness.liveKitInstance.connect).toHaveBeenCalledOnce();
    expect(harness.liveKitInstance.connect).toHaveBeenCalledWith(
      liveKitAssignment,
    );

    const track = { id: "livekit-mic" } as MediaStreamTrack;
    const observation = Object.freeze({
      trackId: "livekit-mic",
      observedAtMs: 42,
      mediaStreamTrackEnabled: false,
      livekitMuted: true,
      readyState: "live" as const,
    });
    harness.liveKitOptions.callbacks.onLocalMicrophoneTrack(track);
    harness.liveKitOptions.callbacks.onLocalMicrophonePublication(
      track,
      observation,
    );
    expect(callbacks.onLocalMicrophoneTrack).toHaveBeenCalledWith(track);
    expect(callbacks.onLocalMicrophoneDiagnostic).toHaveBeenCalledWith(track, {
      runtime: "livekit_v2",
      kind: "publication",
      observation,
    });
    expect(
      Object.isFrozen(callbacks.onLocalMicrophoneDiagnostic.mock.calls[0]?.[1]),
    ).toBe(true);
  });

  it("strictly types LiveKit events, keeps canonical Ready identity, and fresh-closes reconnect", async () => {
    const harness = createImporterHarness();
    const callbacks = createCallbacks();
    const transport = await createVoiceTransportLoader(harness.importers)(
      liveKitAssignment,
      { ttsEnabled: true, callbacks },
    );

    harness.liveKitOptions.callbacks.onTransportInput(
      wireReady(liveKitAssignment),
    );
    const ready = callbacks.onEvent.mock.calls[0]?.[0];
    if (!ready || ready.event_type !== "agent_ready") {
      throw new Error("Expected the exact decoded Ready object");
    }
    await expect(
      transport.activateMicrophoneAfterReady(
        structuredClone(ready) as EventOf<"agent_ready">,
      ),
    ).rejects.toThrow("canonical Ready");
    await transport.activateMicrophoneAfterReady(ready);
    expect(
      harness.liveKitInstance.activateMicrophoneAfterReady,
    ).toHaveBeenCalledOnce();

    harness.liveKitOptions.callbacks.onTransportInput(
      wireReady(liveKitAssignment, {
        event_id: "wrong-trace",
        producer_sequence: 2,
        trace_id: "different-trace",
      }),
    );
    expect(callbacks.onEvent).toHaveBeenCalledOnce();
    expect(callbacks.onInvalidEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ code: "event_scope_mismatch" }),
    );
    harness.liveKitOptions.callbacks.onInvalidEventChannel();
    expect(callbacks.onInvalidEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ code: "invalid_event_channel" }),
    );

    harness.liveKitOptions.callbacks.onReconnecting(1);
    harness.liveKitOptions.callbacks.onReconnected();
    expect(callbacks.onFreshCallRequired).toHaveBeenCalledOnce();
    expect(callbacks.onTransportError).toHaveBeenCalledOnce();
    expect(harness.liveKitInstance.disconnect).toHaveBeenCalledOnce();
    await transport.disconnect();
    expect(harness.liveKitInstance.disconnect).toHaveBeenCalledOnce();
  });

  it("maps only Pipecat fields, retains fresh auth, and preserves Ready identity", async () => {
    const harness = createImporterHarness();
    const callbacks = createCallbacks();
    const authHeaderProvider = vi
      .fn<() => Promise<Record<string, string>>>()
      .mockResolvedValueOnce({ Authorization: "Bearer first" })
      .mockResolvedValueOnce({ Authorization: "Bearer second" });
    const transport = await createVoiceTransportLoader(harness.importers)(
      pipecatAssignment,
      { ttsEnabled: false, callbacks, authHeaderProvider },
    );

    expect(transport.runtime).toBe("pipecat_smallwebrtc_v1");
    expect(harness.loadPipecat).toHaveBeenCalledOnce();
    expect(harness.loadLiveKit).not.toHaveBeenCalled();
    expect(harness.pipecatOptions.outputEnabled).toBe(false);
    expect(authHeaderProvider).not.toHaveBeenCalled();
    expect(
      harness.pipecatOptions.signalingPortFactory(pipecatAssignment.webrtc_url),
    ).toBe(harness.signalingPort);
    const signalingOptions = harness.createPipecatSignalingPort.mock
      .calls[0]?.[1] as {
      readonly authHeaderProvider?: () => Promise<Record<string, string>>;
    };
    expect(signalingOptions.authHeaderProvider).toBe(authHeaderProvider);
    await expect(signalingOptions.authHeaderProvider?.()).resolves.toEqual({
      Authorization: "Bearer first",
    });
    await expect(signalingOptions.authHeaderProvider?.()).resolves.toEqual({
      Authorization: "Bearer second",
    });

    await transport.connect();
    expect(harness.pipecatInstance.connect).toHaveBeenCalledWith({
      runtime: "pipecat_smallwebrtc_v1",
      eventProtocol: "rtvi-murmur-v2",
      webrtcUrl: pipecatAssignment.webrtc_url,
      peerReservationId: pipecatAssignment.peer_reservation_id,
      eventScope: {
        traceId: pipecatAssignment.trace_id,
        voiceCallId: pipecatAssignment.voice_call_id,
        sessionId: pipecatAssignment.session_id,
        profileId: pipecatAssignment.profile_id,
      },
      iceServers: [
        {
          urls: ["stun:stun.example.test:3478"],
          credentialType: "password",
        },
        {
          urls: ["turns:turn.example.test:5349?transport=tcp"],
          credentialType: "password",
          username: "turn-user",
          credential: "turn-password",
        },
      ],
    });
    const mappedConnection = harness.pipecatInstance.connect.mock
      .calls[0]?.[0] as {
      readonly eventScope: object;
      readonly iceServers: readonly { readonly urls: readonly string[] }[];
    };
    expect(Object.isFrozen(mappedConnection)).toBe(true);
    expect(Object.isFrozen(mappedConnection.eventScope)).toBe(true);
    expect(Object.isFrozen(mappedConnection.iceServers)).toBe(true);
    expect(Object.isFrozen(mappedConnection.iceServers[0]?.urls)).toBe(true);

    const ready = canonicalReady(pipecatAssignment);
    harness.pipecatOptions.callbacks.onEvent(ready);
    expect(callbacks.onEvent).toHaveBeenCalledWith(ready);
    expect(callbacks.onEvent.mock.calls[0]?.[0]).toBe(ready);
    await expect(
      transport.activateMicrophoneAfterReady(
        structuredClone(ready) as EventOf<"agent_ready">,
      ),
    ).rejects.toThrow("canonical Ready");
    await transport.activateMicrophoneAfterReady(ready);
    expect(harness.pipecatInstance.acceptCanonicalReady).toHaveBeenCalledWith(
      ready,
    );

    const track = { id: "pipecat-mic" } as MediaStreamTrack;
    const observation = Object.freeze({
      direction: "local" as const,
      trackId: "pipecat-mic",
      observedAtMs: 43,
      enabled: false,
      muted: false,
      readyState: "live" as const,
    });
    harness.pipecatOptions.callbacks.onLocalMicrophoneTrack(track, observation);
    expect(callbacks.onLocalMicrophoneTrack).toHaveBeenCalledWith(track);
    expect(callbacks.onLocalMicrophoneDiagnostic).toHaveBeenCalledWith(track, {
      runtime: "pipecat_smallwebrtc_v1",
      kind: "track",
      observation,
    });
  });

  it("coalesces Pipecat fresh-call teardown through its DELETE-owning disconnect", async () => {
    const harness = createImporterHarness();
    const callbacks = createCallbacks();
    const transport = await createVoiceTransportLoader(harness.importers)(
      pipecatAssignment,
      { ttsEnabled: true, callbacks },
    );
    const close = deferred<void>();
    harness.pipecatInstance.disconnect.mockImplementationOnce(
      () => close.promise,
    );

    harness.pipecatOptions.callbacks.onFreshCallRequired();
    harness.pipecatOptions.callbacks.onFreshCallRequired();
    const publicClose = transport.disconnect();

    expect(callbacks.onFreshCallRequired).toHaveBeenCalledOnce();
    expect(harness.pipecatInstance.disconnect).toHaveBeenCalledOnce();
    expect(publicClose).toBe(
      harness.pipecatInstance.disconnect.mock.results[0]?.value,
    );
    let settled = false;
    void publicClose.then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);
    close.resolve(undefined);
    await publicClose;
    expect(settled).toBe(true);
  });

  it.each([
    ["livekit_v2", liveKitAssignment],
    ["pipecat_smallwebrtc_v1", pipecatAssignment],
  ] as const)(
    "never loads the other arm when %s import fails",
    async (runtime, assignment) => {
      const harness = createImporterHarness();
      const failure = new Error(`${runtime} import failed`);
      if (runtime === "livekit_v2") {
        harness.loadLiveKit.mockRejectedValueOnce(failure);
      } else {
        harness.loadPipecat.mockRejectedValueOnce(failure);
      }
      const loader = createVoiceTransportLoader(harness.importers);

      await expect(
        loader(assignment, { ttsEnabled: true, callbacks: createCallbacks() }),
      ).rejects.toBe(failure);
      expect(harness.loadLiveKit).toHaveBeenCalledTimes(
        runtime === "livekit_v2" ? 1 : 0,
      );
      expect(harness.loadPipecat).toHaveBeenCalledTimes(
        runtime === "pipecat_smallwebrtc_v1" ? 1 : 0,
      );
    },
  );

  it("does not fall back after the selected adapter fails to connect", async () => {
    const harness = createImporterHarness();
    const callbacks = createCallbacks();
    const failure = new Error("selected Pipecat connect failed");
    const transport = await createVoiceTransportLoader(harness.importers)(
      pipecatAssignment,
      { ttsEnabled: true, callbacks },
    );
    harness.pipecatInstance.connect.mockRejectedValueOnce(failure);

    await expect(transport.connect()).rejects.toBe(failure);
    await expect(transport.connect()).rejects.toBe(failure);
    expect(harness.pipecatInstance.connect).toHaveBeenCalledOnce();
    expect(harness.loadLiveKit).not.toHaveBeenCalled();
  });

  it("rejects an unsupported discriminant before importing any arm", async () => {
    const harness = createImporterHarness();
    const loader = createVoiceTransportLoader(harness.importers);

    await expect(
      loader(
        { runtime: "unknown_runtime" } as unknown as VoiceSessionBootstrap,
        { ttsEnabled: true, callbacks: createCallbacks() },
      ),
    ).rejects.toThrow("unsupported runtime");
    expect(harness.loadLiveKit).not.toHaveBeenCalled();
    expect(harness.loadPipecat).not.toHaveBeenCalled();
  });
});
