import { describe, expect, it } from "vitest";

import {
  classifyBackendError,
  INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
  INITIAL_VOICE_LIFECYCLE_STATE,
  lifecycleSignalForBackendError,
  reduceSDLPlaybackLifecycle,
  reduceVoiceLifecycle,
  type VoiceLifecycleState,
} from "./webrtc-lifecycle";

describe("legacy WebRTC SDL playback lifecycle", () => {
  it("keeps the sequence interruptible after backend completion while audio is buffered", () => {
    const started = reduceSDLPlaybackLifecycle(
      INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
      { type: "sequence_started", sequenceId: "seq-1" }
    );
    const buffered = reduceSDLPlaybackLifecycle(started.state, {
      type: "audio_scheduled",
      sequenceId: "seq-1",
    });
    const backendComplete = reduceSDLPlaybackLifecycle(buffered.state, {
      type: "backend_complete",
      sequenceId: "seq-1",
      reason: "completed",
    });

    expect(backendComplete).toEqual({
      state: {
        activeSequenceId: "seq-1",
        backendComplete: true,
        playbackPending: true,
      },
    });

    const interrupted = reduceSDLPlaybackLifecycle(backendComplete.state, {
      type: "interrupted",
    });
    expect(interrupted).toEqual({
      state: INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
      ended: { sequenceId: "seq-1", reason: "interrupted" },
    });
    expect(
      reduceSDLPlaybackLifecycle(interrupted.state, { type: "playback_drained" })
    ).toEqual({ state: INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE });
  });

  it("finishes a transmitted sequence only after pending playback drains", () => {
    const started = reduceSDLPlaybackLifecycle(
      INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
      { type: "sequence_started", sequenceId: "seq-1" }
    );
    const buffered = reduceSDLPlaybackLifecycle(started.state, {
      type: "audio_scheduled",
      sequenceId: "seq-1",
    });
    const backendComplete = reduceSDLPlaybackLifecycle(buffered.state, {
      type: "backend_complete",
      sequenceId: "seq-1",
      reason: "completed",
    });

    expect(
      reduceSDLPlaybackLifecycle(backendComplete.state, {
        type: "playback_drained",
      })
    ).toEqual({
      state: INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
      ended: { sequenceId: "seq-1", reason: "completed" },
    });
  });

  it("does not mistake an inter-step audio gap for sequence completion", () => {
    const started = reduceSDLPlaybackLifecycle(
      INITIAL_SDL_PLAYBACK_LIFECYCLE_STATE,
      { type: "sequence_started", sequenceId: "seq-1" }
    );
    const firstChunk = reduceSDLPlaybackLifecycle(started.state, {
      type: "audio_scheduled",
      sequenceId: "seq-1",
    });
    const gap = reduceSDLPlaybackLifecycle(firstChunk.state, {
      type: "playback_drained",
    });

    expect(gap).toEqual({
      state: {
        activeSequenceId: "seq-1",
        backendComplete: false,
        playbackPending: false,
      },
    });
    expect(
      reduceSDLPlaybackLifecycle(gap.state, {
        type: "audio_scheduled",
        sequenceId: "seq-1",
      }).state.playbackPending
    ).toBe(true);
  });
});

describe("legacy WebRTC lifecycle", () => {
  it("does not treat an open data channel as genuine voice readiness", () => {
    const connecting = reduceVoiceLifecycle(INITIAL_VOICE_LIFECYCLE_STATE, {
      type: "connect_requested",
    });
    const transportOpen = reduceVoiceLifecycle(connecting, { type: "transport_open" });

    expect(transportOpen).toBe(connecting);
    expect(transportOpen).toEqual({
      status: "connecting",
      pipeline: "idle",
      terminal: false,
    });

    const ready = reduceVoiceLifecycle(transportOpen, { type: "backend_ready" });
    expect(ready).toEqual({
      status: "connected",
      pipeline: "listening",
      terminal: false,
    });
    expect(reduceVoiceLifecycle(ready, { type: "backend_ready" })).toBe(ready);
  });

  it("starts processing only on turn_committed, never transcript finality", () => {
    const ready: VoiceLifecycleState = {
      status: "connected",
      pipeline: "listening",
      terminal: false,
    };

    expect(
      reduceVoiceLifecycle(ready, { type: "transcript_segment" })
    ).toBe(ready);
    expect(reduceVoiceLifecycle(ready, { type: "turn_committed" })).toEqual({
      ...ready,
      pipeline: "processing",
    });

    const speaking: VoiceLifecycleState = { ...ready, pipeline: "speaking" };
    expect(reduceVoiceLifecycle(speaking, { type: "turn_committed" })).toBe(speaking);
  });

  it("uses explicit recoverability rather than one terminal error code", () => {
    expect(
      classifyBackendError({ code: "stt_eot_missing", recoverable: false })
    ).toBe("terminal");
    expect(
      classifyBackendError({ code: "future_terminal_code", recoverable: false })
    ).toBe("terminal");
    expect(
      classifyBackendError({ code: "voice_unavailable", recoverable: true })
    ).toBe("recoverable");
    expect(classifyBackendError({ code: "voice_unavailable" })).toBe("terminal");
    expect(classifyBackendError({ code: "turn_retry" })).toBe("recoverable");
  });

  it("routes a real terminal backend error through the lifecycle reducer", () => {
    const ready: VoiceLifecycleState = {
      status: "connected",
      pipeline: "listening",
      terminal: false,
    };
    const signal = lifecycleSignalForBackendError({
      code: "stt_eot_missing",
      recoverable: false,
    });

    expect(signal).toEqual({ type: "terminal_failure" });
    expect(reduceVoiceLifecycle(ready, signal)).toEqual({
      status: "error",
      pipeline: "idle",
      terminal: true,
    });
  });

  it("does not let a late ready or close overwrite a terminal failure", () => {
    const terminal = reduceVoiceLifecycle(
      { status: "connecting", pipeline: "idle", terminal: false },
      { type: "terminal_failure" }
    );

    expect(reduceVoiceLifecycle(terminal, { type: "backend_ready" })).toBe(terminal);
    expect(reduceVoiceLifecycle(terminal, { type: "channel_closed" })).toBe(terminal);
    expect(terminal).toEqual({ status: "error", pipeline: "idle", terminal: true });
  });

  it("keeps explicitly recoverable pre-ready errors from faking readiness", () => {
    const connecting: VoiceLifecycleState = {
      status: "connecting",
      pipeline: "idle",
      terminal: false,
    };
    expect(
      reduceVoiceLifecycle(connecting, { type: "recoverable_error" })
    ).toBe(connecting);
  });
});
