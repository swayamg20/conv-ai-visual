/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const livekit = vi.hoisted(() => {
  type Listener = (...args: unknown[]) => void;

  class FakeRoom {
    static instances: FakeRoom[] = [];
    static connectError: Error | null = null;

    readonly listeners = new Map<string, Set<Listener>>();
    readonly localParticipant = {
      setMicrophoneEnabled: vi.fn(async () => undefined),
    };
    readonly connect = vi.fn(async () => {
      if (FakeRoom.connectError) throw FakeRoom.connectError;
    });
    readonly disconnect = vi.fn(async () => undefined);
    readonly startAudio = vi.fn(async () => undefined);
    canPlaybackAudio = true;

    constructor() {
      FakeRoom.instances.push(this);
    }

    on(event: string, listener: Listener) {
      const listeners = this.listeners.get(event) ?? new Set<Listener>();
      listeners.add(listener);
      this.listeners.set(event, listeners);
      return this;
    }

    off(event: string, listener: Listener) {
      this.listeners.get(event)?.delete(listener);
      return this;
    }

    emit(event: string, ...args: unknown[]) {
      for (const listener of this.listeners.get(event) ?? []) {
        listener(...args);
      }
    }

    firstListener(event: string): Listener | undefined {
      return this.listeners.get(event)?.values().next().value;
    }
  }

  return {
    FakeRoom,
    events: {
      Connected: "connected",
      Reconnecting: "reconnecting",
      Reconnected: "reconnected",
      Disconnected: "disconnected",
      DataReceived: "dataReceived",
      TrackSubscribed: "trackSubscribed",
      TrackUnsubscribed: "trackUnsubscribed",
      MediaDevicesError: "mediaDevicesError",
      AudioPlaybackStatusChanged: "audioPlaybackChanged",
    },
  };
});

const api = vi.hoisted(() => {
  class VoiceSessionApiError extends Error {
    readonly status?: number;

    constructor(message: string, status?: number) {
      super(message);
      this.name = "VoiceSessionApiError";
      this.status = status;
    }
  }

  return {
    bootstrapVoiceSession: vi.fn(),
    endVoiceSession: vi.fn<
      (
        request?: unknown,
        options?: { apiUrl?: string; signal?: AbortSignal }
      ) => Promise<void>
    >(async () => undefined),
    VoiceSessionApiError,
  };
});

vi.mock("livekit-client", () => ({
  Room: livekit.FakeRoom,
  RoomEvent: livekit.events,
  DataPacket_Kind: { RELIABLE: 0, LOSSY: 1 },
  isAudioTrack: (track: { kind?: string }) => track.kind === "audio",
}));

vi.mock("@/features/voice/session-api", () => ({
  VOICE_V2_EVENT_TOPIC: "murmur.voice.v2.events",
  bootstrapVoiceSession: api.bootstrapVoiceSession,
  endVoiceSession: api.endVoiceSession,
  VoiceSessionApiError: api.VoiceSessionApiError,
}));

import { REQUIRED_VOICE_READY_COMPONENTS } from "@/features/voice/events";
import {
  classifyVoiceBootstrapFailure,
  useVoiceSession,
  type VoiceSessionTranscriptEvent,
} from "./use-voice-session";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const eventTopic = "murmur.voice.v2.events";
const sessionId = "a4f4328e-185e-4c65-b3f7-101e04a37578";
const agentId = "90bd1253-90a6-459a-bf37-365bc3039a76";
const assignmentTraceId = "025bcf26-dcab-4f8c-bb44-af298875f638";
let producerSequence = 0;

function workerEvent(
  voiceCallId: string,
  eventType: string,
  payload: Record<string, unknown>,
  envelope: Record<string, unknown> = {}
) {
  producerSequence += 1;
  return {
    schema_version: 1,
    event_id: `worker-event-${producerSequence}`,
    event_type: eventType,
    trace_id: assignmentTraceId,
    voice_call_id: voiceCallId,
    session_id: sessionId,
    producer_id: "worker-1",
    producer_sequence: producerSequence,
    emitted_at: "2026-08-12T00:00:00Z",
    payload,
    ...envelope,
  };
}

function dataPayload(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

interface MountedHook {
  readonly root: Root;
  readonly read: () => ReturnType<typeof useVoiceSession>;
}

async function mountHook(callbacks: {
  onTranscript?: (event: VoiceSessionTranscriptEvent) => void;
  onError?: (message: string) => void;
} = {}): Promise<MountedHook> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let current: ReturnType<typeof useVoiceSession> | undefined;

  function Harness() {
    current = useVoiceSession({
      enabled: true,
      agentId,
      sessionId,
      onTranscript: callbacks.onTranscript,
      onError: callbacks.onError,
    });
    return null;
  }

  await act(async () => {
    root.render(<Harness />);
  });

  return {
    root,
    read: () => {
      if (!current) throw new Error("Hook did not render");
      return current;
    },
  };
}

async function connectHook(mounted: MountedHook) {
  await act(async () => {
    await mounted.read().connect({ sessionId });
  });
  const room = livekit.FakeRoom.instances.at(-1);
  if (!room) throw new Error("Room was not created");
  return room;
}

describe("useVoiceSession", () => {
  beforeEach(() => {
    producerSequence = 0;
    livekit.FakeRoom.instances.length = 0;
    livekit.FakeRoom.connectError = null;
    api.bootstrapVoiceSession.mockReset();
    api.endVoiceSession.mockReset().mockResolvedValue(undefined);
    api.bootstrapVoiceSession.mockImplementation(
      async (request: { session_id: string; voice_call_id: string }) => ({
        runtime: "livekit_v2",
        trace_id: assignmentTraceId,
        profile_id: "cascade-v1",
        server_url: "wss://voice.example.test",
        room_name: "room-1",
        participant_token: "signed.jwt.token",
        participant_identity: "user-1",
        agent_participant_identity: "agent-worker-1",
        session_id: request.session_id,
        agent_id: agentId,
        voice_call_id: request.voice_call_id,
        dispatch_id: "dispatch-1",
        worker_name: "murmur-worker",
        event_topic: eventTopic,
        expires_at: "2099-01-01T00:00:00Z",
      })
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it("classifies transient and contract bootstrap errors conservatively", () => {
    expect(
      classifyVoiceBootstrapFailure(
        new api.VoiceSessionApiError("Slow down", 429)
      )
    ).toMatchObject({ canRetry: true, retainCallIdentity: true });
    expect(
      classifyVoiceBootstrapFailure(
        new api.VoiceSessionApiError("Malformed assignment")
      )
    ).toMatchObject({ canRetry: false, retainCallIdentity: false });
    expect(
      classifyVoiceBootstrapFailure(
        new api.VoiceSessionApiError("Forbidden", 403)
      )
    ).toMatchObject({ canRetry: false, retainCallIdentity: false });
    expect(
      classifyVoiceBootstrapFailure({ name: "AbortError" })
    ).toMatchObject({ canRetry: true, retainCallIdentity: true });
    expect(classifyVoiceBootstrapFailure(new TypeError("network"))).toMatchObject({
      canRetry: true,
      retainCallIdentity: true,
    });
  });

  it("keeps transport connectivity separate from ready and follows semantic events", async () => {
    const onTranscript = vi.fn();
    const mounted = await mountHook({ onTranscript });
    const room = await connectHook(mounted);

    expect(mounted.read().phase).toBe("connecting");
    act(() => room.emit(livekit.events.Connected));
    expect(mounted.read().phase).toBe("transport_connected");
    expect(mounted.read().session.voiceReady).toBe(false);

    const agent = { isAgent: true, identity: "agent-worker-1" };
    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(mounted.read().voiceCallId, "agent_ready", {
            profile_id: "cascade-v1",
            required_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
            ready_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
          })
        ),
        agent,
        0,
        eventTopic
      )
    );
    expect(mounted.read().phase).toBe("ready");

    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(mounted.read().voiceCallId, "transcript_segment", {
            segment_id: "segment-1",
            text: "Explain gravity",
            is_final: true,
          })
        ),
        agent,
        0,
        eventTopic
      )
    );
    expect(mounted.read().phase).toBe("listening");
    expect(onTranscript).toHaveBeenCalledWith({
      text: "Explain gravity",
      isFinal: true,
      speechFinal: false,
    });

    const audioElement = document.createElement("audio");
    const audioTrack = {
      kind: "audio",
      isLocal: false,
      setVolume: vi.fn(),
      attach: vi.fn(() => audioElement),
      detach: vi.fn(() => [audioElement]),
    };
    act(() => room.emit(livekit.events.TrackSubscribed, audioTrack, {}, agent));
    expect(audioElement.isConnected).toBe(true);

    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(
            mounted.read().voiceCallId,
            "turn_committed",
            { text: "Explain gravity" },
            { turn_id: "turn-1" }
          )
        ),
        agent,
        0,
        eventTopic
      )
    );
    expect(mounted.read().phase).toBe("thinking");

    act(() => room.emit(livekit.events.Reconnecting));
    expect(mounted.read().phase).toBe("reconnecting");
    await act(async () => mounted.root.unmount());
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);
    expect(audioTrack.detach).toHaveBeenCalledTimes(1);
    expect(audioElement.isConnected).toBe(false);
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: expect.any(String) },
      { apiUrl: undefined }
    );
  });

  it("fails a reconnected call immediately and rotates identity for a fresh retry", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    const originalCallId = mounted.read().voiceCallId;

    act(() => room.emit(livekit.events.Connected));
    act(() => room.emit(livekit.events.Reconnecting));
    expect(mounted.read().phase).toBe("reconnecting");
    act(() => room.emit(livekit.events.Reconnected));

    expect(mounted.read().phase).toBe("unavailable");
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "reconnect_not_supported",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith(
      "Voice transport reconnected, but this runtime cannot safely restore the event stream yet. Start a fresh voice call."
    );
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);
    await act(async () => mounted.root.unmount());
  });

  it("fails closed on an unknown contract and removes stale room callbacks", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    await act(async () => undefined);
    const room = await connectHook(mounted);
    const staleConnected = room.firstListener(livekit.events.Connected);

    act(() => room.emit(livekit.events.Connected));
    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(mounted.read().voiceCallId, "future_event", { unsafe: true })
        ),
        { isAgent: true, identity: "agent-worker-1" },
        0,
        eventTopic
      )
    );

    expect(mounted.read().phase).toBe("unavailable");
    expect(mounted.read().eventState.compatibilityFailure?.code).toBe(
      "unknown_event_type"
    );
    expect(onError).toHaveBeenCalled();
    expect(mounted.read().assignment).toBeNull();
    expect(room.disconnect).toHaveBeenCalledTimes(1);

    act(() => staleConnected?.());
    expect(mounted.read().phase).toBe("unavailable");
    await act(async () => mounted.root.unmount());
    expect(room.disconnect).toHaveBeenCalledTimes(1);
  });

  it("times out genuine readiness and releases the call for a fresh retry", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    const originalCallId = mounted.read().voiceCallId;

    act(() => room.emit(livekit.events.Connected));
    expect(mounted.read().phase).toBe("transport_connected");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(mounted.read().phase).toBe("unavailable");
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "agent_ready_timeout",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith(
      "The voice agent did not become ready in time. Continue in text mode."
    );
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);

    await act(async () => mounted.root.unmount());
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("releases a microphone-unavailable call and permits a fresh retry", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    const originalCallId = mounted.read().voiceCallId;

    act(() =>
      room.emit(
        livekit.events.MediaDevicesError,
        new Error("permission denied"),
        "audioinput"
      )
    );

    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "microphone_unavailable",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith(
      "Microphone unavailable: permission denied"
    );
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);
    await act(async () => mounted.root.unmount());
  });

  it("releases a microphone-control failure and permits a fresh retry", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    const originalCallId = mounted.read().voiceCallId;
    const agent = { isAgent: true, identity: "agent-worker-1" };

    act(() => room.emit(livekit.events.Connected));
    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(originalCallId, "agent_ready", {
            profile_id: "cascade-v1",
            required_components: REQUIRED_VOICE_READY_COMPONENTS,
            ready_components: REQUIRED_VOICE_READY_COMPONENTS,
          })
        ),
        agent,
        0,
        eventTopic
      )
    );
    room.localParticipant.setMicrophoneEnabled.mockRejectedValueOnce(
      new Error("capture track ended")
    );

    await act(async () => mounted.read().toggleMicMute());

    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "microphone_control_failed",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith(
      "Could not mute microphone: capture track ended"
    );
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);
    await act(async () => mounted.root.unmount());
  });

  it("releases a transport-connect failure and permits a fresh retry", async () => {
    livekit.FakeRoom.connectError = new Error("gateway refused connection");
    const onError = vi.fn();
    const mounted = await mountHook({ onError });

    const failedRoom = await connectHook(mounted);
    const originalCallId = api.bootstrapVoiceSession.mock.calls[0]?.[0]
      ?.voice_call_id;
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "transport_connect_failed",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith(
      "Voice transport failed: gateway refused connection"
    );
    expect(failedRoom.disconnect).toHaveBeenCalledTimes(1);
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    livekit.FakeRoom.connectError = null;
    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);
    await act(async () => mounted.root.unmount());
  });

  it("turns an unexpected disconnect into a retryable fresh call", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    const originalCallId = mounted.read().voiceCallId;

    act(() => room.emit(livekit.events.Disconnected));

    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "transport_unavailable",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(onError).toHaveBeenCalledWith("Voice transport disconnected");
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: originalCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().voiceCallId).not.toBe(originalCallId);
    await act(async () => mounted.root.unmount());
  });

  it("rejects packets outside the assigned reliable agent channel", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    act(() => room.emit(livekit.events.Connected));

    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(mounted.read().voiceCallId, "agent_ready", {
            profile_id: "cascade-v1",
            required_components: REQUIRED_VOICE_READY_COMPONENTS,
            ready_components: REQUIRED_VOICE_READY_COMPONENTS,
          })
        ),
        { isAgent: false, identity: "impostor-1" },
        1,
        "unassigned.topic"
      )
    );

    expect(mounted.read().phase).toBe("unavailable");
    expect(onError).toHaveBeenCalledWith(
      "Voice received data outside its authenticated reliable event channel"
    );
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    await act(async () => mounted.root.unmount());
  });

  it("rejects an otherwise valid worker event from another trace", async () => {
    const onError = vi.fn();
    const mounted = await mountHook({ onError });
    const room = await connectHook(mounted);
    act(() => room.emit(livekit.events.Connected));

    act(() =>
      room.emit(
        livekit.events.DataReceived,
        dataPayload(
          workerEvent(
            mounted.read().voiceCallId,
            "agent_ready",
            {
              profile_id: "cascade-v1",
              required_components: REQUIRED_VOICE_READY_COMPONENTS,
              ready_components: REQUIRED_VOICE_READY_COMPONENTS,
            },
            { trace_id: "8e785c3f-7363-49cd-9f67-d7d97f6a826a" }
          )
        ),
        { isAgent: true, identity: "agent-worker-1" },
        0,
        eventTopic
      )
    );

    expect(mounted.read().phase).toBe("unavailable");
    expect(onError).toHaveBeenCalledWith(
      "Voice received an event outside its assigned trace"
    );
    expect(room.disconnect).toHaveBeenCalledTimes(1);
    await act(async () => mounted.root.unmount());
  });

  it("reuses a call ID for retryable bootstrap failure but rotates after explicit end", async () => {
    api.bootstrapVoiceSession
      .mockRejectedValueOnce(new Error("temporary control-plane outage"))
      .mockImplementation(
        async (request: { session_id: string; voice_call_id: string }) => ({
          runtime: "livekit_v2",
          trace_id: "98e09418-b221-48ad-8d59-570efbb51f54",
          profile_id: "cascade-v1",
          server_url: "wss://voice.example.test",
          room_name: "room-2",
          participant_token: "signed.jwt.token",
          participant_identity: "user-1",
          agent_participant_identity: "agent-worker-1",
          session_id: request.session_id,
          agent_id: agentId,
          voice_call_id: request.voice_call_id,
          dispatch_id: "dispatch-2",
          worker_name: "murmur-worker",
          event_topic: eventTopic,
          expires_at: "2099-01-01T00:00:00Z",
        })
    );
    const mounted = await mountHook();
    await act(async () => undefined);

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().phase).toBe("unavailable");
    await act(async () => mounted.read().connect({ sessionId }));
    const retryStableCallId = api.bootstrapVoiceSession.mock.calls[0]?.[0]
      ?.voice_call_id;
    expect(api.bootstrapVoiceSession.mock.calls[0]?.[0]).toMatchObject({
      voice_call_id: retryStableCallId,
    });
    expect(api.bootstrapVoiceSession.mock.calls[1]?.[0]).toMatchObject({
      voice_call_id: retryStableCallId,
    });

    act(() => mounted.read().disconnect());
    await act(async () => mounted.read().connect({ sessionId }));
    expect(api.bootstrapVoiceSession.mock.calls[2]?.[0]?.voice_call_id).not.toBe(
      retryStableCallId
    );
    await act(async () => mounted.root.unmount());
  });

  it("rotates the call ID when a 409 permits a fresh assignment attempt", async () => {
    api.bootstrapVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Voice call assignment conflict", 409)
    );
    const mounted = await mountHook();

    await act(async () => mounted.read().connect({ sessionId }));
    const conflictedCallId = api.bootstrapVoiceSession.mock.calls[0]?.[0]
      ?.voice_call_id;
    expect(mounted.read().phase).toBe("unavailable");
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "bootstrap_assignment_conflict",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe("");

    await act(async () => mounted.read().connect({ sessionId }));
    expect(api.bootstrapVoiceSession.mock.calls[1]?.[0]?.voice_call_id).not.toBe(
      conflictedCallId
    );
    await act(async () => mounted.root.unmount());
  });

  it("retains the call ID across a retryable 503 bootstrap failure", async () => {
    api.bootstrapVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Voice runtime unavailable", 503)
    );
    const mounted = await mountHook();

    await act(async () => mounted.read().connect({ sessionId }));
    const stableCallId = api.bootstrapVoiceSession.mock.calls[0]?.[0]
      ?.voice_call_id;
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "bootstrap_unavailable",
      retryable: true,
    });
    expect(mounted.read().voiceCallId).toBe(stableCallId);

    await act(async () => mounted.read().connect({ sessionId }));
    expect(api.bootstrapVoiceSession.mock.calls[1]?.[0]?.voice_call_id).toBe(
      stableCallId
    );
    await act(async () => mounted.root.unmount());
  });

  it("abandons and releases a retry-retained bootstrap identity on cancel", async () => {
    api.bootstrapVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Voice runtime unavailable", 503)
    );
    const mounted = await mountHook();

    await act(async () => mounted.read().connect({ sessionId }));
    const retainedCallId = mounted.read().voiceCallId;
    expect(retainedCallId).not.toBe("");

    act(() => mounted.read().cancelConnection());

    expect(mounted.read().phase).toBe("idle");
    expect(mounted.read().voiceCallId).toBe("");
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: retainedCallId },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );
    await act(async () => mounted.root.unmount());
  });

  it("times out a bootstrap promise that ignores its AbortSignal", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    api.bootstrapVoiceSession.mockImplementationOnce(
      (_request: unknown, options?: { signal?: AbortSignal }) => {
        requestSignal = options?.signal;
        return new Promise(() => undefined);
      }
    );
    const mounted = await mountHook();
    let settled = false;

    await act(async () => {
      void mounted.read().connect({ sessionId }).then(() => {
        settled = true;
      });
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
      await Promise.resolve();
    });

    expect(requestSignal?.aborted).toBe(true);
    expect(settled).toBe(true);
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "bootstrap_timeout",
      retryable: true,
    });
    await act(async () => mounted.root.unmount());
    expect(vi.getTimerCount()).toBe(0);
  });

  it("lets cancel abandon a hanging bootstrap and start a fresh retry immediately", async () => {
    let requestSignal: AbortSignal | undefined;
    api.bootstrapVoiceSession.mockImplementationOnce(
      (_request: unknown, options?: { signal?: AbortSignal }) => {
        requestSignal = options?.signal;
        return new Promise(() => undefined);
      }
    );
    const mounted = await mountHook();
    let firstConnect: Promise<void> | undefined;

    await act(async () => {
      firstConnect = mounted.read().connect({ sessionId });
      await Promise.resolve();
    });
    const abandonedCallId = api.bootstrapVoiceSession.mock.calls[0]?.[0]
      ?.voice_call_id;
    let retryConnect: Promise<void> | undefined;
    await act(async () => {
      mounted.read().cancelConnection();
      retryConnect = mounted.read().connect({ sessionId });
      expect(retryConnect).not.toBe(firstConnect);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requestSignal?.aborted).toBe(true);
    expect(api.bootstrapVoiceSession).toHaveBeenCalledTimes(2);
    await act(async () => {
      await Promise.all([firstConnect, retryConnect]);
    });
    expect(api.bootstrapVoiceSession.mock.calls[1]?.[0]?.voice_call_id).not.toBe(
      abandonedCallId
    );
    await act(async () => mounted.root.unmount());
  });

  it("retries one transient assignment-release failure without duplicating the call", async () => {
    vi.useFakeTimers();
    api.endVoiceSession
      .mockRejectedValueOnce(new Error("auth token refresh failed"))
      .mockResolvedValue(undefined);
    const mounted = await mountHook();
    await connectHook(mounted);

    act(() => mounted.read().cancelConnection());
    await act(async () => Promise.resolve());
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    act(() => mounted.read().cancelConnection());
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(api.endVoiceSession).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => mounted.root.unmount());
  });

  it("times out a hanging assignment release and recovers on the next attempt", async () => {
    vi.useFakeTimers();
    let releaseSignal: AbortSignal | undefined;
    api.endVoiceSession
      .mockImplementationOnce(
        (_request: unknown, options?: { signal?: AbortSignal }) => {
          releaseSignal = options?.signal;
          return new Promise<void>(() => undefined);
        }
      )
      .mockResolvedValue(undefined);
    const mounted = await mountHook();
    await connectHook(mounted);

    act(() => mounted.read().cancelConnection());
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    expect(releaseSignal?.aborted).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
      await Promise.resolve();
    });
    expect(releaseSignal?.aborted).toBe(true);
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(api.endVoiceSession).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => mounted.root.unmount());
  });

  it("does not retry a terminal assignment-release conflict", async () => {
    vi.useFakeTimers();
    api.endVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Release scope conflict", 409)
    );
    const mounted = await mountHook();
    await connectHook(mounted);

    act(() => mounted.read().cancelConnection());
    await act(async () => Promise.resolve());
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    await act(async () => mounted.root.unmount());
  });

  it("clears a pending assignment-release retry when the hook unmounts", async () => {
    vi.useFakeTimers();
    api.endVoiceSession.mockRejectedValueOnce(new TypeError("network unavailable"));
    const mounted = await mountHook();
    await connectHook(mounted);

    act(() => mounted.read().cancelConnection());
    await act(async () => Promise.resolve());
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    await act(async () => mounted.root.unmount());
    expect(vi.getTimerCount()).toBe(0);
    await vi.runAllTimersAsync();
    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
  });

  it("cancels an in-flight bootstrap without surfacing a false failure", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    let requestSignal: AbortSignal | undefined;
    api.bootstrapVoiceSession.mockImplementation(
      (_request: unknown, options?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          requestSignal = options?.signal;
          requestSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true }
          );
        })
    );
    const mounted = await mountHook({ onError });

    let connecting: Promise<void> | undefined;
    await act(async () => {
      connecting = mounted.read().connect({ sessionId });
      await Promise.resolve();
    });
    expect(mounted.read().phase).toBe("connecting");
    expect(requestSignal?.aborted).toBe(false);

    act(() => mounted.read().cancelConnection());
    await act(async () => connecting);

    const room = livekit.FakeRoom.instances.at(-1);
    expect(requestSignal?.aborted).toBe(true);
    expect(mounted.read().phase).toBe("idle");
    expect(onError).not.toHaveBeenCalled();
    expect(room?.disconnect).toHaveBeenCalledTimes(1);
    expect(room?.listeners.size).toBe(0);
    expect(api.endVoiceSession).toHaveBeenCalledWith(
      { session_id: sessionId, voice_call_id: expect.any(String) },
      expect.objectContaining({ apiUrl: undefined, signal: expect.any(AbortSignal) })
    );
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => mounted.root.unmount());
  });

  it("treats authentication bootstrap failure as terminal", async () => {
    api.bootstrapVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Authentication is required", 401)
    );
    const mounted = await mountHook();

    await act(async () => mounted.read().connect({ sessionId }));
    expect(mounted.read().phase).toBe("unavailable");
    expect(mounted.read().session.unavailableReason).toMatchObject({
      code: "bootstrap_unauthenticated",
      retryable: false,
    });
    expect(mounted.read().voiceCallId).toBe("");
    expect(api.bootstrapVoiceSession).toHaveBeenCalledTimes(1);
    await act(async () => mounted.root.unmount());
  });
});
