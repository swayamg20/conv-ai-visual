import { describe, expect, it } from "vitest";

import {
  decodeVoiceEvent,
  REQUIRED_VOICE_READY_COMPONENTS,
  type VoiceEvent,
  type VoiceEventType,
} from "./events";
import {
  createInitialVoiceSessionState,
  transitionVoiceSession,
  type VoiceSessionState,
} from "./session-machine";

let sequence = 0;

function event(
  eventType: VoiceEventType,
  payload: Record<string, unknown>,
  envelope: Record<string, unknown> = {}
): VoiceEvent {
  sequence += 1;
  const decoded = decodeVoiceEvent({
    schema_version: 1,
    event_id: `event-${sequence}`,
    event_type: eventType,
    trace_id: "trace-1",
    voice_call_id: "call-1",
    session_id: "session-1",
    producer_id: "worker-1",
    producer_sequence: sequence,
    emitted_at: `2026-08-12T00:00:${String(sequence).padStart(2, "0")}.000Z`,
    payload,
    ...envelope,
  });
  if (!decoded.ok) throw new Error(decoded.error.message);
  return decoded.event;
}

function connectedState(): VoiceSessionState {
  let state = transitionVoiceSession(createInitialVoiceSessionState(), {
    type: "connect_requested",
  });
  state = transitionVoiceSession(state, {
    type: "event",
    event: event("transport_connected", {}),
  });
  return state;
}

function readyState(): VoiceSessionState {
  return transitionVoiceSession(connectedState(), {
    type: "event",
    event: event("agent_ready", {
      profile_id: "cascade-v1",
      required_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
      ready_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
    }),
  });
}

describe("Voice V2 session machine", () => {
  it("distinguishes a connected transport from a genuinely ready voice path", () => {
    const initial = createInitialVoiceSessionState();
    const connecting = transitionVoiceSession(initial, { type: "connect_requested" });
    const prepared = transitionVoiceSession(connecting, {
      type: "transport_prepared",
    });
    const activating = transitionVoiceSession(prepared, {
      type: "connect_requested",
    });
    const transportConnected = transitionVoiceSession(activating, {
      type: "event",
      event: event("transport_connected", { connection_id: "rtc-1" }),
    });

    expect(connecting).toMatchObject({
      phase: "connecting",
      transportConnected: false,
      voiceReady: false,
    });
    expect(prepared).toMatchObject({
      phase: "awaiting_audio",
      transportConnected: false,
      voiceReady: false,
    });
    expect(activating).toMatchObject({
      phase: "connecting",
      transportConnected: false,
      voiceReady: false,
    });
    expect(transportConnected).toMatchObject({
      phase: "transport_connected",
      transportConnected: true,
      voiceReady: false,
    });

    const ready = transitionVoiceSession(transportConnected, {
      type: "event",
      event: event("agent_ready", {
        profile_id: "cascade-v1",
        required_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
        ready_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
      }),
    });
    expect(ready).toMatchObject({
      phase: "ready",
      transportConnected: true,
      voiceReady: true,
    });
  });

  it("does not let a delayed transport fact regress readiness or revive unavailability", () => {
    const ready = readyState();
    const listening = transitionVoiceSession(ready, {
      type: "event",
      event: event("transcript_segment", {
        segment_id: "segment-delayed",
        text: "Still listening",
        is_final: false,
      }),
    });
    const thinking = transitionVoiceSession(ready, {
      type: "event",
      event: event(
        "turn_committed",
        { text: "Thinking now" },
        { turn_id: "turn-thinking" }
      ),
    });
    const speaking = transitionVoiceSession(thinking, {
      type: "event",
      event: event(
        "assistant_speech_started",
        { speech_id: "speech-delayed" },
        { turn_id: "turn-thinking" }
      ),
    });

    for (const semanticState of [ready, listening, thinking, speaking]) {
      expect(
        transitionVoiceSession(semanticState, {
          type: "event",
          event: event("transport_connected", { connection_id: "rtc-delayed" }),
        })
      ).toBe(semanticState);
    }

    const unavailable = transitionVoiceSession(ready, {
      type: "event",
      event: event("agent_unavailable", {
        code: "provider_unavailable",
        message: "Voice is unavailable",
        retryable: true,
      }),
    });
    expect(
      transitionVoiceSession(unavailable, {
        type: "event",
        event: event("transport_connected", { connection_id: "rtc-stale" }),
      })
    ).toBe(unavailable);
  });

  it("accepts preparation only while connecting", () => {
    const initial = createInitialVoiceSessionState();
    expect(
      transitionVoiceSession(initial, { type: "transport_prepared" })
    ).toBe(initial);

    const prepared = transitionVoiceSession(
      transitionVoiceSession(initial, { type: "connect_requested" }),
      { type: "transport_prepared" }
    );
    expect(
      transitionVoiceSession(prepared, { type: "transport_prepared" })
    ).toBe(prepared);
  });

  it("keeps session bootstrap distinct from transport connectivity", () => {
    const starting = transitionVoiceSession(createInitialVoiceSessionState(), {
      type: "event",
      event: event("session_starting", {}),
    });
    const started = transitionVoiceSession(starting, {
      type: "event",
      event: event("session_started", {}),
    });

    expect(starting).toMatchObject({
      phase: "connecting",
      transportConnected: false,
      voiceReady: false,
    });
    expect(started).toMatchObject({
      phase: "connecting",
      transportConnected: false,
      voiceReady: false,
    });

    const connected = transitionVoiceSession(started, {
      type: "event",
      event: event("transport_connected", {}),
    });
    expect(
      transitionVoiceSession(connected, {
        type: "event",
        event: event("session_started", {}),
      })
    ).toBe(connected);
  });

  it("models listening, thinking, and speaking without conflating segment finality with EOT", () => {
    let state = readyState();

    state = transitionVoiceSession(state, {
      type: "event",
      event: event("transcript_segment", {
        segment_id: "segment-1",
        text: "Explain",
        is_final: true,
      }),
    });
    expect(state.phase).toBe("listening");

    state = transitionVoiceSession(state, {
      type: "event",
      event: event(
        "turn_committed",
        { text: "Explain gravity" },
        { turn_id: "turn-1" }
      ),
    });
    expect(state.phase).toBe("thinking");

    state = transitionVoiceSession(state, {
      type: "event",
      event: event(
        "assistant_speech_started",
        { speech_id: "speech-1" },
        { turn_id: "turn-1" }
      ),
    });
    expect(state.phase).toBe("speaking");

    state = transitionVoiceSession(state, {
      type: "event",
      event: event(
        "assistant_speech_stopped",
        {
          speech_id: "speech-1",
          reason: "completed",
        },
        { turn_id: "turn-1" }
      ),
    });
    expect(state.phase).toBe("ready");
  });

  it("clears readiness across reconnect and requires a fresh ready event", () => {
    const reconnecting = transitionVoiceSession(readyState(), {
      type: "event",
      event: event("transport_reconnecting", { attempt: 1, reason: "network_change" }),
    });
    expect(reconnecting).toMatchObject({
      phase: "reconnecting",
      transportConnected: false,
      voiceReady: false,
      reconnectAttempt: 1,
    });

    const transportConnected = transitionVoiceSession(reconnecting, {
      type: "event",
      event: event("transport_connected", { connection_id: "rtc-2" }),
    });
    expect(transportConnected.phase).toBe("transport_connected");
    expect(transportConnected.voiceReady).toBe(false);
  });

  it("fails closed when ready or speech arrives before transport/provider readiness", () => {
    const prematureReady = transitionVoiceSession(createInitialVoiceSessionState(), {
      type: "event",
      event: event("agent_ready", {
        profile_id: "cascade-v1",
        required_components: REQUIRED_VOICE_READY_COMPONENTS,
        ready_components: REQUIRED_VOICE_READY_COMPONENTS,
      }),
    });
    expect(prematureReady).toMatchObject({
      phase: "unavailable",
      voiceReady: false,
      unavailableReason: { code: "protocol_ready_before_transport", retryable: false },
    });

    const prematureSpeech = transitionVoiceSession(connectedState(), {
      type: "event",
      event: event(
        "assistant_speech_started",
        { speech_id: "speech-early" },
        { turn_id: "turn-early" }
      ),
    });
    expect(prematureSpeech).toMatchObject({
      phase: "unavailable",
      voiceReady: false,
      unavailableReason: { code: "protocol_event_before_voice_ready" },
    });
  });

  it("supports explicit unavailability and treats ended as terminal", () => {
    const unavailable = transitionVoiceSession(connectedState(), {
      type: "event",
      event: event("agent_unavailable", {
        code: "tts_auth_failed",
        message: "Speech output is unavailable",
        retryable: false,
      }),
    });
    expect(unavailable).toMatchObject({
      phase: "unavailable",
      transportConnected: true,
      voiceReady: false,
      unavailableReason: { code: "tts_auth_failed" },
    });

    const retrying = transitionVoiceSession(unavailable, { type: "connect_requested" });
    expect(retrying.phase).toBe("connecting");

    const ended = transitionVoiceSession(retrying, { type: "end_requested" });
    const ignored = transitionVoiceSession(ended, {
      type: "event",
      event: event("transport_connected", {}),
    });
    expect(ignored).toBe(ended);
    expect(ignored.phase).toBe("ended");
  });

  it("accepts terminal confirmation after entering the ending state", () => {
    const ending = transitionVoiceSession(readyState(), {
      type: "event",
      event: event("session_ending", { reason: "user_requested" }),
    });
    const ended = transitionVoiceSession(ending, {
      type: "event",
      event: event("session_ended", { reason: "worker_stopped" }),
    });

    expect(ending).toMatchObject({ phase: "ended", terminationStage: "ending" });
    expect(ended).toMatchObject({ phase: "ended", terminationStage: "ended" });
  });
});
