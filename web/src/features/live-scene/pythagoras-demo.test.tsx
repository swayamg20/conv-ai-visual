/** @vitest-environment happy-dom */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MotionPlan } from "@/lib/live-scene";

const testState = vi.hoisted(() => ({
  cancelPlayback: vi.fn(),
  cancelMotion: vi.fn(),
  clear: vi.fn(),
  emphasizeElement: vi.fn(),
  playMotionPlan: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children?: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/murmur-doodles", () => ({
  MurmurLogoMark: () => <span data-testid="logo" />,
}));

vi.mock("@/components/theme-toggle", () => ({
  ThemeToggle: () => <button>Theme</button>,
}));

vi.mock("@/components/svg-canvas", async () => {
  const React = await import("react");
  return {
    SVGCanvas: React.forwardRef(function MockCanvas(_props, ref) {
      React.useImperativeHandle(ref, () => ({
        cancelMotion: testState.cancelMotion,
        clear: testState.clear,
        emphasizeElement: testState.emphasizeElement,
        playMotionPlan: testState.playMotionPlan,
      }));
      return React.createElement("svg", { "data-testid": "scene-canvas" });
    }),
  };
});

import { PythagorasDemo } from "./pythagoras-demo";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedDemo {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

async function mountDemo(): Promise<MountedDemo> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<PythagorasDemo />));
  return { container, root };
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const match = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label
  );
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

function immediatePlayback(plan: MotionPlan) {
  return {
    finished: Promise.resolve({
      status: "completed" as const,
      appliedStepIds: plan.steps.map((step) => step.id),
    }),
    pause: vi.fn(),
    resume: vi.fn(),
    cancel: testState.cancelPlayback,
  };
}

function pendingPlayback() {
  return {
    finished: new Promise<void>(() => undefined),
    pause: vi.fn(),
    resume: vi.fn(),
    cancel: testState.cancelPlayback,
  };
}

describe("PythagorasDemo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    testState.cancelPlayback.mockReset().mockReturnValue({
      status: "cancelled",
      appliedStepIds: [],
    });
    testState.cancelMotion.mockReset();
    testState.clear.mockReset();
    testState.emphasizeElement.mockReset();
    testState.playMotionPlan.mockReset().mockImplementation(pendingPlayback);
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.useRealTimers();
  });

  it("auto-interrupts before the queued theorem and never admits it later", async () => {
    testState.playMotionPlan
      .mockImplementationOnce(pendingPlayback)
      .mockImplementation(immediatePlayback);
    const mounted = await mountDemo();

    act(() => button(mounted.container, "Auto-interrupt").click());
    expect(testState.playMotionPlan).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(700);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.cancelPlayback).toHaveBeenCalledTimes(1);
    expect(testState.cancelMotion).toHaveBeenCalled();
    expect(testState.emphasizeElement).toHaveBeenCalledWith(
      "triangle-right-angle",
      "hsl(var(--amber))"
    );
    expect(testState.playMotionPlan).toHaveBeenCalledTimes(2);

    act(() => vi.advanceTimersByTime(10_000));

    const admittedIds = testState.playMotionPlan.mock.calls.flatMap(
      ([plan]) => (plan as MotionPlan).steps.map((step) => step.id)
    );
    expect(admittedIds).not.toContain("equation-pythagoras");
    expect(mounted.container.textContent).toContain("Focused on the right angle");
    expect(mounted.container.textContent).toContain("g2 · r2");

    await act(async () => mounted.root.unmount());
  });

  it("replays accepted semantic revisions without changing the ledger", async () => {
    testState.playMotionPlan.mockImplementation(immediatePlayback);
    const mounted = await mountDemo();

    act(() => button(mounted.container, "Play lesson").click());
    await act(async () => {
      vi.advanceTimersByTime(2_050);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.playMotionPlan).toHaveBeenCalledTimes(2);
    expect(mounted.container.textContent).toContain("2 commits");

    act(() => button(mounted.container, "Replay accepted").click());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.clear).toHaveBeenCalledTimes(1);
    expect(testState.playMotionPlan).toHaveBeenCalledTimes(4);
    expect(mounted.container.textContent).toContain("2 commits");
    expect(mounted.container.textContent).toContain("revision 2");

    await act(async () => mounted.root.unmount());
  });

  it("replays the materialized cancellation snapshot during active drawing", async () => {
    testState.cancelPlayback.mockReturnValue({
      status: "cancelled",
      appliedStepIds: ["lesson-title"],
    });
    testState.playMotionPlan
      .mockImplementationOnce(pendingPlayback)
      .mockImplementation(immediatePlayback);
    const mounted = await mountDemo();

    act(() => button(mounted.container, "Play lesson").click());
    expect(testState.playMotionPlan).toHaveBeenCalledTimes(1);

    act(() => button(mounted.container, "Replay accepted").click());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.cancelPlayback).toHaveBeenCalledTimes(1);
    expect(testState.clear).toHaveBeenCalledTimes(1);
    expect(testState.playMotionPlan).toHaveBeenCalledTimes(2);
    const replayPlan = testState.playMotionPlan.mock.calls[1][0] as MotionPlan;
    expect(replayPlan.steps.map((step) => step.id)).toEqual(["lesson-title"]);
    expect(mounted.container.textContent).toContain("1 objects");

    await act(async () => mounted.root.unmount());
  });

  it("replays the materialized board after an immediate interruption", async () => {
    testState.cancelPlayback.mockReturnValue({
      status: "cancelled",
      appliedStepIds: ["lesson-title"],
    });
    testState.playMotionPlan
      .mockImplementationOnce(pendingPlayback)
      .mockImplementation(immediatePlayback);
    const mounted = await mountDemo();

    act(() => button(mounted.container, "Play lesson").click());
    await act(async () => {
      button(mounted.container, "Ask why 90°").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.playMotionPlan).toHaveBeenCalledTimes(2);
    const interruptionPlan = testState.playMotionPlan.mock.calls[1][0] as MotionPlan;
    expect(interruptionPlan.steps.map((step) => step.id)).not.toContain(
      "equation-pythagoras"
    );
    expect(mounted.container.textContent).toContain("2 commits");

    act(() => button(mounted.container, "Replay accepted").click());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(testState.playMotionPlan).toHaveBeenCalledTimes(4);
    const replayFoundation = testState.playMotionPlan.mock.calls[2][0] as MotionPlan;
    const replayInterruption = testState.playMotionPlan.mock.calls[3][0] as MotionPlan;
    expect(replayFoundation.steps.map((step) => step.id)).toEqual(["lesson-title"]);
    expect(replayInterruption.steps.map((step) => [step.type, step.id])).toEqual(
      interruptionPlan.steps.map((step) => [step.type, step.id])
    );
    expect(mounted.container.textContent).toContain("revision 2");

    await act(async () => mounted.root.unmount());
  });
});
