"use client";

import { useRef, useCallback } from "react";

export function useAudio() {
  const audioContextRef = useRef<AudioContext | null>(null);
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);

  const initAudio = useCallback(() => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    }
    return audioContextRef.current;
  }, []);

  const playPCM = useCallback((bytes: Uint8Array, rate = 16000) => {
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

    // Track current source for interruption handling
    currentSourceRef.current = source;
  }, [initAudio]);

  const stopAudio = useCallback(() => {
    // Stop current audio playback
    if (currentSourceRef.current) {
      try {
        currentSourceRef.current.stop();
      } catch {
        // Already stopped or never started
      }
      currentSourceRef.current = null;
    }

    // Suspend audio context to clear buffer
    if (audioContextRef.current && audioContextRef.current.state === 'running') {
      audioContextRef.current.suspend().then(() => {
        // Resume after a brief pause to clear buffers
        setTimeout(() => {
          audioContextRef.current?.resume();
        }, 50);
      });
    }
  }, []);

  return { initAudio, playPCM, stopAudio };
}

