import { describe, expect, it, vi } from "vitest";

import {
  PYTHAGOREAN_ROLE_ORDER,
  applySemanticScenePatch,
  createSemanticSceneState,
  createSceneState,
  type SceneState,
  type SemanticScenePatchEvent,
  type SemanticSceneState,
} from "@/lib/live-scene";

import type {
  SemanticSceneStreamEvent,
  SemanticSceneStreamRequest,
} from "./model-stream";
import {
  SemanticSceneFixtureError,
  createSemanticSceneFixtureEvents,
  createSemanticSceneFixtureRunner,
} from "./semantic-scene-stream-fixture";

function request(
  generation = 1,
  baseScene: SceneState = createSceneState({ revision: 0, nodes: [] }),
  baseSemanticScene: SemanticSceneState = createSemanticSceneState({
    revision: 0,
    components: [],
  })
): SemanticSceneStreamRequest {
  return Object.freeze({
    prompt: "Teach the Pythagorean area identity",
    generation,
    baseScene,
    baseSemanticScene,
  });
}

function patches(
  events: readonly SemanticSceneStreamEvent[]
): readonly SemanticScenePatchEvent[] {
  return events.filter(
    (event): event is SemanticScenePatchEvent =>
      event.type === "semantic_scene_patch"
  );
}

function goldenPrefix(atomCount: number): {
  readonly scene: SceneState;
  readonly semanticScene: SemanticSceneState;
  readonly sourceEvents: readonly SemanticScenePatchEvent[];
} {
  const sourceEvents = patches(createSemanticSceneFixtureEvents(request()));
  let scene = createSceneState({ revision: 0, nodes: [] });
  let semanticScene = createSemanticSceneState({ revision: 0, components: [] });
  for (const event of sourceEvents.slice(0, atomCount)) {
    const applied = applySemanticScenePatch(scene, semanticScene, event);
    scene = applied.scene;
    semanticScene = applied.semanticScene;
  }
  return { scene, semanticScene, sourceEvents };
}

describe("semantic scene stream fixture", () => {
  it("strictly emits and applies all eight compiler-certified golden atoms", () => {
    const events = createSemanticSceneFixtureEvents(request(7));
    const atoms = patches(events);
    let scene = createSceneState({ revision: 0, nodes: [] });
    let semanticScene = createSemanticSceneState({ revision: 0, components: [] });

    for (const event of atoms) {
      const applied = applySemanticScenePatch(scene, semanticScene, event);
      scene = applied.scene;
      semanticScene = applied.semanticScene;
    }

    expect(events[0]).toEqual({
      type: "scene_stream_started",
      generation: 7,
      attempt: 1,
      baseRevision: 0,
    });
    expect(atoms).toHaveLength(8);
    expect(atoms.map((event) => event.semantic.role)).toEqual(
      PYTHAGOREAN_ROLE_ORDER
    );
    expect(atoms.map((event) => event.generation)).toEqual(Array(8).fill(7));
    expect(atoms.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(scene.revision).toBe(8);
    expect(semanticScene).toEqual({
      revision: 8,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "areas",
          revealedRoles: PYTHAGOREAN_ROLE_ORDER,
        },
      ],
      certificateHeadSha256:
        "df4f7cec75afb919a3c54017f816c43ec54b7f23a66ffa5f3ff252e54c36f279",
    });
    expect(events.at(-1)).toEqual(
      expect.objectContaining({
        type: "scene_stream_completed",
        generation: 7,
        finalRevision: 8,
        patchCount: 8,
        repaired: false,
      })
    );
  });

  it("emits only the missing suffix from an exact paired prefix", () => {
    const prefix = goldenPrefix(3);
    const events = createSemanticSceneFixtureEvents(
      request(9, prefix.scene, prefix.semanticScene)
    );
    const suffix = patches(events);
    let resumedScene = prefix.scene;
    let resumedSemanticScene = prefix.semanticScene;
    for (const event of suffix) {
      const applied = applySemanticScenePatch(
        resumedScene,
        resumedSemanticScene,
        event
      );
      resumedScene = applied.scene;
      resumedSemanticScene = applied.semanticScene;
    }

    expect(suffix).toHaveLength(5);
    expect(suffix[0]).toEqual(
      expect.objectContaining({
        generation: 9,
        attempt: 1,
        sequence: 1,
        baseRevision: 3,
        resultRevision: 4,
      })
    );
    expect(suffix.map((event) => event.semantic.role)).toEqual(
      PYTHAGOREAN_ROLE_ORDER.slice(3)
    );
    expect(events.at(-1)).toEqual(
      expect.objectContaining({
        type: "scene_stream_completed",
        finalRevision: 8,
        patchCount: 5,
      })
    );
    expect(resumedScene.revision).toBe(8);
    expect(resumedSemanticScene.components[0]?.revealedRoles).toEqual(
      PYTHAGOREAN_ROLE_ORDER
    );
  });

  it("re-stamps only unbound envelope fields and preserves certificate-bound values", () => {
    const prefix = goldenPrefix(3);
    const [resumed] = patches(
      createSemanticSceneFixtureEvents(
        request(11, prefix.scene, prefix.semanticScene)
      )
    );
    const source = prefix.sourceEvents[3];

    expect(resumed.generation).toBe(11);
    expect(resumed.sequence).toBe(1);
    expect(source.sequence).toBe(4);
    expect(resumed.attempt).toBe(source.attempt);
    expect(resumed.baseRevision).toBe(source.baseRevision);
    expect(resumed.resultRevision).toBe(source.resultRevision);
    expect(resumed.patch).toEqual(source.patch);
    expect(resumed.semantic).toEqual(source.semantic);
  });

  it("fails closed when either requested base is not the exact golden prefix", () => {
    const prefix = goldenPrefix(1);
    const missingTriangle = createSceneState({ revision: 1, nodes: [] });
    const missingHead = createSemanticSceneState({
      revision: 1,
      components: prefix.semanticScene.components,
    });

    for (const invalidRequest of [
      request(2, missingTriangle, prefix.semanticScene),
      request(2, prefix.scene, missingHead),
    ]) {
      expect(() => createSemanticSceneFixtureEvents(invalidRequest)).toThrow(
        expect.objectContaining<Partial<SemanticSceneFixtureError>>({
          name: "SemanticSceneFixtureError",
          code: "base_mismatch",
        })
      );
    }
  });

  it("returns an honest non-retryable terminal event for a complete prefix", () => {
    const complete = goldenPrefix(PYTHAGOREAN_ROLE_ORDER.length);
    const events = createSemanticSceneFixtureEvents(
      request(2, complete.scene, complete.semanticScene)
    );

    expect(patches(events)).toHaveLength(0);
    expect(events.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "scene_stream_failed",
    ]);
    expect(events.at(-1)).toEqual(
      expect.objectContaining({
        code: "semantic_fixture_complete",
        lastAcceptedRevision: 8,
        retryable: false,
      })
    );
  });

  it("uses the production byte-stream decoder without calling fetch", async () => {
    const received: SemanticSceneStreamEvent[] = [];
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("fixture must not use fetch"));
    try {
      const runner = createSemanticSceneFixtureRunner({
        eventDelayMs: 0,
        chunkDelayMs: 0,
      });
      await runner({
        request: request(),
        signal: new AbortController().signal,
        onEvent: (event) => received.push(event),
      });
    } finally {
      fetchSpy.mockRestore();
    }

    expect(received).toEqual(createSemanticSceneFixtureEvents(request()));
    expect(patches(received)).toHaveLength(8);
  });

  it("honors cancellation while stale mode can deliberately deliver late events", async () => {
    const controller = new AbortController();
    controller.abort();
    const normal = createSemanticSceneFixtureRunner({
      mode: "normal",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });

    await expect(
      normal({ request: request(), signal: controller.signal, onEvent: vi.fn() })
    ).rejects.toMatchObject({ name: "AbortError" });

    const lateEvents: SemanticSceneStreamEvent[] = [];
    const stale = createSemanticSceneFixtureRunner({
      mode: "stale",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    await stale({
      request: request(),
      signal: controller.signal,
      onEvent: (event) => lateEvents.push(event),
    });

    expect(lateEvents.at(-1)?.type).toBe("scene_stream_completed");
    expect(patches(lateEvents)).toHaveLength(8);
  });
});
