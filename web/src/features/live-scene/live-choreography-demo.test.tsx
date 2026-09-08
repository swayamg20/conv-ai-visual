/** @vitest-environment happy-dom */

import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedCheckpointChoreography } from "@/lib/live-scene";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
} from "./choreography-executor";
import { createChoreographySceneFixtureRunner } from "./choreography-scene-stream-fixture";

const canvas = vi.hoisted(() => ({
  cancelMotion: vi.fn(),
  clear: vi.fn(),
  materializeScene: vi.fn(),
  materializeViewport: vi.fn(),
  playCheckpointChoreography: vi.fn(),
  renderProps: vi.fn(),
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
    SVGCanvas: React.forwardRef(function MockCanvas(props, ref) {
      canvas.renderProps(props);
      React.useImperativeHandle(ref, () => ({
        cancelMotion: canvas.cancelMotion,
        clear: canvas.clear,
        materializeScene: canvas.materializeScene,
        materializeViewport: canvas.materializeViewport,
        playCheckpointChoreography: canvas.playCheckpointChoreography,
      }));
      return React.createElement("svg", {
        "data-testid": "choreography-canvas",
      });
    }),
  };
});

import {
  LiveChoreographyDemo,
  type ChoreographyLessonPath,
  type ChoreographyRunnerFactory,
} from "./live-choreography-demo";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

interface MountedDemo {
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

function fixtureFactory(
  invocations: ChoreographyLessonPath[],
): ChoreographyRunnerFactory {
  return (path) => {
    const runner = createChoreographySceneFixtureRunner({
      mode: path === "full" ? "main" : "adaptive",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    return async (invocation) => {
      invocations.push(path);
      await runner(invocation);
    };
  };
}

async function mount(
  props: Partial<ComponentProps<typeof LiveChoreographyDemo>> = {},
): Promise<MountedDemo> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <LiveChoreographyDemo layout="cinematic" reducedMotion {...props} />,
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

async function flushWork(iterations = 80): Promise<void> {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
  for (let index = 0; index < iterations; index += 1) await Promise.resolve();
}

function stage(container: HTMLElement): HTMLElement {
  const result = container.querySelector<HTMLElement>(
    '[data-testid="live-choreography-stage"]',
  );
  if (!result) throw new Error("Missing choreography stage");
  return result;
}

describe("LiveChoreographyDemo", () => {
  beforeEach(() => {
    canvas.cancelMotion.mockReset();
    canvas.clear.mockReset();
    canvas.materializeScene.mockReset();
    canvas.materializeViewport.mockReset();
    canvas.playCheckpointChoreography
      .mockReset()
      .mockImplementation(immediatePlayback);
    canvas.renderProps.mockReset();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("presents the complete fixture as eight settled visual checkpoints", async () => {
    const invocations: ChoreographyLessonPath[] = [];
    const demo = await mount({
      initialPath: "full",
      runnerFactory: fixtureFactory(invocations),
    });

    await act(async () => {
      button(demo.container, "Begin the lesson").click();
      await flushWork();
    });

    expect(stage(demo.container).dataset.phase).toBe("completed");
    expect(stage(demo.container).dataset.checkpointId).toBe("solve_roots");
    expect(stage(demo.container).dataset.settledMainCount).toBe("8");
    expect(demo.container.textContent).toContain("x is one or negative seven");
    expect(canvas.playCheckpointChoreography).toHaveBeenCalledTimes(8);
    expect(invocations).toEqual(["full"]);

    await act(async () => demo.root.unmount());
  });

  it("pauses at the missing corner, clarifies nine, and continues without wiping", async () => {
    const invocations: ChoreographyLessonPath[] = [];
    const demo = await mount({ runnerFactory: fixtureFactory(invocations) });

    await act(async () => {
      button(demo.container, "Begin the lesson").click();
      await flushWork();
    });
    expect(stage(demo.container).dataset.phase).toBe("streaming");
    expect(stage(demo.container).dataset.checkpointId).toBe("missing_corner");
    expect(stage(demo.container).dataset.settledMainCount).toBe("5");
    expect(demo.container.textContent).toContain("Waiting for your question");

    await act(async () => {
      button(demo.container, "Stop here and ask").click();
      await flushWork();
    });
    expect(stage(demo.container).dataset.phase).toBe("interrupted");

    await act(async () => {
      button(demo.container, "Why is the corner 9?").click();
      await flushWork();
    });
    expect(stage(demo.container).dataset.checkpointId).toBe("corner_detail");
    expect(stage(demo.container).dataset.settledMainCount).toBe("5");
    expect(stage(demo.container).dataset.cornerClarified).toBe("true");

    await act(async () => {
      button(demo.container, "Continue the solution").click();
      await flushWork();
    });
    expect(stage(demo.container).dataset.phase).toBe("completed");
    expect(stage(demo.container).dataset.checkpointId).toBe("solve_roots");
    expect(stage(demo.container).dataset.settledMainCount).toBe("8");
    expect(invocations).toEqual([
      "ask_at_corner",
      "ask_at_corner",
      "ask_at_corner",
    ]);

    const requestCountBeforeReplay = invocations.length;
    await act(async () => {
      button(demo.container, "Replay").click();
      await flushWork(160);
    });
    expect(stage(demo.container).dataset.phase).toBe("completed");
    expect(invocations).toHaveLength(requestCountBeforeReplay);

    await act(async () => demo.root.unmount());
  });

  it("keeps the capture variant stage-only and can start it automatically", async () => {
    const invocations: ChoreographyLessonPath[] = [];
    const demo = await mount({
      initialPath: "full",
      pathLocked: true,
      stageOnly: true,
      autoStart: true,
      runnerFactory: fixtureFactory(invocations),
    });

    await act(async () => flushWork());

    expect(stage(demo.container).dataset.phase).toBe("completed");
    expect(stage(demo.container).dataset.layout).toBe("cinematic");
    expect(demo.container.querySelector("button")).toBeNull();
    expect(demo.container.querySelector("textarea")).toBeNull();
    expect(demo.container.textContent).not.toContain("Post-paint commits");
    expect(invocations).toEqual(["full"]);

    await act(async () => demo.root.unmount());
  });

  it("threads the closed accelerated playback rate into the choreography canvas", async () => {
    const demo = await mount({ playbackRate: 16, stageOnly: true });

    expect(canvas.renderProps).toHaveBeenCalled();
    expect(canvas.renderProps.mock.calls.at(-1)?.[0]).toMatchObject({
      choreographyPlaybackRate: 16,
    });

    await act(async () => demo.root.unmount());
  });

  it("hides a renderer state that cannot be reconciled", async () => {
    const warning = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    try {
      const invocations: ChoreographyLessonPath[] = [];
      canvas.playCheckpointChoreography.mockReturnValueOnce(
        {} as unknown as ChoreographyPlayback,
      );
      canvas.cancelMotion.mockImplementation(() => {
        throw new Error("renderer cancellation failed");
      });
      const demo = await mount({
        initialPath: "full",
        runnerFactory: fixtureFactory(invocations),
      });

      await act(async () => {
        button(demo.container, "Begin the lesson").click();
        await flushWork();
      });

      expect(stage(demo.container).dataset.rendererTrusted).toBe("false");
      expect(demo.container.textContent).toContain("Board quarantined");
      expect(demo.container.textContent).toContain(
        "This visual state could not be verified",
      );

      await act(async () => demo.root.unmount());
    } finally {
      warning.mockRestore();
    }
  });
});
