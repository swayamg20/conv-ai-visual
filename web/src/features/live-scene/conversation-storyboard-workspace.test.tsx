/** @vitest-environment happy-dom */

import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SVGCanvasHandle } from "@/features/canvas/types";

import {
  ConversationStoryboardWorkspace,
  type ActiveConversationStoryboard,
} from "./conversation-storyboard-workspace";

const state = vi.hoisted(() => ({
  storyboardProps: null as Record<string, unknown> | null,
  lifecycle: [] as Array<{
    phase: "mount" | "unmount";
    prompt: unknown;
  }>,
  runnerCreations: [] as Array<{
    runner: unknown;
    sessionId: string | undefined;
  }>,
}));

vi.mock("@/components/svg-canvas", () => ({
  SVGCanvas: () => <div data-testid="svg-canvas">Legacy canvas</div>,
}));

vi.mock("./live-semantic-storyboard", async () => {
  const { Component } = await import("react");

  class MockLiveSemanticStoryboard extends Component<Record<string, unknown>> {
    componentDidMount() {
      state.lifecycle.push({
        phase: "mount",
        prompt: this.props.initialPrompt,
      });
    }

    componentWillUnmount() {
      state.lifecycle.push({
        phase: "unmount",
        prompt: this.props.initialPrompt,
      });
    }

    render() {
      state.storyboardProps = this.props;
      return <div data-testid="embedded-storyboard">Storyboard</div>;
    }
  }

  return { LiveSemanticStoryboard: MockLiveSemanticStoryboard };
});

vi.mock("./semantic-storyboard-model-stream", () => ({
  createSemanticStoryboardSceneStreamRunner: (options: {
    sessionId?: string;
  }) => {
    const runner = vi.fn(async () => undefined);
    state.runnerCreations.push({ runner, sessionId: options.sessionId });
    return runner;
  },
}));

vi.mock("lucide-react", () => ({
  X: () => <span aria-hidden="true">x</span>,
}));

vi.mock("@/lib/api", () => ({ API_BASE: "https://api.example.test" }));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: vi.fn(async () => ({ Authorization: "Bearer test" })),
}));

const ACTIVE = {
  sessionId: "a4f4328e-185e-4c65-b3f7-101e04a37578",
  command: {
    v: 1 as const,
    commandId: "0f2a1a6d-676b-4e49-9201-45841041a28d",
    protocol: "projectile_comparison_storyboard_v1" as const,
    problemSpec: {
      v: 1 as const,
      speedMps: 25 as const,
      anglesDeg: [30, 45] as const,
    },
    prompt: "Compare both arcs before deriving their ranges.",
  },
} satisfies ActiveConversationStoryboard;

const REPLACEMENT = {
  sessionId: ACTIVE.sessionId,
  command: {
    v: 1 as const,
    commandId: "47ec4d9b-13e7-48a4-8eb7-bb73efcaa74c",
    protocol: "projectile_comparison_storyboard_v1" as const,
    problemSpec: {
      v: 1 as const,
      speedMps: 30 as const,
      anglesDeg: [45, 60] as const,
    },
    prompt: "Use the second lesson to compare the 45 and 60 degree arcs exactly.",
  },
} satisfies ActiveConversationStoryboard;

async function renderWorkspace(
  activeStoryboard: ActiveConversationStoryboard | null,
  onCloseStoryboard = vi.fn(),
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const canvasRef = createRef<SVGCanvasHandle>();
  const rerender = async (
    nextStoryboard: ActiveConversationStoryboard | null,
  ) => {
    await act(async () => {
      root.render(
        <ConversationStoryboardWorkspace
          activeStoryboard={nextStoryboard}
          canvasRef={canvasRef}
          onCloseStoryboard={onCloseStoryboard}
        />,
      );
    });
  };
  await rerender(activeStoryboard);
  return { container, root, onCloseStoryboard, rerender };
}

describe("ConversationStoryboardWorkspace", () => {
  afterEach(() => {
    state.storyboardProps = null;
    state.lifecycle.length = 0;
    state.runnerCreations.length = 0;
    document.body.replaceChildren();
  });

  it("keeps the legacy canvas mounted behind an auto-starting embedded lesson", async () => {
    const rendered = await renderWorkspace(ACTIVE);

    expect(rendered.container.querySelector("[data-testid='svg-canvas']")).not.toBeNull();
    expect(
      rendered.container.querySelector("[data-testid='legacy-session-canvas']")?.getAttribute("aria-hidden"),
    ).toBe("true");
    expect(rendered.container.querySelector("[data-testid='embedded-storyboard']")).not.toBeNull();
    expect(state.storyboardProps).toMatchObject({
      presentation: "embedded",
      autoStart: true,
      initialProblemSpec: ACTIVE.command.problemSpec,
      initialPrompt: ACTIVE.command.prompt,
    });
    expect(typeof state.storyboardProps?.runStream).toBe("function");

    await act(async () => rendered.root.unmount());
  });

  it("replaces the keyed embedded lifecycle with the second command's exact lesson and session", async () => {
    const rendered = await renderWorkspace(ACTIVE);
    const firstRunner = state.storyboardProps?.runStream;

    expect(state.lifecycle).toEqual([
      { phase: "mount", prompt: ACTIVE.command.prompt },
    ]);
    expect(state.runnerCreations).toHaveLength(1);
    expect(state.runnerCreations[0]?.sessionId).toBe(ACTIVE.sessionId);
    expect(firstRunner).toBe(state.runnerCreations[0]?.runner);

    await rendered.rerender(REPLACEMENT);

    expect(state.lifecycle).toEqual([
      { phase: "mount", prompt: ACTIVE.command.prompt },
      { phase: "unmount", prompt: ACTIVE.command.prompt },
      { phase: "mount", prompt: REPLACEMENT.command.prompt },
    ]);
    expect(state.storyboardProps?.initialPrompt).toBe(
      REPLACEMENT.command.prompt,
    );
    expect(state.storyboardProps?.initialProblemSpec).toBe(
      REPLACEMENT.command.problemSpec,
    );
    expect(state.runnerCreations).toHaveLength(1);
    expect(state.runnerCreations[0]?.sessionId).toBe(REPLACEMENT.sessionId);
    expect(state.storyboardProps?.runStream).toBe(firstRunner);

    await act(async () => rendered.root.unmount());
    expect(state.lifecycle.at(-1)).toEqual({
      phase: "unmount",
      prompt: REPLACEMENT.command.prompt,
    });
  });

  it("closes the lesson without removing the retained canvas", async () => {
    const onCloseStoryboard = vi.fn();
    const rendered = await renderWorkspace(ACTIVE, onCloseStoryboard);
    const close = rendered.container.querySelector<HTMLButtonElement>(
      "button[aria-label='Close visual lesson']",
    );

    expect(close).not.toBeNull();
    await act(async () => close?.click());
    expect(onCloseStoryboard).toHaveBeenCalledOnce();
    expect(rendered.container.querySelector("[data-testid='svg-canvas']")).not.toBeNull();

    await act(async () => rendered.root.unmount());
  });

  it("shows only the legacy canvas without a storyboard command", async () => {
    const rendered = await renderWorkspace(null);

    expect(rendered.container.querySelector("[data-testid='svg-canvas']")).not.toBeNull();
    expect(rendered.container.querySelector("[data-testid='embedded-storyboard']")).toBeNull();
    expect(
      rendered.container.querySelector("[data-testid='legacy-session-canvas']")?.hasAttribute("aria-hidden"),
    ).toBe(false);

    await act(async () => rendered.root.unmount());
  });
});
