import { describe, expect, it } from "vitest";

import {
  resolveVoiceRuntimeAssignment,
  voiceSessionView,
} from "./session-view";
import type { VoiceSessionPhase } from "./session-machine";

describe("Voice V2 session view", () => {
  it("keeps the canary default on legacy unless V2 is explicitly assigned", () => {
    expect(resolveVoiceRuntimeAssignment(undefined)).toBe("legacy");
    expect(resolveVoiceRuntimeAssignment("legacy")).toBe("legacy");
    expect(resolveVoiceRuntimeAssignment("unexpected")).toBe("legacy");
    expect(resolveVoiceRuntimeAssignment("livekit_v2")).toBe("livekit_v2");
  });

  it("renders every transport and semantic phase distinctly", () => {
    const phases: readonly VoiceSessionPhase[] = [
      "idle",
      "connecting",
      "transport_connected",
      "ready",
      "listening",
      "thinking",
      "speaking",
      "reconnecting",
      "unavailable",
      "ended",
    ];

    const views = phases.map((phase) => voiceSessionView(phase));
    expect(views.map((view) => view.label)).toEqual([
      "Start voice",
      "Connecting transport...",
      "Transport connected · checking agent...",
      "Agent ready",
      "Listening",
      "Thinking",
      "Speaking",
      "Reconnecting transport...",
      "Voice unavailable",
      "Voice ended",
    ]);
    expect(voiceSessionView("transport_connected")).toMatchObject({
      transportConnected: true,
      voiceReady: false,
      indicatorState: "connecting",
    });
    expect(voiceSessionView("ready")).toMatchObject({
      transportConnected: true,
      voiceReady: true,
      indicatorState: "connected",
    });
    expect(voiceSessionView("ended")).toMatchObject({ terminal: true });
  });
});
