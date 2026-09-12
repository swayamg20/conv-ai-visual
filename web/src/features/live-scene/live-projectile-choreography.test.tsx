/** @vitest-environment happy-dom */

import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ChoreographyCueKindV2,
  PlannedCheckpointChoreography,
} from "@/lib/live-scene";
import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import { decodeProjectileChoreographySceneStreamEventV1 } from "@/lib/live-scene/projectile-choreography-stream";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
} from "./choreography-executor";
import {
  LiveProjectileChoreography,
  runAuthenticatedProjectileChoreographyStream,
} from "./live-projectile-choreography";
import type { ProjectileChoreographyRuntimeSnapshot } from "./projectile-choreography-stream-runtime";
import type {
  ProjectileChoreographySceneStreamRunInvocation,
  ProjectileChoreographySceneStreamRunner,
} from "./projectile-choreography-model-stream";
import {
  DEFAULT_PROJECTILE_PROBLEM,
  createProjectileLifecycleFixture,
} from "./projectile-choreography-test-fixture";

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
        "data-testid": "projectile-canvas",
      });
    }),
  };
});

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

interface MountedProduct {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

function immediatePlayback(
  plan: PlannedCheckpointChoreography,
  observer?: ChoreographyExecutorObserver<ChoreographyCueKindV2>,
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
  props: Partial<ComponentProps<typeof LiveProjectileChoreography>> = {},
): Promise<MountedProduct> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <LiveProjectileChoreography
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

function labelledButton(
  container: HTMLElement,
  label: string,
): HTMLButtonElement {
  const match = container.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
  if (!match) throw new Error(`Missing labelled button: ${label}`);
  return match;
}

function stage(container: HTMLElement): HTMLElement {
  const result = container.querySelector<HTMLElement>(
    '[data-testid="projectile-choreography-stage"]',
  );
  if (!result) throw new Error("Missing projectile choreography stage");
  return result;
}

async function flushWork(iterations = 220): Promise<void> {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
  for (let index = 0; index < iterations; index += 1) await Promise.resolve();
}

function emitFixture(
  invocation: ProjectileChoreographySceneStreamRunInvocation,
  maxAdvanceCheckpoints?: number,
): void {
  for (const event of createProjectileLifecycleFixture(invocation.request, {
    ...(maxAdvanceCheckpoints ? { maxAdvanceCheckpoints } : {}),
  })) {
    invocation.onEvent(event);
  }
}

const EMPTY_REQUEST: ProjectileMotionRequestV1 = {
  protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  routingMode: "reflex",
  problemSpec: DEFAULT_PROJECTILE_PROBLEM,
  generation: 1,
  baseScene: { revision: 0, nodes: [] },
  baseSemanticScene: { revision: 0, components: [] },
  requestedRoute: { intent: "advance", targetStage: "solve" },
};

function sseResponse(generation: number): Response {
  return new Response(
    [
      `data: ${JSON.stringify({
        type: "scene_stream_started",
        generation,
        attempt: 1,
        baseRevision: 0,
      })}`,
      `data: ${JSON.stringify({
        type: "projectile_choreography_scene_stream_declined",
        generation,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "unsupported_intent",
        message: "No supported visual move.",
      })}`,
      "",
    ].join("\n\n"),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("LiveProjectileChoreography", () => {
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

  it("presents the physics studio and continues from an exact partial frontier", async () => {
    const invocations: ProjectileChoreographySceneStreamRunInvocation[] = [];
    const runStream: ProjectileChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocations.push(invocation);
      const firstAdvance =
        invocation.request.routingMode === "reflex" &&
        invocation.request.requestedRoute.intent === "advance" &&
        invocation.request.baseScene.revision === 0;
      emitFixture(invocation, firstAdvance ? 2 : undefined);
    };
    const mounted = await mount({ runStream });

    expect(mounted.container.textContent).toContain(
      "Throw an idea. Watch gravity answer.",
    );
    expect(mounted.container.textContent).toContain("no audio required");
    expect(
      mounted.container.querySelector('a[href="/canvas/generate"]')
        ?.textContent,
    ).toContain("Equation studio");
    expect(
      labelledButton(
        mounted.container,
        "Set launch speed to 20 metres per second",
      ).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      labelledButton(
        mounted.container,
        "Set launch angle to 45 degrees",
      ).getAttribute("aria-pressed"),
    ).toBe("true");

    await act(async () => {
      button(mounted.container, "Draw this launch").click();
      await flushWork();
    });

    expect(invocations[0]?.request).toEqual(EMPTY_REQUEST);
    expect(stage(mounted.container).dataset.settledMainCount).toBe("2");
    expect(stage(mounted.container).dataset.acceptedSpeed).toBe("20");
    expect(stage(mounted.container).dataset.acceptedAngle).toBe("45");
    expect(
      button(mounted.container, "Why does horizontal speed stay constant?"),
    ).toBeTruthy();
    expect(mounted.container.textContent).not.toContain(
      "At the apex, why is acceleration still down?",
    );

    await act(async () => {
      button(
        mounted.container,
        "Why does horizontal speed stay constant?",
      ).click();
      await flushWork();
    });

    expect(invocations[1]?.request).toMatchObject({
      protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
      problemSpec: DEFAULT_PROJECTILE_PROBLEM,
      baseScene: { revision: 2 },
      baseSemanticScene: { revision: 2 },
      requestedRoute: { intent: "clarify", topic: "horizontal_velocity" },
    });
    expect(mounted.container.textContent).not.toContain(
      "Why does horizontal speed stay constant?",
    );
    expect(stage(mounted.container).textContent).toContain(
      "horizontal speed explained",
    );

    await act(async () => {
      button(mounted.container, "Continue the flight").click();
      await flushWork();
    });

    expect(invocations[2]?.request).toMatchObject({
      problemSpec: DEFAULT_PROJECTILE_PROBLEM,
      baseScene: { revision: 3 },
      baseSemanticScene: { revision: 3 },
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });
    expect(stage(mounted.container).dataset.settledMainCount).toBe("6");
    expect(stage(mounted.container).dataset.visibleCheckpointId).toBe(
      "summary",
    );
    await act(async () => mounted.root.unmount());
  });

  it("publishes the final committed runtime snapshot to an E2E observer", async () => {
    const runtimeSnapshots: ProjectileChoreographyRuntimeSnapshot[] = [];
    const mounted = await mount({
      runStream: async (invocation) => emitFixture(invocation),
      onRuntimeSnapshot: (snapshot) => runtimeSnapshots.push(snapshot),
    });

    await act(async () => {
      button(mounted.container, "Draw this launch").click();
      await flushWork();
    });

    const finalRuntimeSnapshot = runtimeSnapshots.at(-1);
    expect(finalRuntimeSnapshot?.phase).toBe("completed");
    expect(finalRuntimeSnapshot?.committedScene.revision).toBe(6);
    expect(finalRuntimeSnapshot?.committedSemanticScene.revision).toBe(6);
    expect(finalRuntimeSnapshot?.accepted).toHaveLength(6);
    await act(async () => mounted.root.unmount());
  });

  it("keeps desired controls separate and retargets the accepted board without a wipe", async () => {
    const invocations: ProjectileChoreographySceneStreamRunInvocation[] = [];
    const runStream: ProjectileChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocations.push(invocation);
      emitFixture(invocation);
    };
    const mounted = await mount({ runStream });

    await act(async () => {
      button(mounted.container, "Draw this launch").click();
      await flushWork();
    });
    await act(async () => {
      labelledButton(
        mounted.container,
        "Set launch speed to 30 metres per second",
      ).click();
      labelledButton(
        mounted.container,
        "Set launch angle to 60 degrees",
      ).click();
    });

    expect(stage(mounted.container).dataset.acceptedSpeed).toBe("20");
    expect(stage(mounted.container).dataset.acceptedAngle).toBe("45");
    expect(mounted.container.textContent).toContain("Next morph");

    await act(async () => {
      button(mounted.container, "Morph to 30 m/s · 60°").click();
      await flushWork();
    });

    expect(invocations[1]?.request).toMatchObject({
      routingMode: "reflex",
      problemSpec: DEFAULT_PROJECTILE_PROBLEM,
      generation: 2,
      baseScene: { revision: 6 },
      baseSemanticScene: { revision: 6 },
      requestedRoute: {
        intent: "retarget",
        targetProblemSpec: { v: 1, speedMps: 30, angleDeg: 60 },
      },
    });
    expect(stage(mounted.container).dataset.acceptedSpeed).toBe("30");
    expect(stage(mounted.container).dataset.acceptedAngle).toBe("60");
    expect(stage(mounted.container).dataset.settledMainCount).toBe("6");
    expect(canvas.clear).not.toHaveBeenCalled();
    await act(async () => mounted.root.unmount());
  });

  it("locks launch controls while drawing, then stops, replays locally, and resets", async () => {
    const invocations: ProjectileChoreographySceneStreamRunInvocation[] = [];
    const runStream: ProjectileChoreographySceneStreamRunner = (invocation) => {
      invocations.push(invocation);
      const fixture = createProjectileLifecycleFixture(invocation.request, {
        maxAdvanceCheckpoints: 1,
      });
      invocation.onEvent(fixture[0]);
      invocation.onEvent(fixture[1]);
      return new Promise<void>((resolve) => {
        invocation.signal.addEventListener("abort", () => resolve(), {
          once: true,
        });
      });
    };
    const mounted = await mount({ runStream });

    await act(async () => {
      button(mounted.container, "Draw this launch").click();
      await flushWork();
    });
    expect(stage(mounted.container).dataset.settledMainCount).toBe("1");
    expect(
      labelledButton(
        mounted.container,
        "Set launch speed to 30 metres per second",
      )
        .closest("fieldset")
        ?.hasAttribute("disabled"),
    ).toBe(true);

    await act(async () => {
      button(mounted.container, "Stop at this moment").click();
      await flushWork();
    });
    expect(stage(mounted.container).dataset.phase).toBe("interrupted");
    expect(
      labelledButton(
        mounted.container,
        "Set launch speed to 30 metres per second",
      )
        .closest("fieldset")
        ?.hasAttribute("disabled"),
    ).toBe(false);

    await act(async () => {
      button(mounted.container, "Replay").click();
      await flushWork();
    });
    expect(invocations).toHaveLength(1);
    expect(stage(mounted.container).dataset.settledMainCount).toBe("1");

    await act(async () => {
      button(mounted.container, "Reset board").click();
      await flushWork();
    });
    expect(stage(mounted.container).dataset.settledMainCount).toBe("0");
    expect(stage(mounted.container).dataset.acceptedSpeed).toBe("none");
    expect(canvas.clear).toHaveBeenCalled();
    expect(button(mounted.container, "Draw this launch")).toBeTruthy();
    await act(async () => mounted.root.unmount());
  });

  it("keeps the last accepted caption when a follow-up declines or fails", async () => {
    let invocationCount = 0;
    const runStream: ProjectileChoreographySceneStreamRunner = async (
      invocation,
    ) => {
      invocationCount += 1;
      if (invocationCount === 1) {
        emitFixture(invocation);
        return;
      }
      invocation.onEvent(
        decodeProjectileChoreographySceneStreamEventV1({
          type: "scene_stream_started",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision: 6,
        }),
      );
      invocation.onEvent(
        invocationCount === 2
          ? decodeProjectileChoreographySceneStreamEventV1({
              type: "projectile_choreography_scene_stream_declined",
              generation: invocation.request.generation,
              attempt: 1,
              finalRevision: 6,
              reasonCode: "unsupported_intent",
              message: "That question does not change this flight.",
            })
          : decodeProjectileChoreographySceneStreamEventV1({
              type: "projectile_choreography_scene_stream_failed",
              generation: invocation.request.generation,
              attempt: 1,
              lastAcceptedRevision: 6,
              code: "provider_timeout",
              message: "The projectile Director timed out.",
              retryable: true,
            }),
      );
    };
    const mounted = await mount({ runStream });
    await act(async () => {
      button(mounted.container, "Draw this launch").click();
      await flushWork();
    });
    const acceptedCaption =
      "The same launch components determine flight time, maximum height, and range.";
    expect(stage(mounted.container).textContent).toContain(acceptedCaption);

    await act(async () => {
      button(
        mounted.container,
        "Why does horizontal speed stay constant?",
      ).click();
      await flushWork();
    });
    expect(stage(mounted.container).dataset.phase).toBe("declined");
    expect(stage(mounted.container).textContent).toContain(acceptedCaption);
    expect(stage(mounted.container).textContent).not.toContain(
      "That question does not change this flight.",
    );
    expect(mounted.container.textContent).toContain(
      "That question does not change this flight.",
    );

    await act(async () => {
      button(
        mounted.container,
        "Why does horizontal speed stay constant?",
      ).click();
      await flushWork();
    });
    expect(stage(mounted.container).dataset.phase).toBe("failed");
    expect(stage(mounted.container).textContent).toContain(acceptedCaption);
    expect(stage(mounted.container).textContent).not.toContain(
      "The projectile Director timed out.",
    );
    expect(mounted.container.textContent).toContain(
      "The projectile Director timed out.",
    );
    await act(async () => mounted.root.unmount());
  });

  it("refreshes Firebase auth for each request and fails before fetch when signed out", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (_input, init) => {
      const generation = JSON.parse(String(init?.body)).generation as number;
      return sseResponse(generation);
    });
    vi.stubGlobal("fetch", fetchImpl);
    product.getAuthHeaders
      .mockResolvedValueOnce({ Authorization: "Bearer first-fresh-token" })
      .mockResolvedValueOnce({ Authorization: "Bearer second-fresh-token" })
      .mockResolvedValueOnce({});

    for (let generation = 1; generation <= 2; generation += 1) {
      await runAuthenticatedProjectileChoreographyStream({
        request: { ...EMPTY_REQUEST, generation },
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      });
    }

    expect(product.getAuthHeaders).toHaveBeenCalledTimes(2);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer first-fresh-token",
    });
    expect(fetchImpl.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer second-fresh-token",
    });

    await expect(
      runAuthenticatedProjectileChoreographyStream({
        request: { ...EMPTY_REQUEST, generation: 3 },
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("Sign in again to launch a live visual lesson.");
    expect(product.getAuthHeaders).toHaveBeenCalledTimes(3);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
