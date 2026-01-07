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
  } = options;

  const [status, setStatus] = useState<ConnectionStatus>("idle");
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Uint8Array[]>([]);
  const isReceivingAudioRef = useRef(false);
  const isTTSPlayingRef = useRef(false);
  const { initAudio, playPCM, stopAudio } = useAudio();

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
          onTranscript?.({ text: data.text, isFinal: data.is_final });
        } else if (data.type === "llm_response") {
          onLLMResponse?.(data.text);
        } else if (data.type === "canvas_update") {
          log(`Canvas: ${data.operations.length} ops`);
          onCanvasUpdate?.(data.operations);
        } else if (data.type === "interruption_ack") {
          log(`Interruption: ${data.message}`);

          // Stop playing TTS immediately
          stopAudio();

          // Clear buffered chunks
          audioChunksRef.current = [];
          isReceivingAudioRef.current = false;
          isTTSPlayingRef.current = false;

          // Notify callback
          onInterruptionDetected?.(data.message);
        } else if (data.type === "tts_cancelled") {
          log(`TTS cancelled: ${data.chunks_sent} chunks sent`);

          // Clear any remaining buffered chunks
          audioChunksRef.current = [];
          isReceivingAudioRef.current = false;
          isTTSPlayingRef.current = false;

          // Notify callback
          onTTSCancelled?.(data.chunks_sent);
        } else if (data.type === "tts_audio_chunk") {
          if (!isReceivingAudioRef.current) {
            isReceivingAudioRef.current = true;
            isTTSPlayingRef.current = true;
            audioChunksRef.current = [];
          }
          const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
          audioChunksRef.current.push(bytes);
        } else if (data.type === "tts_audio_end") {
          isReceivingAudioRef.current = false;
          const chunks = audioChunksRef.current;
          const total = chunks.reduce((s, a) => s + a.length, 0);
          const combined = new Uint8Array(total);
          let offset = 0;
          for (const chunk of chunks) {
            combined.set(chunk, offset);
            offset += chunk.length;
          }
          playPCM(combined, 16000);
          audioChunksRef.current = [];
          isTTSPlayingRef.current = false;
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
    playPCM,
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

