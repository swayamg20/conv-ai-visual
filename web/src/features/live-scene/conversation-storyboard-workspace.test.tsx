/** @vitest-environment happy-dom */

import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SVGCanvasHandle } from "@/features/canvas/types";

import { ConversationStoryboardWorkspace } from "./conversation-storyboard-workspace";

const state = vi.hoisted(() => ({
  storyboardProps: null as Record<string, unknown> | null,
}));

vi.mock("@/components/svg-canvas", () => ({
  SVGCanvas: () => <div data-testid="svg-canvas">Legacy canvas</div>,
}));

vi.mock("./live-semantic-storyboard", () => ({
  LiveSemanticStoryboard: (props: Record<string, unknown>) => {
    state.storyboardProps = props;
    return <div data-testid="embedded-storyboard">Storyboard</div>;
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
    problemSpec: { v: 1 as const, speedMps: 25 as const, anglesDeg: [30, 45] as const },
    prompt: "Compare both arcs before deriving their ranges.",
  },
};

async function renderWorkspace(
  activeStoryboard: typeof ACTIVE | null,
  onCloseStoryboard = vi.fn(),
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <ConversationStoryboardWorkspace
        activeStoryboard={activeStoryboard}
        canvasRef={createRef<SVGCanvasHandle>()}
        onCloseStoryboard={onCloseStoryboard}
      />,
    );
  });
  return { container, root, onCloseStoryboard };
}

describe("ConversationStoryboardWorkspace", () => {
  afterEach(() => {
    state.storyboardProps = null;
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
