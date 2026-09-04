/** @vitest-environment happy-dom */

import { gsap } from "gsap";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SVGPrimitiveRenderer } from "@/features/canvas/primitives";
import type { CanvasOperation, SVGElementData } from "@/features/canvas/types";
import {
  createSceneState,
  planSceneTransition,
  type PathSceneNode,
  type SceneNode,
  type TextSceneNode,
} from "@/lib/live-scene";

import { createSvgMotionExecutor } from "./svg-motion-executor";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function textNode(
  id: string,
  text: string,
  x: number,
  enter: TextSceneNode["presentation"]["enter"] = "none",
  opacity = 1
): TextSceneNode {
  return {
    id,
    kind: "text",
    x,
    y: 40,
    text,
    presentation: { enter, exit: "fade" },
    style: {
      color: "#ffffff",
      fontSize: 18,
      opacity,
      anchor: "start",
    },
  };
}

function pathNode(id: string): PathSceneNode {
  return {
    id,
    kind: "path",
    points: [
      [20, 120],
      [60, 70],
      [100, 120],
    ],
    closed: true,
    presentation: { enter: "draw", exit: "fade" },
    style: {
      stroke: "#ffffff",
      strokeWidth: 3,
      fill: "#f59e0b",
      opacity: 0.8,
      roughness: 0,
    },
  };
}

function renderText(operation: CanvasOperation): SVGGElement {
  const group = document.createElementNS(SVG_NAMESPACE, "g");
  group.setAttribute("id", operation.id as string);
  group.setAttribute("data-element-id", operation.id as string);
  const text = document.createElementNS(SVG_NAMESPACE, "text");
  text.setAttribute("x", String(operation.x ?? 0));
  text.setAttribute("y", String(operation.y ?? 0));
  text.textContent = operation.text ?? "";
  group.appendChild(text);
  return group;
}

function createRenderer(
  created: Map<string, SVGElement>,
  failOnId?: string,
  calls: string[] = []
): SVGPrimitiveRenderer {
  return {
    draw(operation) {
      const id = operation.id as string;
      calls.push(id);
      if (id === failOnId) throw new Error("renderer exploded");
      if (operation.action !== "text") return null;
      const element = renderText(operation);
      created.set(id, element);
      return element;
    },
    drawLatex(operation) {
      const group = document.createElementNS(SVG_NAMESPACE, "g");
      group.setAttribute("id", operation.id);
      group.setAttribute("data-element-id", operation.id);
      created.set(operation.id, group);
      return group;
    },
    drawFunctionPlot: () => null,
  };
}

function seedText(
  svg: SVGSVGElement,
  elements: Map<string, SVGElementData>,
  node: TextSceneNode
): SVGElement {
  const element = renderText({
    action: "text",
    id: node.id,
    text: node.text,
    x: node.x,
    y: node.y,
  });
  svg.appendChild(element);
  elements.set(node.id, {
    element,
    id: node.id,
    type: node.kind,
    x: node.x,
    y: node.y,
    data: node,
  });
  return element;
}

function deferred(): { readonly promise: Promise<void>; resolve(): void } {
  let resolvePromise!: () => void;
  const promise = new Promise<void>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

function createHarness(options: {
  readonly barrier?: () => Promise<void>;
  readonly failOnId?: string;
  readonly calls?: string[];
} = {}) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  document.body.appendChild(svg);
  const elements = new Map<string, SVGElementData>();
  const created = new Map<string, SVGElement>();
  const renderer = createRenderer(
    created,
    options.failOnId,
    options.calls
  );
  const context = {
    elements,
    getSvg: () => svg,
    getRenderer: () => renderer,
    getHighlightColor: () => "#ef4444",
    invalidate: () => undefined,
  };
  const executor = options.barrier
    ? createSvgMotionExecutor(context, {
        presentationBarrier: options.barrier,
      })
    : createSvgMotionExecutor(context);
  return { created, elements, executor, svg };
}

function expectCanonicalNodeData(
  elements: Map<string, SVGElementData>,
  nodes: readonly SceneNode[]
): void {
  expect([...elements.keys()]).toEqual(nodes.map((node) => node.id));
  nodes.forEach((node) => {
    expect(elements.get(node.id)?.data).toEqual(node);
  });
}

afterEach(() => {
  gsap.globalTimeline.clear();
  document.body.replaceChildren();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("createSvgMotionExecutor", () => {
  it("commits every started motion to canonical DOM and acknowledges only after the barrier", async () => {
    const paint = deferred();
    const presentationBarrier = vi.fn(() => paint.promise);
    const { elements, executor, svg } = createHarness({
      barrier: presentationBarrier,
    });
    const previous = createSceneState({
      revision: 1,
      nodes: [
        textNode("remove-me", "Remove", 20),
        textNode("move-me", "Move", 20),
        textNode("replace-me", "Old", 20),
      ],
    });
    const next = createSceneState({
      revision: 2,
      nodes: [
        textNode("move-me", "Move", 80),
        textNode("replace-me", "New", 20),
        textNode("fade-in", "Fade", 20, "fade", 0.65),
        textNode("scale-in", "Scale", 20, "scale", 0.7),
        pathNode("draw-in"),
      ],
    });
    previous.nodes.forEach((node) => {
      if (node.kind !== "text") throw new Error("Expected a text seed");
      seedText(svg, elements, node);
    });
    const movedElement = elements.get("move-me")?.element;
    movedElement?.setAttribute("clip-path", "url(#stale-reveal)");
    movedElement?.style.setProperty("clip-path", "url(#stale-reveal)");
    movedElement
      ?.querySelector("text")
      ?.setAttribute("clip-path", "url(#stale-child-reveal)");

    const plan = planSceneTransition(previous, next);
    const playback = executor.play(plan, { staggerMs: 0 });
    const firstCancel = playback.cancel();
    const secondCancel = playback.cancel();

    expect(secondCancel).toBe(firstCancel);
    expect(firstCancel).toMatchObject({
      status: "cancelled",
      appliedStepIds: plan.steps.map((step) => step.id),
    });

    let receipt: unknown;
    void playback.finished.then((outcome) => {
      receipt = outcome;
    });
    await Promise.resolve();
    expect(presentationBarrier).toHaveBeenCalledOnce();
    expect(receipt).toBeUndefined();

    expect(svg.querySelector("[data-element-id='remove-me']")).toBeNull();
    expect(elements.has("remove-me")).toBe(false);

    const moved = svg.querySelector<SVGElement>("[data-element-id='move-me']");
    expect(moved).toBe(movedElement);
    expect(moved?.querySelector("text")?.getAttribute("x")).toBe("80");
    expect(moved?.style.opacity).toBe("1");
    expect(moved?.style.transform).toBe("");
    expect(moved?.style.transformOrigin).toBe("");
    expect(moved?.style.clipPath).toBe("");
    expect(moved?.getAttribute("clip-path")).toBeNull();
    expect(moved?.querySelector("[clip-path]")).toBeNull();
    expect(gsap.getProperty(moved as SVGElement, "scale")).toBe(1);

    const replaced = svg.querySelectorAll("[data-element-id='replace-me']");
    expect(replaced).toHaveLength(1);
    expect(replaced[0].querySelector("text")?.textContent).toBe("New");
    expect(svg.querySelector("[id$='--incoming'], [id$='--outgoing']")).toBeNull();
    expect(
      svg.querySelector(
        "[data-element-id$='--incoming'], [data-element-id$='--outgoing']"
      )
    ).toBeNull();

    const fade = svg.querySelector<SVGElement>("[data-element-id='fade-in']");
    expect(fade?.style.opacity).toBe("0.65");
    expect(fade?.style.transform).toBe("");
    const scale = svg.querySelector<SVGElement>("[data-element-id='scale-in']");
    expect(scale?.style.opacity).toBe("0.7");
    expect(scale?.style.transform).toBe("");
    expect(scale?.style.transformOrigin).toBe("");
    expect(gsap.getProperty(scale as SVGElement, "scale")).toBe(1);

    const drawGroup = svg.querySelector<SVGElement>("[data-element-id='draw-in']");
    const drawnPath = drawGroup?.querySelector<SVGPathElement>("path");
    expect(drawGroup?.style.opacity).toBe("0.8");
    expect(drawnPath?.getAttribute("fill")).toBe("#f59e0b");
    expect(drawnPath?.style.fill).toBe("");
    expect(drawnPath?.getAttribute("stroke-dasharray")).toBeNull();
    expect(drawnPath?.getAttribute("stroke-dashoffset")).toBeNull();
    expect(drawnPath?.style.strokeDasharray).toBe("");
    expect(drawnPath?.style.strokeDashoffset).toBe("");
    expect(svg.querySelector("[clip-path]")).toBeNull();
    expectCanonicalNodeData(elements, next.nodes);

    paint.resolve();
    await expect(playback.finished).resolves.toBe(firstCancel);
    expect(presentationBarrier).toHaveBeenCalledOnce();
  });

  it("rolls back in-flight DOM and withholds its ID when the renderer fails", async () => {
    const paint = deferred();
    const presentationBarrier = vi.fn(() => paint.promise);
    const calls: string[] = [];
    const { created, elements, executor, svg } = createHarness({
      barrier: presentationBarrier,
      failOnId: "boom",
      calls,
    });
    const previous = createSceneState({ revision: 0, nodes: [] });
    const next = createSceneState({
      revision: 1,
      nodes: [
        textNode("started", "Started", 20, "fade"),
        textNode("boom", "Fail", 20, "fade"),
      ],
    });

    const playback = executor.play(planSceneTransition(previous, next), {
      staggerMs: 0,
    });
    const failed = playback.cancel();

    expect(calls).toEqual(["started", "boom"]);
    expect(failed).toMatchObject({
      status: "failed",
      appliedStepIds: [],
      error: "renderer exploded",
    });
    expect(svg.querySelector("[data-element-id='started']")).toBeNull();
    expect(svg.querySelector("[data-element-id='boom']")).toBeNull();
    expect(elements.size).toBe(0);
    expect(gsap.getTweensOf(created.get("started") as SVGElement)).toHaveLength(0);

    let receipt: unknown;
    void playback.finished.then((outcome) => {
      receipt = outcome;
    });
    await Promise.resolve();
    expect(presentationBarrier).toHaveBeenCalledOnce();
    expect(receipt).toBeUndefined();

    paint.resolve();
    await expect(playback.finished).resolves.toBe(failed);
    expect(playback.cancel()).toBe(failed);
  });

  it("clears GSAP state when a retained transform rolls back", async () => {
    const calls: string[] = [];
    const { elements, executor, svg } = createHarness({
      barrier: () => Promise.resolve(),
      failOnId: "boom",
      calls,
    });
    const retained = textNode("retained", "Move", 20);
    const previous = createSceneState({ revision: 1, nodes: [retained] });
    const failedTarget = createSceneState({
      revision: 2,
      nodes: [textNode("retained", "Move", 80), textNode("boom", "Fail", 20)],
    });
    const retainedElement = seedText(svg, elements, retained);

    const failedPlayback = executor.play(
      planSceneTransition(previous, failedTarget),
      { staggerMs: 0 }
    );

    await expect(failedPlayback.finished).resolves.toMatchObject({
      status: "failed",
      appliedStepIds: [],
      error: "renderer exploded",
    });
    expect(calls).toEqual(["retained", "boom"]);
    expect(elements.get("retained")?.data).toEqual(retained);
    expect(retainedElement.querySelector("text")?.getAttribute("x")).toBe("20");
    expect(retainedElement.getAttribute("style")).toBeNull();
    expect(gsap.getProperty(retainedElement, "scale")).toBe(1);
    expect(gsap.getProperty(retainedElement, "opacity")).toBe(1);

    const recoveredTarget = createSceneState({
      revision: 2,
      nodes: [textNode("retained", "Move", 100)],
    });
    const recoveredPlayback = executor.play(
      planSceneTransition(previous, recoveredTarget),
      { staggerMs: 0 }
    );
    const recovered = recoveredPlayback.cancel();

    await expect(recoveredPlayback.finished).resolves.toBe(recovered);
    expect(recovered).toMatchObject({
      status: "cancelled",
      appliedStepIds: ["retained"],
    });
    expect(elements.get("retained")?.data).toEqual(recoveredTarget.nodes[0]);
    expect(retainedElement.querySelector("text")?.getAttribute("x")).toBe("100");
    expect(retainedElement.style.transform).toBe("");
    expect(gsap.getProperty(retainedElement, "scale")).toBe(1);
  });

  it("preserves target sibling order after a crossfade", async () => {
    const { elements, executor, svg } = createHarness({
      barrier: () => Promise.resolve(),
    });
    const previous = createSceneState({
      revision: 1,
      nodes: [textNode("first", "Old", 20), textNode("second", "Second", 20)],
    });
    const next = createSceneState({
      revision: 2,
      nodes: [textNode("first", "New", 20), textNode("second", "Second", 20)],
    });
    previous.nodes.forEach((node) => {
      if (node.kind !== "text") throw new Error("Expected a text seed");
      seedText(svg, elements, node);
    });

    const playback = executor.play(planSceneTransition(previous, next), {
      staggerMs: 0,
    });
    const cancelled = playback.cancel();

    await expect(playback.finished).resolves.toBe(cancelled);
    expect(
      Array.from(svg.children, (child) => child.getAttribute("data-element-id"))
    ).toEqual(["first", "second"]);
    expect(svg.querySelector("[data-element-id='first'] text")?.textContent).toBe(
      "New"
    );
  });

  it("prevents a rejected old barrier from rolling back newer DOM", async () => {
    let rejectFirstBarrier!: (reason?: unknown) => void;
    const firstBarrier = new Promise<void>((_resolve, reject) => {
      rejectFirstBarrier = reject;
    });
    const barriers = [firstBarrier, Promise.resolve()];
    const { elements, executor, svg } = createHarness({
      barrier: () => barriers.shift() ?? Promise.resolve(),
    });
    const initial = textNode("retained", "Initial", 20);
    const firstTarget = createSceneState({
      revision: 2,
      nodes: [textNode("retained", "First", 20)],
    });
    const secondTarget = createSceneState({
      revision: 3,
      nodes: [textNode("retained", "Second", 20)],
    });
    const initialScene = createSceneState({ revision: 1, nodes: [initial] });
    seedText(svg, elements, initial);

    const firstPlayback = executor.play(
      planSceneTransition(initialScene, firstTarget),
      { staggerMs: 0 }
    );
    firstPlayback.cancel();
    expect(() =>
      executor.play(planSceneTransition(firstTarget, secondTarget), {
        staggerMs: 0,
      })
    ).toThrow("still active or awaiting its presentation receipt");

    executor.cancel();
    const secondPlayback = executor.play(
      planSceneTransition(firstTarget, secondTarget),
      { staggerMs: 0 }
    );
    const secondOutcome = secondPlayback.cancel();
    await expect(secondPlayback.finished).resolves.toBe(secondOutcome);
    expect(svg.querySelector("[data-element-id='retained'] text")?.textContent).toBe(
      "Second"
    );

    rejectFirstBarrier(new Error("paint barrier failed"));
    await expect(firstPlayback.finished).resolves.toMatchObject({
      status: "failed",
      appliedStepIds: [],
      error: "paint barrier failed",
    });
    expect(svg.querySelector("[data-element-id='retained'] text")?.textContent).toBe(
      "Second"
    );
    expect(elements.get("retained")?.data).toEqual(secondTarget.nodes[0]);
  });

  it("uses two animation frames as the default presentation barrier", async () => {
    const frameCallbacks: FrameRequestCallback[] = [];
    const requestFrame = vi.fn((callback: FrameRequestCallback) => {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
    });
    vi.stubGlobal("requestAnimationFrame", requestFrame);
    const { executor } = createHarness();
    const previous = createSceneState({ revision: 0, nodes: [] });
    const next = createSceneState({
      revision: 1,
      nodes: [textNode("instant", "Instant", 20)],
    });

    const playback = executor.play(planSceneTransition(previous, next), {
      staggerMs: 0,
    });
    let receipt: unknown;
    void playback.finished.then((outcome) => {
      receipt = outcome;
    });
    await Promise.resolve();

    expect(requestFrame).toHaveBeenCalledOnce();
    expect(receipt).toBeUndefined();
    frameCallbacks.shift()?.(0);
    expect(requestFrame).toHaveBeenCalledTimes(2);
    expect(receipt).toBeUndefined();
    frameCallbacks.shift()?.(16);
    await expect(playback.finished).resolves.toMatchObject({
      status: "completed",
      appliedStepIds: ["instant"],
    });
  });
});
