import { describe, expect, it, vi } from "vitest";

import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import {
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";

import {
  consumeProjectileChoreographySceneStreamResponse,
  createProjectileChoreographySceneStreamRunner,
  runProjectileChoreographySceneModelStream,
} from "./projectile-choreography-model-stream";

const REQUEST: ProjectileMotionRequestV1 = {
  protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  routingMode: "reflex",
  problemSpec: { v: 1, speedMps: 20, angleDeg: 45 },
  generation: 1,
  baseScene: { revision: 0, nodes: [] },
  baseSemanticScene: { revision: 0, components: [] },
  requestedRoute: { intent: "advance", targetStage: "setup" },
};

const EVENTS = [
  decodeProjectileChoreographySceneStreamEventV1({
    type: "scene_stream_started",
    generation: 1,
    attempt: 1,
    baseRevision: 0,
  }),
  decodeProjectileChoreographySceneStreamEventV1({
    type: "projectile_choreography_scene_stream_declined",
    generation: 1,
    attempt: 1,
    finalRevision: 0,
    reasonCode: "unsupported_intent",
    message: "That request does not match a supported projectile explanation.",
  }),
] as const;

function response(
  events: readonly ProjectileChoreographySceneStreamEventV1[],
): Response {
  return new Response(
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );
}

describe("projectile choreography model transport", () => {
  it("decodes every projectile SSE event before admitting it", async () => {
    const received: ProjectileChoreographySceneStreamEventV1[] = [];

    await consumeProjectileChoreographySceneStreamResponse(
      response(EVENTS),
      (event) => received.push(event),
    );

    expect(received).toEqual(EVENTS);
    expect(received.every(Object.isFrozen)).toBe(true);
  });

  it.each([
    ["product", "/api/live-scenes/choreography/stream"],
    ["developmentLab", "/api/live-scenes/lab/choreography/stream"],
  ] as const)(
    "posts the exact request to the %s endpoint",
    async (endpoint, path) => {
      const fetchImpl = vi.fn<typeof fetch>(async () => response(EVENTS));
      const getHeaders = vi.fn(async () => ({
        Authorization: "Bearer fresh-token",
      }));
      const received: ProjectileChoreographySceneStreamEventV1[] = [];
      const controller = new AbortController();

      await runProjectileChoreographySceneModelStream({
        apiUrl: "https://murmur.example/",
        endpoint,
        request: REQUEST,
        signal: controller.signal,
        onEvent: (event) => received.push(event),
        headers: { "X-Client": "gate-1.7" },
        getHeaders,
        fetchImpl,
      });

      expect(received).toEqual(EVENTS);
      expect(getHeaders).toHaveBeenCalledOnce();
      expect(fetchImpl).toHaveBeenCalledOnce();
      expect(fetchImpl.mock.calls[0]).toEqual([
        `https://murmur.example${path}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Client": "gate-1.7",
            Authorization: "Bearer fresh-token",
          },
          body: JSON.stringify(REQUEST),
          signal: controller.signal,
        },
      ]);
    },
  );

  it("resolves the auth hook anew for every runner invocation", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => response(EVENTS));
    const getHeaders = vi
      .fn()
      .mockResolvedValueOnce({ Authorization: "Bearer first" })
      .mockResolvedValueOnce({ Authorization: "Bearer second" });
    const runner = createProjectileChoreographySceneStreamRunner({
      apiUrl: "https://murmur.example",
      endpoint: "product",
      getHeaders,
      fetchImpl,
    });

    for (let index = 0; index < 2; index += 1) {
      await runner({
        request: REQUEST,
        signal: new AbortController().signal,
        onEvent: () => undefined,
      });
    }

    expect(getHeaders).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer first",
    });
    expect(fetchImpl.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer second",
    });
  });
});
