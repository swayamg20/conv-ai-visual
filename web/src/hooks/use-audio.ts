"use client";

import { useRef, useCallback } from "react";

export function useAudio() {
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
      const t0 = performance.now();

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

      const t1 = performance.now();
      console.log(`[Audio] 🆕 New TTS session ${currentSessionRef.current} started (cleanup took ${(t1-t0).toFixed(1)}ms)`);
    }

    // If not active, this is stale - ignore
    if (!isActiveRef.current) {
      console.log(`[Audio] ⏭️ Ignoring stale chunk (session inactive)`);
      return;
    }

    const sessionId = currentSessionRef.current;

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

      const isFirstChunk = scheduledSourcesRef.current.length === 1;
      const delay = startTime - ctx.currentTime;
      console.log(`[Audio] 🔊 Scheduled chunk (session=${sessionId}, first=${isFirstChunk}, delay=${(delay*1000).toFixed(0)}ms, duration=${(chunkDuration*1000).toFixed(0)}ms, queued=${scheduledSourcesRef.current.length})`);

      // Clean up on end
      source.onended = () => {
        const index = scheduledSourcesRef.current.indexOf(source);
        if (index > -1) {
          scheduledSourcesRef.current.splice(index, 1);
        }

        // If no more sources scheduled, mark inactive
        if (scheduledSourcesRef.current.length === 0) {
          isActiveRef.current = false;
          console.log(`[Audio] ✅ Session ${sessionId} playback complete`);
        }
      };

    } catch (error) {
      console.error("[Audio] ❌ Error processing audio chunk:", error);
    }
  }, [initAudio]);

  const stopAudio = useCallback(() => {
    const ctx = audioContextRef.current;
    const sourceCount = scheduledSourcesRef.current.length;
    const currentTime = ctx?.currentTime || 0;

    console.log(`[Audio] 🛑 STOPPING all audio (had ${sourceCount} scheduled sources, currentTime=${currentTime.toFixed(3)}s)`);

    // Mark session as inactive immediately to reject incoming chunks
    isActiveRef.current = false;
    currentSessionRef.current += 1;

    // Stop all scheduled sources - CRITICAL: disconnect BEFORE stop
    scheduledSourcesRef.current.forEach((source, idx) => {
      try {
        // DISCONNECT FIRST - this prevents scheduled sources from playing
        source.disconnect();
        // Then stop (might throw if not started yet, that's OK)
        source.stop(0);
        console.log(`[Audio]   ↳ Stopped source ${idx+1}/${sourceCount}`);
      } catch (e) {
        console.log(`[Audio]   ↳ Source ${idx+1}/${sourceCount} already stopped`);
      }
    });
    scheduledSourcesRef.current = [];

    // Reset timing
    if (ctx) {
      nextStartTimeRef.current = ctx.currentTime;
    }

    console.log(`[Audio] ✓ Stop complete, session now ${currentSessionRef.current}`);
  }, []);

  return { initAudio, playPCM, playChunkStreaming, stopAudio };
}
