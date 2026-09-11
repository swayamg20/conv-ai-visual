/** @vitest-environment happy-dom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import type { ProjectileChoreographyRuntimeSnapshot } from "@/features/live-scene/projectile-choreography-stream-runtime";

import { createProjectileMotionE2ESession } from "./client";

vi.mock("@/lib/firebase", () => ({ getAuthHeaders: vi.fn() }));

describe("projectile-motion e2e client session", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("records a frozen request frontier while staying provider-free", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const session = createProjectileMotionE2ESession("main", false);
    const events: string[] = [];
    await session.runner({
      request: decodeProjectileMotionRequestV1({
        protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
        routingMode: "reflex",
        problemSpec: { v: 1, speedMps: 20, angleDeg: 45 },
        generation: 1,
        baseScene: { revision: 0, nodes: [] },
        baseSemanticScene: { revision: 0, components: [] },
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event.type),
    });

    const state = session.bridge.getState();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(events).toHaveLength(8);
    expect(state).toEqual({
      runnerCallCount: 1,
      calls: [
        {
          ordinal: 1,
          generation: 1,
          routingMode: "reflex",
          problemSpec: { v: 1, speedMps: 20, angleDeg: 45 },
          baseRevision: 0,
          semanticRevision: 0,
          certificateHeadSha256: null,
          checkpointId: null,
          clarifiedTopics: [],
          activeClarification: null,
          requestedRoute: { intent: "advance", targetStage: "solve" },
        },
      ],
    });
    expect(Object.isFrozen(session.bridge)).toBe(true);
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(state.calls)).toBe(true);
    expect(Object.isFrozen(state.calls[0])).toBe(true);
    expect(Object.isFrozen(state.calls[0].problemSpec)).toBe(true);
    expect(Object.isFrozen(state.calls[0].clarifiedTopics)).toBe(true);
    expect(session.bridge.getRuntimeObservation()).toBeNull();
    expect(session.bridge.getRuntimeObservationHistory()).toEqual([]);

    const emptyScene = Object.freeze({
      revision: 0,
      nodes: Object.freeze([]),
    });
    const emptySemanticScene = Object.freeze({
      revision: 0,
      components: Object.freeze([]),
    });
    const runtimeSnapshot: ProjectileChoreographyRuntimeSnapshot =
      Object.freeze({
        phase: "idle",
        generation: 0,
        attempt: 1,
        sequence: 0,
        committedScene: emptyScene,
        provisionalScene: emptyScene,
        committedSemanticScene: emptySemanticScene,
        provisionalSemanticScene: emptySemanticScene,
        committedViewport: null,
        provisionalViewport: null,
        accepted: Object.freeze([]),
        queuedCheckpointCount: 0,
        narration: "Ready for a live projectile lesson.",
        rendererTrusted: true,
      });
    session.observeRuntimeSnapshot(runtimeSnapshot);
    const observation = session.bridge.getRuntimeObservation();
    expect(observation?.snapshot).toBe(runtimeSnapshot);
    expect(observation?.observedAtMs).toBeGreaterThanOrEqual(0);
    expect(Object.isFrozen(observation)).toBe(true);
    expect(Object.isFrozen(observation?.snapshot)).toBe(true);
    const history = session.bridge.getRuntimeObservationHistory();
    expect(history).toEqual([observation]);
    expect(Object.isFrozen(history)).toBe(true);
    expect(Object.isFrozen(history[0])).toBe(true);
  });
});
