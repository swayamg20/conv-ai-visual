/** @vitest-environment happy-dom */

import { beforeEach, describe, expect, it, vi } from "vitest";

const livekit = vi.hoisted(() => {
  type Listener = (...args: unknown[]) => void;

  class FakeRoom {
    static instances: FakeRoom[] = [];

    readonly listeners = new Map<string, Set<Listener>>();
    readonly localParticipant = {
      setMicrophoneEnabled: vi.fn(async (..._args: unknown[]) => undefined),
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

vi.mock("livekit-client", () => ({
  Room: livekit.FakeRoom,
  RoomEvent: livekit.events,
  DataPacket_Kind: { RELIABLE: 0, LOSSY: 1 },
  isAudioTrack: (track: { kind?: string }) => track.kind === "audio",
}));

import type { VoiceSessionBootstrap } from "./session-api";
import { LiveKitVoiceTransport } from "./livekit-transport";

const assignment: VoiceSessionBootstrap = {
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
      onTransportInput: vi.fn(),
      onInvalidEventChannel: vi.fn(),
      onMicrophoneUnavailable: vi.fn(),
      onAudioPlaybackBlockedChange: vi.fn(),
    },
    makeStale: () => {
      current = false;
    },
  };
}

describe("LiveKitVoiceTransport", () => {
  beforeEach(() => {
    livekit.FakeRoom.instances.length = 0;
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
    await transport.setMicrophoneEnabled(false);
    await transport.disconnect();

    expect(track.detach).toHaveBeenCalledTimes(1);
    expect(element.isConnected).toBe(false);
    expect(room.disconnect).toHaveBeenCalledWith(true);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenLastCalledWith(
      false
    );
    expect([...room.listeners.values()].every((listeners) => listeners.size === 0)).toBe(
      true
    );

    staleConnected?.();
    expect(owner.callbacks.onConnected).not.toHaveBeenCalled();
  });
});
