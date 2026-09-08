import { describe, expect, it, vi } from "vitest";

import fixtureValue from "./fixtures/completing-the-square.v1.json";
import {
  consumeChoreographySceneStreamResponse,
  decodeChoreographySceneStreamEvent,
  runChoreographySceneModelStream,
  type ChoreographySceneCheckpointEvent,
  type ChoreographySceneStreamEvent,
} from "./choreography-model-stream";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function record(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>;
}

function checkpointEvents(): Record<string, unknown>[] {
  return fixtureValue.events.filter(
    (event) => event.type === "choreography_scene_checkpoint",
  ) as unknown as Record<string, unknown>[];
}

function encodedFixtureResponse(): Response {
  const body = fixtureValue.events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("choreography model stream", () => {
  it("decodes the complete compiler-generated lifecycle and freezes checkpoint claims", () => {
    const events = fixtureValue.events.map(decodeChoreographySceneStreamEvent);
    const checkpoints = events.filter(
      (event): event is ChoreographySceneCheckpointEvent =>
        event.type === "choreography_scene_checkpoint",
    );

    expect(events.map((event) => event.type)).toEqual([
      "scene_stream_started",
      ...Array<string>(8).fill("choreography_scene_checkpoint"),
      "scene_stream_completed",
    ]);
    expect(checkpoints.map((event) => event.semantic.checkpointId)).toEqual(
      fixtureValue.transcript.checkpointIds,
    );
    expect(checkpoints.map((event) => event.sequence)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8,
    ]);
    expect(checkpoints.at(0)?.semantic.resultComponent).toEqual({
      kind: "completing_square",
      id: "square-lesson",
      lastMainCheckpoint: "problem",
      cornerClarified: false,
    });
    expect(
      checkpoints.at(-1)?.semantic.resultComponent.lastMainCheckpoint,
    ).toBe("solve_roots");
    expect(Object.isFrozen(checkpoints[0])).toBe(true);
    expect(Object.isFrozen(checkpoints[0].semantic)).toBe(true);
    expect(Object.isFrozen(checkpoints[0].semantic.resultComponent)).toBe(true);
    expect(Object.isFrozen(checkpoints[0].patch.operations)).toBe(true);
  });

  it.each([
    [
      "unknown event field",
      (event: Record<string, unknown>) => {
        event.rawGsap = { scale: 9 };
      },
      /unknown field rawGsap/,
    ],
    [
      "revision gap",
      (event: Record<string, unknown>) => {
        event.resultRevision = 7;
      },
      /revisions must advance exactly once/,
    ],
    [
      "semantic revision drift",
      (event: Record<string, unknown>) => {
        record(event.semantic).semanticResultRevision = 7;
      },
      /semantic metadata revisions must advance exactly once/,
    ],
    [
      "component ownership drift",
      (event: Record<string, unknown>) => {
        record(record(event.semantic).resultComponent).id = "other";
      },
      /resultComponent id must match/,
    ],
    [
      "frontier drift",
      (event: Record<string, unknown>) => {
        record(record(event.semantic).resultComponent).lastMainCheckpoint =
          "area_model";
      },
      /main checkpoint must match/,
    ],
    [
      "sequence overflow",
      (event: Record<string, unknown>) => {
        event.sequence = 9;
      },
      /checkpoint sequence/,
    ],
  ])("rejects %s", (_label, mutate, expected) => {
    const event = clone(checkpointEvents()[0]);
    mutate(event);
    expect(() => decodeChoreographySceneStreamEvent(event)).toThrow(expected);
  });

  it("decodes only the closed choreography decline vocabulary", () => {
    const source = {
      type: "choreography_scene_stream_declined",
      generation: 2,
      attempt: 1,
      finalRevision: 4,
      reasonCode: "no_forward_progress",
      message: "The requested checkpoint is already visible.",
    };

    expect(decodeChoreographySceneStreamEvent(source)).toEqual(source);
    expect(() =>
      decodeChoreographySceneStreamEvent({
        ...source,
        reasonCode: "model_decides_later",
      }),
    ).toThrow(/reasonCode is unsupported/);
  });

  it("consumes the full SSE fixture through the shared byte-safe transport", async () => {
    const events: ChoreographySceneStreamEvent[] = [];

    await consumeChoreographySceneStreamResponse(
      encodedFixtureResponse(),
      (event) => events.push(event),
    );

    expect(events).toHaveLength(10);
    expect(events.at(1)?.type).toBe("choreography_scene_checkpoint");
    expect(events.at(-1)).toMatchObject({
      type: "scene_stream_completed",
      finalRevision: 8,
      patchCount: 8,
    });
  });

  it.each([
    ["product", "/api/live-scenes/choreography/stream"],
    ["developmentLab", "/api/live-scenes/lab/choreography/stream"],
  ] as const)("posts to the explicit %s endpoint", async (endpoint, path) => {
    const request = {
      prompt: fixtureValue.transcript.prompt,
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    } as const;
    const received: ChoreographySceneStreamEvent[] = [];
    const fetchImpl = vi.fn<typeof fetch>(async () => encodedFixtureResponse());
    const controller = new AbortController();

    await runChoreographySceneModelStream({
      apiUrl: "https://murmur.example/",
      endpoint,
      request,
      signal: controller.signal,
      onEvent: (event) => received.push(event),
      headers: { Authorization: "Bearer fixture" },
      fetchImpl,
    });

    expect(received).toHaveLength(10);
    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`https://murmur.example${path}`);
    expect(init).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer fixture",
      },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
  });
});
