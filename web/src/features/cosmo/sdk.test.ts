import type { RealtimeSession, SessionStartOptions } from "cosmo-ai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CosmoCleanupError, DEFAULT_INSTRUCTIONS, type ConnectRequest } from "./contracts";
import { connectCosmo } from "./sdk";

const fixtures = vi.hoisted(() => ({ start: vi.fn(), mint: vi.fn() }));
vi.mock("./api", () => ({ mintCosmoToken: fixtures.mint }));
vi.mock("cosmo-ai", () => ({
  RealtimeClient: class { agent() { return { start: fixtures.start }; } },
  SessionConfigError: class extends Error {},
  SessionBusyError: class extends Error {},
  SessionEntitlementError: class extends Error {},
  SessionStartTransportError: class extends Error {},
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function setup() {
  const abort = new AbortController();
  const listeners = new Map<string, (event: unknown) => void>();
  const port = {
    [Symbol.asyncIterator]: () => ({ next: async () => ({ done: true, value: undefined }) }),
    on: vi.fn((name: string, handler: (event: unknown) => void) => {
      listeners.set(name, handler);
      return () => { listeners.delete(name); };
    }),
    end: vi.fn(async () => {}),
    waitUntilReady: vi.fn(async () => {}),
    getSnapshot: () => ({ agentState: "listening", mediaState: { mic: "granted", output: "playing" } }),
    setMuted: vi.fn(async () => {}),
    sendText: vi.fn(async () => {}),
    resumeAudioPlayback: vi.fn(async () => {}),
  };
  const session = port as unknown as RealtimeSession;
  const request: ConnectRequest = {
    signal: abort.signal,
    options: { instructions: DEFAULT_INSTRUCTIONS, provider: "default", interruption: "default", storeRecording: false, microphone: true },
    tools: [],
    observer: { transcript: vi.fn(), agent: vi.fn(), media: vi.fn(), lifecycle: vi.fn(), ready: vi.fn(), event: vi.fn(), ended: vi.fn(), error: vi.fn() },
  };
  return { abort, listeners, port, session, request };
}

beforeEach(() => { vi.resetAllMocks(); fixtures.mint.mockResolvedValue("fixture-token"); });
afterEach(() => { vi.useRealTimers(); });

describe("Cosmo SDK boundary", () => {
  it("captures raw speech boundaries during startup without confusing them with transcripts", async () => {
    const { session, request, port } = setup();
    Object.assign(port, { [Symbol.asyncIterator]: async function* () {
      yield { type: "user-started-speaking" };
      yield { type: "user-stopped-speaking" };
      yield { type: "turn-complete", role: "USER" };
    } });
    fixtures.start.mockImplementation(async (options: SessionStartOptions) => {
      options.onSession!(session);
      return session;
    });
    const connected = await connectCosmo(request);
    await vi.waitFor(() => expect(request.observer.event).toHaveBeenCalledWith("User turn completed"));
    expect(request.observer.event).toHaveBeenCalledWith("Speech detected");
    expect(request.observer.event).toHaveBeenCalledWith("Speech stop detected");
    expect(request.observer.transcript).not.toHaveBeenCalled();
    await connected.end();
  });

  it("captures transcript events emitted before start resolves", async () => {
    const { session, listeners, request, port } = setup();
    const event = { id: "early", turnId: "greeting", role: "assistant", text: "Hello", append: true, isFinal: true };
    fixtures.start.mockImplementation(async (options: SessionStartOptions) => {
      options.onSession!(session);
      listeners.get("transcript")!(event);
      return session;
    });
    const connected = await connectCosmo(request);
    expect(request.observer.transcript).toHaveBeenCalledWith(event);
    await connected.end();
    await connected.end();
    expect(port.end).toHaveBeenCalledTimes(1);
    expect(listeners.size).toBe(0);
  });

  it("defers the first SDK end during startup and closes the eventual transport once", async () => {
    const { abort, session, listeners, request, port } = setup();
    const pending = deferred<RealtimeSession>();
    fixtures.start.mockImplementation((options: SessionStartOptions) => {
      options.onSession!(session);
      return pending.promise;
    });
    const connecting = connectCosmo(request);
    const rejected = expect(connecting).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(fixtures.start).toHaveBeenCalled());
    abort.abort();
    await Promise.resolve();
    expect(port.end).not.toHaveBeenCalled();
    expect(listeners.size).toBe(0);
    pending.resolve(session);
    await rejected;
    expect(port.end).toHaveBeenCalledTimes(1);
    expect(port.waitUntilReady).not.toHaveBeenCalled();
  });

  it("cleans up a failed start and redacts provider error details", async () => {
    const { session, request, port } = setup();
    const { SessionConfigError } = await import("cosmo-ai");
    fixtures.start.mockImplementation(async (options: SessionStartOptions) => {
      options.onSession!(session);
      throw new SessionConfigError(422, "sensitive upstream detail", null);
    });
    await expect(connectCosmo(request)).rejects.toThrow("Cosmo rejected the conversation settings");
    expect(port.end).toHaveBeenCalledTimes(1);
  });

  it("propagates uncertain teardown so the controller cannot open another call", async () => {
    const { session, request, port } = setup();
    port.end.mockRejectedValue(new Error("private error"));
    fixtures.start.mockImplementation(async (options: SessionStartOptions) => {
      options.onSession!(session);
      throw new Error("start failed");
    });
    await expect(connectCosmo(request)).rejects.toBeInstanceOf(CosmoCleanupError);
    expect(port.end).toHaveBeenCalledTimes(1);
  });

  it("ends a connected room that never becomes ready", async () => {
    vi.useFakeTimers();
    const { session, request, port } = setup();
    port.waitUntilReady.mockImplementation(() => new Promise(() => {}));
    fixtures.start.mockImplementation(async (options: SessionStartOptions) => {
      options.onSession!(session);
      return session;
    });
    const connecting = connectCosmo(request);
    const rejected = expect(connecting).rejects.toThrow("did not become ready");
    await vi.waitFor(() => expect(port.waitUntilReady).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(45000);
    await rejected;
    expect(port.end).toHaveBeenCalledTimes(1);
  });
});
