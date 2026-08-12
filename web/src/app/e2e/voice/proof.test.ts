import { describe, expect, it } from "vitest";

import { interruptionAttribution } from "./proof";

const base = {
  silenceStartMs: 10_464.4,
  nextAssistantSpeechStartMs: 11_849.8,
  activeRms: 0.02,
  requiredSilenceMs: 200,
  samplingToleranceMs: 100,
};

describe("interruptionAttribution", () => {
  it("accepts legitimate PCM from the next canonical assistant reply", () => {
    const result = interruptionAttribution({
      ...base,
      samples: [
        { t_ms: 10_664.4, rms: 0 },
        { t_ms: 11_740, rms: 0 },
        // Within sampling tolerance of the next canonical speech boundary.
        { t_ms: 11_780, rms: 0.03 },
        { t_ms: 11_950, rms: 0.05 },
      ],
    });

    expect(result).toEqual({
      guard_start_ms: 10_664.4,
      guard_end_ms: 11_749.8,
      observation_complete: true,
      stale_audio_detected: false,
    });
  });

  it("rejects resumed PCM from the interrupted reply before the next boundary", () => {
    const result = interruptionAttribution({
      ...base,
      samples: [
        { t_ms: 10_664.4, rms: 0 },
        { t_ms: 11_100, rms: 0.04 },
        { t_ms: 11_950, rms: 0.05 },
      ],
    });

    expect(result.observation_complete).toBe(true);
    expect(result.stale_audio_detected).toBe(true);
  });

  it("does not pass before samples cross the next-reply boundary", () => {
    const result = interruptionAttribution({
      ...base,
      samples: [{ t_ms: 11_700, rms: 0 }],
    });

    expect(result.observation_complete).toBe(false);
    expect(result.stale_audio_detected).toBe(false);
  });
});
