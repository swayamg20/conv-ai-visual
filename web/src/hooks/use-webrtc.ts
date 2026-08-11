"use client";

import { useState, useRef, useCallback } from "react";
import { useAudio } from "./use-audio";
import { useVAD, VAD_PRESETS } from "./use-vad";
import { playReadySound, playDisconnectSound, playErrorSound } from "@/lib/sounds";
import { getAuthHeaders } from "@/lib/firebase";
import type { CanvasOperation } from "@/features/canvas/types";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";
export type PipelineState = "idle" | "listening" | "processing" | "speaking";

interface ConnectOverrides {
  agentId?: string;
  sessionId?: string;
}

export interface TranscriptEvent {
  text: string;
  isFinal: boolean;
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
  onSDLComplete?: (sequenceId: string) => void;
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

  const pipelineStateRef = useRef<PipelineState>("idle");  // Ref for latest state in callbacks
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const isFirstTTSChunkRef = useRef(true);  // Track if this is first chunk of TTS session
  const isTTSEnabledRef = useRef(true);  // Ref for TTS enabled state
  const sdlStepChunkTrackerRef = useRef<{ sequenceId: string; stepIndex: number; firstChunkSent: boolean } | null>(null);

  const log = useCallback((msg: string) => {
    onLog?.(msg);
    console.log(`[WebRTC] ${msg}`);
  }, [onLog]);

  const updatePipelineState = useCallback((state: PipelineState) => {
    const prevState = pipelineStateRef.current;
    console.log(`[State] ${prevState} → ${state}`);
    pipelineStateRef.current = state;  // Update ref immediately
    setPipelineState(state);
    onStateChange?.(state);
    log(`State: ${state}`);
  }, [onStateChange, log]);

  // Callback when audio playback completes
  const handlePlaybackComplete = useCallback(() => {
    console.log("[Playback] ✅ All audio chunks finished playing");
    // Only transition to listening if we're currently in speaking state
    if (pipelineStateRef.current === "speaking") {
      console.log("[Playback] → Transitioning speaking → listening");
      updatePipelineState("listening");
    }
  }, [updatePipelineState]);

  const { initAudio, playChunkStreaming, stopAudio } = useAudio({
    onPlaybackComplete: handlePlaybackComplete
  });

  // VAD for instant interruption detection
  // Only active when TTS is playing (speaking state)
  const handleVADSpeechDetected = useCallback(() => {
    console.log("[VAD] 🎤 Speech detected during TTS - interrupting immediately!");

    // 1. Stop audio playback instantly
    stopAudio();

    // 2. Update state to listening
    updatePipelineState("listening");

    // 3. Tell server to stop generating TTS
    if (channelRef.current?.readyState === "open") {
      channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
      console.log("[VAD] ✓ Sent stop_tts to server");
    }

    // Note: Deepgram transcript will arrive shortly and be processed normally
  }, [stopAudio, updatePipelineState]);

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
    debug: true,
  });

  // Toggle microphone mute
  const toggleMicMute = useCallback(() => {
    if (!localStreamRef.current) return;

    const audioTrack = localStreamRef.current.getAudioTracks()[0];
    if (audioTrack) {
      audioTrack.enabled = !audioTrack.enabled;
      setIsMicMuted(!audioTrack.enabled);
      log(`Microphone ${audioTrack.enabled ? "unmuted" : "muted"}`);
      console.log(`[Mic] ${audioTrack.enabled ? "🎤 Unmuted" : "🔇 Muted"}`);
    }
  }, [log]);

  // Toggle TTS playback
  const toggleTTS = useCallback(() => {
    const newState = !isTTSEnabledRef.current;
    isTTSEnabledRef.current = newState;
    setIsTTSEnabled(newState);
    log(`TTS ${newState ? "enabled" : "disabled"}`);
    console.log(`[TTS] ${newState ? "🔊 Enabled" : "🔇 Disabled"}`);

    // If disabling TTS while speaking, stop current playback
    if (!newState && pipelineStateRef.current === "speaking") {
      stopAudio();
      updatePipelineState("listening");
    }
  }, [log, stopAudio, updatePipelineState]);

  const connect = useCallback(async (overrides: ConnectOverrides = {}) => {
    console.log("[Connection] Connect called, current state:", pipelineStateRef.current);
    if (pcRef.current) {
      console.log("[Connection] Already connected, ignoring");
      return;
    }

    const effectiveAgentId = overrides.agentId ?? agentId;
    const effectiveSessionId = overrides.sessionId ?? sessionId;

    setStatus("connecting");
    log("Connecting...");

    const pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
    pcRef.current = pc;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 48000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      localStreamRef.current = stream;
      stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));
      log("Microphone ready");
    } catch (e) {
      const error = e as Error;
      log(`Mic error: ${error.name}`);
      onError?.(`Microphone error: ${error.name}`);
    }

    pc.addEventListener("iceconnectionstatechange", () => {
      log(`ICE: ${pc.iceConnectionState}`);
    });

    const channel = pc.createDataChannel("chat");
    channelRef.current = channel;
    console.log("[Connection] DataChannel created, readyState:", channel.readyState);

    channel.addEventListener("open", () => {
      console.log("[Connection] DataChannel opened");
      log("Connected");
      setStatus("connected");
      updatePipelineState("listening");
      console.log("[Connection] State set to listening");
    });

    channel.addEventListener("close", () => {
      console.log("[Connection] DataChannel closed");
      log("Disconnected");
      isFirstTTSChunkRef.current = true;  // Reset
      setStatus("disconnected");
      updatePipelineState("idle");
      playDisconnectSound();
    });

    channel.addEventListener("message", (e) => {
      try {
        const data = JSON.parse(e.data);
        // Skip verbose logging for tts_chunk (already logged in case handler)
        if (data.type !== "tts_chunk") {
          console.log(`[Event] ${data.type}`, data);
        }

        switch (data.type) {
          case "ready":
            log("Ready - you can start speaking");
            playReadySound();
            break;

          case "transcript":
            onTranscript?.({ text: data.text, isFinal: data.is_final });

            const currentState = pipelineStateRef.current;
            console.log(`[Transcript] Received: "${data.text}" (final=${data.is_final}, state=${currentState})`);

            // IMMEDIATE INTERRUPTION: If we're speaking (AI talking) and user talks, stop TTS immediately
            // Note: "speaking" state means AI is talking AND we're listening for interruptions
            if (data.text.trim() && currentState === "speaking") {
              console.log(`[Interrupt] 🛑 INTERRUPTION DETECTED - User spoke during TTS playback`);
              console.log(`[Interrupt] 📝 Transcript: "${data.text}" (final=${data.is_final})`);
              log("🛑 Interrupting TTS immediately");

              // Stop audio playback immediately
              stopAudio();
              isFirstTTSChunkRef.current = true;  // Reset for next session
              updatePipelineState("listening");

              // Tell server to stop TTS generation immediately
              if (channelRef.current?.readyState === "open") {
                channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
                console.log("[Interrupt] ✓ Sent stop_tts to server");
              }
            } else if (data.text.trim() && currentState !== "speaking") {
              console.log(`[Transcript] Not interrupting - state is ${currentState}, not speaking`);
            }

            // Process final transcripts (even after interruption)
            if (data.is_final && data.text.trim()) {
              // Only move to processing if we're listening (not already processing)
              if (pipelineStateRef.current === "listening") {
                console.log(`[Transcript] ✓ Processing final transcript through LLM`);
                updatePipelineState("processing");
              }
            }
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
            onSDLComplete?.(data.sequence_id);
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
              console.log("[TTS] TTS disabled, skipping playback");
              // Tell server to stop sending TTS chunks
              if (channelRef.current?.readyState === "open") {
                channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
              }
            }
            break;

          case "tts_chunk": {
            // Skip TTS playback if TTS is disabled
            if (!isTTSEnabledRef.current) {
              console.log("[TTS] Skipping chunk (TTS disabled)");
              break;
            }

            // Decode and play audio
            const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
            const isFirstChunk = isFirstTTSChunkRef.current;

            // Ensure we're in speaking state when receiving TTS chunks
            if (pipelineStateRef.current !== "speaking") {
              console.log(`[TTS] State was ${pipelineStateRef.current}, setting to speaking`);
              updatePipelineState("speaking");
            }

            if (isFirstChunk) {
              isFirstTTSChunkRef.current = false;
              console.log(`[TTS] First chunk: ${bytes.length} bytes`);
            }

            // Fire onSDLStepAudioStart on first chunk for each SDL step
            const tracker = sdlStepChunkTrackerRef.current;
            if (tracker && !tracker.firstChunkSent && data.sequence_id) {
              tracker.firstChunkSent = true;
              onSDLStepAudioStart?.(tracker.sequenceId, tracker.stepIndex);
            }

            playChunkStreaming(bytes, 16000, isFirstChunk);
            break;
          }

          case "tts_complete":
            log("TTS complete (backend done sending)");
            isFirstTTSChunkRef.current = true;  // Reset for next session
            // NOTE: Don't change state here! State will change to "listening" when
            // audio playback actually completes (via handlePlaybackComplete callback)
            console.log("[TTS] Backend finished sending chunks, waiting for playback to complete...");
            break;

          case "tts_interrupted":
            log(`TTS interrupted (${data.chunks_sent} chunks sent)`);
            stopAudio();
            isFirstTTSChunkRef.current = true;  // Reset for next session
            sdlStepChunkTrackerRef.current = null;
            // Clean up any active SDL sequence timelines
            if (data.sequence_id) {
              onSDLComplete?.(data.sequence_id);
            }
            updatePipelineState("listening");
            break;

          case "pipeline_metrics":
            log(`Metrics: total=${data.latency_total_ms}ms llm=${data.latency_llm_ms}ms tts=${data.latency_tts_ms}ms`);
            onPipelineMetrics?.(data);
            break;

          case "error":
            log(`Error: ${data.message}`);
            playErrorSound();
            onError?.(data.message);
            updatePipelineState("listening");
            break;
        }
      } catch (err) {
        console.error("Failed to parse message:", err);
      }
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    // Wait for ICE gathering
    await new Promise<void>((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const check = () => {
        if (pc.iceGatheringState === "complete") {
          pc.removeEventListener("icegatheringstatechange", check);
          resolve();
        }
      };
      pc.addEventListener("icegatheringstatechange", check);
      setTimeout(resolve, 2000);
    });

    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${apiUrl}/offer`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify({
        sdp: pc.localDescription?.sdp,
        type: pc.localDescription?.type,
        canvas_mode: canvasMode,
        agent_id: effectiveAgentId,
        session_id: effectiveSessionId,
      }),
    });

    if (!response.ok) {
      log(`Error: ${response.status}`);
      setStatus("error");
      return;
    }

    const answer = await response.json();
    if (answer.session_id) {
      onSessionReady?.(answer.session_id);
    }
    await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });

    console.log("[Connection] Connection setup complete, waiting for datachannel to open...");
  }, [
    apiUrl,
    agentId,
    canvasMode,
    log,
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
    playChunkStreaming,
    sessionId,
    stopAudio,
    updatePipelineState,
  ]);

  const disconnect = useCallback(() => {
    try {
      channelRef.current?.close();
    } catch {
      // Ignore
    }

    localStreamRef.current?.getTracks().forEach((track) => track.stop());

    if (pcRef.current) {
      pcRef.current.getSenders().forEach((sender) => sender.track?.stop());
      pcRef.current.close();
    }

    pcRef.current = null;
    channelRef.current = null;
    localStreamRef.current = null;
    isFirstTTSChunkRef.current = true;  // Reset

    setStatus("disconnected");
    updatePipelineState("idle");
    playDisconnectSound();
    log("Disconnected");
  }, [log, updatePipelineState]);

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
