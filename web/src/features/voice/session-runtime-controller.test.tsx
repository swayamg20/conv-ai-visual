/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  useWebRTC: vi.fn(),
  useVoiceSession: vi.fn(),
}));

vi.mock("@/hooks/use-webrtc", () => ({
  useWebRTC: hooks.useWebRTC,
}));

vi.mock("@/hooks/use-voice-session", () => ({
  useVoiceSession: hooks.useVoiceSession,
}));

import {
  SessionVoiceRuntimeController,
  type SessionVoiceCallbacks,
  type SessionVoiceRuntime,
} from "./session-runtime-controller";

const callbacks: SessionVoiceCallbacks = {
  onSessionReady: () => undefined,
  onTranscript: () => undefined,
  onAssistantSpeech: () => undefined,
  onCanvasUpdate: () => undefined,
  onSDLScene: () => undefined,
  onSDLStart: () => undefined,
  onSDLStepAudioStart: () => undefined,
  onSDLStepComplete: () => undefined,
  onSDLComplete: () => undefined,
  onPipelineMetrics: () => undefined,
  onError: () => undefined,
  onLog: () => undefined,
  onStateChange: () => undefined,
};

function legacyResult() {
  return {
    status: "idle",
    pipelineState: "idle",
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(),
    cancelConnection: vi.fn(),
    initAudio: vi.fn(),
    isMicMuted: false,
    isTTSEnabled: true,
    toggleMicMute: vi.fn(),
    toggleTTS: vi.fn(),
  };
}

function v2Result() {
  return {
    phase: "idle",
    session: {
      phase: "idle",
      transportConnected: false,
      voiceReady: false,
      reconnectAttempt: 0,
    },
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(async () => undefined),
    cancelConnection: vi.fn(async () => undefined),
    isMicMuted: false,
    isTTSEnabled: true,
    toggleMicMute: vi.fn(async () => undefined),
    toggleTTS: vi.fn(),
    audioPlaybackBlocked: false,
    resumeAudio: vi.fn(async () => undefined),
  };
}

describe("SessionVoiceRuntimeController", () => {
  it("mounts only the assigned media runtime", async () => {
    hooks.useWebRTC.mockReset().mockReturnValue(legacyResult());
    hooks.useVoiceSession.mockReset().mockReturnValue(v2Result());
    const container = document.createElement("div");
    const root = createRoot(container);
    let rendered: SessionVoiceRuntime | undefined;

    await act(async () => {
      root.render(
        <SessionVoiceRuntimeController
          runtime="voice_v2"
          agentId="agent-1"
          sessionId="session-1"
          callbacks={callbacks}
        >
          {(voice) => {
            rendered = voice;
            return null;
          }}
        </SessionVoiceRuntimeController>
      );
    });

    expect(rendered?.runtime).toBe("voice_v2");
    expect(rendered?.unavailableReason).toBeUndefined();
    expect(hooks.useVoiceSession).toHaveBeenCalledTimes(1);
    expect(hooks.useWebRTC).not.toHaveBeenCalled();

    await act(async () => {
      root.render(
        <SessionVoiceRuntimeController
          runtime="legacy"
          agentId="agent-1"
          sessionId="session-1"
          callbacks={callbacks}
        >
          {(voice) => {
            rendered = voice;
            return null;
          }}
        </SessionVoiceRuntimeController>
      );
    });

    expect(rendered?.runtime).toBe("legacy");
    expect(hooks.useWebRTC).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it("exposes the exact Voice V2 teardown promises", async () => {
    hooks.useWebRTC.mockReset().mockReturnValue(legacyResult());
    let resolveDisconnect!: () => void;
    let resolveCancel!: () => void;
    const disconnectPromise = new Promise<void>((resolve) => {
      resolveDisconnect = resolve;
    });
    const cancelPromise = new Promise<void>((resolve) => {
      resolveCancel = resolve;
    });
    const result = {
      ...v2Result(),
      disconnect: vi.fn(() => disconnectPromise),
      cancelConnection: vi.fn(() => cancelPromise),
    };
    hooks.useVoiceSession.mockReset().mockReturnValue(result);
    const container = document.createElement("div");
    const root = createRoot(container);
    let rendered: SessionVoiceRuntime | undefined;

    await act(async () => {
      root.render(
        <SessionVoiceRuntimeController
          runtime="voice_v2"
          agentId="agent-1"
          sessionId="session-1"
          callbacks={callbacks}
        >
          {(voice) => {
            rendered = voice;
            return null;
          }}
        </SessionVoiceRuntimeController>
      );
    });

    expect(rendered?.disconnect()).toBe(disconnectPromise);
    expect(rendered?.cancelConnection()).toBe(cancelPromise);
    expect(result.disconnect).toHaveBeenCalledTimes(1);
    expect(result.cancelConnection).toHaveBeenCalledTimes(1);

    resolveDisconnect();
    resolveCancel();
    await Promise.all([disconnectPromise, cancelPromise]);
    await act(async () => root.unmount());
  });

  it("wraps legacy teardown in observable resolved promises", async () => {
    const result = legacyResult();
    hooks.useWebRTC.mockReset().mockReturnValue(result);
    hooks.useVoiceSession.mockReset().mockReturnValue(v2Result());
    const container = document.createElement("div");
    const root = createRoot(container);
    let rendered: SessionVoiceRuntime | undefined;

    await act(async () => {
      root.render(
        <SessionVoiceRuntimeController
          runtime="legacy"
          agentId="agent-1"
          sessionId="session-1"
          callbacks={callbacks}
        >
          {(voice) => {
            rendered = voice;
            return null;
          }}
        </SessionVoiceRuntimeController>
      );
    });

    await rendered?.connect("owned-session");
    expect(result.initAudio).toHaveBeenCalledTimes(1);
    expect(result.connect).toHaveBeenCalledWith({
      agentId: "agent-1",
      sessionId: "owned-session",
    });
    expect(result.initAudio.mock.invocationCallOrder[0]).toBeLessThan(
      result.connect.mock.invocationCallOrder[0]
    );
    await expect(rendered?.disconnect()).resolves.toBeUndefined();
    await expect(rendered?.cancelConnection()).resolves.toBeUndefined();
    expect(result.disconnect).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
  });

  it("exposes the exact V2 failure and safe retry decision", async () => {
    hooks.useWebRTC.mockReset().mockReturnValue(legacyResult());
    hooks.useVoiceSession.mockReset().mockReturnValue({
      ...v2Result(),
      phase: "unavailable",
      session: {
        phase: "unavailable",
        transportConnected: false,
        voiceReady: false,
        reconnectAttempt: 0,
        unavailableReason: {
          code: "bootstrap_unauthenticated",
          message: "Sign in again before starting voice.",
          retryable: false,
        },
      },
    });
    const container = document.createElement("div");
    const root = createRoot(container);
    let rendered: SessionVoiceRuntime | undefined;

    await act(async () => {
      root.render(
        <SessionVoiceRuntimeController
          runtime="voice_v2"
          agentId="agent-1"
          sessionId="session-1"
          callbacks={callbacks}
        >
          {(voice) => {
            rendered = voice;
            return null;
          }}
        </SessionVoiceRuntimeController>
      );
    });

    expect(rendered?.canStartVoice).toBe(false);
    expect(rendered?.unavailableReason).toEqual({
      code: "bootstrap_unauthenticated",
      message: "Sign in again before starting voice.",
      retryable: false,
    });
    await act(async () => root.unmount());
  });
});
