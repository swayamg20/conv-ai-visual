import { describe, expect, it, vi } from "vitest";

import { createSceneState, createSemanticSceneState } from "@/lib/live-scene";

import {
  consumeSemanticSceneStreamResponse,
  consumeSceneStreamResponse,
  decodeSemanticSceneStreamEvent,
  decodeSceneStreamEvent,
  parseSemanticSceneStreamEvent,
  parseSceneStreamEvent,
  runSemanticSceneModelStream,
  runSceneModelStream,
  SceneModelStreamError,
  type SemanticSceneStreamEvent,
  type SemanticSceneStreamRunInvocation,
  type SemanticSceneStreamRunner,
  type SceneStreamEvent,
} from "./model-stream";

const encoder = new TextEncoder();
const semanticDigests = {
  beat: "2985081a36afc4116b715142c8384af329e752ce57ba41bed8e42b3b03955458",
  base: "a8a338405d84e56a062bb45ec4800cfb09e8af57bbcec8560069464ff3f7dee0",
  result: "2f47395b2aa4c0e994301c2698a2ebb5814b176d23f30035348728bfb1b54ae8",
  patch: "4cc1d990a304e1dc5c9a291c774cca4b4c0775c5413daae1ab9ac76a13af01f2",
  receipt: "518b80fd923323c07939edb97a9dad39aebd3ec475518be0074c85b890fbee47",
  certificate: "28ab5dd89fb5a900354b63a1279c2c5566eebc99f8f9142500a082d1436f485d",
} as const;

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

function semanticPatchEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "semantic_scene_patch",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: 0,
    resultRevision: 1,
    patch: {
      v: 1,
      patchId: "areas__atom_triangle",
      narration: "Relate the three square areas.",
      operations: [
        {
          op: "put",
          node: {
            id: "areas__triangle",
            kind: "path",
            presentation: { enter: "draw", exit: "fade" },
            points: [
              [320, 260],
              [440, 260],
              [320, 420],
            ],
            closed: true,
            style: {
              stroke: "hsl(var(--chalk))",
              strokeWidth: 4,
              opacity: 1,
              roughness: 0.45,
              fill: "transparent",
            },
          },
        },
      ],
    },
    semantic: {
      beat: {
        v: 1,
        beatId: "beat-identity",
        narration: "Relate the three square areas.",
        act: "derive",
        directive: {
          kind: "pythagorean_area_identity",
          id: "areas",
          revealThrough: "identity",
        },
      },
      atomId: "areas__atom_triangle",
      componentId: "areas",
      role: "triangle",
      atomOrdinal: 1,
      semanticBaseRevision: 0,
      semanticResultRevision: 1,
      receipt: {
        issuer: "semantic_verifier",
        componentId: "areas",
        role: "triangle",
        nodeId: "areas__triangle",
        obligationCodes: [
          "stable_id",
          "unique_ids",
          "board_bounds",
          "right_angle",
          "hypotenuse_ratio",
        ],
        verified: true,
      },
      certificate: {
        body: {
          v: 1,
          issuer: "semantic_compiler",
          compilerVersion: "murmur.pythagorean_area_identity.v1",
          canonicalization: "murmur-json-v1",
          hashAlgorithm: "sha256",
          atomId: "areas__atom_triangle",
          beatId: "beat-identity",
          beatSha256: semanticDigests.beat,
          componentId: "areas",
          role: "triangle",
          nodeId: "areas__triangle",
          atomOrdinal: 1,
          baseSemanticRevision: 0,
          resultSemanticRevision: 1,
          baseSceneSha256: semanticDigests.base,
          resultSceneSha256: semanticDigests.result,
          patchSha256: semanticDigests.patch,
          receiptSha256: semanticDigests.receipt,
          previousCertificateSha256: null,
        },
        certificateSha256: semanticDigests.certificate,
      },
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

function declinedEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "semantic_scene_stream_declined",
    generation: 1,
    attempt: 1,
    finalRevision: 0,
    reasonCode: "unsupported_intent",
    message: "  This request does not have a supported visual yet.  ",
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

describe("semantic scene model stream event decoder", () => {
  it("decodes exact lifecycle events and compiler-certified patch events", () => {
    const inputs = [
      startedEvent(),
      semanticPatchEvent(),
      {
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 8,
        totalMs: 12,
        repaired: false,
      },
    ];

    const decoded = inputs.map(decodeSemanticSceneStreamEvent);

    expect(decoded.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "semantic_scene_patch",
      "scene_stream_completed",
    ]);
    expect(decoded.every(Object.isFrozen)).toBe(true);
    expect(decoded[1]).toMatchObject({
      semantic: {
        componentId: "areas",
        role: "triangle",
        atomOrdinal: 1,
      },
    });
    expect(parseSemanticSceneStreamEvent(JSON.stringify(inputs[1]))).toEqual(
      decoded[1]
    );
  });

  it("decodes a semantic-only declined terminal and rejects it on the raw path", () => {
    const input = declinedEvent();
    const decoded = decodeSemanticSceneStreamEvent(input);

    expect(decoded).toEqual({
      type: "semantic_scene_stream_declined",
      generation: 1,
      attempt: 1,
      finalRevision: 0,
      reasonCode: "unsupported_intent",
      message: "This request does not have a supported visual yet.",
    });
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(parseSemanticSceneStreamEvent(JSON.stringify(input))).toEqual(decoded);
    expect(errorCode(() => decodeSceneStreamEvent(input))).toBe("invalid_event");
  });

  it("rejects malformed semantic declined terminals", () => {
    const invalid = [
      declinedEvent({ extra: true }),
      declinedEvent({ attempt: 0 }),
      declinedEvent({ finalRevision: -1 }),
      declinedEvent({ reasonCode: "model_unsure" }),
      declinedEvent({ reasonCode: " unsupported_intent " }),
      declinedEvent({ message: "   " }),
    ];

    for (const value of invalid) {
      expect(errorCode(() => decodeSemanticSceneStreamEvent(value))).toBe(
        "invalid_event"
      );
    }
  });

  it("keeps raw and semantic patch discriminators on separate trust paths", () => {
    expect(errorCode(() => decodeSceneStreamEvent(semanticPatchEvent()))).toBe(
      "invalid_event"
    );
    expect(errorCode(() => decodeSemanticSceneStreamEvent(patchEvent()))).toBe(
      "invalid_event"
    );

    const extraMetadata = semanticPatchEvent();
    Object.assign(extraMetadata.semantic, { providerTrace: "unsafe" });
    expect(
      errorCode(() => decodeSemanticSceneStreamEvent(extraMetadata))
    ).toBe("invalid_event");
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

  it("cancels on callback failure without masking that original error", async () => {
    const callbackError = new Error("consumer rejected the scene event");
    const cancelSpy = vi.fn(() => {
      throw new Error("transport cancellation failed");
    });
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(sseFrame(startedEvent())));
        },
        cancel: cancelSpy,
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } }
    );

    await expect(
      consumeSceneStreamResponse(response, () => {
        throw callbackError;
      })
    ).rejects.toBe(callbackError);
    expect(cancelSpy).toHaveBeenCalledOnce();
    expect(cancelSpy).toHaveBeenCalledWith(callbackError);
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

  it("posts a changed prompt to the auth-free development-lab endpoint", async () => {
    const controller = new AbortController();
    const baseScene = createSceneState({ revision: 0, nodes: [] });
    const request = {
      prompt: "Compare merge sort with quicksort using my exact wording",
      generation: 3,
      baseScene,
    };
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      streamedResponse([
        encoder.encode(
          sseFrame(startedEvent({ generation: 3 })) +
            sseFrame(failedEvent({ generation: 3 }), "scene_stream_failed")
        ),
      ])
    );

    await runSceneModelStream({
      apiUrl: "http://127.0.0.1:8000/",
      endpoint: "developmentLab",
      request,
      signal: controller.signal,
      fetchImpl,
      onEvent: vi.fn(),
    });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/live-scenes/lab/stream");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(init?.body))).toEqual(request);
  });
});

describe("semantic scene model stream transport", () => {
  it("uses the shared byte-safe SSE loop for semantic events", async () => {
    const values = [
      startedEvent(),
      semanticPatchEvent(),
      failedEvent({ lastAcceptedRevision: 1 }),
    ];
    const wire = encoder.encode(
      values.map((value) => sseFrame(value, value.type, "\r\n")).join("")
    );
    const chunks = Array.from(wire, (byte) => Uint8Array.of(byte));
    const received: SemanticSceneStreamEvent[] = [];

    await consumeSemanticSceneStreamResponse(streamedResponse(chunks), (event) => {
      received.push(event);
    });

    expect(received.map((event) => event.type)).toEqual([
      "scene_stream_started",
      "semantic_scene_patch",
      "scene_stream_failed",
    ]);
  });

  it("cancels a never-ending response after a malformed semantic event", async () => {
    const cancelSpy = vi.fn();
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode("data: {bad-json\n\n"));
        },
        cancel: cancelSpy,
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } }
    );

    let originalError: unknown;
    try {
      await consumeSemanticSceneStreamResponse(response, vi.fn());
    } catch (error) {
      originalError = error;
    }

    expect(originalError).toMatchObject({ code: "invalid_json" });
    expect(cancelSpy).toHaveBeenCalledOnce();
    expect(cancelSpy).toHaveBeenCalledWith(originalError);
  });

  it("posts paired snapshots to the explicit auth-free semantic lab endpoint", async () => {
    const controller = new AbortController();
    const request = {
      prompt: "Explain the Pythagorean area identity",
      generation: 4,
      baseScene: createSceneState({ revision: 0, nodes: [] }),
      baseSemanticScene: createSemanticSceneState({
        revision: 0,
        components: [],
      }),
    };
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      streamedResponse([
        encoder.encode(
          sseFrame(startedEvent({ generation: 4 })) +
            sseFrame(
              failedEvent({ generation: 4 }),
              "scene_stream_failed"
            )
        ),
      ])
    );
    const onEvent = vi.fn();
    const invocation: SemanticSceneStreamRunInvocation = {
      request,
      signal: controller.signal,
      onEvent,
    };
    const runner: SemanticSceneStreamRunner = async (streamInvocation) => {
      await runSemanticSceneModelStream({
        apiUrl: "http://127.0.0.1:8000/",
        endpoint: "developmentLab",
        headers: { "X-Test": "semantic-gate" },
        fetchImpl,
        ...streamInvocation,
      });
    };

    await runner(invocation);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/live-scenes/lab/semantic/stream"
    );
    expect(init).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Test": "semantic-gate",
      },
      signal: controller.signal,
    });
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      "scene_stream_started",
      "scene_stream_failed",
    ]);
  });

  it("posts paired snapshots and authorization to the semantic product endpoint", async () => {
    const controller = new AbortController();
    const request = {
      prompt: "Continue the Pythagorean area identity",
      generation: 2,
      baseScene: createSceneState({ revision: 0, nodes: [] }),
      baseSemanticScene: createSemanticSceneState({ revision: 0, components: [] }),
    };
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      streamedResponse([
        encoder.encode(
          sseFrame(startedEvent({ generation: 2 })) +
            sseFrame(failedEvent({ generation: 2 }), "scene_stream_failed")
        ),
      ])
    );

    await runSemanticSceneModelStream({
      apiUrl: "https://murmur.example/",
      endpoint: "product",
      headers: { Authorization: "Bearer verified-user-token" },
      request,
      signal: controller.signal,
      onEvent: vi.fn(),
      fetchImpl,
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("https://murmur.example/api/live-scenes/semantic/stream");
    expect(init).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer verified-user-token",
      },
      signal: controller.signal,
    });
    expect(JSON.parse(String(init?.body))).toEqual(request);
  });
});
