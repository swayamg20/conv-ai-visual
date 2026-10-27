import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_INSTRUCTIONS,
  CosmoCleanupError,
  type CallOptions,
  type ConnectRequest,
  type SessionPort,
} from "./contracts";
import { CosmoLabController } from "./controller";

const options: CallOptions = {
  instructions: DEFAULT_INSTRUCTIONS,
  provider: "default",
  interruption: "default",
  storeRecording: false,
  microphone: true,
};
const session = (): SessionPort => ({
  end: vi.fn(async () => {}),
  setMuted: vi.fn(async () => {}),
  sendText: vi.fn(async () => {}),
  resumeAudioPlayback: vi.fn(async () => {}),
});
const delta = (text: string, id = "d1") => ({
  id,
  turnId: "t1",
  role: "user" as const,
  text,
  isFinal: false,
  append: true,
});

describe("Cosmo call ownership", () => {
  it("holds the call while a cancelled connection settles, then closes it exactly once", async () => {
    const port = session();
    let resolve!: (value: SessionPort) => void;
    const connect = vi.fn(
      () =>
        new Promise<SessionPort>((done) => {
          resolve = done;
        }),
    );
    const controller = new CosmoLabController(connect);
    const starting = controller.start(options);
    await Promise.resolve();
    const stopping = controller.stop();
    await controller.start(options);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot().phase).toBe("ending");
    resolve(port);
    await Promise.all([starting, stopping]);
    expect(port.end).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot().phase).toBe("ended");
  });

  it("ignores old-call events and renders only current-call thoughts", async () => {
    const requests: ConnectRequest[] = [];
    const controller = new CosmoLabController(async (request) => {
      requests.push(request);
      return session();
    });
    await controller.start(options);
    const old = requests[0];
    old.observer.transcript(delta("old"));
    await controller.stop();
    await controller.start(options);
    old.observer.transcript(delta("late", "d2"));
    old.observer.error("stale error");
    expect(controller.getSnapshot().transcript).toEqual([]);
    expect(controller.getSnapshot().error).toBeNull();
    const active = requests[1];
    active.observer.transcript(delta("hello"));
    active.observer.transcript(delta("hello"));
    expect(controller.getSnapshot().transcript[0].text).toBe("hello");
    await controller.stop();
  });

  it("removes the failed attempt and allows an explicit retry without leaking its error", async () => {
    const connect = vi
      .fn()
      .mockRejectedValueOnce(new Error("secret-upstream-detail"))
      .mockResolvedValueOnce(session());
    const controller = new CosmoLabController(connect);
    await controller.start(options);
    expect(controller.getSnapshot().phase).toBe("error");
    expect(controller.getSnapshot().error).not.toContain(
      "secret-upstream-detail",
    );
    await controller.start(options);
    expect(controller.getSnapshot().phase).toBe("ready");
    expect(controller.getSnapshot().error).toBeNull();
    await controller.stop();
  });

  it("cannot send text after stop while cleanup is pending", async () => {
    const port = session();
    const controller = new CosmoLabController(async () => port);
    await controller.start(options);
    await controller.sendText(" first ");
    const stopping = controller.stop();
    await controller.sendText("late");
    await stopping;
    expect(port.sendText).toHaveBeenCalledExactlyOnceWith("first");
  });

  it("retains ownership when a failed startup could not clean up", async () => {
    const connect = vi.fn(async () => { throw new CosmoCleanupError(); });
    const controller = new CosmoLabController(connect);
    await controller.start(options);
    expect(controller.getSnapshot().phase).toBe("ending");
    await controller.start(options);
    await controller.stop();
    expect(connect).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot().phase).toBe("ending");
    expect(controller.getSnapshot().error).toContain("Reload this tab");
  });

  it("leaves the connected state and cleans up after a remote disconnect", async () => {
    let request!: ConnectRequest;
    const port = session();
    const controller = new CosmoLabController(async (incoming) => {
      request = incoming;
      return port;
    });
    await controller.start(options);
    request.observer.lifecycle("disconnected");
    await vi.waitFor(() => expect(controller.getSnapshot().phase).toBe("ended"));
    expect(request.signal.aborted).toBe(true);
    expect(port.end).toHaveBeenCalledTimes(1);
  });
});
