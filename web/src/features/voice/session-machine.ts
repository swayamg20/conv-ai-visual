/** Pure Voice V2 session lifecycle. Transport connectivity is not voice readiness. */

import type { VoiceEvent } from "./events";

export type VoiceSessionPhase =
  | "idle"
  | "connecting"
  | "awaiting_audio"
  | "transport_connected"
  | "ready"
  | "listening"
  | "thinking"
  | "speaking"
  | "reconnecting"
  | "unavailable"
  | "ended";

export interface VoiceUnavailableReason {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface VoiceSessionState {
  readonly phase: VoiceSessionPhase;
  readonly transportConnected: boolean;
  readonly voiceReady: boolean;
  readonly reconnectAttempt: number;
  readonly terminationStage?: "ending" | "ended";
  readonly unavailableReason?: VoiceUnavailableReason;
}

export type VoiceSessionSignal =
  | { readonly type: "connect_requested" }
  | { readonly type: "transport_prepared" }
  | { readonly type: "end_requested" }
  | {
      readonly type: "compatibility_error";
      readonly code: string;
      readonly message: string;
    }
  | { readonly type: "event"; readonly event: VoiceEvent };

export function createInitialVoiceSessionState(): VoiceSessionState {
  return {
    phase: "idle",
    transportConnected: false,
    voiceReady: false,
    reconnectAttempt: 0,
  };
}

function unavailableState(
  state: VoiceSessionState,
  reason: VoiceUnavailableReason
): VoiceSessionState {
  return {
    ...state,
    phase: "unavailable",
    voiceReady: false,
    unavailableReason: reason,
  };
}

function protocolUnavailable(state: VoiceSessionState, eventType: string): VoiceSessionState {
  return unavailableState(state, {
    code: "protocol_event_before_voice_ready",
    message: `Received ${eventType} before the voice path was ready`,
    retryable: false,
  });
}

/**
 * Apply one local lifecycle signal or one already-validated event.
 * The function has no clock, I/O, mutation, or transport dependency.
 */
export function transitionVoiceSession(
  state: VoiceSessionState,
  signal: VoiceSessionSignal
): VoiceSessionState {
  if (signal.type === "connect_requested") {
    if (state.phase === "ended") {
      return state;
    }
    return {
      phase: "connecting",
      transportConnected: false,
      voiceReady: false,
      reconnectAttempt: 0,
    };
  }

  if (signal.type === "transport_prepared") {
    if (state.phase !== "connecting") return state;
    return {
      phase: "awaiting_audio",
      transportConnected: false,
      voiceReady: false,
      reconnectAttempt: state.reconnectAttempt,
    };
  }

  if (signal.type === "end_requested") {
    return {
      phase: "ended",
      transportConnected: false,
      voiceReady: false,
      reconnectAttempt: state.reconnectAttempt,
      terminationStage: "ending",
    };
  }

  if (signal.type === "compatibility_error") {
    return unavailableState(state, {
      code: signal.code,
      message: signal.message,
      retryable: false,
    });
  }

  if (
    state.phase === "ended" &&
    (signal.event.event_type !== "session_ended" || state.terminationStage === "ended")
  ) {
    return state;
  }

  const event = signal.event;
  switch (event.event_type) {
    case "session_starting":
      if (state.phase !== "idle" && state.phase !== "connecting") {
        return state;
      }
      return {
        phase: "connecting",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: 0,
      };

    case "session_started":
      if (state.phase !== "idle" && state.phase !== "connecting") {
        return state;
      }
      return {
        phase: "connecting",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: state.reconnectAttempt,
      };

    case "transport_connected":
      return {
        phase: "transport_connected",
        transportConnected: true,
        voiceReady: false,
        reconnectAttempt: state.reconnectAttempt,
      };

    case "transport_reconnecting":
      return {
        phase: "reconnecting",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: event.payload.attempt,
      };

    case "transport_disconnected":
      if (event.payload.recoverable) {
        return {
          phase: "reconnecting",
          transportConnected: false,
          voiceReady: false,
          reconnectAttempt: Math.max(1, state.reconnectAttempt + 1),
        };
      }
      return unavailableState(
        {
          ...state,
          transportConnected: false,
          voiceReady: false,
        },
        {
          code: "transport_unavailable",
          message: event.payload.reason ?? "Voice transport disconnected",
          retryable: false,
        }
      );

    case "agent_ready":
      if (!state.transportConnected) {
        return unavailableState(state, {
          code: "protocol_ready_before_transport",
          message: "The agent reported ready before the transport connected",
          retryable: false,
        });
      }
      return {
        phase: "ready",
        transportConnected: true,
        voiceReady: true,
        reconnectAttempt: state.reconnectAttempt,
      };

    case "agent_unavailable":
      return unavailableState(state, {
        code: event.payload.code,
        message: event.payload.message,
        retryable: event.payload.retryable,
      });

    case "transcript_segment":
      if (!event.payload.text.trim()) {
        return state;
      }
      return state.voiceReady
        ? { ...state, phase: "listening", unavailableReason: undefined }
        : protocolUnavailable(state, event.event_type);

    case "turn_resumed":
      return state.voiceReady
        ? { ...state, phase: "listening", unavailableReason: undefined }
        : protocolUnavailable(state, event.event_type);

    case "turn_committed":
      return state.voiceReady
        ? { ...state, phase: "thinking", unavailableReason: undefined }
        : protocolUnavailable(state, event.event_type);

    case "assistant_speech_started":
      return state.voiceReady
        ? { ...state, phase: "speaking", unavailableReason: undefined }
        : protocolUnavailable(state, event.event_type);

    case "assistant_speech_stopped":
      return state.voiceReady
        ? { ...state, phase: "ready", unavailableReason: undefined }
        : state;

    case "session_ending":
      return {
        phase: "ended",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: state.reconnectAttempt,
        terminationStage: "ending",
      };

    case "session_ended":
      return {
        phase: "ended",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: state.reconnectAttempt,
        terminationStage: "ended",
      };

    case "task_queued":
    case "task_working":
    case "task_needs_input":
    case "task_verified":
    case "task_failed":
    case "task_cancelled":
    case "task_superseded":
    case "artifact_proposed":
    case "artifact_accepted":
    case "artifact_rejected":
    case "canvas_patch":
    case "canvas_apply_ack":
    case "canvas_first_visible":
    case "canvas_animation_complete":
    case "canvas_render_failed":
    case "usage_recorded":
      return state;
  }
}
