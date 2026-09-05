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
import {
  decodeSemanticSceneStreamEvent,
  type SemanticSceneStreamRunner,
} from "./model-stream";
import { createSceneFixtureEvents, createSceneFixtureRunner } from "./scene-stream-fixture";
import { createSemanticSceneFixtureRunner } from "./semantic-scene-stream-fixture";
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

async function mountSemantic(
  runStream: SemanticSceneStreamRunner,
  startLabel?: string
): Promise<MountedDemo> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <ModelSceneDemo
        protocol="semantic"
        runStream={runStream}
        suggestions={[]}
        startLabel={startLabel}
      />
    );
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

  it("presents semantic acts with an explicit three-party trust ledger", async () => {
    const demo = await mountSemantic(
      createSemanticSceneFixtureRunner({ eventDelayMs: 0, chunkDelayMs: 0 })
    );

    expect(demo.container.textContent).toContain("Verified");
    expect(demo.container.textContent).toContain("Presented act ledger");
    expect(demo.container.textContent).toContain("0 presented");

    await act(async () => {
      button(demo.container, "Present verified acts").click();
      await flushWork();
    });

    expect(canvas.playMotionPlan).toHaveBeenCalledTimes(8);
    expect(demo.container.textContent).toContain("Verified acts presented");
    expect(demo.container.textContent).toContain("8 presented");
    expect(demo.container.textContent).toContain("atom areas__atom_triangle");
    expect(demo.container.textContent).toContain("stable_id · unique_ids · board_bounds");
    expect(demo.container.textContent).toContain(
      "compiler certificate 47002ee295…4736"
    );
    expect(demo.container.textContent).toContain(
      "Browser post-paint acknowledgement · completed"
    );
    expect(demo.container.textContent).toContain("node areas__triangle");
    expect(demo.container.textContent).toContain(
      "Server compiler certificates and verifier obligations are claims received by the browser."
    );
    expect(demo.container.textContent).toContain(
      "it does not re-run cryptography or geometry."
    );
    expect(demo.container.textContent).toContain(
      "Narration remains explanatory copy and is not fact-checked by this gate."
    );
    expect(demo.container.textContent).toContain("Browser presentation timing");
    expect(demo.container.textContent).toContain("First presented");
    expect(demo.container.textContent).toContain("request settled");
    expect(demo.container.textContent).toContain("server stream timing");
    expect(demo.container.textContent).toContain("first atom");

    await act(async () => demo.root.unmount());
  });

  it("accepts a semantic start label without changing board controls", async () => {
    const demo = await mountSemantic(
      createSemanticSceneFixtureRunner({ eventDelayMs: 0, chunkDelayMs: 0 }),
      "Compose the proof"
    );

    expect(button(demo.container, "Compose the proof").disabled).toBe(false);
    expect(button(demo.container, "Stop drawing").disabled).toBe(true);
    expect(button(demo.container, "Replay presented").disabled).toBe(true);
    expect(button(demo.container, "Wipe board").disabled).toBe(true);

    await act(async () => demo.root.unmount());
  });

  it("shows a declined semantic route as a calm unchanged outcome", async () => {
    const declinedRunner: SemanticSceneStreamRunner = async (invocation) => {
      invocation.onEvent(
        decodeSemanticSceneStreamEvent({
          type: "scene_stream_started",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision: invocation.request.baseScene.revision,
        })
      );
      invocation.onEvent(
        decodeSemanticSceneStreamEvent({
          type: "semantic_scene_stream_declined",
          generation: invocation.request.generation,
          attempt: 1,
          finalRevision: invocation.request.baseScene.revision,
          reasonCode: "unsupported_intent",
          message: "This request does not have a supported visual yet.",
        })
      );
    };
    const demo = await mountSemantic(declinedRunner);

    await act(async () => {
      button(demo.container, "Present verified acts").click();
      await flushWork();
    });

    expect(canvas.playMotionPlan).not.toHaveBeenCalled();
    expect(demo.container.textContent).toContain("No visual change");
    expect(demo.container.textContent).toContain(
      "This request does not have a supported visual yet."
    );
    expect(demo.container.textContent).toContain("0 presented");
    expect(demo.container.textContent).toContain("scene 0");
    expect(demo.container.querySelector('[role="alert"]')).toBeNull();
    expect(button(demo.container, "Present verified acts").disabled).toBe(false);
    expect(button(demo.container, "Stop drawing").disabled).toBe(true);

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
