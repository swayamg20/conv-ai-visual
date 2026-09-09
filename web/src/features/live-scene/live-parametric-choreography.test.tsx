/** @vitest-environment happy-dom */

import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedCheckpointChoreography } from "@/lib/live-scene";
import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "@/lib/live-scene/parametric-choreography";
import { decodeParametricChoreographySceneStreamEventV3 } from "@/lib/live-scene/parametric-choreography-stream";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
} from "./choreography-executor";
import {
  LiveParametricChoreography,
  runAuthenticatedParametricChoreographyStream,
} from "./live-parametric-choreography";
import type {
  ParametricChoreographySceneStreamRunInvocation,
  ParametricChoreographySceneStreamRunner,
} from "./parametric-choreography-model-stream";
import { createParametricLifecycleFixture } from "./parametric-choreography-test-fixture";

const product = vi.hoisted(() => ({
  getAuthHeaders: vi.fn(),
}));

const canvas = vi.hoisted(() => ({
  cancelMotion: vi.fn(),
  clear: vi.fn(),
  materializeScene: vi.fn(),
  materializeViewport: vi.fn(),
  playCheckpointChoreography: vi.fn(),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: product.getAuthHeaders,
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children?: ReactNode;
    href: string;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/murmur-doodles", () => ({
  MurmurLogoMark: () => <span data-testid="logo" />,
}));

vi.mock("@/components/theme-toggle", () => ({
  ThemeToggle: () => <button type="button">Theme</button>,
}));

vi.mock("@/components/svg-canvas", async () => {
  const React = await import("react");
  return {
    SVGCanvas: React.forwardRef(function MockCanvas(_props, ref) {
      React.useImperativeHandle(ref, () => ({
        cancelMotion: canvas.cancelMotion,
        clear: canvas.clear,
        materializeScene: canvas.materializeScene,
        materializeViewport: canvas.materializeViewport,
        playCheckpointChoreography: canvas.playCheckpointChoreography,
      }));
      return React.createElement("svg", {
        "data-testid": "parametric-canvas",
      });
    }),
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedProduct {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

function immediatePlayback(
  plan: PlannedCheckpointChoreography,
  observer?: ChoreographyExecutorObserver,
): ChoreographyPlayback {
  const finished = new Promise<{
    readonly status: "completed";
    readonly firstCuePresented: true;
  }>((resolve) => {
    queueMicrotask(() => {
      for (const cue of plan.choreographyPlan.phase.cues) {
        observer?.({ type: "cueStarted", cue: cue.cue });
      }
      observer?.({ type: "firstCuePresented" });
      observer?.({ type: "checkpointSettled", settlement: "completed" });
      resolve({ status: "completed", firstCuePresented: true });
    });
  });
  return {
    firstCuePresented: Promise.resolve(true),
    finished,
    cancel: vi.fn(),
  };
}

async function mount(
  props: Partial<ComponentProps<typeof LiveParametricChoreography>> = {},
): Promise<MountedProduct> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <LiveParametricChoreography
        layout="cinematic"
        reducedMotion
        {...props}
      />,
    );
  });
  return { container, root };
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const match = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

function stage(container: HTMLElement): HTMLElement {
  const result = container.querySelector<HTMLElement>(
    '[data-testid="live-choreography-stage"]',
  );
  if (!result) throw new Error("Missing live choreography stage");
  return result;
}

async function flushWork(iterations = 160): Promise<void> {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
  for (let index = 0; index < iterations; index += 1) await Promise.resolve();
}

function emptyReflexRequest(generation: number) {
  return {
    protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    routingMode: "reflex" as const,
    problemText: "x² + 8x = 20",
    generation,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
    requestedRoute: { intent: "advance" as const, targetStage: "solve" as const },
  };
}

describe("LiveParametricChoreography", () => {
  beforeEach(() => {
    product.getAuthHeaders.mockReset();
    canvas.cancelMotion.mockReset();
    canvas.clear.mockReset();
    canvas.materializeScene.mockReset();
    canvas.materializeViewport.mockReset();
    canvas.playCheckpointChoreography
      .mockReset()
      .mockImplementation(immediatePlayback);
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it("locks one equation, settles a derived corner, replays locally, and resets", async () => {
    const invocations: ParametricChoreographySceneStreamRunInvocation[] = [];
    const runStream: ParametricChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocations.push(invocation);
      for (const event of createParametricLifecycleFixture(
        5,
        invocation.request.generation,
      )) {
        invocation.onEvent(event);
      }
    };
    const mounted = await mount({ runStream });
    const equation = mounted.container.querySelector<HTMLInputElement>(
      "#parametric-equation",
    );
    expect(equation?.value).toBe("x² + 8x = 20");
    expect(equation?.disabled).toBe(false);
    await act(async () => {
      if (!equation) throw new Error("Missing equation input");
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      valueSetter?.call(equation, "x² + 6x = 7");
      equation.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      button(mounted.container, "Teach this equation").click();
      await flushWork();
    });

    expect(invocations).toHaveLength(1);
    expect(invocations[0]?.request).toMatchObject({
      protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemText: "x² + 6x = 7",
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });
    expect(equation?.disabled).toBe(true);
    expect(stage(mounted.container).dataset.phase).toBe("completed");
    expect(stage(mounted.container).dataset.settledMainCount).toBe("5");
    expect(button(mounted.container, "Why is the corner 9?")).toBeTruthy();

    await act(async () => {
      button(mounted.container, "Replay").click();
      await flushWork();
    });
    expect(invocations).toHaveLength(1);
    expect(stage(mounted.container).dataset.settledMainCount).toBe("5");

    await act(async () => {
      button(mounted.container, "Reset board").click();
      await flushWork();
    });
    expect(equation?.disabled).toBe(false);
    expect(stage(mounted.container).dataset.settledMainCount).toBe("0");
    expect(canvas.clear).toHaveBeenCalled();
    await act(async () => mounted.root.unmount());
  });

  it("keeps equation identity separate from a Director teaching prompt", async () => {
    const invocations: ParametricChoreographySceneStreamRunInvocation[] = [];
    const runStream: ParametricChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocations.push(invocation);
      invocation.onEvent(
        decodeParametricChoreographySceneStreamEventV3({
          type: "scene_stream_started",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision: 0,
        }),
      );
      invocation.onEvent(
        decodeParametricChoreographySceneStreamEventV3({
          type: "parametric_choreography_scene_stream_declined",
          generation: invocation.request.generation,
          attempt: 1,
          finalRevision: 0,
          reasonCode: "unsupported_intent",
          message: "Try asking for a visual teaching move.",
        }),
      );
    };
    const mounted = await mount({ runStream });

    await act(async () => {
      button(mounted.container, "Direct the next visual").click();
      await flushWork();
    });

    expect(invocations).toHaveLength(1);
    expect(invocations[0]?.request).toEqual({
      protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
      routingMode: "director",
      problemText: "x² + 8x = 20",
      prompt: "Show the next idea in the clearest visual way, one chapter at a time.",
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    });
    expect(mounted.container.textContent).toContain("Director · model routed");
    expect(mounted.container.textContent).toContain(
      "Try asking for a visual teaching move.",
    );
    await act(async () => mounted.root.unmount());
  });

  it("keeps the last accepted teaching caption when a continuation is declined", async () => {
    let invocationCount = 0;
    const runStream: ParametricChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocationCount += 1;
      if (invocationCount === 1) {
        for (const event of createParametricLifecycleFixture(
          5,
          invocation.request.generation,
        )) {
          invocation.onEvent(event);
        }
        return;
      }
      invocation.onEvent(
        decodeParametricChoreographySceneStreamEventV3({
          type: "scene_stream_started",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision: 5,
        }),
      );
      invocation.onEvent(
        decodeParametricChoreographySceneStreamEventV3({
          type: "parametric_choreography_scene_stream_declined",
          generation: invocation.request.generation,
          attempt: 1,
          finalRevision: 5,
          reasonCode: "unsupported_intent",
          message: "That question does not change the board.",
        }),
      );
    };
    const mounted = await mount({ runStream });

    await act(async () => {
      button(mounted.container, "Teach this equation").click();
      await flushWork();
    });
    const acceptedCaption =
      "The almost-square is missing one corner whose side lengths are both three.";
    expect(stage(mounted.container).textContent).toContain(acceptedCaption);

    await act(async () => {
      button(mounted.container, "Why is the corner 9?").click();
      await flushWork();
    });

    expect(stage(mounted.container).dataset.phase).toBe("declined");
    expect(stage(mounted.container).textContent).toContain(acceptedCaption);
    expect(stage(mounted.container).textContent).not.toContain(
      "That question does not change the board.",
    );
    expect(mounted.container.textContent).toContain(
      "That question does not change the board.",
    );
    await act(async () => mounted.root.unmount());
  });

  it("refreshes product auth and fails before fetch when the user is signed out", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          [
            'data: {"type":"scene_stream_started","generation":1,"attempt":1,"baseRevision":0}',
            'data: {"type":"parametric_choreography_scene_stream_declined","generation":1,"attempt":1,"finalRevision":0,"reasonCode":"unsupported_intent","message":"No visual move."}',
            "",
          ].join("\n\n"),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetchImpl);
    product.getAuthHeaders.mockResolvedValue({
      Authorization: "Bearer verified-user-token",
    });
    const onEvent = vi.fn();

    await runAuthenticatedParametricChoreographyStream({
      request: emptyReflexRequest(1),
      signal: new AbortController().signal,
      onEvent,
    });

    expect(product.getAuthHeaders).toHaveBeenCalledOnce();
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://localhost:8000/api/live-scenes/choreography/stream",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer verified-user-token",
        }),
      }),
    );
    expect(onEvent).toHaveBeenCalledTimes(2);

    product.getAuthHeaders.mockResolvedValue({});
    await expect(
      runAuthenticatedParametricChoreographyStream({
        request: emptyReflexRequest(2),
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("Sign in again to start a live visual explanation.");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});
