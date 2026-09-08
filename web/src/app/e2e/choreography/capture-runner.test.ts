import { describe, expect, it, vi } from "vitest";

import { createSceneState } from "@/lib/live-scene";
import type {
  ChoreographySceneStreamEvent,
  ChoreographySceneStreamRequest,
} from "@/features/live-scene/choreography-model-stream";
import { EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE } from "@/features/live-scene/choreography-playback";

import { createChoreographyCaptureSession } from "./capture-runner";

const CHECKPOINT_IDS = [
  "problem",
  "area_model",
  "split_linear_term",
  "rearrange_halves",
  "missing_corner",
  "balance_and_complete",
  "factor_square",
  "solve_roots",
] as const;

function request(generation = 7): ChoreographySceneStreamRequest {
  return Object.freeze({
    prompt: "Solve x squared plus six x equals seven.",
    generation,
    baseScene: createSceneState({ revision: 0, nodes: [] }),
    baseSemanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  });
}

async function waitForLength(
  events: readonly ChoreographySceneStreamEvent[],
  length: number,
): Promise<void> {
  for (
    let attempt = 0;
    attempt < 20 && events.length !== length;
    attempt += 1
  ) {
    await Promise.resolve();
  }
  expect(events).toHaveLength(length);
}

describe("step-gated choreography capture runner", () => {
  it("withholds every successor and the terminal event until the exact ack", async () => {
    const session = createChoreographyCaptureSession("step");
    const controller = new AbortController();
    const events: ChoreographySceneStreamEvent[] = [];
    const running = session.runner({
      request: request(),
      signal: controller.signal,
      onEvent: (event) => events.push(event),
    });

    expect(Object.isFrozen(session)).toBe(true);
    expect(Object.isFrozen(session.bridge)).toBe(true);
    expect(session.bridge).toMatchObject({ version: 1, pace: "step" });
    expect(session.bridge.getState()).toMatchObject({
      waitingFor: {
        generation: 7,
        sequence: 1,
        checkpointId: "problem",
      },
      acknowledgedThrough: 0,
      evidence: [],
    });
    expect(
      Number.isFinite(session.bridge.getState().waitingFor?.openedAtMs),
    ).toBe(true);
    expect(Object.isFrozen(session.bridge.getState())).toBe(true);
    const evidence = {
      type: "cueStarted" as const,
      ordinal: 1,
      generation: 7,
      attempt: 1,
      sequence: 1,
      checkpointId: "problem" as const,
      certificateSha256: "a".repeat(64),
      cue: "enter" as const,
    };
    session.updateEvidence([evidence]);
    expect(session.bridge.getState().evidence).toEqual([evidence]);
    expect(Object.isFrozen(session.bridge.getState().evidence)).toBe(true);
    expect(Object.isFrozen(session.bridge.getState().evidence[0])).toBe(true);
    expect(events.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "choreography_scene_checkpoint",
    ]);

    expect(() =>
      session.bridge.acknowledgeCheckpoint({
        generation: 7,
        sequence: 1,
        checkpointId: "area_model",
      }),
    ).toThrow("does not match the pending tuple");
    expect(() =>
      session.bridge.acknowledgeCheckpoint({
        generation: 7,
        sequence: 1,
        checkpointId: "problem",
        extra: true,
      }),
    ).toThrow("must contain exactly");

    for (const [index, checkpointId] of CHECKPOINT_IDS.entries()) {
      expect(events.at(-1)).toMatchObject({
        type: "choreography_scene_checkpoint",
        generation: 7,
        sequence: index + 1,
        semantic: { checkpointId },
      });
      expect(
        events.some((event) => event.type === "scene_stream_completed"),
      ).toBe(false);

      const acknowledgement = {
        generation: 7,
        sequence: index + 1,
        checkpointId,
      };
      session.bridge.acknowledgeCheckpoint(acknowledgement);
      expect(() =>
        session.bridge.acknowledgeCheckpoint(acknowledgement),
      ).toThrow();

      await waitForLength(
        events,
        index === CHECKPOINT_IDS.length - 1 ? 10 : index + 3,
      );
      expect(session.bridge.getState().acknowledgedThrough).toBe(index + 1);
    }

    await running;
    expect(events.at(-1)).toMatchObject({
      type: "scene_stream_completed",
      generation: 7,
      finalRevision: 8,
      patchCount: 8,
    });
  });

  it("aborts a pending rendezvous without leaking an acknowledgement slot", async () => {
    const session = createChoreographyCaptureSession("step");
    const controller = new AbortController();
    const events: ChoreographySceneStreamEvent[] = [];
    const running = session.runner({
      request: request(),
      signal: controller.signal,
      onEvent: (event) => events.push(event),
    });
    const rejected = expect(running).rejects.toMatchObject({
      name: "AbortError",
    });

    controller.abort();
    await rejected;

    expect(events).toHaveLength(2);
    expect(
      events.some((event) => event.type === "scene_stream_completed"),
    ).toBe(false);
    expect(() =>
      session.bridge.acknowledgeCheckpoint({
        generation: 7,
        sequence: 1,
        checkpointId: "problem",
      }),
    ).toThrow("No capture checkpoint");
  });

  it("validates and delegates the narrow runtime controls without retaining stale owners", async () => {
    const session = createChoreographyCaptureSession("step");
    const target = {
      generation: 1,
      sequence: 4,
      checkpointId: "rearrange_halves" as const,
      certificateSha256: "a".repeat(64),
      delayAfterPresentedMs: 0,
    };
    const interruption = {
      target: {
        generation: target.generation,
        sequence: target.sequence,
        checkpointId: target.checkpointId,
        certificateSha256: target.certificateSha256,
      },
      trigger: "firstCuePresented" as const,
      delayAfterPresentedMs: 0,
      activeRevision: 4,
      requestedAtMs: 100,
      settledAtMs: 120,
      settleMs: 20,
      evidenceBefore: [],
      evidenceAfter: [],
    };
    const replay = { checkpoints: [], evidence: [] };
    const control = {
      interruptCheckpoint: vi.fn(async () => interruption),
      replayAccepted: vi.fn(async () => replay),
    };
    session.attachControl(control);

    expect(() =>
      session.bridge.interruptCheckpoint({ ...target, extra: true }),
    ).toThrow("must contain exactly");
    expect(() =>
      session.bridge.interruptCheckpoint({
        ...target,
        certificateSha256: "not-a-digest",
      }),
    ).toThrow("must be a SHA-256 digest");
    expect(() =>
      session.bridge.interruptCheckpoint({
        ...target,
        delayAfterPresentedMs: 1_001,
      }),
    ).toThrow("must be between 0 and 1000");

    await expect(session.bridge.interruptCheckpoint(target)).resolves.toEqual(
      interruption,
    );
    expect(control.interruptCheckpoint).toHaveBeenCalledWith(target);
    await expect(session.bridge.replayAccepted()).resolves.toEqual(replay);
    expect(control.replayAccepted).toHaveBeenCalledOnce();

    session.attachControl(null);
    expect(() => session.bridge.interruptCheckpoint(target)).toThrow(
      "capture control is unavailable",
    );
    expect(() => session.bridge.replayAccepted()).toThrow(
      "capture control is unavailable",
    );
  });

  it("keeps automatic capture on the ungated runner", async () => {
    const session = createChoreographyCaptureSession("auto");
    const events: ChoreographySceneStreamEvent[] = [];

    expect(session.bridge).toMatchObject({ version: 1, pace: "auto" });
    expect(session.bridge.getState()).toEqual({
      waitingFor: null,
      acknowledgedThrough: 0,
      evidence: [],
    });
    expect(() =>
      session.bridge.acknowledgeCheckpoint({
        generation: 1,
        sequence: 1,
        checkpointId: "problem",
      }),
    ).toThrow("Automatic capture does not accept");

    await session.runner({
      request: request(),
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(
      events.filter((event) => event.type === "choreography_scene_checkpoint"),
    ).toHaveLength(CHECKPOINT_IDS.length);
    expect(events.at(-1)).toMatchObject({
      type: "scene_stream_completed",
      generation: 7,
      finalRevision: 8,
      patchCount: 8,
    });
  });
});
