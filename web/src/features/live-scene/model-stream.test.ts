import { describe, expect, it, vi } from "vitest";

import { createSceneState } from "@/lib/live-scene";

import {
  consumeSceneStreamResponse,
  decodeSceneStreamEvent,
  parseSceneStreamEvent,
  runSceneModelStream,
  SceneModelStreamError,
  type SceneStreamEvent,
} from "./model-stream";

const encoder = new TextEncoder();

function textNode(id = "lesson-title", text = "Build a triangle") {
  return {
    id,
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x: 400,
    y: 64,
    text,
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 28,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function patchEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "scene_patch",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: 0,
    resultRevision: 1,
    patch: {
      v: 1,
      patchId: "patch-1",
      narration: "Draw π → 😀",
      operations: [{ op: "put", node: textNode() }],
    },
    ...overrides,
  };
}

function startedEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "scene_stream_started",
    generation: 1,
    attempt: 1,
    baseRevision: 0,
    ...overrides,
  };
}

function failedEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "scene_stream_failed",
    generation: 1,
    attempt: 1,
    code: "provider_error",
    message: "The visual generator is unavailable.",
    lastAcceptedRevision: 0,
    retryable: true,
    ...overrides,
  };
}

function sseFrame(value: unknown, event = "message", lineEnding = "\n"): string {
  return `event: ${event}${lineEnding}data: ${JSON.stringify(value)}${lineEnding}${lineEnding}`;
}

function streamedResponse(chunks: readonly Uint8Array[]): Response {
  const remaining = [...chunks];
  return new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        const chunk = remaining.shift();
        if (chunk) controller.enqueue(chunk);
        else controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } }
  );
}

function errorCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof SceneModelStreamError ? error.code : undefined;
  }
  return undefined;
}

describe("scene model stream event decoder", () => {
  it("decodes and freezes every exact lifecycle event", () => {
    const inputs = [
      startedEvent(),
      patchEvent(),
      {
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 1,
        message: "  Repairing the next visual patch.  ",
      },
      {
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 42.5,
        totalMs: 80,
        repaired: false,
      },
      failedEvent({ message: "  The visual generator is unavailable.  " }),
    ];

    const decoded = inputs.map(decodeSceneStreamEvent);

    expect(decoded.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "scene_patch",
      "scene_stream_repairing",
      "scene_stream_completed",
      "scene_stream_failed",
    ]);
    expect(decoded.every(Object.isFrozen)).toBe(true);
    expect(decoded[2]).toMatchObject({ message: "Repairing the next visual patch." });
    expect(decoded[4]).toMatchObject({ message: "The visual generator is unavailable." });
    expect(parseSceneStreamEvent(JSON.stringify(inputs[3]))).toEqual(decoded[3]);
  });

  it("rejects unknown keys, invalid bounds, unsafe codes, and malformed JSON", () => {
    const invalid: unknown[] = [
      { ...startedEvent(), extra: true },
      startedEvent({ attempt: 0 }),
      startedEvent({ generation: 1.5 }),
      {
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 1,
        lastAcceptedRevision: 0,
        message: "Repairing",
      },
      {
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 50,
        totalMs: 49,
        repaired: false,
      },
      failedEvent({ code: " Provider_Error " }),
      failedEvent({ message: "   " }),
      { type: "unsupported" },
      patchEvent({ sequence: 9 }),
      Object.assign(Object.create({ inherited: true }), startedEvent()),
    ];

    for (const [index, value] of invalid.entries()) {
      expect(
        errorCode(() => decodeSceneStreamEvent(value)),
        `invalid case ${index}`
      ).toBe("invalid_event");
    }
    expect(errorCode(() => parseSceneStreamEvent("{not-json"))).toBe("invalid_json");
  });
});

describe("scene model stream transport", () => {
  it("consumes CRLF SSE across arbitrary UTF-8 and network splits", async () => {
    const values = [startedEvent(), patchEvent(), failedEvent({ lastAcceptedRevision: 1 })];
    const wire = encoder.encode(
      values.map((value) => sseFrame(value, value.type as string, "\r\n")).join("")
    );
    const chunks = Array.from(wire, (byte) => Uint8Array.of(byte));
    const received: SceneStreamEvent[] = [];

    await consumeSceneStreamResponse(streamedResponse(chunks), (event) => {
      received.push(event);
    });

    expect(received.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "scene_patch",
      "scene_stream_failed",
    ]);
    expect(received[1]).toMatchObject({
      type: "scene_patch",
      patch: { narration: "Draw π → 😀" },
    });
  });

  it("emits an unterminated final event and exposes bounded response failures", async () => {
    const received: SceneStreamEvent[] = [];
    await consumeSceneStreamResponse(
      streamedResponse([encoder.encode(`data: ${JSON.stringify(startedEvent())}`)]),
      (event) => received.push(event)
    );
    expect(received).toHaveLength(1);

    await expect(
      consumeSceneStreamResponse(new Response("no", { status: 503 }), vi.fn())
    ).rejects.toMatchObject({ code: "http_error" });
    await expect(
      consumeSceneStreamResponse(new Response(null, { status: 200 }), vi.fn())
    ).rejects.toMatchObject({ code: "missing_body" });
    await expect(
      consumeSceneStreamResponse(
        streamedResponse([encoder.encode("data: {bad-json\n\n")]),
        vi.fn()
      )
    ).rejects.toMatchObject({ code: "invalid_json" });
  });

  it("posts the exact request, headers, and abort signal to the live-scene endpoint", async () => {
    const controller = new AbortController();
    const baseScene = createSceneState({ revision: 0, nodes: [] });
    const request = { prompt: "Explain Pythagoras", generation: 1, baseScene };
    const fetchCalls: Array<readonly [RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      fetchCalls.push([input, init]);
      return streamedResponse([
        encoder.encode(
          sseFrame(startedEvent()) + sseFrame(failedEvent(), "scene_stream_failed")
        ),
      ]);
    };
    const onEvent = vi.fn();

    await runSceneModelStream({
      apiUrl: "http://127.0.0.1:8000/",
      request,
      signal: controller.signal,
      headers: { Authorization: "Bearer fixture-token", "X-Test": "gate-1" },
      fetchImpl,
      onEvent,
    });

    expect(fetchCalls).toHaveLength(1);
    const [url, init] = fetchCalls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/live-scenes/stream");
    expect(init).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer fixture-token",
        "X-Test": "gate-1",
      },
      signal: controller.signal,
    });
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      "scene_stream_started",
      "scene_stream_failed",
    ]);
  });
});
