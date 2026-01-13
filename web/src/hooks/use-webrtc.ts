"use client";

import { useState, useRef, useCallback } from "react";
import { useAudio } from "./use-audio";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";

export interface TranscriptEvent {
  text: string;
  isFinal: boolean;
}

export interface CanvasOperation {
  id?: string;
  action: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  color?: string;
  fill?: string;
  stroke_width?: number;
  points?: [number, number][];
  text?: string;
  font_size?: number;
  font_family?: string;
  target_id?: string;
}

export interface LatencyMetrics {
  vadLatency?: number;
  sttFirstTranscript?: number;
  sttFinalTranscript?: number;
  llmFirstChunk?: number;
  llmComplete?: number;
  ttsFirstChunk?: number;
  ttsComplete?: number;
  totalPipeline?: number;
}

interface UseWebRTCOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  onTranscript?: (event: TranscriptEvent) => void;
  onLLMResponse?: (text: string) => void;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onError?: (message: string) => void;
  onLog?: (message: string) => void;
  onInterruptionDetected?: (message: string) => void;
  onTTSCancelled?: (chunksSent: number) => void;
  onLatencyUpdate?: (metrics: LatencyMetrics) => void;
}

export function useWebRTC(options: UseWebRTCOptions = {}) {
  const {
    apiUrl = "http://localhost:8000",
    canvasMode = false,
    onTranscript,
    onLLMResponse,
    onCanvasUpdate,
    onError,
    onLog,
    onInterruptionDetected,
    onTTSCancelled,
    onLatencyUpdate,
  } = options;

  const [status, setStatus] = useState<ConnectionStatus>("idle");
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const isTTSActiveRef = useRef(false);
  const isFirstChunkRef = useRef(true);
  const latencyMetricsRef = useRef<LatencyMetrics>({});
  const utteranceStartRef = useRef<number>(0);
  const ttsStartTimeRef = useRef<number>(0);
  // Grace period (ms) after TTS starts before client-side interrupts are allowed
  const TTS_INTERRUPT_GRACE_MS = 500;
  const { initAudio, playChunkStreaming, stopAudio } = useAudio();

  const log = useCallback((msg: string) => {
    onLog?.(msg);
    console.log(`[WebRTC] ${msg}`);
  }, [onLog]);

  const connect = useCallback(async () => {
    if (pcRef.current) return;
    
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

    channel.addEventListener("open", () => {
      log("Connected");
      setStatus("connected");
    });

    channel.addEventListener("close", () => {
      log("Disconnected");
      setStatus("disconnected");
    });

    channel.addEventListener("message", (e) => {
      try {
        const data = JSON.parse(e.data);

        if (data.type === "transcript") {
          const t = performance.now();
          console.log(`[WebRTC] 📝 Transcript at ${t.toFixed(0)}ms: "${data.text}" (final=${data.is_final}, TTS active=${isTTSActiveRef.current})`);

          // Track first transcript timing
          if (data.text.trim() && !latencyMetricsRef.current.sttFirstTranscript) {
            utteranceStartRef.current = t;
            latencyMetricsRef.current.sttFirstTranscript = 0; // From user speech start
            console.log(`[Latency] 🎤 First transcript received`);
          }

          // Track final transcript timing
          if (data.is_final && data.text.trim()) {
            const sttLatency = t - utteranceStartRef.current;
            latencyMetricsRef.current.sttFinalTranscript = sttLatency;
            console.log(`[Latency] ✅ STT complete: ${sttLatency.toFixed(0)}ms`);
          }

          // 🔥 CRITICAL INTERRUPTION LOGIC:
          // If we're receiving a transcript while TTS is playing, WE'RE SPEAKING!
          // This means the user is interrupting the AI.
          // Stop audio IMMEDIATELY - don't wait for server round-trip.
          //
          // Flow:
          // 1. User speaks → microphone captures
          // 2. Audio sent to server → STT transcribes (~50-100ms)
          // 3. Transcript sent back to client
          // 4. We detect: transcript received + TTS active = INTERRUPTION
          // 5. Stop audio instantly (total latency: ~50-150ms)
          // 6. Send interrupt signal to server to stop TTS generation
          //
          // Grace period: Wait for TTS_INTERRUPT_GRACE_MS after TTS starts
          // to avoid false interrupts from trailing audio/echo
          const timeSinceTTSStart = t - ttsStartTimeRef.current;
          const inGracePeriod = timeSinceTTSStart < TTS_INTERRUPT_GRACE_MS;
          
          if (isTTSActiveRef.current && data.text.trim() && !inGracePeriod) {
            console.log(`[WebRTC] 🚨 CLIENT-SIDE INTERRUPTION: User speaking while TTS active (${timeSinceTTSStart.toFixed(0)}ms since TTS start) - STOPPING AUDIO NOW`);
            stopAudio();
            isTTSActiveRef.current = false;
            isFirstChunkRef.current = true;
            ttsStartTimeRef.current = 0;
            log(`⚠️ Interrupted by user speech`);

            // Send interrupt signal to server so it stops generating TTS immediately
            if (channelRef.current?.readyState === "open") {
              channelRef.current.send(JSON.stringify({
                type: "client_interrupt",
                timestamp: Date.now()
              }));
              console.log(`[WebRTC] 📤 Sent client_interrupt to server`);
            }
          } else if (isTTSActiveRef.current && data.text.trim() && inGracePeriod) {
            console.log(`[WebRTC] 🛡️ Ignoring transcript during TTS grace period (${timeSinceTTSStart.toFixed(0)}ms < ${TTS_INTERRUPT_GRACE_MS}ms)`);
          }

          onTranscript?.({ text: data.text, isFinal: data.is_final });
        } else if (data.type === "canvas_update") {
          log(`Canvas: ${data.operations.length} ops`);
          onCanvasUpdate?.(data.operations);
        } else if (data.type === "llm_response") {
          const t = performance.now();
          const llmLatency = t - (utteranceStartRef.current + (latencyMetricsRef.current.sttFinalTranscript || 0));
          latencyMetricsRef.current.llmComplete = llmLatency;
          console.log(`[Latency] 🧠 LLM complete: ${llmLatency.toFixed(0)}ms`);

          onLLMResponse?.(data.text);
          // Reset for next TTS session
          isFirstChunkRef.current = true;
        } else if (data.type === "interruption_ack") {
          const t = performance.now();
          console.log(`[WebRTC] 🚨 INTERRUPTION_ACK received at ${t.toFixed(0)}ms: ${data.message}`);
          log(`⚠️ INTERRUPTION: ${data.message}`);

          // Stop playing TTS immediately and clear queue
          console.log(`[WebRTC] Calling stopAudio()...`);
          stopAudio();
          console.log(`[WebRTC] stopAudio() returned`);

          isTTSActiveRef.current = false;
          isFirstChunkRef.current = true;
          ttsStartTimeRef.current = 0;

          // Notify callback
          onInterruptionDetected?.(data.message);
          console.log(`[WebRTC] ✓ Interruption handled`);
        } else if (data.type === "tts_cancelled") {
          const t = performance.now();
          console.log(`[WebRTC] 🛑 TTS_CANCELLED received at ${t.toFixed(0)}ms: ${data.chunks_sent} chunks sent`);
          log(`TTS cancelled: ${data.chunks_sent} chunks`);

          // Stop and clear audio immediately
          stopAudio();
          isTTSActiveRef.current = false;
          isFirstChunkRef.current = true;
          ttsStartTimeRef.current = 0;

          // Notify callback
          onTTSCancelled?.(data.chunks_sent);
        } else if (data.type === "tts_audio_chunk") {
          const t = performance.now();
          // Mark first chunk of new session
          const isNewSession = isFirstChunkRef.current;
          if (isFirstChunkRef.current) {
            isFirstChunkRef.current = false;
            isTTSActiveRef.current = true;
            ttsStartTimeRef.current = t; // Track when TTS started for grace period

            const ttsLatency = t - (utteranceStartRef.current + (latencyMetricsRef.current.sttFinalTranscript || 0) + (latencyMetricsRef.current.llmComplete || 0));
            latencyMetricsRef.current.ttsFirstChunk = ttsLatency;
            console.log(`[Latency] 🔊 TTS first chunk: ${ttsLatency.toFixed(0)}ms`);
            console.log(`[WebRTC] 🎤 First TTS chunk received at ${t.toFixed(0)}ms, starting new session (grace period until ${(t + TTS_INTERRUPT_GRACE_MS).toFixed(0)}ms)`);
            log(`Starting new TTS session`);
          }

          const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
          console.log(`[WebRTC] 🔊 TTS chunk received at ${t.toFixed(0)}ms (${bytes.length} bytes, isNewSession=${isNewSession})`);
          playChunkStreaming(bytes, 16000, isNewSession);
        } else if (data.type === "tts_audio_end") {
          const t = performance.now();
          const totalLatency = t - utteranceStartRef.current;
          latencyMetricsRef.current.ttsComplete = t - (utteranceStartRef.current + (latencyMetricsRef.current.sttFinalTranscript || 0) + (latencyMetricsRef.current.llmComplete || 0));
          latencyMetricsRef.current.totalPipeline = totalLatency;

          console.log(`[Latency] ✅ TTS complete: ${latencyMetricsRef.current.ttsComplete?.toFixed(0)}ms`);
          console.log(`[Latency] 🎯 TOTAL PIPELINE: ${totalLatency.toFixed(0)}ms`);
          console.log(`[WebRTC] ✅ TTS_AUDIO_END received at ${t.toFixed(0)}ms`);

          // Send metrics to callback
          if (onLatencyUpdate) {
            onLatencyUpdate({ ...latencyMetricsRef.current });
          }

          // Reset for next utterance
          latencyMetricsRef.current = {};

          isTTSActiveRef.current = false;
          isFirstChunkRef.current = true;
          ttsStartTimeRef.current = 0;
          log(`TTS playback completed`);
        } else if (data.type === "vad_speech_detected") {
          // VAD latency from server
          latencyMetricsRef.current.vadLatency = data.latency_ms;
          console.log(`[Latency] 🎙️ VAD: ${data.latency_ms.toFixed(2)}ms`);
        } else if (data.type === "error") {
          log(`Error: ${data.message}`);
          onError?.(data.message);
        }
      } catch {
        // Ignore parse errors
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

    const response = await fetch(`${apiUrl}/offer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription?.sdp,
        type: pc.localDescription?.type,
        canvas_mode: canvasMode,
      }),
    });

    if (!response.ok) {
      log(`Error: ${response.status}`);
      setStatus("error");
      return;
    }

    const answer = await response.json();
    await pc.setRemoteDescription(answer);
  }, [
    apiUrl,
    canvasMode,
    log,
    onTranscript,
    onLLMResponse,
    onCanvasUpdate,
    onError,
    onInterruptionDetected,
    onTTSCancelled,
    playChunkStreaming,
    stopAudio,
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
    
    setStatus("disconnected");
    log("Disconnected");
  }, [log]);

  return {
    status,
    connect,
    disconnect,
    initAudio,
  };
}

