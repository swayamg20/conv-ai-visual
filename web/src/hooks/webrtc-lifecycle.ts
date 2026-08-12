/** Pure lifecycle rules for the legacy WebRTC voice transport. */

export type VoiceConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";

export type VoicePipelineState = "idle" | "listening" | "processing" | "speaking";

export interface VoiceLifecycleState {
  readonly status: VoiceConnectionStatus;
  readonly pipeline: VoicePipelineState;
  readonly terminal: boolean;
}

export type VoiceLifecycleSignal =
  | { readonly type: "connect_requested" }
  | { readonly type: "transport_open" }
  | { readonly type: "backend_ready" }
  | { readonly type: "transcript_segment" }
  | { readonly type: "turn_committed" }
  | { readonly type: "recoverable_error" }
  | { readonly type: "terminal_failure" }
  | { readonly type: "channel_closed" }
  | { readonly type: "disconnect_requested" };

export interface BackendErrorPayload {
  readonly code?: unknown;
  readonly recoverable?: unknown;
}

export type BackendErrorDisposition = "recoverable" | "terminal";

export const INITIAL_VOICE_LIFECYCLE_STATE: VoiceLifecycleState = Object.freeze({
  status: "idle",
  pipeline: "idle",
  terminal: false,
});

/**
 * Explicit recoverability is authoritative. The code fallback keeps older
 * `voice_unavailable` events terminal when they predate the boolean field.
 */
export function classifyBackendError(
  payload: BackendErrorPayload
): BackendErrorDisposition {
  if (payload.recoverable === true) {
    return "recoverable";
  }
  if (payload.recoverable === false) {
    return "terminal";
  }
  return payload.code === "voice_unavailable" ? "terminal" : "recoverable";
}

export function lifecycleSignalForBackendError(
  payload: BackendErrorPayload
): Extract<VoiceLifecycleSignal, { type: "recoverable_error" | "terminal_failure" }> {
  return classifyBackendError(payload) === "terminal"
    ? { type: "terminal_failure" }
    : { type: "recoverable_error" };
}

/**
 * The data channel is transport only. Voice becomes connected/listening only
 * after the backend confirms that every required provider is ready.
 */
export function reduceVoiceLifecycle(
  state: VoiceLifecycleState,
  signal: VoiceLifecycleSignal
): VoiceLifecycleState {
  switch (signal.type) {
    case "connect_requested":
      return { status: "connecting", pipeline: "idle", terminal: false };

    case "transport_open":
    case "transcript_segment":
      return state;

    case "backend_ready":
      return state.terminal || state.status === "connected"
        ? state
        : { status: "connected", pipeline: "listening", terminal: false };

    case "turn_committed":
      if (state.terminal || state.status !== "connected" || state.pipeline === "speaking") {
        return state;
      }
      return { ...state, pipeline: "processing" };

    case "recoverable_error":
      if (state.terminal || state.status !== "connected") {
        return state;
      }
      return { ...state, pipeline: "listening" };

    case "terminal_failure":
      return { status: "error", pipeline: "idle", terminal: true };

    case "channel_closed":
      return state.terminal
        ? state
        : { status: "disconnected", pipeline: "idle", terminal: false };

    case "disconnect_requested":
      return { status: "disconnected", pipeline: "idle", terminal: false };
  }
}
