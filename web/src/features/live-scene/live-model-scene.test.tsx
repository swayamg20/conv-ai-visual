/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSceneState, createSemanticSceneState } from "@/lib/live-scene";

import type { ModelSceneDemoProps } from "./model-scene-demo";

const product = vi.hoisted(() => ({
  demoProps: null as ModelSceneDemoProps | null,
  getAuthHeaders: vi.fn(),
  runSemanticSceneModelStream: vi.fn(),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: product.getAuthHeaders,
}));

vi.mock("./model-stream", () => ({
  runSemanticSceneModelStream: product.runSemanticSceneModelStream,
}));

vi.mock("./model-scene-demo", () => ({
  ModelSceneDemo: (props: ModelSceneDemoProps) => {
    product.demoProps = props;
    return <main data-testid="verified-product-scene" />;
  },
}));

import { LiveModelScene } from "./live-model-scene";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedProduct {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

async function mount(): Promise<MountedProduct> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<LiveModelScene />);
  });
  return { container, root };
}

function semanticProps(): Extract<ModelSceneDemoProps, { protocol: "semantic" }> {
  if (!product.demoProps || product.demoProps.protocol !== "semantic") {
    throw new Error("LiveModelScene did not render the semantic product protocol");
  }
  return product.demoProps;
}

describe("LiveModelScene", () => {
  beforeEach(() => {
    product.demoProps = null;
    product.getAuthHeaders.mockReset();
    product.runSemanticSceneModelStream.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("uses the authenticated semantic product boundary with paired snapshots", async () => {
    product.getAuthHeaders.mockResolvedValue({
      Authorization: "Bearer verified-user-token",
    });
    const mounted = await mount();
    const props = semanticProps();
    const invocation = {
      request: {
        prompt: "Teach the Pythagorean area identity",
        generation: 1,
        baseScene: createSceneState({ revision: 0, nodes: [] }),
        baseSemanticScene: createSemanticSceneState({
          revision: 0,
          components: [],
        }),
      },
      signal: new AbortController().signal,
      onEvent: vi.fn(),
    };

    expect(props.sourceLabel).toBe("Verified live model");
    expect(props.startLabel).toBe("Begin verified lesson");
    expect(props.defaultPrompt).toBe(
      "Show the Pythagorean area identity one verified step at a time."
    );
    expect(props.suggestions).toEqual([
      "Build a right triangle and reveal its side relationship",
      "Continue the Pythagorean area identity one step at a time",
      "Show the complete relationship between a², b², and c²",
    ]);

    await props.runStream(invocation);

    expect(product.getAuthHeaders).toHaveBeenCalledOnce();
    expect(product.runSemanticSceneModelStream).toHaveBeenCalledWith({
      apiUrl: "http://localhost:8000",
      endpoint: "product",
      headers: { Authorization: "Bearer verified-user-token" },
      ...invocation,
    });

    await act(async () => mounted.root.unmount());
  });

  it("fails before transport when no authenticated user is available", async () => {
    product.getAuthHeaders.mockResolvedValue({});
    const mounted = await mount();
    const props = semanticProps();

    await expect(
      props.runStream({
        request: {
          prompt: "Teach the Pythagorean area identity",
          generation: 1,
          baseScene: createSceneState({ revision: 0, nodes: [] }),
          baseSemanticScene: createSemanticSceneState({
            revision: 0,
            components: [],
          }),
        },
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      })
    ).rejects.toThrow("Sign in again to start a live visual explanation.");
    expect(product.runSemanticSceneModelStream).not.toHaveBeenCalled();

    await act(async () => mounted.root.unmount());
  });
});
