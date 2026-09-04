/** @vitest-environment happy-dom */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSceneState,
  createSemanticSceneState,
  type SemanticScenePatchEvent,
} from "@/lib/live-scene";

import type { ModelSceneDemoProps } from "./model-scene-demo";

const demo = vi.hoisted(() => ({
  props: null as ModelSceneDemoProps | null,
}));

vi.mock("./model-scene-demo", async () => {
  const React = await import("react");
  return {
    ModelSceneDemo: (props: ModelSceneDemoProps) => {
      demo.props = props;
      return React.createElement(
        "main",
        null,
        React.createElement("p", { "data-testid": "source-label" }, props.sourceLabel),
        React.createElement("p", { "data-testid": "start-label" }, props.startLabel),
        props.scenarioControl as ReactNode
      );
    },
  };
});

import { LiveSceneLab } from "./live-scene-lab";
import type { SemanticSceneStreamEvent } from "./model-stream";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedLab {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

async function mount(): Promise<MountedLab> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<LiveSceneLab />);
  });
  return { container, root };
}

function latestProps(): ModelSceneDemoProps {
  if (!demo.props) throw new Error("ModelSceneDemo did not render");
  return demo.props;
}

function radio(container: HTMLElement, value: string): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>(
    `input[type="radio"][value="${value}"]`
  );
  if (!input) throw new Error(`Missing radio value: ${value}`);
  return input;
}

async function choose(container: HTMLElement, value: string): Promise<void> {
  await act(async () => {
    radio(container, value).click();
  });
}

function interceptedSemanticResponse(): Response {
  const body = [
    {
      type: "scene_stream_started",
      generation: 1,
      attempt: 1,
      baseRevision: 0,
    },
    {
      type: "scene_stream_failed",
      generation: 1,
      attempt: 1,
      code: "intercepted_test_stop",
      message: "No provider request was made.",
      lastAcceptedRevision: 0,
      retryable: true,
    },
  ]
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("LiveSceneLab", () => {
  beforeEach(() => {
    demo.props = null;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("defaults to the verified fixture and runs all eight acts without network", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("the fixture must stay offline"));
    const lab = await mount();

    expect(radio(lab.container, "semantic").checked).toBe(true);
    expect(radio(lab.container, "fixture").checked).toBe(true);
    expect(lab.container.textContent).toContain("no sign-in, network request, or Azure spend");
    expect(lab.container.textContent).toContain(
      "The model routes only start, continue, or abstain and a target stage"
    );
    expect(lab.container.textContent).toContain(
      "the server owns narration, teaching acts, geometry, and verified atoms"
    );
    expect(lab.container.textContent).not.toContain("The model chooses a teaching act");
    expect(lab.container.textContent).not.toContain("Baseline scenario");

    const props = latestProps();
    expect(props.protocol).toBe("semantic");
    expect(props.sourceLabel).toBe("Verified fixture · $0");
    expect(props.startLabel).toBe("Begin verified lesson");
    if (props.protocol !== "semantic") throw new Error("Expected semantic props");

    const events: SemanticSceneStreamEvent[] = [];
    await props.runStream({
      request: {
        prompt: "This prompt deliberately does not author fixture ink",
        generation: 1,
        baseScene: createSceneState({ revision: 0, nodes: [] }),
        baseSemanticScene: createSemanticSceneState({
          revision: 0,
          components: [],
        }),
      },
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(
      events.filter(
        (event): event is SemanticScenePatchEvent =>
          event.type === "semantic_scene_patch"
      )
    ).toHaveLength(8);
    expect(fetchSpy).not.toHaveBeenCalled();

    await act(async () => lab.root.unmount());
  });

  it("makes paid Azure intent explicit and does not request it on selection", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(interceptedSemanticResponse());
    const lab = await mount();

    await choose(lab.container, "azure");

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(lab.container.textContent).toContain("consumes paid Azure quota");
    const props = latestProps();
    expect(props.protocol).toBe("semantic");
    expect(props.sourceLabel).toBe("Verified acts · Azure paid");
    expect(props.startLabel).toBe("Run paid Azure lesson");
    if (props.protocol !== "semantic") throw new Error("Expected semantic props");

    const request = {
      prompt: "Intercept this paid lesson request",
      generation: 1,
      baseScene: createSceneState({ revision: 0, nodes: [] }),
      baseSemanticScene: createSemanticSceneState({
        revision: 0,
        components: [],
      }),
    };
    await props.runStream({
      request,
      signal: new AbortController().signal,
      onEvent: vi.fn(),
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/live-scenes\/lab\/semantic\/stream$/);
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(init?.headers).not.toHaveProperty("Authorization");
    expect(JSON.parse(String(init?.body))).toEqual(request);

    await act(async () => lab.root.unmount());
  });

  it("keeps the raw-coordinate fixture available as an explicit baseline", async () => {
    const lab = await mount();

    await choose(lab.container, "raw");

    expect(lab.container.textContent).toContain("Baseline scenario");
    expect(radio(lab.container, "normal").checked).toBe(true);
    const props = latestProps();
    expect(props.protocol ?? "raw").toBe("raw");
    expect(props.sourceLabel).toBe("Raw fixture · Normal · $0");
    expect(props.startLabel).toBe("Run raw fixture");

    await act(async () => lab.root.unmount());
  });
});
