import type { VoiceSessionPhase } from "./session-machine";

export type VoiceRuntimeAssignment = "legacy" | "livekit_v2";
export type VoiceViewPipelineState = "idle" | "listening" | "processing" | "speaking";
export type VoiceViewOrbState =
  | "idle"
  | "connecting"
  | "listening"
  | "processing"
  | "speaking"
  | "error";
export type VoiceViewIndicatorState = "idle" | "connecting" | "connected" | "error";

export interface VoiceSessionView {
  readonly label: string;
  readonly orbState: VoiceViewOrbState;
  readonly indicatorState: VoiceViewIndicatorState;
  readonly pipelineState: VoiceViewPipelineState;
  readonly transportConnected: boolean;
  readonly voiceReady: boolean;
  readonly busy: boolean;
  readonly terminal: boolean;
}

/** Unknown and unset values preserve the legacy path during the canary. */
export function resolveVoiceRuntimeAssignment(
  value: string | undefined
): VoiceRuntimeAssignment {
  return value === "livekit_v2" ? "livekit_v2" : "legacy";
}

export function voiceSessionView(phase: VoiceSessionPhase): VoiceSessionView {
  switch (phase) {
    case "idle":
      return {
        label: "Start voice",
        orbState: "idle",
        indicatorState: "idle",
        pipelineState: "idle",
        transportConnected: false,
        voiceReady: false,
        busy: false,
        terminal: false,
      };
    case "connecting":
      return {
        label: "Connecting transport...",
        orbState: "connecting",
        indicatorState: "connecting",
        pipelineState: "idle",
        transportConnected: false,
        voiceReady: false,
        busy: true,
        terminal: false,
      };
    case "transport_connected":
      return {
        label: "Transport connected · checking agent...",
        orbState: "connecting",
        indicatorState: "connecting",
        pipelineState: "idle",
        transportConnected: true,
        voiceReady: false,
        busy: true,
        terminal: false,
      };
    case "ready":
      return {
        label: "Agent ready",
        orbState: "listening",
        indicatorState: "connected",
        pipelineState: "listening",
        transportConnected: true,
        voiceReady: true,
        busy: false,
        terminal: false,
      };
    case "listening":
      return {
        label: "Listening",
        orbState: "listening",
        indicatorState: "connected",
        pipelineState: "listening",
        transportConnected: true,
        voiceReady: true,
        busy: false,
        terminal: false,
      };
    case "thinking":
      return {
        label: "Thinking",
        orbState: "processing",
        indicatorState: "connected",
        pipelineState: "processing",
        transportConnected: true,
        voiceReady: true,
        busy: false,
        terminal: false,
      };
    case "speaking":
      return {
        label: "Speaking",
        orbState: "speaking",
        indicatorState: "connected",
        pipelineState: "speaking",
        transportConnected: true,
        voiceReady: true,
        busy: false,
        terminal: false,
      };
    case "reconnecting":
      return {
        label: "Reconnecting transport...",
        orbState: "connecting",
        indicatorState: "connecting",
        pipelineState: "idle",
        transportConnected: false,
        voiceReady: false,
        busy: true,
        terminal: false,
      };
    case "unavailable":
      return {
        label: "Voice unavailable",
        orbState: "error",
        indicatorState: "error",
        pipelineState: "idle",
        transportConnected: false,
        voiceReady: false,
        busy: false,
        terminal: false,
      };
    case "ended":
      return {
        label: "Voice ended",
        orbState: "idle",
        indicatorState: "idle",
        pipelineState: "idle",
        transportConnected: false,
        voiceReady: false,
        busy: false,
        terminal: true,
      };
  }
}
