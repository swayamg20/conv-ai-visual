/** @vitest-environment happy-dom */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MotionPlan } from "@/lib/live-scene";

const canvas = vi.hoisted(() => ({
  cancelMotion: vi.fn(),
  clear: vi.fn(),
  playMotionPlan: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children?: ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
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
        playMotionPlan: canvas.playMotionPlan,
      }));
      return React.createElement("svg", { "data-testid": "live-scene-canvas" });
    }),
  };
});

import { ModelSceneDemo } from "./model-scene-demo";
import { createSceneFixtureEvents, createSceneFixtureRunner } from "./scene-stream-fixture";
import type { SceneStreamRunner } from "./stream-runtime";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedDemo {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

async function mount(runStream: SceneStreamRunner): Promise<MountedDemo> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<ModelSceneDemo runStream={runStream} suggestions={[]} />);
  });
  return { container, root };
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const match = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label
  );
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

async function flushWork(): Promise<void> {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
  for (let index = 0; index < 30; index += 1) await Promise.resolve();
}

function immediatePlayback(plan: MotionPlan) {
  return {
    finished: Promise.resolve({
      status: "completed" as const,
      appliedStepIds: plan.steps.map((step) => step.id),
    }),
    pause: vi.fn(),
    resume: vi.fn(),
    cancel: vi.fn(() => ({
      status: "cancelled" as const,
      appliedStepIds: [] as string[],
    })),
  };
}

describe("ModelSceneDemo", () => {
  beforeEach(() => {
    canvas.cancelMotion.mockReset();
    canvas.clear.mockReset();
    canvas.playMotionPlan.mockReset().mockImplementation(immediatePlayback);
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("shows progressive accepted patches and completion metrics", async () => {
    const demo = await mount(
      createSceneFixtureRunner({ mode: "normal", eventDelayMs: 0, chunkDelayMs: 0 })
    );

    await act(async () => {
      button(demo.container, "Generate live").click();
      await flushWork();
    });

    expect(canvas.playMotionPlan).toHaveBeenCalledTimes(4);
    expect(demo.container.textContent).toContain("Explanation complete");
    expect(demo.container.textContent).toContain("4 accepted");
    expect(demo.container.textContent).toContain("revision 4");
    expect(demo.container.textContent).toContain("First patch 340 ms · total 1460 ms.");
    expect(demo.container.textContent).toContain("Read the equation as an area statement");

    await act(async () => demo.root.unmount());
  });

  it("surfaces a friendly terminal repair failure with an unchanged board", async () => {
    const demo = await mount(
      createSceneFixtureRunner({ mode: "failure", eventDelayMs: 0, chunkDelayMs: 0 })
    );

    await act(async () => {
      button(demo.container, "Generate live").click();
      await flushWork();
    });

    expect(canvas.playMotionPlan).not.toHaveBeenCalled();
    expect(demo.container.textContent).toContain("Stream stopped");
    expect(demo.container.textContent).toContain(
      "Couldn’t update the board. Your last accepted scene is safe."
    );
    expect(demo.container.textContent).toContain("revision 0");
    expect(button(demo.container, "Try again").disabled).toBe(false);

    await act(async () => demo.root.unmount());
  });

  it("replaces retry with a reset action for a non-retryable failure", async () => {
    const nonRetryableRunner: SceneStreamRunner = async (invocation) => {
      invocation.onEvent({
        type: "scene_stream_started",
        generation: invocation.request.generation,
        attempt: 1,
        baseRevision: invocation.request.baseScene.revision,
      });
      invocation.onEvent({
        type: "scene_stream_failed",
        generation: invocation.request.generation,
        attempt: 1,
        code: "context_too_large",
        message: "This board is too large. The current board remains safe.",
        lastAcceptedRevision: invocation.request.baseScene.revision,
        retryable: false,
      });
    };
    const demo = await mount(nonRetryableRunner);

    await act(async () => {
      button(demo.container, "Generate live").click();
      await flushWork();
    });

    expect(demo.container.textContent).toContain(
      "Reset the board before starting another generation."
    );
    expect(() => button(demo.container, "Try again")).toThrow();

    await act(async () => {
      button(demo.container, "Reset to continue").click();
      await flushWork();
    });
    expect(button(demo.container, "Generate live").disabled).toBe(false);
    expect(demo.container.textContent).toContain("Ready for a visual explanation.");

    await act(async () => demo.root.unmount());
  });

  it("retains visible ink and ignores deliberately late old-generation events", async () => {
    let releaseLateEvents: (() => void) | undefined;
    const lateGate = new Promise<void>((resolve) => {
      releaseLateEvents = resolve;
    });
    const lateRunner: SceneStreamRunner = async (invocation) => {
      const events = createSceneFixtureEvents(invocation.request, "stale");
      invocation.onEvent(events[0]);
      invocation.onEvent(events[1]);
      await lateGate;
      for (const event of events.slice(2)) invocation.onEvent(event);
    };

    const cancelPlayback = vi.fn(() => ({
      status: "cancelled" as const,
      appliedStepIds: ["titleG1"],
    }));
    canvas.playMotionPlan.mockImplementation(() => ({
      finished: new Promise(() => undefined),
      pause: vi.fn(),
      resume: vi.fn(),
      cancel: cancelPlayback,
    }));
    const demo = await mount(lateRunner);

    await act(async () => {
      button(demo.container, "Generate live").click();
      await flushWork();
    });
    expect(canvas.playMotionPlan).toHaveBeenCalledTimes(1);

    await act(async () => {
      button(demo.container, "Interrupt").click();
      releaseLateEvents?.();
      await flushWork();
    });

    expect(cancelPlayback).toHaveBeenCalledTimes(1);
    expect(canvas.playMotionPlan).toHaveBeenCalledTimes(1);
    expect(demo.container.textContent).toContain("Interrupted safely");
    expect(demo.container.textContent).toContain("1 accepted");
    expect(demo.container.textContent).toContain("retained at interruption");
    expect(demo.container.textContent).not.toContain("fixtureG1P4");

    await act(async () => demo.root.unmount());
  });
});
