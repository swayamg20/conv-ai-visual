/** @vitest-environment happy-dom */

import type {
  Participant,
  RTVIEventCallbacks,
  Tracks,
} from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const dailySdk = vi.hoisted(() => {
  type Listener = (...arguments_: unknown[]) => void;

  let activeCall: ReturnType<typeof buildCall> | undefined;
  let nextCallId = 1;
  let nextOnFailure: number | undefined;
  let nextDestroyRejection = false;

  function buildCall() {
    const listeners = new Map<string, Set<Listener>>();
    const onFailure = nextOnFailure;
    const rejectDestroy = nextDestroyRejection;
    nextOnFailure = undefined;
    nextDestroyRejection = false;
    let onCallCount = 0;
    let readyState: MediaStreamTrackState = "live";
    let destroyed = false;
    let destroyBarrier: Promise<void> | undefined;
    let releaseDestroyBarrier: (() => void) | undefined;
    const localTrack = {
      kind: "audio",
      id: `daily-local-${nextCallId}`,
      label: "Daily microphone",
      enabled: true,
      muted: false,
      get readyState() {
        return readyState;
      },
      stop: vi.fn(() => {
        readyState = "ended";
      }),
    };
    const call = {
      callClientId: `daily-call-${nextCallId++}`,
      listeners,
      localTrack,
      on: vi.fn((event: string, listener: Listener) => {
        onCallCount += 1;
        if (onCallCount === onFailure) {
          throw new Error("synthetic Daily listener registration failure");
        }
        const eventListeners = listeners.get(event) ?? new Set<Listener>();
        eventListeners.add(listener);
        listeners.set(event, eventListeners);
        return call;
      }),
      off: vi.fn((event: string, listener: Listener) => {
        listeners.get(event)?.delete(listener);
        return call;
      }),
      participants: vi.fn(() => ({
        local: { tracks: { audio: { persistentTrack: localTrack } } },
      })),
      setLocalAudio: vi.fn((enabled: boolean) => {
        localTrack.enabled = enabled;
      }),
      localAudio: vi.fn(() => localTrack.enabled),
      leave: vi.fn(async (): Promise<void> => undefined),
      destroy: vi.fn(async () => {
        await destroyBarrier;
        await call.leave();
        localTrack.stop();
        destroyed = true;
        if (activeCall === call) activeCall = undefined;
        if (rejectDestroy) {
          throw new Error("synthetic Daily destroy rejection");
        }
      }),
      isDestroyed: vi.fn(() => destroyed),
      blockDestroy: () => {
        destroyBarrier = new Promise<void>((resolve) => {
          releaseDestroyBarrier = resolve;
        });
        return () => releaseDestroyBarrier?.();
      },
    };
    return call;
  }

  const createdCalls: ReturnType<typeof buildCall>[] = [];
  const createCallObject = vi.fn(() => {
    const call = buildCall();
    activeCall = call;
    createdCalls.push(call);
    return call;
  });
  const getCallInstance = vi.fn((_callClientId?: string) => activeCall);

  return {
    createdCalls,
    createCallObject,
    getCallInstance,
    activeCall: () => activeCall,
    failNextOnRegistration: (registrationNumber: number) => {
      nextOnFailure = registrationNumber;
    },
    rejectNextDestroy: () => {
      nextDestroyRejection = true;
    },
    reset: () => {
      activeCall = undefined;
      nextCallId = 1;
      nextOnFailure = undefined;
      nextDestroyRejection = false;
      createdCalls.splice(0);
      createCallObject.mockClear();
      getCallInstance.mockClear();
    },
  };
});

vi.mock("@daily-co/daily-js", () => {
  return {
    default: {
      createCallObject: dailySdk.createCallObject,
      getCallInstance: dailySdk.getCallInstance,
    },
  };
});

import {
  REQUIRED_VOICE_READY_COMPONENTS,
  type EventOf,
  type VoiceEvent,
} from "./events";
import {
  PIPECAT_EVENT_PROTOCOL,
  PIPECAT_VOICE_RUNTIME,
  PipecatVoiceTransport,
  type PipecatVoiceEventScope,
  type PipecatVoiceTransportConnection,
} from "./pipecat-transport";

class FakeMediaStream {
  constructor(readonly tracks: readonly MediaStreamTrack[]) {}

  getTracks(): readonly MediaStreamTrack[] {
    return this.tracks;
  }
}

function createTrack(id: string, enabled = true): MediaStreamTrack {
  let readyState: MediaStreamTrackState = "live";
  const track = {
    kind: "audio",
    id,
    label: id,
    enabled,
    muted: false,
    get readyState() {
      return readyState;
    },
    stop: vi.fn(() => {
      readyState = "ended";
    }),
  };
  return track as unknown as MediaStreamTrack;
}

class FakeClient {
  readonly connect = vi.fn(
    async (_params?: unknown): Promise<void> => undefined,
  );
  readonly disconnect = vi.fn(async (): Promise<void> => undefined);
  readonly stopReconnectAttempts = vi.fn();
  readonly enableMic = vi.fn((enabled: boolean) => {
    this.localTrack.enabled = enabled;
  });
  localTrack = createTrack("local-microphone");

  constructor(readonly callbacks: RTVIEventCallbacks) {}

  tracks(): Tracks {
    return { local: { audio: this.localTrack } };
  }

  emitConnected(): void {
    this.callbacks.onTrackStarted?.(this.localTrack, {
      id: "browser",
      name: "browser",
      local: true,
    });
    this.callbacks.onConnected?.();
  }

  emitServerMessage(input: unknown): void {
    this.callbacks.onServerMessage?.(input);
  }

  emitTrackStarted(track: MediaStreamTrack, participant?: Participant): void {
    this.callbacks.onTrackStarted?.(track, participant);
  }

  emitTrackStopped(track: MediaStreamTrack, participant?: Participant): void {
    this.callbacks.onTrackStopped?.(track, participant);
  }

  replaceLocalTrack(track: MediaStreamTrack): void {
    this.localTrack = track;
    this.emitTrackStarted(track, {
      id: "browser",
      name: "browser",
      local: true,
    });
  }
}

const scope: PipecatVoiceEventScope = {
  traceId: "trace-1",
  voiceCallId: "voice-call-1",
  sessionId: "session-1",
  profileId: "cascade-v1",
};

const connection: PipecatVoiceTransportConnection = {
  runtime: PIPECAT_VOICE_RUNTIME,
  eventProtocol: PIPECAT_EVENT_PROTOCOL,
  webrtcUrl: "https://voice.example.test/signal/opaque-token",
  peerReservationId: "reservation-1",
  eventScope: scope,
  iceServers: [
    {
      urls: ["stun:stun.example.test:3478"],
    },
  ],
};

function wireEvent(
  eventType: VoiceEvent["event_type"],
  payload: Readonly<Record<string, unknown>>,
  overrides: Readonly<Record<string, unknown>> = {},
): unknown {
  return {
    schema_version: 1,
    event_id: `event-${String(overrides.producer_sequence ?? 1)}`,
    event_type: eventType,
    trace_id: scope.traceId,
    voice_call_id: scope.voiceCallId,
    session_id: scope.sessionId,
    producer_id: "pipecat-worker-1",
    producer_sequence: 1,
    emitted_at: "2026-08-12T12:00:00Z",
    payload,
    ...overrides,
  };
}

function readyEvent(
  overrides: Readonly<Record<string, unknown>> = {},
): unknown {
  return wireEvent(
    "agent_ready",
    {
      profile_id: scope.profileId,
      required_components: REQUIRED_VOICE_READY_COMPONENTS,
      ready_components: REQUIRED_VOICE_READY_COMPONENTS,
    },
    overrides,
  );
}

function createCallbacks() {
  let current = true;
  return {
    callbacks: {
      isCurrent: vi.fn(() => current),
      onConnected: vi.fn(),
      onDisconnected: vi.fn(),
      onAgentDisconnected: vi.fn(),
      onTransportStateChanged: vi.fn(),
      onEvent: vi.fn<(event: VoiceEvent) => void>(),
      onInvalidEvent: vi.fn(),
      onTransportError: vi.fn(),
      onMicrophoneUnavailable: vi.fn(),
      onAudioPlaybackBlockedChange: vi.fn(),
      onLocalMicrophoneTrack: vi.fn(),
      onRemoteAudioTrack: vi.fn(),
    },
    makeStale: () => {
      current = false;
    },
  };
}

function createAudioElement() {
  const element = document.createElement("audio");
  Object.defineProperties(element, {
    play: { configurable: true, value: vi.fn(async () => undefined) },
    pause: { configurable: true, value: vi.fn() },
    srcObject: { configurable: true, writable: true, value: null },
  });
  return element;
}

function createHarness(options: { disconnectTimeoutMs?: number } = {}) {
  const owner = createCallbacks();
  let client!: FakeClient;
  const audioElement = createAudioElement();
  const transport = new PipecatVoiceTransport({
    callbacks: owner.callbacks,
    clientFactory: (callbacks) => {
      client = new FakeClient(callbacks);
      return client;
    },
    audioElementFactory: () => audioElement,
    now: () => 42,
    disconnectTimeoutMs: options.disconnectTimeoutMs,
  });
  return { ...owner, transport, client, audioElement };
}

describe("PipecatVoiceTransport", () => {
  beforeEach(() => {
    dailySdk.reset();
    vi.stubGlobal("MediaStream", FakeMediaStream);
    document.body.replaceChildren();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("connects only the opaque assignment and keeps the local track disabled", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });

    harness.transport.primeAudioPlayback();
    await harness.transport.connect(connection);

    expect(harness.client.connect).toHaveBeenCalledWith({
      webrtcRequestParams: {
        endpoint: connection.webrtcUrl,
      },
      iceConfig: {
        iceServers: [{ urls: ["stun:stun.example.test:3478"] }],
      },
    });
    const sentParams = harness.client.connect.mock.calls[0]?.[0] as {
      iceConfig: { iceServers: RTCIceServer[] };
    };
    expect(sentParams.iceConfig.iceServers).not.toBe(connection.iceServers);
    expect(sentParams.iceConfig.iceServers[0]?.urls).not.toBe(
      connection.iceServers?.[0]?.urls,
    );
    expect(
      harness.client.enableMic.mock.calls.every(([enabled]) => !enabled),
    ).toBe(true);
    expect(harness.client.localTrack.enabled).toBe(false);
    expect(harness.callbacks.onConnected).toHaveBeenCalledTimes(1);
    expect(harness.audioElement.dataset.murmurVoiceCall).toBe(
      scope.voiceCallId,
    );
    expect(harness.audioElement.isConnected).toBe(true);

    await harness.transport.disconnect();
  });

  it("owns an immutable connection and event-scope snapshot", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    const mutableScope = { ...scope };
    const mutableIceUrls = ["stun:stun.example.test:3478"];
    const mutableConnection = {
      runtime: PIPECAT_VOICE_RUNTIME,
      eventProtocol: PIPECAT_EVENT_PROTOCOL,
      webrtcUrl: "https://voice.example.test/signal/opaque-token",
      peerReservationId: "reservation-1",
      eventScope: mutableScope,
      iceServers: [{ urls: mutableIceUrls }],
    } satisfies PipecatVoiceTransportConnection;

    await harness.transport.connect(mutableConnection);
    const sentParams = harness.client.connect.mock.calls[0]?.[0] as {
      webrtcRequestParams: { endpoint: string };
      iceConfig: { iceServers: RTCIceServer[] };
    };
    mutableConnection.webrtcUrl = "https://mutated.example.test/signal/other";
    mutableConnection.peerReservationId = "mutated-reservation";
    mutableScope.traceId = "mutated-trace";
    mutableScope.voiceCallId = "mutated-call";
    mutableScope.sessionId = "mutated-session";
    mutableScope.profileId = "mutated-profile";
    mutableIceUrls[0] = "stun:mutated.example.test:3478";

    const ownedConnection: unknown = Reflect.get(
      harness.transport,
      "connection",
    );
    if (typeof ownedConnection !== "object" || ownedConnection === null) {
      throw new Error("Expected an owned connection snapshot");
    }
    const ownedScope: unknown = Reflect.get(ownedConnection, "eventScope");
    const ownedIceServers: unknown = Reflect.get(ownedConnection, "iceServers");
    if (typeof ownedScope !== "object" || ownedScope === null) {
      throw new Error("Expected an owned event-scope snapshot");
    }
    if (!Array.isArray(ownedIceServers)) {
      throw new Error("Expected an owned ICE-server snapshot");
    }
    const ownedIceServer: unknown = ownedIceServers[0];
    if (typeof ownedIceServer !== "object" || ownedIceServer === null) {
      throw new Error("Expected an owned ICE server");
    }
    const ownedIceUrls: unknown = Reflect.get(ownedIceServer, "urls");
    if (!Array.isArray(ownedIceUrls)) {
      throw new Error("Expected owned ICE URLs");
    }

    expect(Object.isFrozen(ownedConnection)).toBe(true);
    expect(Object.isFrozen(ownedScope)).toBe(true);
    expect(Object.isFrozen(ownedIceServers)).toBe(true);
    expect(Object.isFrozen(ownedIceServer)).toBe(true);
    expect(Object.isFrozen(ownedIceUrls)).toBe(true);
    expect(Reflect.get(ownedConnection, "webrtcUrl")).toBe(
      connection.webrtcUrl,
    );
    expect(Reflect.get(ownedScope, "traceId")).toBe(scope.traceId);
    expect(ownedIceUrls).toEqual(["stun:stun.example.test:3478"]);
    expect(sentParams).toEqual({
      webrtcRequestParams: { endpoint: connection.webrtcUrl },
      iceConfig: {
        iceServers: [{ urls: ["stun:stun.example.test:3478"] }],
      },
    });

    harness.client.emitServerMessage(readyEvent());
    expect(harness.callbacks.onEvent).toHaveBeenCalledTimes(1);
    harness.client.emitServerMessage(
      readyEvent({
        event_id: "caller-mutated-scope",
        trace_id: mutableScope.traceId,
        voice_call_id: mutableScope.voiceCallId,
        session_id: mutableScope.sessionId,
      }),
    );
    expect(harness.callbacks.onInvalidEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ code: "event_scope_mismatch" }),
    );

    await harness.transport.disconnect();
  });

  it("delivers only strict scoped envelopes and activates once for the accepted Ready", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);

    harness.client.emitServerMessage({ event_type: "agent_ready" });
    harness.client.emitServerMessage(
      readyEvent({ trace_id: "another-trace", event_id: "scope-mismatch" }),
    );
    expect(harness.callbacks.onInvalidEvent).toHaveBeenCalledTimes(2);
    expect(harness.callbacks.onEvent).not.toHaveBeenCalled();

    const readyWireValue = readyEvent();
    harness.client.emitServerMessage(readyWireValue);
    harness.client.emitServerMessage(structuredClone(readyWireValue));

    expect(harness.callbacks.onEvent).toHaveBeenCalledTimes(1);
    const accepted = harness.callbacks.onEvent.mock.calls[0]?.[0];
    expect(accepted?.event_type).toBe("agent_ready");
    expect(Object.isFrozen(accepted)).toBe(true);
    expect(Object.isFrozen(accepted?.payload)).toBe(true);
    if (!accepted || accepted.event_type !== "agent_ready") {
      throw new Error("Expected strict Ready envelope");
    }

    await expect(
      harness.transport.acceptCanonicalReady(
        structuredClone(accepted) as EventOf<"agent_ready">,
      ),
    ).rejects.toThrow("this transport's canonical Ready");

    const firstActivation = harness.transport.acceptCanonicalReady(accepted);
    const duplicateActivation =
      harness.transport.acceptCanonicalReady(accepted);
    expect(duplicateActivation).toBe(firstActivation);
    await firstActivation;
    expect(
      harness.client.enableMic.mock.calls.filter(([enabled]) => enabled),
    ).toHaveLength(1);
    expect(harness.client.localTrack.enabled).toBe(true);

    harness.client.emitServerMessage(
      readyEvent({ event_id: "second-ready", producer_sequence: 2 }),
    );
    expect(harness.callbacks.onEvent).toHaveBeenCalledTimes(1);
    expect(harness.callbacks.onInvalidEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ code: "conflicting_agent_ready" }),
    );

    await harness.transport.setMicrophoneEnabled(false);
    expect(harness.client.localTrack.enabled).toBe(false);
    await harness.transport.disconnect();
  });

  it("preserves the requested microphone state when the authorized track is replaced", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    harness.client.emitServerMessage(readyEvent());
    const accepted = harness.callbacks.onEvent.mock.calls[0]?.[0];
    if (!accepted || accepted.event_type !== "agent_ready") {
      throw new Error("Expected strict Ready envelope");
    }
    await harness.transport.acceptCanonicalReady(accepted);
    const originalTrack = harness.client.localTrack;
    expect(originalTrack.enabled).toBe(true);

    const activeReplacement = createTrack("active-replacement", false);
    harness.client.replaceLocalTrack(activeReplacement);
    expect(originalTrack.enabled).toBe(false);
    expect(activeReplacement.enabled).toBe(true);

    await harness.transport.setMicrophoneEnabled(false);
    expect(originalTrack.enabled).toBe(false);
    expect(activeReplacement.enabled).toBe(false);

    const mutedReplacement = createTrack("muted-replacement", true);
    harness.client.replaceLocalTrack(mutedReplacement);
    expect(activeReplacement.enabled).toBe(false);
    expect(mutedReplacement.enabled).toBe(false);

    await harness.transport.setMicrophoneEnabled(true);
    expect(activeReplacement.enabled).toBe(false);
    expect(mutedReplacement.enabled).toBe(true);
    const enabledReplacement = createTrack("enabled-replacement", false);
    harness.client.replaceLocalTrack(enabledReplacement);
    expect(mutedReplacement.enabled).toBe(false);
    expect(enabledReplacement.enabled).toBe(true);
    harness.client.emitTrackStopped(enabledReplacement, {
      id: "browser",
      name: "browser",
      local: true,
    });
    expect(enabledReplacement.enabled).toBe(false);

    await harness.transport.disconnect();
  });

  it("fail-closes a stale local track before returning from its start callback", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    harness.makeStale();
    const staleLocalTrack = createTrack("stale-local", true);
    const localDiagnosticsBeforeStart =
      harness.callbacks.onLocalMicrophoneTrack.mock.calls.length;

    harness.client.emitTrackStarted(staleLocalTrack, {
      id: "browser",
      name: "browser",
      local: true,
    });

    expect(staleLocalTrack.enabled).toBe(false);
    expect(harness.callbacks.onLocalMicrophoneTrack).toHaveBeenCalledTimes(
      localDiagnosticsBeforeStart,
    );
    await harness.transport.disconnect();
  });

  it("cannot activate a stale generation while connection is still settling", async () => {
    const harness = createHarness();
    let resolveConnection!: () => void;
    harness.client.connect.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveConnection = resolve;
          harness.client.emitConnected();
        }),
    );

    const connecting = harness.transport.connect(connection);
    harness.client.emitServerMessage(readyEvent());
    const accepted = harness.callbacks.onEvent.mock.calls[0]?.[0];
    if (!accepted || accepted.event_type !== "agent_ready") {
      throw new Error("Expected strict Ready envelope");
    }
    const activation = harness.transport.acceptCanonicalReady(accepted);

    harness.makeStale();
    harness.client.emitServerMessage(
      wireEvent("session_started", {}, { event_id: "stale-event" }),
    );
    resolveConnection();

    await expect(connecting).rejects.toThrow("stale during connection");
    await expect(activation).rejects.toThrow("stale during connection");
    expect(
      harness.client.enableMic.mock.calls.filter(([enabled]) => enabled),
    ).toHaveLength(0);
    expect(harness.callbacks.onEvent).toHaveBeenCalledTimes(1);
  });

  it("owns remote playback and exposes immutable local and remote diagnostics", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    const remoteTrack = createTrack("remote-audio");

    harness.client.emitTrackStarted(remoteTrack);

    const stream = harness.audioElement.srcObject as unknown as FakeMediaStream;
    expect(stream.getTracks()).toEqual([remoteTrack]);
    expect(harness.audioElement.muted).toBe(false);
    expect(harness.callbacks.onRemoteAudioTrack).toHaveBeenCalledWith(
      remoteTrack,
      expect.objectContaining({
        direction: "remote",
        trackId: "remote-audio",
        observedAtMs: 42,
      }),
    );
    const remoteObservation =
      harness.callbacks.onRemoteAudioTrack.mock.calls.at(-1)?.[1];
    expect(Object.isFrozen(remoteObservation)).toBe(true);
    const localObservation =
      harness.callbacks.onLocalMicrophoneTrack.mock.calls.at(-1)?.[1];
    expect(localObservation).toMatchObject({
      direction: "local",
      trackId: "local-microphone",
      enabled: false,
    });
    expect(Object.isFrozen(localObservation)).toBe(true);

    harness.transport.setOutputEnabled(false);
    expect(harness.audioElement.muted).toBe(true);
    harness.transport.setTtsEnabled(true);
    expect(harness.audioElement.muted).toBe(false);

    harness.client.emitTrackStopped(remoteTrack);
    expect(harness.audioElement.srcObject).toBeNull();
    expect(harness.audioElement.pause).toHaveBeenCalled();
    await harness.transport.disconnect();
    expect(remoteTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.client.localTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.callbacks.onLocalMicrophoneTrack).toHaveBeenLastCalledWith(
      null,
      null,
    );
    expect(harness.callbacks.onRemoteAudioTrack).toHaveBeenLastCalledWith(
      null,
      null,
    );
    expect(harness.audioElement.isConnected).toBe(false);
  });

  it("does not let a superseded remote track stop clear current playback", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    const firstRemoteTrack = createTrack("remote-first");
    const currentRemoteTrack = createTrack("remote-current");

    harness.client.emitTrackStarted(firstRemoteTrack);
    harness.client.emitTrackStarted(currentRemoteTrack);
    const diagnosticCallsBeforeStaleStop =
      harness.callbacks.onRemoteAudioTrack.mock.calls.length;
    harness.client.emitTrackStopped(firstRemoteTrack);

    const stream = harness.audioElement.srcObject as unknown as FakeMediaStream;
    expect(stream.getTracks()).toEqual([currentRemoteTrack]);
    expect(harness.audioElement.pause).not.toHaveBeenCalled();
    expect(harness.callbacks.onRemoteAudioTrack).toHaveBeenCalledTimes(
      diagnosticCallsBeforeStaleStop,
    );

    harness.client.emitTrackStopped(currentRemoteTrack);
    expect(harness.audioElement.srcObject).toBeNull();
    expect(harness.audioElement.pause).toHaveBeenCalledTimes(1);
    await harness.transport.disconnect();
  });

  it("latches teardown before isolated diagnostic callbacks run", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    const remoteTrack = createTrack("remote-audio");
    harness.client.emitTrackStarted(remoteTrack);
    let reentrantDisconnect: Promise<void> | null = null;
    let reconnectWasLatchedBeforeCallback = false;
    harness.callbacks.onLocalMicrophoneTrack.mockImplementation((track) => {
      if (track) return;
      reconnectWasLatchedBeforeCallback =
        harness.client.stopReconnectAttempts.mock.calls.length === 1;
      reentrantDisconnect = harness.transport.disconnect();
      throw new Error("local diagnostic failed");
    });
    harness.callbacks.onRemoteAudioTrack.mockImplementation((track) => {
      if (!track) throw new Error("remote diagnostic failed");
    });

    const disconnecting = harness.transport.disconnect();
    expect(reentrantDisconnect).toBe(disconnecting);
    expect(reconnectWasLatchedBeforeCallback).toBe(true);
    await expect(disconnecting).resolves.toBeUndefined();
    expect(harness.client.disconnect).toHaveBeenCalledTimes(1);
    expect(remoteTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.client.localTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.audioElement.isConnected).toBe(false);
    expect(harness.callbacks.onRemoteAudioTrack).toHaveBeenLastCalledWith(
      null,
      null,
    );
  });

  it("suppresses teardown diagnostics after its generation becomes stale", async () => {
    const harness = createHarness();
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    await harness.transport.connect(connection);
    const remoteTrack = createTrack("remote-audio");
    harness.client.emitTrackStarted(remoteTrack);
    harness.makeStale();

    await harness.transport.disconnect();

    expect(harness.callbacks.onLocalMicrophoneTrack).not.toHaveBeenCalledWith(
      null,
      null,
    );
    expect(harness.callbacks.onRemoteAudioTrack).not.toHaveBeenCalledWith(
      null,
      null,
    );
    expect(remoteTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.client.localTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.audioElement.isConnected).toBe(false);
  });

  it("does not rely on Daily destroy to remove application listeners", async () => {
    const call = dailySdk.createCallObject();
    const listener = vi.fn();
    call.on("track-started", listener);

    await call.destroy();

    expect(call.isDestroyed()).toBe(true);
    expect(call.listeners.get("track-started")?.has(listener)).toBe(true);
  });

  it("fully releases two sequential default Daily media owners", async () => {
    const firstOwner = createCallbacks();
    const firstTransport = new PipecatVoiceTransport({
      callbacks: firstOwner.callbacks,
      audioElementFactory: createAudioElement,
    });
    const firstCall = dailySdk.createdCalls[0];
    if (!firstCall) throw new Error("Expected the first owned Daily call");
    expect(
      [...firstCall.listeners.values()].reduce(
        (total, listeners) => total + listeners.size,
        0,
      ),
    ).toBe(6);
    const firstClient: unknown = Reflect.get(firstTransport, "client");
    if (typeof firstClient !== "object" || firstClient === null) {
      throw new Error("Expected the default Pipecat client");
    }
    const firstSdkTransport: unknown = Reflect.get(firstClient, "_transport");
    if (typeof firstSdkTransport !== "object" || firstSdkTransport === null) {
      throw new Error("Expected the pinned SmallWebRTC transport");
    }
    const replaceTrack = vi.fn(async (_track: MediaStreamTrack) => undefined);
    expect(
      Reflect.set(firstSdkTransport, "pc", {
        getTransceivers: () => [{ sender: { replaceTrack } }],
      }),
    ).toBe(true);
    const pinnedTrackStartedListener = firstCall.listeners
      .get("track-started")
      ?.values()
      .next().value;
    if (!pinnedTrackStartedListener) {
      throw new Error("Expected Daily's pinned track-started listener");
    }
    const replacementTrack = createTrack("daily-replacement");
    await pinnedTrackStartedListener({
      type: "audio",
      track: replacementTrack,
      participant: { local: true, user_id: "browser", user_name: "browser" },
    });
    expect(replaceTrack).toHaveBeenCalledWith(replacementTrack);
    expect(Reflect.set(firstSdkTransport, "pc", null)).toBe(true);
    expect(
      () =>
        new PipecatVoiceTransport({
          callbacks: createCallbacks().callbacks,
          audioElementFactory: createAudioElement,
        }),
    ).toThrow("cannot share an existing Daily call instance");

    await firstTransport.disconnect();

    expect(firstCall.destroy).toHaveBeenCalledTimes(1);
    expect(firstCall.off).toHaveBeenCalledTimes(6);
    expect(firstCall.off.mock.calls.map(([eventName]) => eventName)).toEqual([
      "track-started",
      "track-stopped",
      "available-devices-updated",
      "selected-devices-updated",
      "camera-error",
      "local-audio-level",
    ]);
    expect(Math.max(...firstCall.off.mock.invocationCallOrder)).toBeLessThan(
      firstCall.destroy.mock.invocationCallOrder[0] ?? 0,
    );
    expect(firstCall.isDestroyed()).toBe(true);
    expect(firstCall.localTrack.readyState).toBe("ended");
    expect(
      [...firstCall.listeners.values()].every(
        (listeners) => listeners.size === 0,
      ),
    ).toBe(true);
    expect(dailySdk.activeCall()).toBeUndefined();

    const secondOwner = createCallbacks();
    const secondTransport = new PipecatVoiceTransport({
      callbacks: secondOwner.callbacks,
      audioElementFactory: createAudioElement,
    });
    const secondCall = dailySdk.createdCalls[1];
    if (!secondCall) throw new Error("Expected the second owned Daily call");
    expect(secondCall).not.toBe(firstCall);
    expect(
      [...secondCall.listeners.values()].reduce(
        (total, listeners) => total + listeners.size,
        0,
      ),
    ).toBe(6);

    await secondTransport.disconnect();

    expect(secondCall.destroy).toHaveBeenCalledTimes(1);
    expect(secondCall.off).toHaveBeenCalledTimes(6);
    expect(secondCall.isDestroyed()).toBe(true);
    expect(secondCall.localTrack.readyState).toBe("ended");
    expect(
      [...secondCall.listeners.values()].every(
        (listeners) => listeners.size === 0,
      ),
    ).toBe(true);
    expect(dailySdk.activeCall()).toBeUndefined();
    expect(dailySdk.createdCalls).toHaveLength(2);
  });

  it("rolls back partial Daily listener construction without leaking destroy rejection", async () => {
    dailySdk.failNextOnRegistration(3);
    dailySdk.rejectNextDestroy();
    const unhandledRejection = vi.fn();
    window.addEventListener("unhandledrejection", unhandledRejection);

    try {
      expect(
        () =>
          new PipecatVoiceTransport({
            callbacks: createCallbacks().callbacks,
            audioElementFactory: createAudioElement,
          }),
      ).toThrow("synthetic Daily listener registration failure");

      const failedCall = dailySdk.createdCalls[0];
      if (!failedCall) throw new Error("Expected the failed Daily call");
      await vi.waitFor(() => expect(dailySdk.activeCall()).toBeUndefined());
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      expect(failedCall.off).toHaveBeenCalledTimes(3);
      expect(
        [...failedCall.listeners.values()].every(
          (listeners) => listeners.size === 0,
        ),
      ).toBe(true);
      expect(unhandledRejection).not.toHaveBeenCalled();

      const nextOwner = new PipecatVoiceTransport({
        callbacks: createCallbacks().callbacks,
        audioElementFactory: createAudioElement,
      });
      await nextOwner.disconnect();
    } finally {
      window.removeEventListener("unhandledrejection", unhandledRejection);
    }
  });

  it("rolls back all listeners when restoring Daily's listener API fails", async () => {
    const originalReflectSet = Reflect.set;
    let onAssignments = 0;
    const reflectSet = vi
      .spyOn(Reflect, "set")
      .mockImplementation((target, propertyKey, value, receiver) => {
        if (propertyKey === "on") {
          onAssignments += 1;
          if (onAssignments === 2) {
            throw new Error("synthetic Daily listener restore failure");
          }
        }
        return receiver === undefined
          ? originalReflectSet(target, propertyKey, value)
          : originalReflectSet(target, propertyKey, value, receiver);
      });

    try {
      expect(
        () =>
          new PipecatVoiceTransport({
            callbacks: createCallbacks().callbacks,
            audioElementFactory: createAudioElement,
          }),
      ).toThrow("synthetic Daily listener restore failure");
    } finally {
      reflectSet.mockRestore();
    }

    const failedCall = dailySdk.createdCalls[0];
    if (!failedCall) throw new Error("Expected the failed Daily call");
    await vi.waitFor(() => expect(dailySdk.activeCall()).toBeUndefined());
    expect(failedCall.off).toHaveBeenCalledTimes(6);
    expect(
      [...failedCall.listeners.values()].every(
        (listeners) => listeners.size === 0,
      ),
    ).toBe(true);

    const nextOwner = new PipecatVoiceTransport({
      callbacks: createCallbacks().callbacks,
      audioElementFactory: createAudioElement,
    });
    await nextOwner.disconnect();
  });

  it("contains rejected SDK cleanup during a coalesced connect failure", async () => {
    dailySdk.rejectNextDestroy();
    const unhandledRejection = vi.fn();
    window.addEventListener("unhandledrejection", unhandledRejection);
    const parentDisconnect = vi
      .spyOn(SmallWebRTCTransport.prototype, "disconnect")
      .mockRejectedValueOnce(
        new Error("synthetic SmallWebRTC disconnect failure"),
      );
    try {
      const owner = createCallbacks();
      const transport = new PipecatVoiceTransport({
        callbacks: owner.callbacks,
        audioElementFactory: createAudioElement,
      });
      const dailyCall = dailySdk.createdCalls[0];
      if (!dailyCall) throw new Error("Expected an owned Daily call");
      const client: unknown = Reflect.get(transport, "client");
      if (typeof client !== "object" || client === null) {
        throw new Error("Expected the default Pipecat client");
      }
      const sdkTransport: unknown = Reflect.get(client, "_transport");
      if (typeof sdkTransport !== "object" || sdkTransport === null) {
        throw new Error("Expected the pinned SmallWebRTC transport");
      }
      const originalDisconnect: unknown = Reflect.get(
        sdkTransport,
        "disconnect",
      );
      if (typeof originalDisconnect !== "function") {
        throw new Error("Expected the pinned transport disconnect entry point");
      }
      const disconnectSpy = vi.fn((...arguments_: unknown[]): unknown =>
        Reflect.apply(originalDisconnect, sdkTransport, arguments_),
      );
      expect(Reflect.set(sdkTransport, "disconnect", disconnectSpy)).toBe(true);
      expect(
        Reflect.set(
          sdkTransport,
          "_connect",
          vi.fn(async () => {
            throw new Error("synthetic SmallWebRTC connect failure");
          }),
        ),
      ).toBe(true);
      const releaseDestroy = dailyCall.blockDestroy();
      let connectSettled = false;

      const connecting = transport.connect(connection);
      void connecting.then(
        () => {
          connectSettled = true;
        },
        () => {
          connectSettled = true;
        },
      );
      await vi.waitFor(() => {
        expect(dailyCall.destroy).toHaveBeenCalledTimes(1);
        expect(disconnectSpy).toHaveBeenCalledTimes(2);
      });

      expect(connectSettled).toBe(false);
      expect(disconnectSpy.mock.results[0]?.value).toBe(
        disconnectSpy.mock.results[1]?.value,
      );
      releaseDestroy();
      await expect(connecting).rejects.toThrow(
        "synthetic SmallWebRTC connect failure",
      );
      expect(dailyCall.destroy).toHaveBeenCalledTimes(1);
      expect(dailyCall.off).toHaveBeenCalledTimes(6);
      expect(dailyCall.isDestroyed()).toBe(true);
      expect(dailyCall.localTrack.readyState).toBe("ended");
      expect(
        [...dailyCall.listeners.values()].every(
          (listeners) => listeners.size === 0,
        ),
      ).toBe(true);
      expect(dailySdk.activeCall()).toBeUndefined();
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      expect(unhandledRejection).not.toHaveBeenCalled();

      const nextOwner = new PipecatVoiceTransport({
        callbacks: createCallbacks().callbacks,
        audioElementFactory: createAudioElement,
      });
      await nextOwner.disconnect();
    } finally {
      parentDisconnect.mockRestore();
      window.removeEventListener("unhandledrejection", unhandledRejection);
    }
  });

  it("makes the pinned SmallWebRTC reconnect entry point inert after disconnect", async () => {
    const owner = createCallbacks();
    const peerConnectionConstructor = vi.fn();
    vi.stubGlobal("RTCPeerConnection", peerConnectionConstructor);
    const transport = new PipecatVoiceTransport({
      callbacks: owner.callbacks,
      audioElementFactory: createAudioElement,
    });
    const client = Reflect.get(transport, "client");
    if (typeof client !== "object" || client === null) {
      throw new Error("Expected the default Pipecat client");
    }
    const sdkTransport = Reflect.get(client, "_transport");
    if (typeof sdkTransport !== "object" || sdkTransport === null) {
      throw new Error("Expected the pinned SmallWebRTC transport");
    }
    const attemptReconnection = Reflect.get(
      sdkTransport,
      "attemptReconnection",
    );
    if (typeof attemptReconnection !== "function") {
      throw new Error("Expected the pinned SmallWebRTC reconnect entry point");
    }

    await transport.disconnect();
    await Reflect.apply(attemptReconnection, sdkTransport, [true]);

    expect(peerConnectionConstructor).not.toHaveBeenCalled();
  });

  it("bounds and coalesces disconnect even when the SDK never settles", async () => {
    vi.useFakeTimers();
    const harness = createHarness({ disconnectTimeoutMs: 25 });
    harness.client.connect.mockImplementationOnce(async () => {
      harness.client.emitConnected();
    });
    harness.client.disconnect.mockImplementationOnce(
      () => new Promise<void>(() => undefined),
    );
    await harness.transport.connect(connection);

    const first = harness.transport.disconnect();
    const second = harness.transport.disconnect();
    expect(second).toBe(first);
    expect(harness.client.stopReconnectAttempts).toHaveBeenCalledTimes(1);
    expect(harness.client.localTrack.enabled).toBe(false);
    expect(harness.client.localTrack.stop).toHaveBeenCalledTimes(1);
    expect(harness.audioElement.isConnected).toBe(false);

    await Promise.resolve();
    expect(harness.client.disconnect).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(25);
    await first;
  });

  it("rejects unsafe signaling locators before invoking the SDK", async () => {
    const harness = createHarness();

    await expect(
      harness.transport.connect({
        ...connection,
        webrtcUrl:
          "https://voice.example.test/signal/opaque-token?authorization=secret",
      }),
    ).rejects.toThrow("unsafe WebRTC locator");
    expect(harness.client.connect).not.toHaveBeenCalled();
    await harness.transport.disconnect();
  });
});
