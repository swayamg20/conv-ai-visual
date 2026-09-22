/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  getAuthHeaders: vi.fn(async () => ({ Authorization: "Bearer test" })),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: auth.getAuthHeaders,
}));

import { PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL } from "@/lib/live-scene/semantic-storyboard";
import {
  useChat,
  type UseChatOptions,
} from "./use-chat";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const COMMAND_ID = "7ed2205c-24af-4f6f-8c44-367f5ed832a0";

function validCommand(overrides: Record<string, unknown> = {}) {
  return {
    v: 1,
    commandId: COMMAND_ID,
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
    prompt: "  Compare both trajectories 😀.  ",
    ...overrides,
  };
}

function sseWire(events: readonly unknown[]): Uint8Array {
  const frames = events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
  return new TextEncoder().encode(frames);
}

function fragmentedResponse(events: readonly unknown[]): Response {
  const bytes = sseWire(events);
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

interface MountedHook {
  readonly root: Root;
  readonly read: () => ReturnType<typeof useChat>;
}

const mountedRoots: Root[] = [];

async function mountHook(options: UseChatOptions = {}): Promise<MountedHook> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push(root);
  let current: ReturnType<typeof useChat> | undefined;

  function Harness() {
    current = useChat({ apiUrl: "https://api.example.test", ...options });
    return null;
  }

  await act(async () => {
    root.render(<Harness />);
  });

  return {
    root,
    read: () => {
      if (!current) throw new Error("Hook did not render");
      return current;
    },
  };
}

describe("useChat", () => {
  beforeEach(() => {
    auth.getAuthHeaders.mockClear();
  });

  afterEach(async () => {
    while (mountedRoots.length > 0) {
      const root = mountedRoots.pop();
      if (root) {
        await act(async () => root.unmount());
      }
    }
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("decodes fragmented SSE once while preserving session, canvas, SDL, chunk, and error behavior", async () => {
    const onSessionReady = vi.fn();
    const onCanvasUpdate = vi.fn();
    const onSDLScene = vi.fn();
    const onStoryboardCommand = vi.fn();
    const canvasOperations = [{ op: "clear" }];
    const sdl = { version: "2.0", title: "Projectile motion" };
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        fragmentedResponse([
          { type: "session", session_id: "session-1" },
          { type: "canvas_update", operations: canvasOperations },
          { type: "animation_event", tool: "teach_with_visuals", sdl },
          { type: "storyboard_command", command: validCommand() },
          { type: "chunk", text: "Hel" },
          { type: "chunk", text: "lo" },
          { type: "error", message: "provider warning" },
          { type: "done" },
        ]),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const mounted = await mountHook({
      canvasMode: true,
      agentId: "agent-1",
      onSessionReady,
      onCanvasUpdate,
      onSDLScene,
      onStoryboardCommand,
    });

    await act(async () => {
      await mounted.read().sendMessage("Teach me projectile motion");
    });

    expect(onSessionReady).toHaveBeenCalledOnce();
    expect(onSessionReady).toHaveBeenCalledWith("session-1");
    expect(onCanvasUpdate).toHaveBeenCalledOnce();
    expect(onCanvasUpdate).toHaveBeenCalledWith(canvasOperations);
    expect(onSDLScene).toHaveBeenCalledOnce();
    expect(onSDLScene).toHaveBeenCalledWith(sdl);
    expect(onStoryboardCommand).toHaveBeenCalledOnce();
    expect(onStoryboardCommand).toHaveBeenCalledWith({
      v: 1,
      commandId: COMMAND_ID,
      protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
      problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
      prompt: "Compare both trajectories 😀.",
    });
    expect(mounted.read().messages.map(({ role, content }) => ({ role, content })))
      .toEqual([
        { role: "user", content: "Teach me projectile motion" },
        { role: "assistant", content: "Hello" },
        { role: "assistant", content: "Error: provider warning" },
      ]);
    expect(mounted.read().isLoading).toBe(false);
    expect(fetchSpy).toHaveBeenCalledWith(
      "https://api.example.test/chat",
      expect.objectContaining({
        method: "POST",
        signal: expect.any(AbortSignal),
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer test",
        },
      }),
    );
    const request = fetchSpy.mock.calls[0]?.[1];
    expect(request).toBeDefined();
    expect(JSON.parse(String(request?.body))).toEqual({
      message: "Teach me projectile motion",
      session_id: null,
      canvas_mode: true,
      agent_id: "agent-1",
    });
  });

  it("logs and ignores malformed or unknown visual commands without mutating the board", async () => {
    const onSessionReady = vi.fn();
    const onCanvasUpdate = vi.fn();
    const onSDLScene = vi.fn();
    const onStoryboardCommand = vi.fn();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fragmentedResponse([
          { type: "session", session_id: "" },
          {
            type: "storyboard_command",
            command: validCommand({ commandId: "not-a-uuid" }),
          },
          { type: "animation_event", tool: "unknown_visual_tool", sdl: {} },
          { type: "chunk", text: "Still chatting." },
        ]),
      ),
    );
    const mounted = await mountHook({
      onSessionReady,
      onCanvasUpdate,
      onSDLScene,
      onStoryboardCommand,
    });

    await act(async () => {
      await mounted.read().sendMessage("hello");
    });

    expect(onSessionReady).not.toHaveBeenCalled();
    expect(onStoryboardCommand).not.toHaveBeenCalled();
    expect(onCanvasUpdate).not.toHaveBeenCalled();
    expect(onSDLScene).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(
      "[Chat] Ignoring malformed storyboard command:",
      expect.any(Error),
    );
    expect(mounted.read().messages.map((message) => message.content)).toEqual([
      "hello",
      "Still chatting.",
    ]);
  });

  it("aborts a replaced request and suppresses its stale storyboard command", async () => {
    const onStoryboardCommand = vi.fn();
    let callCount = 0;
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        callCount += 1;
        if (callCount === 1) {
          return await new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          });
        }
        return fragmentedResponse([
          {
            type: "storyboard_command",
            command: validCommand({
              commandId: "74dc6994-03ce-479f-a065-a63f33df6f9d",
              prompt: "Second request",
            }),
          },
        ]);
      },
    );
    vi.stubGlobal("fetch", fetchSpy);
    const mounted = await mountHook({ onStoryboardCommand });
    let firstRequest!: Promise<void>;

    await act(async () => {
      firstRequest = mounted.read().sendMessage("first");
      await Promise.resolve();
    });
    await act(async () => {
      await mounted.read().sendMessage("second");
      await firstRequest;
    });

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(onStoryboardCommand).toHaveBeenCalledOnce();
    expect(onStoryboardCommand.mock.calls[0]?.[0]).toMatchObject({
      commandId: "74dc6994-03ce-479f-a065-a63f33df6f9d",
      prompt: "Second request",
    });
    expect(mounted.read().messages.map((message) => message.content)).toEqual([
      "first",
      "second",
    ]);
    expect(mounted.read().isLoading).toBe(false);
  });

  it("preserves network failures as assistant error messages", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("network down");
    }));
    const mounted = await mountHook();

    await act(async () => {
      await mounted.read().sendMessage("hello");
    });

    expect(mounted.read().messages.map((message) => message.content)).toEqual([
      "hello",
      "Error: network down",
    ]);
    expect(mounted.read().isLoading).toBe(false);
  });
});
