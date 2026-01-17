"use client";

import { useState, useRef, useCallback } from "react";
import { useAudio } from "./use-audio";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";
export type PipelineState = "idle" | "listening" | "processing" | "speaking";

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

interface UseWebRTCOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  onTranscript?: (event: TranscriptEvent) => void;
  onLLMResponse?: (text: string) => void;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onError?: (message: string) => void;
  onLog?: (message: string) => void;
  onStateChange?: (state: PipelineState) => void;
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
    onStateChange,
  } = options;

  const [status, setStatus] = useState<ConnectionStatus>("idle");
  const [pipelineState, setPipelineState] = useState<PipelineState>("idle");
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const isFirstTTSChunkRef = useRef(true);  // Track if this is first chunk of TTS session
  const { initAudio, playChunkStreaming, stopAudio } = useAudio();

  const log = useCallback((msg: string) => {
    onLog?.(msg);
    console.log(`[WebRTC] ${msg}`);
  }, [onLog]);

  const updatePipelineState = useCallback((state: PipelineState) => {
    setPipelineState(state);
    onStateChange?.(state);
    log(`State: ${state}`);
  }, [onStateChange, log]);

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
      updatePipelineState("listening");
    });

    channel.addEventListener("close", () => {
      log("Disconnected");
      isFirstTTSChunkRef.current = true;  // Reset
      setStatus("disconnected");
      updatePipelineState("idle");
    });

    channel.addEventListener("message", (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log(`[Event] ${data.type}`, data);

        switch (data.type) {
          case "transcript":
            onTranscript?.({ text: data.text, isFinal: data.is_final });

            // User is speaking
            if (data.text.trim()) {
              // If we're speaking (TTS active) and user talks, interrupt
              if (pipelineState === "speaking") {
                log("🛑 Interrupting TTS - user is speaking");
                stopAudio();
                isFirstTTSChunkRef.current = true;  // Reset for next session
                updatePipelineState("listening");

                // Tell server to stop TTS
                if (channelRef.current?.readyState === "open") {
                  channelRef.current.send(JSON.stringify({ type: "stop_tts" }));
                }
              } else if (data.is_final) {
                // Final transcript - moving to processing
                updatePipelineState("processing");
              }
            }
            break;

          case "canvas_update":
            log(`Canvas: ${data.operations.length} ops`);
            onCanvasUpdate?.(data.operations);
            break;

          case "llm_response":
            log(`LLM: ${data.text.substring(0, 50)}...`);
            onLLMResponse?.(data.text);
            // Still in processing, waiting for TTS
            break;

          case "tts_started":
            log("TTS started");
            isFirstTTSChunkRef.current = true;  // Reset for new TTS session
            updatePipelineState("speaking");
            break;

          case "tts_chunk":
            // Decode and play audio
            const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
            const isFirstChunk = isFirstTTSChunkRef.current;
            if (isFirstChunk) {
              isFirstTTSChunkRef.current = false;  // Mark that we've received first chunk
              console.log(`[TTS] First chunk: ${bytes.length} bytes`);
            }
            console.log(`[TTS] Playing chunk: ${bytes.length} bytes, isFirst=${isFirstChunk}`);
            playChunkStreaming(bytes, 16000, isFirstChunk);
            break;

          case "tts_complete":
            log("TTS complete");
            isFirstTTSChunkRef.current = true;  // Reset for next session
            updatePipelineState("listening");
            break;

          case "tts_interrupted":
            log(`TTS interrupted (${data.chunks_sent} chunks sent)`);
            stopAudio();
            isFirstTTSChunkRef.current = true;  // Reset for next session
            updatePipelineState("listening");
            break;

          case "error":
            log(`Error: ${data.message}`);
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
    playChunkStreaming,
    stopAudio,
    updatePipelineState,
    pipelineState,
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
    log("Disconnected");
  }, [log, updatePipelineState]);

  return {
    status,
    pipelineState,
    connect,
    disconnect,
    initAudio,
  };
}

