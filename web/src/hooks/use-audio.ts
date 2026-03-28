"use client";

import { useRef, useCallback, useEffect } from "react";

interface UseAudioOptions {
  onPlaybackComplete?: () => void;
}

export function useAudio(options: UseAudioOptions = {}) {
  const { onPlaybackComplete } = options;
  const audioContextRef = useRef<AudioContext | null>(null);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const nextStartTimeRef = useRef<number>(0);
  const currentSessionRef = useRef<number>(0);
  const isActiveRef = useRef(false);

  const initAudio = useCallback(() => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    }
    return audioContextRef.current;
  }, []);

  const playPCM = useCallback((bytes: Uint8Array, rate = 16000) => {
    // Stop any currently playing audio first
    scheduledSourcesRef.current.forEach(source => {
      try {
        source.stop();
        source.disconnect();
      } catch {
        // Already stopped
      }
    });
    scheduledSourcesRef.current = [];

    const ctx = initAudio();
    if (ctx.state === "suspended") ctx.resume();

    const samples = bytes.length / 2;
    const buffer = ctx.createBuffer(1, samples, rate);
    const data = buffer.getChannelData(0);
    const view = new DataView(bytes.buffer);

    for (let i = 0; i < samples; i++) {
      data[i] = view.getInt16(i * 2, true) / 32768.0;
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.start();

    scheduledSourcesRef.current.push(source);
  }, [initAudio]);

  const playChunkStreaming = useCallback((bytes: Uint8Array, rate = 16000, isNewSession = false) => {
    const ctx = initAudio();
    if (ctx.state === "suspended") ctx.resume();

    // If this is a new session, stop everything and start fresh
    if (isNewSession) {
      // Stop all scheduled sources immediately - MUST disconnect to prevent future playback
      scheduledSourcesRef.current.forEach(source => {
        try {
          // Disconnect FIRST to prevent scheduled sources from playing
          source.disconnect();
          source.stop();
        } catch {
          // Already stopped
        }
      });
      scheduledSourcesRef.current = [];

      // Increment session ID
      currentSessionRef.current += 1;

      // Reset scheduling - start immediately
      nextStartTimeRef.current = ctx.currentTime;
      isActiveRef.current = true;

    }

    // If not active, this is stale - ignore
    if (!isActiveRef.current) {
      return;
    }

    try {
      const samples = bytes.length / 2;
      const buffer = ctx.createBuffer(1, samples, rate);
      const data = buffer.getChannelData(0);
      const view = new DataView(bytes.buffer);

      for (let i = 0; i < samples; i++) {
        data[i] = view.getInt16(i * 2, true) / 32768.0;
      }

      // Schedule this chunk to play immediately after the last one (no gaps)
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);

      // Schedule at nextStartTime (ensures gapless playback)
      const startTime = Math.max(ctx.currentTime, nextStartTimeRef.current);
      const chunkDuration = buffer.duration;

      source.start(startTime);

      // Update next start time for gapless playback
      nextStartTimeRef.current = startTime + chunkDuration;

      // Track source
      scheduledSourcesRef.current.push(source);

      // Clean up on end
      source.onended = () => {
        const index = scheduledSourcesRef.current.indexOf(source);
        if (index > -1) {
          scheduledSourcesRef.current.splice(index, 1);
        }

        // If no more sources scheduled, mark inactive and notify
        if (scheduledSourcesRef.current.length === 0) {
          isActiveRef.current = false;
          onPlaybackComplete?.();
        }
      };

    } catch (error) {
      console.error("[Audio] ❌ Error processing audio chunk:", error);
    }
  }, [initAudio, onPlaybackComplete]);

  const stopAudio = useCallback(() => {
    const ctx = audioContextRef.current;

    // Mark session as inactive immediately to reject incoming chunks
    isActiveRef.current = false;
    currentSessionRef.current += 1;

    // Stop all scheduled sources - CRITICAL: disconnect BEFORE stop
    scheduledSourcesRef.current.forEach((source) => {
      try {
        source.disconnect();
        source.stop(0);
      } catch {
        // Already stopped
      }
    });
    scheduledSourcesRef.current = [];

    if (ctx) {
      nextStartTimeRef.current = ctx.currentTime;
    }
  }, []);

  useEffect(() => {
    return () => {
      scheduledSourcesRef.current.forEach((source) => {
        try { source.disconnect(); source.stop(); } catch { /* already stopped */ }
      });
      scheduledSourcesRef.current = [];
      audioContextRef.current?.close();
      audioContextRef.current = null;
    };
  }, []);

  return { initAudio, playPCM, playChunkStreaming, stopAudio };
}
