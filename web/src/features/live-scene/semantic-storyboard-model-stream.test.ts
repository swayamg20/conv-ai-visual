import { describe, expect, it, vi } from "vitest";

import {
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import {
  decodeSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  consumeSemanticStoryboardSceneStreamResponse,
  createSemanticStoryboardSceneStreamRunner,
  runSemanticStoryboardSceneModelStream,
} from "./semantic-storyboard-model-stream";

const REQUEST: SemanticStoryboardRequestV1 = {
  protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  routingMode: "reflex",
  problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
  generation: 1,
  baseScene: { revision: 0, nodes: [] },
  baseSemanticScene: { revision: 0, components: [] },
};

const EVENTS = [
  decodeSemanticStoryboardSceneStreamEventV1({
    type: "semantic_storyboard_scene_stream_started",
    generation: 1,
    attempt: 1,
    baseRevision: 0,
  }),
  decodeSemanticStoryboardSceneStreamEventV1({
    type: "semantic_storyboard_scene_stream_failed",
    generation: 1,
    attempt: 1,
    baseRevision: 0,
    code: "storyboard_integrity_error",
    message: "The storyboard runtime rejected an unsafe result.",
    lastAcceptedRevision: 0,
    retryable: false,
  }),
] as const;

function response(
  events: readonly SemanticStoryboardSceneStreamEventV1[],
): Response {
  return new Response(
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );
}

describe("semantic storyboard model transport", () => {
  it("decodes every dedicated SSE event before admitting it", async () => {
    const received: SemanticStoryboardSceneStreamEventV1[] = [];

    await consumeSemanticStoryboardSceneStreamResponse(
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
      const received: SemanticStoryboardSceneStreamEventV1[] = [];
      const controller = new AbortController();

      await runSemanticStoryboardSceneModelStream({
        apiUrl: "https://murmur.example/",
        endpoint,
        request: REQUEST,
        signal: controller.signal,
        onEvent: (event) => received.push(event),
        headers: { "X-Client": "gate-1.8" },
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
            "X-Client": "gate-1.8",
            Authorization: "Bearer fresh-token",
          },
          body: JSON.stringify(REQUEST),
          signal: controller.signal,
        },
      ]);
    },
  );

  it("resolves fresh auth for every bound runner invocation", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => response(EVENTS));
    const getHeaders = vi
      .fn()
      .mockResolvedValueOnce({ Authorization: "Bearer first" })
      .mockResolvedValueOnce({ Authorization: "Bearer second" });
    const runner = createSemanticStoryboardSceneStreamRunner({
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

  it("rejects a non-canonical nested request before auth or fetch", async () => {
    const getHeaders = vi.fn(async () => ({ Authorization: "Bearer token" }));
    const fetchImpl = vi.fn<typeof fetch>();

    await expect(
      runSemanticStoryboardSceneModelStream({
        apiUrl: "https://murmur.example",
        endpoint: "product",
        request: {
          ...REQUEST,
          problemSpec: {
            v: 1,
            speed_mps: 20,
            anglesDeg: [30, 60],
          },
        } as unknown as SemanticStoryboardRequestV1,
        signal: new AbortController().signal,
        onEvent: () => undefined,
        getHeaders,
        fetchImpl,
      }),
    ).rejects.toThrow(/unknown field speed_mps/);

    expect(getHeaders).not.toHaveBeenCalled();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
