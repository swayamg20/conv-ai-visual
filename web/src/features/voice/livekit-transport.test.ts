/** @vitest-environment happy-dom */

import { beforeEach, describe, expect, it, vi } from "vitest";

const livekit = vi.hoisted(() => {
  type Listener = (...args: unknown[]) => void;

  const microphoneCallOrder: string[] = [];

  class FakeLocalAudioTrack {
    isMuted = false;
    readonly mediaStreamTrack = {
      kind: "audio",
      id: "mic-1",
      label: "fake microphone",
      readyState: "live",
      enabled: true,
    } as unknown as MediaStreamTrack;
    readonly mute = vi.fn(async () => {
      microphoneCallOrder.push("mute");
      this.isMuted = true;
      this.mediaStreamTrack.enabled = false;
      return this;
    });
    readonly unmute = vi.fn(async () => {
      microphoneCallOrder.push("unmute");
      this.isMuted = false;
      this.mediaStreamTrack.enabled = true;
      return this;
    });
    readonly stop = vi.fn(() => {
      microphoneCallOrder.push("stop");
      Object.defineProperty(this.mediaStreamTrack, "readyState", {
        configurable: true,
        value: "ended",
      });
    });
  }

  const createLocalAudioTrack = vi.fn(async () => {
    microphoneCallOrder.push("create");
    const track = new FakeLocalAudioTrack();
    FakeRoom.localAudioTrack = track;
    return track;
  });

  class FakeRoom {
    static instances: FakeRoom[] = [];
    static localAudioTrack: FakeLocalAudioTrack | null = null;

    readonly listeners = new Map<string, Set<Listener>>();
    readonly localParticipant = {
      publishTrack: vi.fn(async (track: FakeLocalAudioTrack) => {
        microphoneCallOrder.push("publish");
        return {
          audioTrack: track,
          get isMuted() {
            return track.isMuted;
          },
          mute: track.mute,
          unmute: track.unmute,
        };
      }),
    };
    readonly connect = vi.fn(async (..._args: unknown[]) => undefined);
    readonly disconnect = vi.fn(async (..._args: unknown[]) => undefined);
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
      for (const listener of this.listeners.get(event) ?? []) listener(...args);
    }

    firstListener(event: string): Listener | undefined {
      return this.listeners.get(event)?.values().next().value;
    }
  }

  return {
    createLocalAudioTrack,
    FakeRoom,
    microphoneCallOrder,
    Track: { Source: { Microphone: "microphone" } },
    events: {
      Connected: "connected",
      Reconnecting: "reconnecting",
      Reconnected: "reconnected",
      Disconnected: "disconnected",
      ParticipantDisconnected: "participantDisconnected",
      DataReceived: "dataReceived",
      TrackSubscribed: "trackSubscribed",
      TrackUnsubscribed: "trackUnsubscribed",
      MediaDevicesError: "mediaDevicesError",
      AudioPlaybackStatusChanged: "audioPlaybackChanged",
    },
  };
});

vi.mock("livekit-client", () => ({
  createLocalAudioTrack: livekit.createLocalAudioTrack,
  Room: livekit.FakeRoom,
  RoomEvent: livekit.events,
  Track: livekit.Track,
  DataPacket_Kind: { RELIABLE: 0, LOSSY: 1 },
  isAudioTrack: (track: { kind?: string }) => track.kind === "audio",
}));

import type { LiveKitVoiceSessionBootstrap } from "./session-api";
import { LiveKitVoiceTransport } from "./livekit-transport";

const assignment: LiveKitVoiceSessionBootstrap = {
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
};

function createCallbacks() {
  let current = true;
  return {
    callbacks: {
      isCurrent: vi.fn(() => current),
      onConnected: vi.fn(),
      onReconnecting: vi.fn(),
      onReconnected: vi.fn(),
      onDisconnected: vi.fn(),
      onAgentDisconnected: vi.fn(),
      onTransportInput: vi.fn(),
      onInvalidEventChannel: vi.fn(),
      onMicrophoneUnavailable: vi.fn(),
      onAudioPlaybackBlockedChange: vi.fn(),
      onLocalMicrophoneTrack: vi.fn(),
      onLocalMicrophonePublication: vi.fn(),
    },
    makeStale: () => {
      current = false;
    },
  };
}

describe("LiveKitVoiceTransport", () => {
  beforeEach(() => {
    livekit.FakeRoom.instances.length = 0;
    livekit.FakeRoom.localAudioTrack = null;
    livekit.createLocalAudioTrack.mockClear();
    livekit.microphoneCallOrder.length = 0;
    document.body.replaceChildren();
  });

  it("accepts only exact-agent reliable packets on the assigned topic", async () => {
    const owner = createCallbacks();
    const transport = new LiveKitVoiceTransport({
      voiceCallId: assignment.voice_call_id,
      ttsEnabled: true,
      callbacks: owner.callbacks,
    });
    await transport.connect(assignment);
    const room = livekit.FakeRoom.instances[0];
    const payload = new TextEncoder().encode(JSON.stringify({ type: "ready" }));

    room.emit(
      livekit.events.DataReceived,
      payload,
      { isAgent: true, identity: assignment.agent_participant_identity },
      1,
      assignment.event_topic
    );
    room.emit(
      livekit.events.DataReceived,
      payload,
      { isAgent: true, identity: "other-agent" },
      0,
      assignment.event_topic
    );
    room.emit(
      livekit.events.DataReceived,
      payload,
      { isAgent: true, identity: assignment.agent_participant_identity },
      0,
      "other.topic"
    );
    expect(owner.callbacks.onInvalidEventChannel).toHaveBeenCalledTimes(3);
    expect(owner.callbacks.onTransportInput).not.toHaveBeenCalled();

    room.emit(
      livekit.events.DataReceived,
      payload,
      { isAgent: true, identity: assignment.agent_participant_identity },
      0,
      assignment.event_topic
    );
    expect(owner.callbacks.onTransportInput).toHaveBeenCalledWith({
      type: "ready",
    });

    owner.makeStale();
    room.emit(
      livekit.events.DataReceived,
      payload,
      { isAgent: true, identity: assignment.agent_participant_identity },
      0,
      assignment.event_topic
    );
    expect(owner.callbacks.onTransportInput).toHaveBeenCalledTimes(1);
    await transport.disconnect();
  });

  it("owns subscribed audio, media controls, listeners, and teardown", async () => {
    const owner = createCallbacks();
    const transport = new LiveKitVoiceTransport({
      voiceCallId: assignment.voice_call_id,
      ttsEnabled: true,
      callbacks: owner.callbacks,
    });
    transport.primeAudioPlayback();
    await transport.connect(assignment);
    const room = livekit.FakeRoom.instances[0];
    const staleConnected = room.firstListener(livekit.events.Connected);
    const element = document.createElement("audio");
    const track = {
      kind: "audio",
      isLocal: false,
      setVolume: vi.fn(),
      attach: vi.fn(() => element),
      detach: vi.fn(() => [element]),
    };

    room.emit(
      livekit.events.TrackSubscribed,
      track,
      {},
      { isAgent: true, identity: "other-agent" }
    );
    expect(track.attach).not.toHaveBeenCalled();

    room.emit(
      livekit.events.TrackSubscribed,
      track,
      {},
      { isAgent: true, identity: assignment.agent_participant_identity }
    );
    expect(element.isConnected).toBe(true);
    expect(element.dataset.murmurVoiceCall).toBe(assignment.voice_call_id);

    transport.setTtsEnabled(false);
    expect(track.setVolume).toHaveBeenLastCalledWith(0);
    await transport.activateMicrophoneAfterReady();
    await transport.setMicrophoneEnabled(false);
    await transport.disconnect();

    expect(track.detach).toHaveBeenCalledTimes(1);
    expect(element.isConnected).toBe(false);
    expect(room.disconnect).toHaveBeenCalledWith(true);
    expect(livekit.FakeRoom.localAudioTrack?.stop).toHaveBeenCalledTimes(1);
    expect([...room.listeners.values()].every((listeners) => listeners.size === 0)).toBe(
      true
    );

    staleConnected?.();
    expect(owner.callbacks.onConnected).not.toHaveBeenCalled();
  });

  it("reports only the exact assigned agent departure", async () => {
    const owner = createCallbacks();
    const transport = new LiveKitVoiceTransport({
      voiceCallId: assignment.voice_call_id,
      ttsEnabled: true,
      callbacks: owner.callbacks,
    });
    await transport.connect(assignment);
    const room = livekit.FakeRoom.instances[0];

    room.emit(livekit.events.ParticipantDisconnected, {
      isAgent: false,
      identity: assignment.agent_participant_identity,
    });
    room.emit(livekit.events.ParticipantDisconnected, {
      isAgent: true,
      identity: "other-agent",
    });
    expect(owner.callbacks.onAgentDisconnected).not.toHaveBeenCalled();

    room.emit(livekit.events.ParticipantDisconnected, {
      isAgent: true,
      identity: assignment.agent_participant_identity,
    });
    expect(owner.callbacks.onAgentDisconnected).toHaveBeenCalledTimes(1);

    await transport.disconnect();
    room.emit(livekit.events.ParticipantDisconnected, {
      isAgent: true,
      identity: assignment.agent_participant_identity,
    });
    expect(owner.callbacks.onAgentDisconnected).toHaveBeenCalledTimes(1);
  });

  it("mutes before publish, activates once after Ready, and stops the exact track", async () => {
    const owner = createCallbacks();
    const transport = new LiveKitVoiceTransport({
      voiceCallId: assignment.voice_call_id,
      ttsEnabled: true,
      callbacks: owner.callbacks,
    });

    await transport.connect(assignment);
    const room = livekit.FakeRoom.instances[0];
    const localAudioTrack = livekit.FakeRoom.localAudioTrack;
    expect(localAudioTrack).not.toBeNull();
    if (!localAudioTrack) throw new Error("Fake microphone was not created");
    expect(livekit.microphoneCallOrder).toEqual(["create", "mute", "publish"]);
    expect(room.localParticipant.publishTrack).toHaveBeenCalledWith(
      localAudioTrack,
      {
        source: "microphone",
        stopMicTrackOnMute: false,
      }
    );
    expect(localAudioTrack.mediaStreamTrack.enabled).toBe(false);
    expect(owner.callbacks.onLocalMicrophoneTrack).toHaveBeenCalledTimes(1);
    expect(owner.callbacks.onLocalMicrophoneTrack).toHaveBeenLastCalledWith(
      localAudioTrack.mediaStreamTrack
    );
    expect(owner.callbacks.onLocalMicrophonePublication).toHaveBeenCalledTimes(1);
    const publicationCall = owner.callbacks.onLocalMicrophonePublication.mock.calls[0];
    const publishedTrack = publicationCall?.[0];
    const publicationObservation = publicationCall?.[1];
    expect(publishedTrack).toBe(localAudioTrack.mediaStreamTrack);
    expect(publicationObservation).toEqual({
      trackId: localAudioTrack.mediaStreamTrack.id,
      observedAtMs: expect.any(Number),
      mediaStreamTrackEnabled: false,
      livekitMuted: true,
      readyState: "live",
    });
    expect(Number.isFinite(publicationObservation?.observedAtMs)).toBe(true);
    expect(Object.isFrozen(publicationObservation)).toBe(true);

    await Promise.all([
      transport.activateMicrophoneAfterReady(),
      transport.activateMicrophoneAfterReady(),
    ]);
    await transport.activateMicrophoneAfterReady();
    expect(localAudioTrack.unmute).toHaveBeenCalledTimes(1);
    expect(localAudioTrack.mediaStreamTrack.enabled).toBe(true);
    expect(publicationObservation).toMatchObject({
      mediaStreamTrackEnabled: false,
      livekitMuted: true,
    });

    await transport.setMicrophoneEnabled(false);
    await transport.setMicrophoneEnabled(true);
    expect(localAudioTrack.mute).toHaveBeenCalledTimes(2);
    expect(localAudioTrack.unmute).toHaveBeenCalledTimes(2);
    expect(owner.callbacks.onLocalMicrophoneTrack).toHaveBeenCalledTimes(1);

    await transport.disconnect();
    expect(localAudioTrack.stop).toHaveBeenCalledTimes(1);
    expect(localAudioTrack.mediaStreamTrack.readyState).toBe("ended");
    expect(owner.callbacks.onLocalMicrophoneTrack).toHaveBeenCalledTimes(2);
    expect(owner.callbacks.onLocalMicrophoneTrack).toHaveBeenLastCalledWith(null);
    expect(owner.callbacks.onLocalMicrophonePublication).toHaveBeenCalledTimes(1);
  });

  it("stops a published muted microphone when torn down before Ready", async () => {
    const owner = createCallbacks();
    const transport = new LiveKitVoiceTransport({
      voiceCallId: assignment.voice_call_id,
      ttsEnabled: true,
      callbacks: owner.callbacks,
    });

    await transport.connect(assignment);
    const localAudioTrack = livekit.FakeRoom.localAudioTrack;
    expect(localAudioTrack?.mediaStreamTrack.enabled).toBe(false);
    expect(localAudioTrack?.unmute).not.toHaveBeenCalled();

    await transport.disconnect();
    expect(localAudioTrack?.stop).toHaveBeenCalledTimes(1);
    expect(localAudioTrack?.unmute).not.toHaveBeenCalled();
  });
});
