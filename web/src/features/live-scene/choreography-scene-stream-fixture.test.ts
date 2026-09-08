import { describe, expect, it, vi } from "vitest";

import { createSceneState } from "@/lib/live-scene";

import type {
  ChoreographySceneCheckpointEvent,
  ChoreographySceneStreamEvent,
  ChoreographySceneStreamRequest,
} from "./choreography-model-stream";
import {
  ChoreographySceneFixtureError,
  createChoreographySceneFixtureEvents,
  createChoreographySceneFixtureRunner,
} from "./choreography-scene-stream-fixture";
import {
  EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  createChoreographyFrontier,
  prepareChoreographyCheckpoint,
  type ChoreographyFrontier,
} from "./choreography-playback";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function emptyFrontier(): ChoreographyFrontier {
  return createChoreographyFrontier({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });
}

function request(
  generation = 1,
  frontier: ChoreographyFrontier = emptyFrontier(),
): ChoreographySceneStreamRequest {
  return Object.freeze({
    prompt: "Solve x² + 6x = 7 visually.",
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
  });
}

function checkpoints(
  events: readonly ChoreographySceneStreamEvent[],
): readonly ChoreographySceneCheckpointEvent[] {
  return events.filter(
    (event): event is ChoreographySceneCheckpointEvent =>
      event.type === "choreography_scene_checkpoint",
  );
}

function applyEvents(
  frontierValue: ChoreographyFrontier,
  events: readonly ChoreographySceneStreamEvent[],
): ChoreographyFrontier {
  let frontier = frontierValue;
  for (const event of checkpoints(events)) {
    frontier = prepareChoreographyCheckpoint(
      frontier,
      event,
      "cinematic",
    ).target;
  }
  return frontier;
}

function eventTypes(events: readonly ChoreographySceneStreamEvent[]): string[] {
  return events.map((event) => event.type);
}

describe("choreography scene stream fixture", () => {
  it("emits the complete eight-checkpoint compiler transcript", () => {
    const events = createChoreographySceneFixtureEvents(request(7));
    const checkpointEvents = checkpoints(events);
    const terminal = events.at(-1);

    expect(eventTypes(events)).toEqual([
      "scene_stream_started",
      ...Array(8).fill("choreography_scene_checkpoint"),
      "scene_stream_completed",
    ]);
    expect(checkpointEvents.map((event) => event.generation)).toEqual(
      Array(8).fill(7),
    );
    expect(checkpointEvents.map((event) => event.sequence)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(
      checkpointEvents.map((event) => event.semantic.checkpointId),
    ).toEqual([
      "problem",
      "area_model",
      "split_linear_term",
      "rearrange_halves",
      "missing_corner",
      "balance_and_complete",
      "factor_square",
      "solve_roots",
    ]);
    expect(terminal).toEqual(
      expect.objectContaining({
        type: "scene_stream_completed",
        generation: 7,
        finalRevision: 8,
        patchCount: 8,
      }),
    );
    expect(applyEvents(emptyFrontier(), events)).toMatchObject({
      scene: { revision: 8 },
      semanticScene: { revision: 8 },
    });
  });

  it("resumes only the missing main suffix from an exact certified prefix", () => {
    const firstFive = createChoreographySceneFixtureEvents(request()).slice(
      0,
      6,
    );
    const prefix = applyEvents(emptyFrontier(), firstFive);
    const resumed = createChoreographySceneFixtureEvents(request(2, prefix));
    const resumedCheckpoints = checkpoints(resumed);

    expect(resumedCheckpoints.map((event) => event.sequence)).toEqual([
      1, 2, 3,
    ]);
    expect(
      resumedCheckpoints.map((event) => event.semantic.checkpointId),
    ).toEqual(["balance_and_complete", "factor_square", "solve_roots"]);
    expect(resumed.at(-1)).toEqual(
      expect.objectContaining({
        type: "scene_stream_completed",
        generation: 2,
        finalRevision: 8,
        patchCount: 3,
      }),
    );
  });

  it("branches at missing_corner, explains nine, then continues the same board", () => {
    const initial = createChoreographySceneFixtureEvents(
      request(1),
      "adaptive",
    );
    expect(eventTypes(initial)).toEqual([
      "scene_stream_started",
      ...Array(5).fill("choreography_scene_checkpoint"),
    ]);
    const missingCorner = applyEvents(emptyFrontier(), initial);
    expect(missingCorner.scene.revision).toBe(5);

    const clarification = createChoreographySceneFixtureEvents(
      request(2, missingCorner),
      "adaptive",
    );
    expect(
      checkpoints(clarification).map((event) => event.semantic.checkpointId),
    ).toEqual(["corner_detail"]);
    const clarified = applyEvents(missingCorner, clarification);
    expect(clarified.semanticScene.components[0]).toMatchObject({
      lastMainCheckpoint: "missing_corner",
      cornerClarified: true,
    });

    const continuation = createChoreographySceneFixtureEvents(
      request(3, clarified),
      "adaptive",
    );
    expect(
      checkpoints(continuation).map((event) => event.semantic.checkpointId),
    ).toEqual(["balance_and_complete", "factor_square", "solve_roots"]);
    const complete = applyEvents(clarified, continuation);
    expect(complete).toMatchObject({
      scene: { revision: 9 },
      semanticScene: {
        revision: 9,
        components: [
          {
            lastMainCheckpoint: "solve_roots",
            cornerClarified: true,
          },
        ],
      },
    });
  });

  it("fails closed for a board that merely resembles a fixture prefix", () => {
    const events = createChoreographySceneFixtureEvents(request());
    const prefix = applyEvents(emptyFrontier(), events.slice(0, 2));
    const impostor = createSceneState({
      revision: prefix.scene.revision,
      nodes: [],
    });

    expect(() =>
      createChoreographySceneFixtureEvents({
        ...request(2, prefix),
        baseScene: impostor,
      }),
    ).toThrow(
      expect.objectContaining<Partial<ChoreographySceneFixtureError>>({
        code: "base_mismatch",
      }),
    );
  });

  it("uses the production SSE decoder without calling fetch", async () => {
    const received: ChoreographySceneStreamEvent[] = [];
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("fixture must not call fetch"));
    try {
      await createChoreographySceneFixtureRunner({
        eventDelayMs: 0,
        chunkDelayMs: 0,
      })({
        request: request(),
        signal: new AbortController().signal,
        onEvent: (event) => received.push(event),
      });
    } finally {
      fetchSpy.mockRestore();
    }

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(eventTypes(received)).toEqual([
      "scene_stream_started",
      ...Array(8).fill("choreography_scene_checkpoint"),
      "scene_stream_completed",
    ]);
  });

  it("keeps the adaptive stream open at the question boundary until interrupted", async () => {
    const controller = new AbortController();
    const reachedBoundary = deferred();
    const received: ChoreographySceneStreamEvent[] = [];
    const running = createChoreographySceneFixtureRunner({
      mode: "adaptive",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    })({
      request: request(),
      signal: controller.signal,
      onEvent: (event) => {
        received.push(event);
        if (received.length === 6) reachedBoundary.resolve();
      },
    });

    await reachedBoundary.promise;
    expect(received.at(-1)).toMatchObject({
      type: "choreography_scene_checkpoint",
      semantic: { checkpointId: "missing_corner" },
    });
    const rejected = expect(running).rejects.toMatchObject({
      name: "AbortError",
    });
    controller.abort();
    await rejected;
  });
});
