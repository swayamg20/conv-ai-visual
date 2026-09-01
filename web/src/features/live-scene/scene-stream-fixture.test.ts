import { describe, expect, it } from "vitest";

import { createSceneState } from "@/lib/live-scene";
import { applyScenePatch } from "@/lib/live-scene/patch";

import type { SceneStreamEvent, SceneStreamRequest } from "./model-stream";
import {
  createSceneFixtureEvents,
  createSceneFixtureRunner,
} from "./scene-stream-fixture";

function request(generation = 1): SceneStreamRequest {
  return Object.freeze({
    prompt: "Teach me why the Pythagorean theorem works",
    generation,
    baseScene: createSceneState({ revision: 0, nodes: [] }),
  });
}

describe("scene stream fixture", () => {
  it("builds a progressive, atomically applicable scene", () => {
    const events = createSceneFixtureEvents(request(), "normal");
    let scene = createSceneState({ revision: 0, nodes: [] });
    const patches = events.filter((event) => event.type === "scene_patch");

    for (const event of patches) scene = applyScenePatch(scene, event);

    expect(patches).toHaveLength(4);
    expect(scene.revision).toBe(4);
    expect(scene.nodes.map((node) => node.id)).toEqual(
      expect.arrayContaining([
        "titleG1",
        "rightAngleG1",
        "equationG1",
        "hypNoteG1",
      ])
    );
    expect(events.at(-1)).toEqual(
      expect.objectContaining({
        type: "scene_stream_completed",
        finalRevision: 4,
        patchCount: 4,
        repaired: false,
      })
    );
  });

  it("models one repair without inventing a second started event", () => {
    const events = createSceneFixtureEvents(request(3), "repair");

    expect(events.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "scene_stream_repairing",
      "scene_patch",
      "scene_patch",
      "scene_patch",
      "scene_patch",
      "scene_stream_completed",
    ]);
    expect(
      events.filter((event) => event.type === "scene_patch").every((event) => event.attempt === 2)
    ).toBe(true);
    expect(events.at(-1)).toEqual(expect.objectContaining({ repaired: true }));
  });

  it("terminates a failed repair without mutating the base revision", () => {
    const events = createSceneFixtureEvents(request(), "failure");

    expect(events).toHaveLength(3);
    expect(events.at(-1)).toEqual(
      expect.objectContaining({
        type: "scene_stream_failed",
        attempt: 2,
        lastAcceptedRevision: 0,
        retryable: true,
      })
    );
    expect(events.some((event) => event.type === "scene_patch")).toBe(false);
  });

  it("passes awkward UTF-8 and SSE chunks through the production decoder", async () => {
    const received: SceneStreamEvent[] = [];
    const runner = createSceneFixtureRunner({
      mode: "normal",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });

    await runner({
      request: request(),
      signal: new AbortController().signal,
      onEvent: (event) => received.push(event),
    });

    expect(received).toEqual(createSceneFixtureEvents(request(), "normal"));
    expect(received.find((event) => event.type === "scene_patch")).toEqual(
      expect.objectContaining({
        patch: expect.objectContaining({ narration: expect.stringContaining("I’m") }),
      })
    );
  });

  it("can deliberately deliver late events after cancellation", async () => {
    const received: SceneStreamEvent[] = [];
    const controller = new AbortController();
    controller.abort();
    const runner = createSceneFixtureRunner({
      mode: "stale",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });

    await runner({
      request: request(),
      signal: controller.signal,
      onEvent: (event) => received.push(event),
    });

    expect(received.at(-1)?.type).toBe("scene_stream_completed");
    expect(received.filter((event) => event.type === "scene_patch")).toHaveLength(4);
  });
});
