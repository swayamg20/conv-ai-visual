"use client";

import { useRef, useCallback } from "react";

export function useAudio() {
  const audioContextRef = useRef<AudioContext | null>(null);

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
  }, [initAudio]);

  return { initAudio, playPCM };
}

