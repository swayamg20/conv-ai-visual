import { describe, expect, it } from "vitest";
import { quadraticVisual } from "./quadratic";
import { agentConfig, DEFAULT_INSTRUCTIONS, type CallOptions } from "./contracts";

describe("quadratic math owned by the application", () => {
  it.each([
    [1, -5, 6, [2, 3], [2.5, -0.25]],
    [1, 0, 0, [0], [0, 0]],
    [1, 0, 1, [], [0, 1]],
    [-1, 0, 4, [-2, 2], [0, 4]],
    [0.5, -2, 0, [0, 4], [2, -2]],
  ])("computes roots, vertex, and finite plotted values for %s,%s,%s", (a, b, c, roots, vertex) => {
    const visual = quadraticVisual(a, b, c);
    expect(visual.roots.map((value) => value + 0)).toEqual(roots);
    expect(visual.vertex.map((value) => value + 0)).toEqual(vertex);
    for (const [x, y] of visual.plot.points) {
      expect(Number.isFinite(y)).toBe(true);
      expect(y).toBeCloseTo((a as number) * x ** 2 + (b as number) * x + (c as number));
    }
    expect(visual.plot.x_range[0]).toBeLessThan(visual.vertex[0]);
    expect(visual.plot.x_range[1]).toBeGreaterThan(visual.vertex[0]);
  });
  it.each([[0, 1, 2], [Infinity, 1, 2], [1, NaN, 2], [1, "2", 3], [101, 1, 2]])("rejects invalid coefficients %s,%s,%s", (a, b, c) => {
    expect(() => quadraticVisual(a, b, c)).toThrow();
  });
});

describe("independent turn-taking controls", () => {
  const options: CallOptions = { provider: "gemini", instructions: DEFAULT_INSTRUCTIONS, interruption: "high", turnTaking: "patient", storeRecording: false, microphone: true };
  it("uses patient Cosmo semantic turn detection with high interruption", () => {
    expect(agentConfig(options, []).modelOptions).toEqual({ provider: "gemini", includeThoughts: false, turnDetection: "cosmo_vad", cosmoVad: { pauseMs: 2000, prefixMs: 400, maxHoldMs: 5000 } });
    expect(agentConfig(options, []).interruptionSensitivity).toBe("high");
  });
  it("uses only OpenAI semantic controls for OpenAI", () => {
    expect(agentConfig({ ...options, provider: "openai" }, []).modelOptions).toEqual({ provider: "openai", turnDetection: "semantic_vad", eagerness: "low" });
  });
  it("preserves workspace defaults without pretending a provider-specific knob applies", () => {
    expect(agentConfig({ ...options, provider: "default" }, []).modelOptions).toBeUndefined();
  });
});
