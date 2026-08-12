import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => {
  class VoiceSessionApiError extends Error {
    readonly status?: number;

    constructor(message: string, status?: number) {
      super(message);
      this.status = status;
    }
  }

  return {
    endVoiceSession: vi.fn<
      (
        request: unknown,
        options?: {
          apiUrl?: string;
          signal?: AbortSignal;
          authHeaderProvider?: () => Promise<Record<string, string>>;
        }
      ) => Promise<void>
    >(async () => undefined),
    VoiceSessionApiError,
  };
});

vi.mock("./session-api", () => ({
  endVoiceSession: api.endVoiceSession,
  VoiceSessionApiError: api.VoiceSessionApiError,
}));

import { AssignmentReleaseScheduler } from "./assignment-release";

const assignment = {
  session_id: "a4f4328e-185e-4c65-b3f7-101e04a37578",
  voice_call_id: "25b7aed8-4342-4def-9638-430309391c5c",
};

describe("AssignmentReleaseScheduler", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.endVoiceSession.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("deduplicates and retries a transient failure within a bounded deadline", async () => {
    api.endVoiceSession
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValue(undefined);
    const log = vi.fn();
    const scheduler = new AssignmentReleaseScheduler({
      apiUrl: "https://api.example.test",
      onLog: log,
    });

    scheduler.release(assignment);
    scheduler.release(assignment);
    await Promise.resolve();
    await Promise.resolve();

    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith(
      "Voice assignment release failed; retrying 2/3"
    );
    await vi.advanceTimersByTimeAsync(100);
    expect(api.endVoiceSession).toHaveBeenCalledTimes(2);
    expect(api.endVoiceSession).toHaveBeenLastCalledWith(
      assignment,
      expect.objectContaining({
        apiUrl: "https://api.example.test",
        signal: expect.any(AbortSignal),
      })
    );
    expect(vi.getTimerCount()).toBe(0);
    scheduler.dispose();
  });

  it("does not retry a terminal scope conflict", async () => {
    api.endVoiceSession.mockRejectedValueOnce(
      new api.VoiceSessionApiError("Release scope conflict", 409)
    );
    const scheduler = new AssignmentReleaseScheduler({
      apiUrl: undefined,
    });

    scheduler.release(assignment);
    await Promise.resolve();
    await vi.runAllTimersAsync();

    expect(api.endVoiceSession).toHaveBeenCalledTimes(1);
    scheduler.dispose();
  });

  it("aborts owned work on dispose and retries that same call once without timers", async () => {
    let activeSignal: AbortSignal | undefined;
    api.endVoiceSession.mockImplementationOnce(
      (_request, options) => {
        activeSignal = options?.signal;
        return new Promise<void>(() => undefined);
      }
    );
    const scheduler = new AssignmentReleaseScheduler({
      apiUrl: undefined,
    });

    scheduler.release(assignment);
    scheduler.dispose();
    expect(activeSignal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);

    api.endVoiceSession.mockResolvedValueOnce(undefined);
    scheduler.release(assignment);
    await Promise.resolve();

    expect(api.endVoiceSession).toHaveBeenCalledTimes(2);
    expect(api.endVoiceSession).toHaveBeenLastCalledWith(assignment, {
      apiUrl: undefined,
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("can release a newly confirmed assignment that reuses an old call ID", async () => {
    const scheduler = new AssignmentReleaseScheduler({
      apiUrl: undefined,
    });

    scheduler.release(assignment);
    await Promise.resolve();
    scheduler.markAssigned(assignment.voice_call_id);
    scheduler.release(assignment);
    await Promise.resolve();

    expect(api.endVoiceSession).toHaveBeenCalledTimes(2);
    scheduler.dispose();
  });

  it("forwards the injected auth provider to scheduled and exit releases", async () => {
    const authHeaderProvider = vi.fn(async () => ({
      Authorization: "Bearer injected-test-token",
    }));
    const scheduler = new AssignmentReleaseScheduler({ authHeaderProvider });

    scheduler.release(assignment);
    await Promise.resolve();
    expect(api.endVoiceSession).toHaveBeenLastCalledWith(
      assignment,
      expect.objectContaining({ authHeaderProvider })
    );

    scheduler.dispose();
    const exitAssignment = {
      ...assignment,
      voice_call_id: "e42bb15d-6d10-4e58-90d6-221383fbcdf9",
    };
    scheduler.release(exitAssignment);
    await Promise.resolve();
    expect(api.endVoiceSession).toHaveBeenLastCalledWith(exitAssignment, {
      apiUrl: undefined,
      authHeaderProvider,
    });
  });
});
