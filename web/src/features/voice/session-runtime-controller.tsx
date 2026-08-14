"use client";

import type { ReactNode } from "react";

import type { ConnectionStatus as IndicatorState } from "@/components/status-indicator";
import type { VoiceState } from "@/components/voice-orb";
import type { CanvasOperation } from "@/features/canvas/types";
import type { SDLScene } from "@/lib/scene-kit";
import {
  useVoiceSession,
  type VoiceSessionTranscriptEvent,
} from "@/hooks/use-voice-session";
import {
  useWebRTC,
  type PipelineState,
  type TranscriptEvent,
} from "@/hooks/use-webrtc";
import type { VoiceUnavailableReason } from "./session-machine";
import { voiceSessionView, type VoiceRuntimeAssignment } from "./session-view";

export interface SessionVoiceCallbacks {
  readonly onSessionReady: (sessionId: string) => void;
  readonly onTranscript: (event: TranscriptEvent | VoiceSessionTranscriptEvent) => void;
  readonly onAssistantSpeech: (text: string) => void;
  readonly onCanvasUpdate: (operations: CanvasOperation[]) => void;
  readonly onSDLScene: (sdl: SDLScene) => void;
  readonly onSDLStart: (
    sdl: SDLScene,
    sequenceId: string,
    totalSteps: number
  ) => void;
  readonly onSDLStepAudioStart: (sequenceId: string, stepIndex: number) => void;
  readonly onSDLStepComplete: (
    sequenceId: string,
    stepIndex: number,
    audioDurationMs: number
  ) => void;
  readonly onSDLComplete: (sequenceId: string) => void;
  readonly onPipelineMetrics: (metrics: Record<string, unknown>) => void;
  readonly onError: (message: string) => void;
  readonly onLog: (message: string) => void;
  readonly onStateChange: (state: PipelineState) => void;
}

export interface SessionVoiceRuntime {
  readonly runtime: VoiceRuntimeAssignment;
  readonly isConnected: boolean;
  readonly isVoiceReady: boolean;
  readonly isConnecting: boolean;
  readonly canStartVoice: boolean;
  readonly terminal: boolean;
  readonly unavailableReason: VoiceUnavailableReason | undefined;
  readonly voiceState: VoiceState;
  readonly indicatorState: IndicatorState;
  readonly statusLabel: string;
  readonly pipelineState: PipelineState;
  readonly isMicMuted: boolean;
  readonly isTTSEnabled: boolean;
  readonly audioPlaybackBlocked: boolean;
  readonly connect: (sessionId: string) => Promise<void>;
  readonly disconnect: () => Promise<void>;
  readonly cancelConnection: () => Promise<void>;
  readonly toggleMicMute: () => void;
  readonly toggleTTS: () => void;
  readonly resumeAudio: () => Promise<void>;
}

interface ControllerProps {
  readonly runtime: VoiceRuntimeAssignment;
  readonly agentId: string;
  readonly sessionId?: string;
  readonly callbacks: SessionVoiceCallbacks;
  readonly children: (voice: SessionVoiceRuntime) => ReactNode;
}

type RuntimeLeafProps = Omit<ControllerProps, "runtime">;

function legacyVoiceState(
  status: ReturnType<typeof useWebRTC>["status"],
  pipelineState: PipelineState
): VoiceState {
  if (status === "error") return "error";
  if (status === "connecting") return "connecting";
  if (status === "idle" || status === "disconnected") return "idle";
  if (pipelineState === "listening") return "listening";
  if (pipelineState === "processing") return "processing";
  if (pipelineState === "speaking") return "speaking";
  return "listening";
}

function legacyStatusLabel(status: ReturnType<typeof useWebRTC>["status"]): string {
  if (status === "idle") return "Start voice";
  if (status === "connecting") return "Checking voice...";
  if (status === "connected") return "Voice ready";
  if (status === "error") return "Voice unavailable";
  return "Disconnected";
}

function LegacyVoiceController({
  agentId,
  sessionId,
  callbacks,
  children,
}: RuntimeLeafProps) {
  const legacy = useWebRTC({
    agentId,
    sessionId,
    onSessionReady: callbacks.onSessionReady,
    onTranscript: callbacks.onTranscript,
    onLLMResponse: callbacks.onAssistantSpeech,
    onCanvasUpdate: callbacks.onCanvasUpdate,
    onSDLScene: callbacks.onSDLScene,
    onSDLStart: callbacks.onSDLStart,
    onSDLStepAudioStart: callbacks.onSDLStepAudioStart,
    onSDLStepComplete: callbacks.onSDLStepComplete,
    onSDLComplete: callbacks.onSDLComplete,
    onPipelineMetrics: callbacks.onPipelineMetrics,
    onError: callbacks.onError,
    onLog: callbacks.onLog,
    onStateChange: callbacks.onStateChange,
  });
  const isConnected = legacy.status === "connected";
  const isConnecting = legacy.status === "connecting";
  const indicatorState: IndicatorState = isConnected
    ? "connected"
    : isConnecting
      ? "connecting"
      : legacy.status === "error"
        ? "error"
        : "idle";

  return children({
    runtime: "legacy",
    isConnected,
    isVoiceReady: isConnected,
    isConnecting,
    canStartVoice: !isConnected && !isConnecting,
    terminal: false,
    unavailableReason: undefined,
    voiceState: legacyVoiceState(legacy.status, legacy.pipelineState),
    indicatorState,
    statusLabel: legacyStatusLabel(legacy.status),
    pipelineState: legacy.pipelineState,
    isMicMuted: legacy.isMicMuted,
    isTTSEnabled: legacy.isTTSEnabled,
    audioPlaybackBlocked: false,
    connect: async (ownedSessionId) => {
      legacy.initAudio();
      await legacy.connect({ agentId, sessionId: ownedSessionId });
    },
    disconnect: async () => legacy.disconnect(),
    cancelConnection: async () => legacy.disconnect(),
    toggleMicMute: legacy.toggleMicMute,
    toggleTTS: legacy.toggleTTS,
    resumeAudio: async () => undefined,
  });
}

function VoiceV2Controller({
  agentId,
  sessionId,
  callbacks,
  children,
}: RuntimeLeafProps) {
  const voice = useVoiceSession({
    enabled: true,
    agentId,
    sessionId,
    onTranscript: callbacks.onTranscript,
    onAssistantSpeech: callbacks.onAssistantSpeech,
    onError: callbacks.onError,
    onLog: callbacks.onLog,
    onPhaseChange: (phase) => callbacks.onLog(`Voice V2 -> ${phase}`),
  });
  const view = voiceSessionView(voice.phase);
  const canRetry =
    voice.phase !== "unavailable" ||
    voice.session.unavailableReason?.retryable === true;

  return children({
    runtime: "voice_v2",
    isConnected: view.transportConnected,
    isVoiceReady: view.voiceReady,
    isConnecting: view.busy,
    canStartVoice:
      !view.transportConnected && !view.busy && !view.terminal && canRetry,
    terminal: view.terminal,
    unavailableReason: voice.session.unavailableReason,
    voiceState: view.orbState,
    indicatorState: view.indicatorState,
    statusLabel:
      voice.audioPlaybackBlocked && view.voiceReady
        ? "Agent ready · enable audio"
        : view.label,
    pipelineState: view.pipelineState,
    isMicMuted: voice.isMicMuted,
    isTTSEnabled: voice.isTTSEnabled,
    audioPlaybackBlocked: voice.audioPlaybackBlocked,
    connect: async (ownedSessionId) => {
      await voice.connect({ sessionId: ownedSessionId });
    },
    disconnect: voice.disconnect,
    cancelConnection: voice.cancelConnection,
    toggleMicMute: () => {
      void voice.toggleMicMute();
    },
    toggleTTS: voice.toggleTTS,
    resumeAudio: voice.resumeAudio,
  });
}

/** Mounts exactly one media runtime, so Voice V2 never initializes legacy VAD. */
export function SessionVoiceRuntimeController(props: ControllerProps) {
  return props.runtime === "voice_v2" ? (
    <VoiceV2Controller {...props} />
  ) : (
    <LegacyVoiceController {...props} />
  );
}
