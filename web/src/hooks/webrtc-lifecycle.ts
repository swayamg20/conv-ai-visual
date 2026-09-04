/** Pure lifecycle rules for the legacy WebRTC voice transport. */

import type { SDLSequenceEndReason } from "@/features/canvas/sequence-lifecycle";

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

export interface SDLPlaybackLifecycleState {
  readonly activeSequenceId: string | null;
  readonly backendComplete: boolean;
  readonly playbackPending: boolean;
}

export type SDLPlaybackLifecycleSignal =
  | { readonly type: "sequence_started"; readonly sequenceId: string }
  | { readonly type: "audio_scheduled"; readonly sequenceId: string }
  | {
      readonly type: "backend_complete";
      readonly sequenceId: string;
      readonly reason: SDLSequenceEndReason;
    }
  | { readonly type: "playback_drained" }
  | { readonly type: "interrupted"; readonly sequenceId?: string };

export interface SDLPlaybackLifecycleTransition {
  readonly state: SDLPlaybackLifecycleState;
  readonly ended?: {
    readonly sequenceId: string;
    readonly reason: SDLSequenceEndReason;
  };
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

export const INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE: SDLPlaybackLifecycleState =
  Object.freeze({
    activeSequenceId: null,
    backendComplete: false,
    playbackPending: false,
  });

/**
 * Keep SDL ownership until both backend transmission and browser playback end.
 * This leaves one exact sequence available for synchronous barge-in cleanup.
 */
export function reduceSDLPlaybackLifecycle(
  state: SDLPlaybackLifecycleState,
  signal: SDLPlaybackLifecycleSignal
): SDLPlaybackLifecycleTransition {
  const end = (
    sequenceId: string,
    reason: SDLSequenceEndReason
  ): SDLPlaybackLifecycleTransition => ({
    state: INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
    ended: { sequenceId, reason },
  });

  switch (signal.type) {
    case "sequence_started":
      return {
        state: {
          activeSequenceId: signal.sequenceId,
          backendComplete: false,
          playbackPending: false,
        },
        ...(state.activeSequenceId && state.activeSequenceId !== signal.sequenceId
          ? {
              ended: {
                sequenceId: state.activeSequenceId,
                reason: "interrupted" as const,
              },
            }
          : {}),
      };

    case "audio_scheduled":
      return state.activeSequenceId === signal.sequenceId && !state.backendComplete
        ? { state: { ...state, playbackPending: true } }
        : { state };

    case "backend_complete":
      if (state.activeSequenceId !== signal.sequenceId) return { state };
      if (signal.reason === "interrupted") {
        return end(signal.sequenceId, "interrupted");
      }
      return state.playbackPending
        ? { state: { ...state, backendComplete: true } }
        : end(signal.sequenceId, "completed");

    case "playback_drained":
      if (!state.activeSequenceId) return { state };
      return state.backendComplete
        ? end(state.activeSequenceId, "completed")
        : { state: { ...state, playbackPending: false } };

    case "interrupted": {
      const sequenceId = signal.sequenceId ?? state.activeSequenceId;
      return sequenceId && sequenceId === state.activeSequenceId
        ? end(sequenceId, "interrupted")
        : { state };
    }
  }
}

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
