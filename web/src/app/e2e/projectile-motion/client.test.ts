/** @vitest-environment happy-dom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";

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
  });
});
