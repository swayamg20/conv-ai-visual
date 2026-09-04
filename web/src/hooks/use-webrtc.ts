"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useAudio } from "./use-audio";
import { useVAD, VAD_PRESETS } from "./use-vad";
import { playReadySound, playDisconnectSound, playErrorSound } from "@/lib/sounds";
import { getAuthHeaders } from "@/lib/firebase";
import type { CanvasOperation } from "@/features/canvas/types";
import type { SDLSequenceEndReason } from "@/features/canvas/sequence-lifecycle";
import {
  INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
  lifecycleSignalForBackendError,
  reduceSDLPlaybackLifecycle,
  reduceVoiceLifecycle,
  type SDLPlaybackLifecycleSignal,
  type SDLPlaybackLifecycleState,
  type VoiceConnectionStatus,
  type VoiceLifecycleSignal,
  type VoicePipelineState,
} from "./webrtc-lifecycle";

const debugLog = (...args: unknown[]) => {
  if (process.env.NODE_ENV !== "production") {
    console.debug(...args);
  }
};

export type ConnectionStatus = VoiceConnectionStatus;
export type PipelineState = VoicePipelineState;

const VOICE_READY_TIMEOUT_MS = 15_000;

interface ConnectOverrides {
  agentId?: string;
  sessionId?: string;
}

export interface TranscriptEvent {
  text: string;
  isFinal: boolean;
  speechFinal: boolean;
}

export interface LatencyMetrics {
  vadLatency?: number;
  sttFinalTranscript?: number;
  llmComplete?: number;
  ttsComplete?: number;
  totalPipeline?: number;
}

interface UseWebRTCOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  agentId?: string;
  sessionId?: string;
  onSessionReady?: (sessionId: string) => void;
  onTranscript?: (event: TranscriptEvent) => void;
  onLLMResponse?: (text: string) => void;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onSDLScene?: (sdl: any) => void;
  onSDLStart?: (sdl: any, sequenceId: string, totalSteps: number) => void;
  onSDLStepAudioStart?: (sequenceId: string, stepIndex: number) => void;
  onSDLStepComplete?: (sequenceId: string, stepIndex: number, audioDurationMs: number) => void;
  onSDLComplete?: (sequenceId: string, reason: SDLSequenceEndReason) => void;
  onPipelineMetrics?: (metrics: Record<string, any>) => void;
  onError?: (message: string) => void;
  onLog?: (message: string) => void;
  onStateChange?: (state: PipelineState) => void;
}

export function useWebRTC(options: UseWebRTCOptions = {}) {
  const {
    apiUrl = "http://localhost:8000",
    canvasMode = false,
    agentId,
    sessionId,
    onSessionReady,
    onTranscript,
    onLLMResponse,
    onCanvasUpdate,
    onSDLScene,
    onSDLStart,
    onSDLStepAudioStart,
    onSDLStepComplete,
    onSDLComplete,
    onPipelineMetrics,
    onError,
    onLog,
    onStateChange,
  } = options;

  const [status, setStatus] = useState<ConnectionStatus>("idle");
  const [pipelineState, setPipelineState] = useState<PipelineState>("idle");
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isTTSEnabled, setIsTTSEnabled] = useState(true);

  const statusRef = useRef<ConnectionStatus>("idle");
  const pipelineStateRef = useRef<PipelineState>("idle");  // Ref for latest state in callbacks
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const isFirstTTSChunkRef = useRef(true);  // Track if this is first chunk of TTS session
  const isTTSEnabledRef = useRef(true);  // Ref for TTS enabled state
  const sdlStepChunkTrackerRef = useRef<{ sequenceId: string; stepIndex: number; firstChunkSent: boolean } | null>(null);
  const sdlPlaybackLifecycleRef = useRef<SDLPlaybackLifecycleState>(
    INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE
  );
  const readinessTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const terminalErrorRef = useRef(false);
  const connectionAttemptRef = useRef(0);
  const offerAbortControllerRef = useRef<AbortController | null>(null);
  const cancelIceWaitRef = useRef<(() => void) | null>(null);

  const clearReadinessTimer = useCallback(() => {
    if (readinessTimerRef.current !== null) {
      clearTimeout(readinessTimerRef.current);
      readinessTimerRef.current = null;
    }
  }, []);

  const cleanupTransport = useCallback(() => {
    connectionAttemptRef.current += 1;
    clearReadinessTimer();
    cancelIceWaitRef.current?.();
    cancelIceWaitRef.current = null;
    offerAbortControllerRef.current?.abort();
    offerAbortControllerRef.current = null;

    const channel = channelRef.current;
    const stream = localStreamRef.current;
    const peerConnection = pcRef.current;
    channelRef.current = null;
    localStreamRef.current = null;
    pcRef.current = null;

    try {
      if (channel && channel.readyState !== "closed") {
        channel.close();
      }
    } catch {
      // Ignore teardown races.
    }
    stream?.getTracks().forEach((track) => track.stop());
    if (peerConnection) {
      peerConnection.getSenders().forEach((sender) => sender.track?.stop());
      peerConnection.close();
    }
    isFirstTTSChunkRef.current = true;
    sdlStepChunkTrackerRef.current = null;
    const activeSequenceId = sdlPlaybackLifecycleRef.current.activeSequenceId;
    sdlPlaybackLifecycleRef.current = INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE;
    if (activeSequenceId) {
      onSDLComplete?.(activeSequenceId, "interrupted");
    }
  }, [clearReadinessTimer, onSDLComplete]);

  const log = useCallback((msg: string) => {
    onLog?.(msg);
    debugLog(`[WebRTC] ${msg}`);
  }, [onLog]);

  const updateConnectionStatus = useCallback((nextStatus: ConnectionStatus) => {
    statusRef.current = nextStatus;
    setStatus(nextStatus);
  }, []);

  const updatePipelineState = useCallback((state: PipelineState) => {
    const prevState = pipelineStateRef.current;
    debugLog(`[State] ${prevState} → ${state}`);
    pipelineStateRef.current = state;  // Update ref immediately
    setPipelineState(state);
    onStateChange?.(state);
    log(`State: ${state}`);
  }, [onStateChange, log]);

  const applyLifecycleSignal = useCallback(
    (signal: VoiceLifecycleSignal) => {
      const previous = {
        status: statusRef.current,
        pipeline: pipelineStateRef.current,
        terminal: terminalErrorRef.current,
      } as const;
      const next = reduceVoiceLifecycle(previous, signal);
      terminalErrorRef.current = next.terminal;
      if (next.status !== previous.status) {
        updateConnectionStatus(next.status);
      }
      if (next.pipeline !== previous.pipeline) {
        updatePipelineState(next.pipeline);
      }
      return { previous, next };
    },
    [updateConnectionStatus, updatePipelineState]
  );

  const applySDLPlaybackSignal = useCallback(
    (signal: SDLPlaybackLifecycleSignal) => {
      const transition = reduceSDLPlaybackLifecycle(
        sdlPlaybackLifecycleRef.current,
        signal
      );
      sdlPlaybackLifecycleRef.current = transition.state;
      if (transition.ended) {
        onSDLComplete?.(
          transition.ended.sequenceId,
          transition.ended.reason
        );
      }
      return transition;
    },
    [onSDLComplete]
  );

  // Callback when audio playback completes
  const handlePlaybackComplete = useCallback(() => {
    if (terminalErrorRef.current || statusRef.current !== "connected") {
      return;
    }
    debugLog("[Playback] All audio chunks finished playing");
    applySDLPlaybackSignal({ type: "playback_drained" });
    // Only transition to listening if we're currently in speaking state
    if (pipelineStateRef.current === "speaking") {
      debugLog("[Playback] Transitioning speaking → listening");
      updatePipelineState("listening");
    }
  }, [applySDLPlaybackSignal, updatePipelineState]);

  const { initAudio, playChunkStreaming, stopAudio } = useAudio({
    onPlaybackComplete: handlePlaybackComplete
  });

  const terminateVoice = useCallback(() => {
    applyLifecycleSignal({ type: "terminal_failure" });
    stopAudio();
    applySDLPlaybackSignal({ type: "interrupted" });
    cleanupTransport();
  }, [applyLifecycleSignal, applySDLPlaybackSignal, cleanupTransport, stopAudio]);

  useEffect(() => {
    return () => {
      // Unmount is terminal for this hook instance. Do not emit React state
      // updates, but invalidate pending media/offer work and stop playback.
      terminalErrorRef.current = true;
      stopAudio();
      cleanupTransport();
    };
  }, [cleanupTransport, stopAudio]);

  // VAD for instant interruption detection
  // Only active when TTS is playing (speaking state)
  const handleVADSpeechDetected = useCallback(() => {
    if (terminalErrorRef.current || statusRef.current !== "connected") {
      return;
    }
    debugLog("[VAD] Speech detected during TTS - interrupting immediately");

    // 1. Stop audio playback instantly
    stopAudio();
    applySDLPlaybackSignal({ type: "interrupted" });

    // 2. Update state to listening
    updatePipelineState("listening");

    // 3. Tell server to stop generating TTS
    if (channelRef.current?.readyState === "open") {
      channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
      debugLog("[VAD] Sent stop_tts to server");
    }

    // Note: Deepgram transcript will arrive shortly and be processed normally
  }, [applySDLPlaybackSignal, stopAudio, updatePipelineState]);

  const vadState = useVAD({
    // Only enable VAD when AI is speaking (TTS playing)
    enabled: pipelineState === "speaking",

    onSpeechDetected: handleVADSpeechDetected,

    // Use balanced preset for good speed/accuracy trade-off
    // Tune these if needed:
    // - ultraFast: faster but more false positives
    // - conservative: slower but more accurate
    ...VAD_PRESETS.balanced,

    // Enable debug logs to see VAD activity
    debug: process.env.NODE_ENV !== "production",
  });

  // Toggle microphone mute
  const toggleMicMute = useCallback(() => {
    if (!localStreamRef.current) return;

    const audioTrack = localStreamRef.current.getAudioTracks()[0];
    if (audioTrack) {
      audioTrack.enabled = !audioTrack.enabled;
      setIsMicMuted(!audioTrack.enabled);
      log(`Microphone ${audioTrack.enabled ? "unmuted" : "muted"}`);
      debugLog(`[Mic] ${audioTrack.enabled ? "Unmuted" : "Muted"}`);
    }
  }, [log]);

  // Toggle TTS playback
  const toggleTTS = useCallback(() => {
    const newState = !isTTSEnabledRef.current;
    isTTSEnabledRef.current = newState;
    setIsTTSEnabled(newState);
    log(`TTS ${newState ? "enabled" : "disabled"}`);
    debugLog(`[TTS] ${newState ? "Enabled" : "Disabled"}`);

    // If disabling TTS while speaking, stop current playback
    if (!newState && pipelineStateRef.current === "speaking") {
      stopAudio();
      updatePipelineState("listening");
    }
  }, [log, stopAudio, updatePipelineState]);

  const connect = useCallback(async (overrides: ConnectOverrides = {}) => {
    debugLog("[Connection] Connect called, current state:", pipelineStateRef.current);
    if (pcRef.current) {
      debugLog("[Connection] Already connected, ignoring");
      return;
    }
    const effectiveAgentId = overrides.agentId ?? agentId;
    const effectiveSessionId = overrides.sessionId ?? sessionId;

    applyLifecycleSignal({ type: "connect_requested" });
    log("Connecting...");

    const pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
    const attemptId = connectionAttemptRef.current + 1;
    connectionAttemptRef.current = attemptId;
    pcRef.current = pc;
    const isActiveConnection = () =>
      connectionAttemptRef.current === attemptId && pcRef.current === pc;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 48000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      if (!isActiveConnection()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      localStreamRef.current = stream;
      stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));
      log("Microphone ready");
    } catch (e) {
      const error = e as Error;
      if (!isActiveConnection()) {
        return;
      }
      log(`Mic error: ${error.name}`);
      onError?.(`Microphone error: ${error.name}`);
      terminateVoice();
      return;
    }

    pc.addEventListener("iceconnectionstatechange", () => {
      log(`ICE: ${pc.iceConnectionState}`);
    });

    const channel = pc.createDataChannel("chat");
    channelRef.current = channel;
    debugLog("[Connection] DataChannel created, readyState:", channel.readyState);

    channel.addEventListener("open", () => {
      if (!isActiveConnection() || channelRef.current !== channel) {
        return;
      }
      debugLog("[Connection] DataChannel opened");
      log("Transport connected; checking voice providers...");
      applyLifecycleSignal({ type: "transport_open" });
      clearReadinessTimer();
      readinessTimerRef.current = setTimeout(() => {
        readinessTimerRef.current = null;
        if (!isActiveConnection() || channelRef.current !== channel) {
          return;
        }
        log("Voice readiness timed out; text mode remains available");
        onError?.("Voice providers did not become ready in time. Continue in text mode.");
        terminateVoice();
      }, VOICE_READY_TIMEOUT_MS);
    });

    channel.addEventListener("close", () => {
      if (!isActiveConnection() || channelRef.current !== channel) {
        return;
      }
      debugLog("[Connection] DataChannel closed");
      log("Disconnected");
      applyLifecycleSignal({ type: "channel_closed" });
      stopAudio();
      cleanupTransport();
      playDisconnectSound();
    });

    channel.addEventListener("message", (e) => {
      if (
        !isActiveConnection() ||
        channelRef.current !== channel ||
        terminalErrorRef.current
      ) {
        return;
      }
      try {
        const data = JSON.parse(e.data);
        // Skip verbose logging for tts_chunk (already logged in case handler)
        if (data.type !== "tts_chunk") {
          debugLog(`[Event] ${data.type}`, data);
        }
        if (
          data.type !== "ready" &&
          data.type !== "error" &&
          statusRef.current !== "connected"
        ) {
          debugLog(`[Event] Ignoring ${String(data.type)} before backend readiness`);
          return;
        }

        switch (data.type) {
          case "ready": {
            clearReadinessTimer();
            const transition = applyLifecycleSignal({ type: "backend_ready" });
            if (transition.next.status !== "connected") {
              break;
            }
            if (transition.previous.status === "connected") {
              break;
            }
            log("Ready - you can start speaking");
            playReadySound();
            break;
          }

          case "transcript":
            applyLifecycleSignal({ type: "transcript_segment" });
            onTranscript?.({
              text: typeof data.text === "string" ? data.text : "",
              isFinal: data.is_final === true,
              speechFinal: data.speech_final === true,
            });

            const currentState = pipelineStateRef.current;
            debugLog(`[Transcript] Received: "${data.text}" (final=${data.is_final}, state=${currentState})`);

            // IMMEDIATE INTERRUPTION: If we're speaking (AI talking) and user talks, stop TTS immediately
            // Note: "speaking" state means AI is talking AND we're listening for interruptions
            if (typeof data.text === "string" && data.text.trim() && currentState === "speaking") {
              debugLog("[Interrupt] User spoke during TTS playback");
              debugLog(`[Interrupt] Transcript: "${data.text}" (final=${data.is_final})`);
              log("🛑 Interrupting TTS immediately");

              // Stop audio playback immediately
              stopAudio();
              applySDLPlaybackSignal({ type: "interrupted" });
              isFirstTTSChunkRef.current = true;  // Reset for next session
              updatePipelineState("listening");

              // Tell server to stop TTS generation immediately
              if (channelRef.current?.readyState === "open") {
                channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
                debugLog("[Interrupt] Sent stop_tts to server");
              }
            } else if (
              typeof data.text === "string" &&
              data.text.trim() &&
              currentState !== "speaking"
            ) {
              debugLog(`[Transcript] Not interrupting - state is ${currentState}, not speaking`);
            }

            // Segment finality is not end-of-turn finality. The backend emits
            // turn_committed only after the selected turn detector confirms it.
            break;

          case "turn_committed":
            log("Turn committed; generating a response");
            applyLifecycleSignal({ type: "turn_committed" });
            break;

          case "canvas_update":
            log(`Canvas: ${data.operations.length} ops`);
            onCanvasUpdate?.(data.operations);
            break;

          case "sdl_scene":
            log(`SDL scene: ${data.sdl?.steps?.length} steps`);
            onSDLScene?.(data.sdl);
            break;

          case "sdl_start":
            log(`SDL sequence started: ${data.total_steps} steps`);
            sdlStepChunkTrackerRef.current = null;
            applySDLPlaybackSignal({
              type: "sequence_started",
              sequenceId: data.sequence_id,
            });
            isFirstTTSChunkRef.current = true;
            onSDLStart?.(data.sdl, data.sequence_id, data.total_steps);
            updatePipelineState("speaking");
            break;

          case "sdl_step":
            log(`SDL step ${data.step_index}`);
            sdlStepChunkTrackerRef.current = {
              sequenceId: data.sequence_id,
              stepIndex: data.step_index,
              firstChunkSent: false,
            };
            break;

          case "tts_step_complete":
            log(`SDL step ${data.step_index} audio done (${data.audio_duration_ms}ms)`);
            onSDLStepComplete?.(data.sequence_id, data.step_index, data.audio_duration_ms);
            sdlStepChunkTrackerRef.current = null;
            break;

          case "sdl_complete":
            log("SDL sequence complete");
            sdlStepChunkTrackerRef.current = null;
            isFirstTTSChunkRef.current = true;
            applySDLPlaybackSignal({
              type: "backend_complete",
              sequenceId: data.sequence_id,
              reason: data.reason === "interrupted" ? "interrupted" : "completed",
            });
            break;

          case "llm_response":
            log(`LLM: ${data.text.substring(0, 50)}...`);
            onLLMResponse?.(data.text);
            // Still in processing, waiting for TTS
            break;

          case "tts_started":
            log("TTS started");
            isFirstTTSChunkRef.current = true;  // Reset for new TTS session

            // Only enter speaking state if TTS is enabled
            if (isTTSEnabledRef.current) {
              updatePipelineState("speaking");
            } else {
              debugLog("[TTS] TTS disabled, skipping playback");
              // Tell server to stop sending TTS chunks
              if (channelRef.current?.readyState === "open") {
                channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
              }
            }
            break;

          case "tts_chunk": {
            // Skip TTS playback if TTS is disabled
            if (!isTTSEnabledRef.current) {
              debugLog("[TTS] Skipping chunk (TTS disabled)");
              break;
            }

            // Decode and play audio
            const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
            const isFirstChunk = isFirstTTSChunkRef.current;

            // Ensure we're in speaking state when receiving TTS chunks
            if (pipelineStateRef.current !== "speaking") {
              debugLog(`[TTS] State was ${pipelineStateRef.current}, setting to speaking`);
              updatePipelineState("speaking");
            }

            if (isFirstChunk) {
              isFirstTTSChunkRef.current = false;
              debugLog(`[TTS] First chunk: ${bytes.length} bytes`);
            }

            // Fire onSDLStepAudioStart on first chunk for each SDL step
            const tracker = sdlStepChunkTrackerRef.current;
            if (tracker && !tracker.firstChunkSent && data.sequence_id) {
              tracker.firstChunkSent = true;
              onSDLStepAudioStart?.(tracker.sequenceId, tracker.stepIndex);
            }

            if (typeof data.sequence_id === "string") {
              applySDLPlaybackSignal({
                type: "audio_scheduled",
                sequenceId: data.sequence_id,
              });
            }

            playChunkStreaming(bytes, 16000, isFirstChunk);
            break;
          }

          case "tts_complete":
            log("TTS complete (backend done sending)");
            isFirstTTSChunkRef.current = true;  // Reset for next session
            // NOTE: Don't change state here! State will change to "listening" when
            // audio playback actually completes (via handlePlaybackComplete callback)
            debugLog("[TTS] Backend finished sending chunks, waiting for playback to complete");
            break;

          case "tts_interrupted":
            log(`TTS interrupted (${data.chunks_sent} chunks sent)`);
            stopAudio();
            isFirstTTSChunkRef.current = true;  // Reset for next session
            sdlStepChunkTrackerRef.current = null;
            applySDLPlaybackSignal({
              type: "interrupted",
              ...(typeof data.sequence_id === "string"
                ? { sequenceId: data.sequence_id }
                : {}),
            });
            updatePipelineState("listening");
            break;

          case "pipeline_metrics":
            log(`Metrics: total=${data.latency_total_ms}ms llm=${data.latency_llm_ms}ms tts=${data.latency_tts_ms}ms`);
            onPipelineMetrics?.(data);
            break;

          case "error": {
            const message =
              typeof data.message === "string" && data.message.trim()
                ? data.message
                : "Voice pipeline error";
            log(`Error: ${message}`);
            playErrorSound();
            onError?.(message);
            const errorSignal = lifecycleSignalForBackendError(data);
            if (errorSignal.type === "terminal_failure") {
              clearReadinessTimer();
              terminateVoice();
            } else {
              applyLifecycleSignal(errorSignal);
            }
            break;
          }
        }
      } catch (err) {
        console.error("Failed to parse message:", err);
      }
    });

    let offerAbortController: AbortController | null = null;
    try {
      const offer = await pc.createOffer();
      if (!isActiveConnection()) return;
      await pc.setLocalDescription(offer);
      if (!isActiveConnection()) return;

      // Wait for ICE gathering without leaving the fallback timer or listener alive.
      await new Promise<void>((resolve) => {
        if (pc.iceGatheringState === "complete") {
          resolve();
          return;
        }

        let settled = false;
        let timeout: ReturnType<typeof setTimeout> | null = null;
        const finish = () => {
          if (settled) return;
          settled = true;
          pc.removeEventListener("icegatheringstatechange", check);
          if (timeout !== null) {
            clearTimeout(timeout);
          }
          if (cancelIceWaitRef.current === finish) {
            cancelIceWaitRef.current = null;
          }
          resolve();
        };
        const check = () => {
          if (pc.iceGatheringState === "complete") {
            finish();
          }
        };
        cancelIceWaitRef.current = finish;
        pc.addEventListener("icegatheringstatechange", check);
        timeout = setTimeout(finish, 2_000);
      });
      if (!isActiveConnection()) return;

      offerAbortController = new AbortController();
      offerAbortControllerRef.current = offerAbortController;
      const authHeaders = await getAuthHeaders();
      if (!isActiveConnection()) return;
      const response = await fetch(`${apiUrl}/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        signal: offerAbortController.signal,
        body: JSON.stringify({
          sdp: pc.localDescription?.sdp,
          type: pc.localDescription?.type,
          canvas_mode: canvasMode,
          agent_id: effectiveAgentId,
          session_id: effectiveSessionId,
        }),
      });
      if (!isActiveConnection()) return;
      if (!response.ok) {
        throw new Error(`offer returned HTTP ${response.status}`);
      }

      const answer = await response.json();
      if (!isActiveConnection()) return;
      await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
      if (!isActiveConnection()) return;
      if (answer.session_id) {
        onSessionReady?.(answer.session_id);
      }

      debugLog("[Connection] Connection setup complete, waiting for data channel to open");
    } catch (error) {
      if (!isActiveConnection() || (error instanceof Error && error.name === "AbortError")) {
        return;
      }
      const message = error instanceof Error ? error.message : "network error";
      log(`Voice connection failed: ${message}`);
      onError?.("Voice connection failed. Continue in text mode and retry later.");
      terminateVoice();
    } finally {
      if (offerAbortControllerRef.current === offerAbortController) {
        offerAbortControllerRef.current = null;
      }
    }
  }, [
    apiUrl,
    agentId,
    applyLifecycleSignal,
    applySDLPlaybackSignal,
    canvasMode,
    clearReadinessTimer,
    cleanupTransport,
    log,
    onSessionReady,
    onTranscript,
    onLLMResponse,
    onCanvasUpdate,
    onSDLScene,
    onSDLStart,
    onSDLStepAudioStart,
    onSDLStepComplete,
    onPipelineMetrics,
    onError,
    playChunkStreaming,
    sessionId,
    stopAudio,
    terminateVoice,
    updatePipelineState,
  ]);

  const disconnect = useCallback(() => {
    applyLifecycleSignal({ type: "disconnect_requested" });
    stopAudio();
    cleanupTransport();

    playDisconnectSound();
    log("Disconnected");
  }, [applyLifecycleSignal, cleanupTransport, log, stopAudio]);

  return {
    status,
    pipelineState,
    connect,
    disconnect,
    initAudio,
    vadState,
    isMicMuted,
    isTTSEnabled,
    toggleMicMute,
    toggleTTS,
  };
}
