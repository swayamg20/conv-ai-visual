import { describe, expect, it, vi } from "vitest";

import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "@/lib/live-scene/parametric-choreography";
import type { ParametricChoreographyRequestV3 } from "@/lib/live-scene/parametric-choreography-request";
import type { ParametricChoreographySceneStreamEventV3 } from "@/lib/live-scene/parametric-choreography-stream";

import {
  consumeParametricChoreographySceneStreamResponse,
  createParametricChoreographySceneStreamRunner,
  runParametricChoreographySceneModelStream,
} from "./parametric-choreography-model-stream";
import { createParametricLifecycleFixture } from "./parametric-choreography-test-fixture";

const REQUEST: ParametricChoreographyRequestV3 = {
  protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
  routingMode: "reflex",
  problemText: "x squared plus six x equals seven",
  generation: 1,
  baseScene: { revision: 0, nodes: [] },
  baseSemanticScene: { revision: 0, components: [] },
  requestedRoute: { intent: "advance", targetStage: "setup" },
};

function response(
  events: readonly ParametricChoreographySceneStreamEventV3[],
): Response {
  return new Response(
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );
}

describe("parametric choreography model transport", () => {
  it("decodes every V3 SSE event before admitting it", async () => {
    const expected = createParametricLifecycleFixture(1);
    const received: ParametricChoreographySceneStreamEventV3[] = [];

    await consumeParametricChoreographySceneStreamResponse(
      response(expected),
      (event) => received.push(event),
    );

    expect(received).toEqual(expected);
    expect(Object.isFrozen(received[1])).toBe(true);
  });

  it.each([
    ["product", "/api/live-scenes/choreography/stream"],
    ["developmentLab", "/api/live-scenes/lab/choreography/stream"],
  ] as const)(
    "posts the exact V3 body to the %s endpoint",
    async (endpoint, path) => {
      const events = createParametricLifecycleFixture(1);
      const fetchImpl = vi.fn<typeof fetch>(async () => response(events));
      const getHeaders = vi.fn(async () => ({
        Authorization: "Bearer fresh-token",
      }));
      const received: ParametricChoreographySceneStreamEventV3[] = [];
      const controller = new AbortController();

      await runParametricChoreographySceneModelStream({
        apiUrl: "https://murmur.example/",
        endpoint,
        request: REQUEST,
        signal: controller.signal,
        onEvent: (event) => received.push(event),
        headers: { "X-Client": "gate-1.6" },
        getHeaders,
        fetchImpl,
      });

      expect(received).toEqual(events);
      expect(getHeaders).toHaveBeenCalledOnce();
      expect(fetchImpl).toHaveBeenCalledOnce();
      expect(fetchImpl.mock.calls[0]).toEqual([
        `https://murmur.example${path}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Client": "gate-1.6",
            Authorization: "Bearer fresh-token",
          },
          body: JSON.stringify(REQUEST),
          signal: controller.signal,
        },
      ]);
    },
  );

  it("resolves the auth hook anew for every runner invocation", async () => {
    const events = createParametricLifecycleFixture(1);
    const fetchImpl = vi.fn<typeof fetch>(async () => response(events));
    const getHeaders = vi
      .fn()
      .mockResolvedValueOnce({ Authorization: "Bearer first" })
      .mockResolvedValueOnce({ Authorization: "Bearer second" });
    const runner = createParametricChoreographySceneStreamRunner({
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
