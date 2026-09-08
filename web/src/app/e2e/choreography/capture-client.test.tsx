/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

const rendered = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("@/features/live-scene/live-choreography-demo", () => ({
  LiveChoreographyDemo: (props: Record<string, unknown>) => {
    rendered.props = props;
    return <div data-testid="capture-demo" />;
  },
}));

import { ChoreographyCaptureClient } from "./capture-client";
import { CHOREOGRAPHY_CAPTURE_BRIDGE_KEY } from "./capture-runner";
import type { LiveChoreographyCaptureControl } from "@/features/live-scene/live-choreography-demo";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  container?.remove();
  container = null;
  delete window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY];
  rendered.props = null;
});

describe("ChoreographyCaptureClient", () => {
  it("installs one frozen versioned bridge and removes it on unmount", async () => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);

    await act(async () => {
      root?.render(
        <ChoreographyCaptureClient
          options={{
            layout: "cinematic",
            reducedMotion: false,
            pace: "step",
            playbackRate: 16,
          }}
        />,
      );
    });

    const bridge = window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY];
    expect(bridge).toMatchObject({ version: 1, pace: "step" });
    expect(Object.isFrozen(bridge)).toBe(true);
    expect(
      Object.getOwnPropertyDescriptor(window, CHOREOGRAPHY_CAPTURE_BRIDGE_KEY),
    ).toMatchObject({
      configurable: true,
      enumerable: false,
      writable: false,
    });
    expect(rendered.props).toMatchObject({
      initialPath: "full",
      pathLocked: true,
      layout: "cinematic",
      reducedMotion: false,
      playbackRate: 16,
      stageOnly: true,
      autoStart: true,
    });
    expect(rendered.props?.onCaptureControlChange).toBeTypeOf("function");
    const evidence = {
      type: "cueStarted",
      ordinal: 1,
      generation: 1,
      attempt: 1,
      sequence: 1,
      checkpointId: "problem",
      certificateSha256: "a".repeat(64),
      cue: "enter",
    };
    const onEvidenceChange = rendered.props?.onEvidenceChange as (
      value: readonly (typeof evidence)[],
    ) => void;
    act(() => onEvidenceChange([evidence]));
    expect(bridge?.getState().evidence).toEqual([evidence]);

    const replay = { checkpoints: [], evidence: [] };
    const captureControl: LiveChoreographyCaptureControl = {
      interruptCheckpoint: vi.fn(async (request) => ({
        target: {
          generation: request.generation,
          sequence: request.sequence,
          checkpointId: request.checkpointId,
          certificateSha256: request.certificateSha256,
        },
        trigger: "firstCuePresented" as const,
        delayAfterPresentedMs: request.delayAfterPresentedMs,
        activeRevision: request.sequence,
        requestedAtMs: 10,
        settledAtMs: 20,
        settleMs: 10,
        evidenceBefore: [],
        evidenceAfter: [],
      })),
      replayAccepted: vi.fn(async () => replay),
    };
    const onCaptureControlChange = rendered.props?.onCaptureControlChange as (
      value: typeof captureControl | null,
    ) => void;
    act(() => onCaptureControlChange(captureControl));
    await expect(bridge?.replayAccepted()).resolves.toEqual(replay);
    expect(captureControl.replayAccepted).toHaveBeenCalledOnce();

    const interruptionTarget = {
      generation: 1,
      sequence: 4,
      checkpointId: "rearrange_halves",
      certificateSha256: "a".repeat(64),
      delayAfterPresentedMs: 0,
    };
    await expect(
      bridge?.interruptCheckpoint(interruptionTarget),
    ).resolves.toMatchObject({
      target: {
        generation: interruptionTarget.generation,
        sequence: interruptionTarget.sequence,
        checkpointId: interruptionTarget.checkpointId,
        certificateSha256: interruptionTarget.certificateSha256,
      },
      delayAfterPresentedMs: interruptionTarget.delayAfterPresentedMs,
    });
    expect(captureControl.interruptCheckpoint).toHaveBeenCalledWith(
      interruptionTarget,
    );

    act(() => onCaptureControlChange(null));
    expect(() => bridge?.replayAccepted()).toThrow(
      "capture control is unavailable",
    );

    await act(async () => root?.unmount());
    root = null;
    expect(window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY]).toBeUndefined();
  });
});
