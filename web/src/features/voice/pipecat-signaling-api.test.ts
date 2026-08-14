import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const firebase = vi.hoisted(() => ({
  getAuthHeaders: vi.fn<() => Promise<Record<string, string>>>(),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: firebase.getAuthHeaders,
}));

import {
  createPipecatSignalingPort,
  PipecatSignalingApiError,
  type PipecatSignalingFetch,
} from "./pipecat-signaling-api";

const signalingUrl =
  "https://voice.example.test/api/voice/pipecat/signal/opaque-token-secret";
const offerSdp = "v=0\r\na=ice-pwd:offer-sdp-secret\r\n";
const answerSdp = "v=0\r\na=ice-pwd:answer-sdp-secret\r\n";
const pcId = "peer-connection-secret";

function answerResponse(
  overrides: Readonly<Record<string, unknown>> = {},
): Response {
  return new Response(
    JSON.stringify({ sdp: answerSdp, type: "answer", pc_id: pcId, ...overrides }),
    {
      status: 200,
      headers: { "Content-Type": "application/json" },
    },
  );
}

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (error: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function requestInit(fetcher: ReturnType<typeof vi.fn>, call: number): RequestInit {
  const init = fetcher.mock.calls[call]?.[1];
  if (!init || typeof init !== "object") {
    throw new Error("Expected a signaling request init");
  }
  return init as RequestInit;
}

function requestBody(fetcher: ReturnType<typeof vi.fn>, call: number): unknown {
  const body = requestInit(fetcher, call).body;
  if (typeof body !== "string") throw new Error("Expected a JSON request body");
  return JSON.parse(body) as unknown;
}

describe("Pipecat authenticated signaling port", () => {
  beforeEach(() => {
    firebase.getAuthHeaders
      .mockReset()
      .mockResolvedValue({ Authorization: "Bearer firebase-token-1" });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("posts the exact non-restarting offer and freezes the strict answer", async () => {
    const fetcher = vi.fn<PipecatSignalingFetch>().mockResolvedValue(answerResponse());
    const mutableOffer: {
      sdp: string;
      type: "offer";
      pcId: string | null;
      ignored?: string;
    } = {
      sdp: offerSdp,
      type: "offer",
      pcId: null,
      ignored: "must-not-cross-the-boundary",
    };
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });

    const pending = port.offer(mutableOffer);
    mutableOffer.sdp = "mutated-sdp";
    mutableOffer.pcId = "mutated-peer";
    const answer = await pending;

    expect(Object.isFrozen(port)).toBe(true);
    expect(Object.isFrozen(answer)).toBe(true);
    expect(answer).toEqual({ sdp: answerSdp, type: "answer", pc_id: pcId });
    expect(fetcher).toHaveBeenCalledWith(signalingUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer firebase-token-1",
      },
      body: JSON.stringify({
        sdp: offerSdp,
        type: "offer",
        pc_id: null,
        restart_pc: false,
      }),
      signal: expect.any(AbortSignal),
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  });

  it("owns candidate copies, maps exact snake-case DTOs, and serializes PATCHes", async () => {
    const firstPatch = deferred<Response>();
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockImplementationOnce(async () => firstPatch.promise)
      .mockResolvedValue(new Response(null, { status: 204 }));
    const authHeaderProvider = vi
      .fn<() => Promise<Record<string, string>>>()
      .mockResolvedValueOnce({ Authorization: "Bearer patch-token-1" })
      .mockResolvedValueOnce({ Authorization: "Bearer patch-token-2" });
    const firstCandidate = {
      candidate: "candidate:1 1 UDP 1 192.0.2.1 5000 typ host",
      sdpMid: "0",
      sdpMLineIndex: 0,
    };
    const firstBatch = { pcId, candidates: [firstCandidate] };
    const port = createPipecatSignalingPort(signalingUrl, {
      fetcher,
      authHeaderProvider,
    });

    const first = port.patchCandidates(firstBatch);
    firstCandidate.candidate = "mutated-candidate";
    firstCandidate.sdpMid = "mutated-mid";
    firstBatch.pcId = "mutated-peer";
    const second = port.patchCandidates({
      pcId,
      candidates: [
        {
          candidate: "candidate:2 1 UDP 2 192.0.2.2 5001 typ host",
          sdpMid: "1",
          sdpMLineIndex: 1,
        },
      ],
    });
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    expect(requestBody(fetcher, 0)).toEqual({
      pc_id: pcId,
      candidates: [
        {
          candidate: "candidate:1 1 UDP 1 192.0.2.1 5000 typ host",
          sdp_mid: "0",
          sdp_mline_index: 0,
        },
      ],
    });
    expect(authHeaderProvider).toHaveBeenCalledTimes(1);
    firstPatch.resolve(new Response(null, { status: 204 }));
    await first;
    await second;

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(authHeaderProvider).toHaveBeenCalledTimes(2);
    expect(requestInit(fetcher, 0).headers).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer patch-token-1",
    });
    expect(requestInit(fetcher, 1).headers).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer patch-token-2",
    });
  });

  it("rejects empty and oversized candidate batches before auth or fetch", async () => {
    const fetcher = vi.fn<PipecatSignalingFetch>();
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });
    const candidate = {
      candidate: "candidate:1 1 UDP 1 192.0.2.1 5000 typ host",
      sdpMid: "0",
      sdpMLineIndex: 0,
    };

    await expect(
      port.patchCandidates({ pcId, candidates: [] }),
    ).rejects.toMatchObject({ code: "invalid_request" });
    await expect(
      port.patchCandidates({
        pcId,
        candidates: Array.from({ length: 129 }, () => ({ ...candidate })),
      }),
    ).rejects.toMatchObject({ code: "invalid_request" });
    expect(firebase.getAuthHeaders).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("sends the exact DELETE body for pre-peer and established teardown", async () => {
    firebase.getAuthHeaders
      .mockReset()
      .mockResolvedValueOnce({ Authorization: "Bearer delete-token-1" })
      .mockResolvedValueOnce({ Authorization: "Bearer delete-token-2" });
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });

    await port.deletePeer(null);
    await port.deletePeer(pcId);

    expect(requestBody(fetcher, 0)).toEqual({ pc_id: null });
    expect(requestBody(fetcher, 1)).toEqual({ pc_id: pcId });
    expect(requestInit(fetcher, 0)).toMatchObject({
      method: "DELETE",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
    expect(requestInit(fetcher, 0).headers).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer delete-token-1",
    });
    expect(requestInit(fetcher, 1).headers).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer delete-token-2",
    });
  });

  it("uses one deadline across auth acquisition and fetch", async () => {
    vi.useFakeTimers();
    const authHeaderProvider = vi.fn(
      () =>
        new Promise<Record<string, string>>((resolve) => {
          setTimeout(
            () => resolve({ Authorization: "Bearer delayed-token" }),
            600,
          );
        }),
    );
    let requestSignal: AbortSignal | undefined;
    const fetcher = vi.fn<PipecatSignalingFetch>(async (_input, init) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    });
    const port = createPipecatSignalingPort(signalingUrl, {
      deadlineMs: 1_000,
      authHeaderProvider,
      fetcher,
    });

    const pending = port.offer({ sdp: offerSdp, type: "offer" });
    const rejection = expect(pending).rejects.toMatchObject({
      code: "timed_out",
    });
    await vi.advanceTimersByTimeAsync(600);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(requestSignal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(400);

    await rejection;
    expect(requestSignal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("settles promptly when the caller aborts during auth acquisition", async () => {
    vi.useFakeTimers();
    const authHeaderProvider = vi.fn(
      () => new Promise<Record<string, string>>(() => undefined),
    );
    const fetcher = vi.fn<PipecatSignalingFetch>();
    const caller = new AbortController();
    const port = createPipecatSignalingPort(signalingUrl, {
      authHeaderProvider,
      fetcher,
    });

    const pending = port.offer(
      { sdp: offerSdp, type: "offer" },
      { signal: caller.signal },
    );
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aborted",
    });
    caller.abort("caller-secret-reason");

    await rejection;
    expect(fetcher).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("releases the serialized mutation lane when an abort-ignoring PATCH times out", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockImplementationOnce(
        async () => new Promise<Response>(() => undefined),
      )
      .mockResolvedValue(new Response(null, { status: 204 }));
    const port = createPipecatSignalingPort(signalingUrl, {
      deadlineMs: 100,
      fetcher,
    });
    const batch = {
      pcId,
      candidates: [
        { candidate: "candidate", sdpMid: "0", sdpMLineIndex: 0 },
      ],
    };

    const first = port.patchCandidates(batch);
    const firstRejection = expect(first).rejects.toMatchObject({
      code: "timed_out",
    });
    await vi.advanceTimersByTimeAsync(100);
    await firstRejection;

    const second = port.patchCandidates(batch);
    await second;

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("requires a fresh normal Firebase bearer for every HTTP method", async () => {
    firebase.getAuthHeaders
      .mockReset()
      .mockResolvedValueOnce({ Authorization: "Bearer method-token-1" })
      .mockResolvedValueOnce({ Authorization: "Bearer method-token-2" })
      .mockResolvedValueOnce({ Authorization: "Bearer method-token-3" });
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockResolvedValueOnce(answerResponse())
      .mockResolvedValue(new Response(null, { status: 204 }));
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });

    await port.offer({ sdp: offerSdp, type: "offer" });
    await port.patchCandidates({
      pcId,
      candidates: [
        { candidate: "candidate", sdpMid: "0", sdpMLineIndex: 0 },
      ],
    });
    await port.deletePeer(pcId);

    expect(firebase.getAuthHeaders).toHaveBeenCalledTimes(3);
    expect(requestInit(fetcher, 0).headers).toMatchObject({
      Authorization: "Bearer method-token-1",
    });
    expect(requestInit(fetcher, 1).headers).toMatchObject({
      Authorization: "Bearer method-token-2",
    });
    expect(requestInit(fetcher, 2).headers).toMatchObject({
      Authorization: "Bearer method-token-3",
    });
  });

  it("fails closed on missing auth and auth-provider failures", async () => {
    const fetcher = vi.fn<PipecatSignalingFetch>();
    const missingAuth = createPipecatSignalingPort(signalingUrl, {
      fetcher,
      authHeaderProvider: async () => ({}),
    });
    const failedAuth = createPipecatSignalingPort(signalingUrl, {
      fetcher,
      authHeaderProvider: async () => {
        throw new Error(`auth failed for ${signalingUrl}`);
      },
    });

    await expect(
      missingAuth.offer({ sdp: offerSdp, type: "offer" }),
    ).rejects.toMatchObject({
      message: "Authentication is required for Pipecat signaling",
      code: "authentication_required",
      status: 401,
    });
    await expect(
      failedAuth.offer({ sdp: offerSdp, type: "offer" }),
    ).rejects.toMatchObject({
      message: "Pipecat signaling authentication failed",
      code: "authentication_failed",
      status: undefined,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("preserves only numeric status for non-2xx POST, PATCH, and DELETE", async () => {
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: `${offerSdp}${pcId}` }), {
          status: 503,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 409 }))
      .mockResolvedValueOnce(new Response(null, { status: 404 }));
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });

    const failures = [
      port.offer({ sdp: offerSdp, type: "offer" }),
      port.patchCandidates({
        pcId,
        candidates: [
          { candidate: "candidate-secret", sdpMid: "0", sdpMLineIndex: 0 },
        ],
      }),
      port.deletePeer(pcId),
    ];
    const expectedStatuses = [503, 409, 404];
    for (const [index, failure] of failures.entries()) {
      await expect(failure).rejects.toMatchObject({
        message: "Pipecat signaling request failed",
        code: "request_failed",
        status: expectedStatuses[index],
      });
    }
  });

  it("rejects unknown or malformed answer fields", async () => {
    const fetcher = vi
      .fn<PipecatSignalingFetch>()
      .mockResolvedValueOnce(answerResponse({ api_secret: "must-not-pass" }))
      .mockResolvedValueOnce(answerResponse({ type: "offer" }))
      .mockResolvedValueOnce(
        new Response("not-json", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    const port = createPipecatSignalingPort(signalingUrl, { fetcher });

    for (let index = 0; index < 3; index += 1) {
      await expect(
        port.offer({ sdp: offerSdp, type: "offer" }),
      ).rejects.toMatchObject({
        message: "Pipecat signaling response is invalid",
        code: "invalid_response",
      });
    }
  });

  it("never logs or reflects signaling secrets from failures", async () => {
    const secrets = [
      signalingUrl,
      "opaque-token-secret",
      offerSdp,
      "candidate-secret",
      "turn-credential-secret",
      pcId,
      "firebase-token-secret",
    ];
    const rawFailure = new Error(secrets.join(" | "));
    const fetcher = vi.fn<PipecatSignalingFetch>().mockRejectedValue(rawFailure);
    const logSpies = [
      vi.spyOn(console, "debug").mockImplementation(() => undefined),
      vi.spyOn(console, "info").mockImplementation(() => undefined),
      vi.spyOn(console, "warn").mockImplementation(() => undefined),
      vi.spyOn(console, "error").mockImplementation(() => undefined),
      vi.spyOn(console, "log").mockImplementation(() => undefined),
    ];
    const port = createPipecatSignalingPort(signalingUrl, {
      fetcher,
      authHeaderProvider: async () => ({
        Authorization: "Bearer firebase-token-secret",
      }),
    });

    const failures = [
      port.offer({ sdp: offerSdp, type: "offer" }),
      port.patchCandidates({
        pcId,
        candidates: [
          { candidate: "candidate-secret", sdpMid: "0", sdpMLineIndex: 0 },
        ],
      }),
      port.deletePeer(pcId),
    ];
    for (const failure of failures) {
      let caught: unknown;
      try {
        await failure;
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(PipecatSignalingApiError);
      const rendered = String(caught);
      for (const secret of secrets) expect(rendered).not.toContain(secret);
    }
    for (const spy of logSpies) expect(spy).not.toHaveBeenCalled();
  });

  it.each([
    "http://voice.example.test/api/voice/pipecat/signal/token",
    "https://user:secret@voice.example.test/api/voice/pipecat/signal/token",
    "https://voice.example.test/api/voice/pipecat/signal/token?secret=1",
  ])("rejects an unsafe bearer destination without reflecting it: %s", (url) => {
    expect(() => createPipecatSignalingPort(url)).toThrowError(
      "Pipecat signaling configuration is invalid",
    );
  });
});
