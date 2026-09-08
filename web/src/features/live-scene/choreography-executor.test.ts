/** @vitest-environment happy-dom */

import { gsap } from "gsap";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SVGPrimitiveRenderer } from "@/features/canvas/primitives";
import type {
  CanvasOperation,
  LatexOperation,
  LatexTokenOperation,
  SVGElementData,
} from "@/features/canvas/types";
import {
  createSceneState,
  planSceneTransition,
  type ChoreographyCueV1,
  type LatexTokenSceneNode,
  type LineSceneNode,
  type PathSceneNode,
  type PlannedCheckpointChoreography,
  type RectSceneNode,
  type SceneNode,
  type SceneState,
  type TextSceneNode,
  type ViewportPoseV1,
} from "@/lib/live-scene";

import { createChoreographyExecutor } from "./choreography-executor";
import { createSvgNodeReconciler } from "./svg-node-reconciler";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const presentation = { enter: "fade", exit: "fade" } as const;
const stroke = {
  stroke: "#f59e0b",
  strokeWidth: 2,
  opacity: 1,
  roughness: 0,
} as const;
const BASE_VIEWPORT = Object.freeze({
  v: 1,
  x: 0,
  y: 0,
  width: 800,
  height: 600,
}) satisfies ViewportPoseV1;
const RESULT_VIEWPORT = Object.freeze({
  v: 1,
  x: 100,
  y: 75,
  width: 400,
  height: 300,
}) satisfies ViewportPoseV1;

interface VoidDeferred {
  readonly promise: Promise<void>;
  resolve(): void;
  reject(error: unknown): void;
}

function deferred(): VoidDeferred {
  let resolve!: () => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<void>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  void promise.catch(() => undefined);
  return { promise, resolve, reject };
}

async function flushMicrotasks(): Promise<void> {
  for (let count = 0; count < 8; count += 1) await Promise.resolve();
}

function group(id: string, child: SVGElement): SVGElement {
  const element = document.createElementNS(SVG_NAMESPACE, "g");
  element.setAttribute("id", id);
  element.setAttribute("data-element-id", id);
  element.appendChild(child);
  return element;
}

function svgChild<Name extends keyof SVGElementTagNameMap>(
  name: Name,
): SVGElementTagNameMap[Name] {
  return document.createElementNS(SVG_NAMESPACE, name);
}

function tokenLeft(operation: LatexTokenOperation): number {
  if (operation.anchor === "middle") return operation.x - operation.width / 2;
  if (operation.anchor === "end") return operation.x - operation.width;
  return operation.x;
}

function renderer(failOnId?: string): SVGPrimitiveRenderer {
  const fail = (id: string) => {
    if (id === failOnId) throw new Error(`renderer failed for ${id}`);
  };
  return {
    draw(operation: CanvasOperation) {
      const id = operation.id as string;
      fail(id);
      if (operation.action === "line") {
        const line = svgChild("line");
        const [start, end] = operation.points as [number, number][];
        line.setAttribute("x1", String(start[0]));
        line.setAttribute("y1", String(start[1]));
        line.setAttribute("x2", String(end[0]));
        line.setAttribute("y2", String(end[1]));
        return group(id, line);
      }
      if (operation.action === "rect") {
        const rect = svgChild("rect");
        rect.setAttribute("x", String(operation.x));
        rect.setAttribute("y", String(operation.y));
        rect.setAttribute("width", String(operation.width));
        rect.setAttribute("height", String(operation.height));
        return group(id, rect);
      }
      if (operation.action === "text") {
        const text = svgChild("text");
        text.setAttribute("x", String(operation.x));
        text.setAttribute("y", String(operation.y));
        text.textContent = operation.text ?? "";
        return group(id, text);
      }
      return null;
    },
    drawLatex(operation: LatexOperation) {
      fail(operation.id);
      const foreignObject = svgChild("foreignObject");
      foreignObject.setAttribute("x", String(operation.x));
      foreignObject.setAttribute("y", String(operation.y));
      return group(operation.id, foreignObject);
    },
    drawLatexToken(operation: LatexTokenOperation) {
      fail(operation.id);
      const foreignObject = svgChild("foreignObject");
      foreignObject.setAttribute("x", String(tokenLeft(operation)));
      foreignObject.setAttribute("y", String(operation.y));
      foreignObject.setAttribute("width", String(operation.width));
      foreignObject.setAttribute("height", String(operation.height));
      const content = document.createElement("div");
      content.textContent = operation.latex;
      foreignObject.appendChild(content);
      return group(operation.id, foreignObject);
    },
    drawFunctionPlot: () => null,
  };
}

function textNode(
  id: string,
  x: number,
  text = "label",
  enter: TextSceneNode["presentation"]["enter"] = "fade",
): TextSceneNode {
  return {
    id,
    kind: "text",
    x,
    y: 80,
    text,
    presentation: { enter, exit: "fade" },
    style: {
      color: "#ffffff",
      fontSize: 24,
      opacity: 1,
      anchor: "start",
    },
  };
}

function pathNode(
  id: string,
  offset: number,
  style: Partial<PathSceneNode["style"]> = {},
): PathSceneNode {
  return {
    id,
    kind: "path",
    points: [
      [20 + offset, 200],
      [100 + offset, 120],
      [180 + offset, 200],
    ],
    closed: true,
    presentation: { enter: "draw", exit: "fade" },
    style: { ...stroke, fill: "transparent", ...style },
  };
}

function lineNode(id: string, offset: number): LineSceneNode {
  return {
    id,
    kind: "line",
    points: [
      [10 + offset, 20 + offset],
      [30 + offset, 40 + offset],
    ],
    presentation,
    style: stroke,
  };
}

function rectNode(id: string, offset: number): RectSceneNode {
  return {
    id,
    kind: "rect",
    x: 40 + offset,
    y: 60 + offset,
    width: 80 + offset,
    height: 100 + offset,
    presentation,
    style: { ...stroke, fill: "none" },
  };
}

function tokenNode(
  id: string,
  x: number,
  overrides: Partial<LatexTokenSceneNode> = {},
): LatexTokenSceneNode {
  return {
    id,
    kind: "latex_token",
    x,
    y: 100,
    width: 80,
    height: 40,
    anchor: "middle",
    latex: "x^2",
    presentation,
    style: { color: "#ffffff", fontSize: 28, opacity: 1 },
    ...overrides,
  };
}

function scene(revision: number, nodes: readonly SceneNode[]): SceneState {
  return createSceneState({ revision, nodes });
}

function planned(
  base: SceneState,
  target: SceneState,
  options: {
    readonly durationMs?: number;
    readonly holdAfterMs?: number;
    readonly emphasize?: readonly string[];
    readonly baseViewport?: ViewportPoseV1;
    readonly resultViewport?: ViewportPoseV1;
  } = {},
): PlannedCheckpointChoreography {
  const motionPlan = planSceneTransition(base, target);
  const cue = (kind: "enter" | "exit" | "transform") =>
    motionPlan.steps
      .filter((step) =>
        kind === "enter"
          ? step.type === "enter"
          : kind === "exit"
            ? step.type === "remove"
            : step.type === "update",
      )
      .map((step) => step.id);
  const candidates: ChoreographyCueV1[] = [
    { cue: "enter", targetIds: cue("enter") },
    { cue: "exit", targetIds: cue("exit") },
    { cue: "transform", targetIds: cue("transform") },
    { cue: "emphasize", targetIds: options.emphasize ?? [] },
    {
      cue: "focus",
      targetIds: target.nodes.length ? [target.nodes[0].id] : [],
    },
  ];
  const cues = candidates.filter((candidate) => candidate.targetIds.length > 0);
  return Object.freeze({
    targetScene: target,
    motionPlan,
    baseViewport: options.baseViewport ?? BASE_VIEWPORT,
    resultViewport: options.resultViewport ?? RESULT_VIEWPORT,
    choreographyPlan: Object.freeze({
      v: 1,
      phase: Object.freeze({
        cues: Object.freeze(cues),
        durationMs: options.durationMs ?? 1_000,
        easing: "linear",
        holdAfterMs: options.holdAfterMs ?? 0,
      }),
    }),
  });
}

function harness(
  options: {
    readonly barrier?: () => Promise<void>;
    readonly reducedMotion?: boolean;
    readonly failOnId?: string;
    readonly viewport?: ViewportPoseV1;
  } = {},
) {
  const svg = svgChild("svg");
  document.body.appendChild(svg);
  const elements = new Map<string, SVGElementData>();
  let viewport = options.viewport ?? BASE_VIEWPORT;
  const writeViewport = (next: ViewportPoseV1) => {
    viewport = Object.freeze({ ...next });
    svg.setAttribute(
      "viewBox",
      `${next.x} ${next.y} ${next.width} ${next.height}`,
    );
  };
  const renderViewportFrame = vi.fn(writeViewport);
  const materializeViewport = vi.fn(writeViewport);
  const context = {
    elements,
    getSvg: () => svg,
    getRenderer: () => renderer(options.failOnId),
    invalidate: vi.fn(),
    readViewport: () => viewport,
    renderViewportFrame,
    materializeViewport,
  };
  const seed = (value: SceneState) => {
    createSvgNodeReconciler(context).reconcile(value);
    materializeViewport(options.viewport ?? BASE_VIEWPORT);
    materializeViewport.mockClear();
  };
  const executor = createChoreographyExecutor(context, {
    ...(options.barrier ? { presentationBarrier: options.barrier } : {}),
    reducedMotion: options.reducedMotion,
  });
  return {
    context,
    elements,
    executor,
    materializeViewport,
    renderViewportFrame,
    readViewport: () => viewport,
    seed,
    svg,
  };
}

function capturedTimeline(
  spy: ReturnType<typeof vi.spyOn>,
): gsap.core.Timeline {
  const value = spy.mock.results.at(-1)?.value;
  if (!value || typeof value !== "object" || !("time" in value)) {
    throw new Error("Expected one captured GSAP timeline");
  }
  return value as gsap.core.Timeline;
}

function expectClean(svg: SVGSVGElement): void {
  expect(
    svg.querySelector(
      "[id$='--incoming'], [id$='--outgoing'], [data-element-id$='--incoming'], [data-element-id$='--outgoing']",
    ),
  ).toBeNull();
  expect(
    svg.querySelector(
      "[transform], [filter], [stroke-dasharray], [stroke-dashoffset]",
    ),
  ).toBeNull();
  svg.querySelectorAll<HTMLElement>("[style]").forEach((element) => {
    expect(element.style.transform).toBe("");
    expect(element.style.filter).toBe("");
  });
}

afterEach(() => {
  gsap.globalTimeline.clear();
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("checkpoint choreography executor", () => {
  it.each([0.25, 0.5, 0.75])(
    "numerically interpolates equal-topology paths, tokens, and camera at %s",
    (sample) => {
      const paint = deferred();
      const base = scene(3, [pathNode("model", 0), tokenNode("term", 200)]);
      const target = scene(4, [
        pathNode("model", 80),
        tokenNode("term", 500, {
          y: 200,
          width: 120,
          height: 60,
          anchor: "end",
        }),
      ]);
      const setup = harness({ barrier: () => paint.promise });
      setup.seed(base);
      const timelineSpy = vi.spyOn(gsap, "timeline");
      const retainedPath = setup.elements.get("model")?.element;
      const retainedToken = setup.elements.get("term")?.element;

      setup.executor.play(planned(base, target));
      const timeline = capturedTimeline(timelineSpy);
      timeline.pause();
      timeline.time(sample, false);

      const expectedOffset = 80 * sample;
      expect(retainedPath?.querySelector("path")?.getAttribute("d")).toBe(
        `M${20 + expectedOffset},200 L${100 + expectedOffset},120 L${
          180 + expectedOffset
        },200 Z`,
      );
      const foreignObject = retainedToken?.querySelector("foreignObject");
      expect(Number(foreignObject?.getAttribute("x"))).toBeCloseTo(
        160 + (380 - 160) * sample,
      );
      expect(Number(foreignObject?.getAttribute("y"))).toBeCloseTo(
        100 + 100 * sample,
      );
      expect(Number(foreignObject?.getAttribute("width"))).toBeCloseTo(
        80 + 40 * sample,
      );
      expect(setup.readViewport()).toEqual({
        v: 1,
        x: 100 * sample,
        y: 75 * sample,
        width: 800 - 400 * sample,
        height: 600 - 300 * sample,
      });
      expect(setup.renderViewportFrame).toHaveBeenCalled();
      expect(setup.materializeViewport).not.toHaveBeenCalled();
    },
  );

  it.each([0.25, 0.5, 0.75])(
    "interpolates retained path paint and stroke at %s without a terminal snap",
    (sample) => {
      const base = scene(5, [
        pathNode("corner", 0, {
          fill: "transparent",
          stroke: "#000000",
          strokeWidth: 2,
        }),
      ]);
      const target = scene(6, [
        pathNode("corner", 0, {
          fill: "#4A3212",
          stroke: "#FFFFFF",
          strokeWidth: 6,
        }),
      ]);
      const setup = harness({ barrier: () => Promise.resolve() });
      setup.seed(base);
      const retained = setup.elements.get("corner")?.element;
      const timelineSpy = vi.spyOn(gsap, "timeline");

      setup.executor.play(
        planned(base, target, {
          durationMs: 1_000,
          holdAfterMs: 500,
          resultViewport: BASE_VIEWPORT,
        }),
      );
      capturedTimeline(timelineSpy).pause().time(sample, false);

      const path = retained?.querySelector("path");
      const fill = gsap.utils.splitColor(path?.getAttribute("fill") ?? "");
      const strokeColor = gsap.utils.splitColor(
        path?.getAttribute("stroke") ?? "",
      );
      expect(setup.elements.get("corner")?.element).toBe(retained);
      expect(fill.slice(0, 3)).toEqual([74, 50, 18]);
      expect(fill[3]).toBeCloseTo(sample, 2);
      expect(strokeColor[0]).toBe(Math.round(255 * sample));
      expect(strokeColor[1]).toBe(Math.round(255 * sample));
      expect(strokeColor[2]).toBe(Math.round(255 * sample));
      expect(Number(path?.getAttribute("stroke-width"))).toBeCloseTo(
        2 + 4 * sample,
      );
    },
  );

  it("reaches exact target styling before the authored reading hold", async () => {
    const base = scene(5, [
      pathNode("corner", 0, { fill: "transparent", strokeWidth: 2 }),
    ]);
    const targetNode = pathNode("corner", 0, {
      fill: "#4A3212",
      strokeWidth: 5,
    });
    const target = scene(6, [targetNode]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    const retained = setup.elements.get("corner")?.element;
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(
      planned(base, target, {
        durationMs: 1_000,
        holdAfterMs: 800,
        resultViewport: BASE_VIEWPORT,
      }),
    );
    const timeline = capturedTimeline(timelineSpy).pause();

    timeline.time(1, false);
    expect(timeline.duration()).toBeCloseTo(1.8);
    expect(retained?.querySelector("path")?.getAttribute("fill")).toBe(
      "#4A3212",
    );
    expect(retained?.querySelector("path")?.getAttribute("stroke-width")).toBe(
      "5",
    );
    expect(setup.elements.get("corner")?.element).toBe(retained);

    await flushMicrotasks();
    playback.cancel();
    await expect(playback.finished).resolves.toMatchObject({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
  });

  it("interpolates compatible line, rectangle, and text positions without replacing groups", () => {
    const paint = deferred();
    const base = scene(10, [
      lineNode("line", 0),
      rectNode("rect", 0),
      textNode("text", 100),
    ]);
    const target = scene(11, [
      lineNode("line", 20),
      rectNode("rect", 40),
      { ...textNode("text", 300), y: 180 },
    ]);
    const setup = harness({ barrier: () => paint.promise });
    setup.seed(base);
    const identities = new Map(
      [...setup.elements].map(([id, data]) => [id, data.element]),
    );
    const timelineSpy = vi.spyOn(gsap, "timeline");

    setup.executor.play(planned(base, target));
    capturedTimeline(timelineSpy).pause().time(0.5, false);

    expect(setup.elements.get("line")?.element).toBe(identities.get("line"));
    expect(setup.elements.get("rect")?.element).toBe(identities.get("rect"));
    expect(setup.elements.get("text")?.element).toBe(identities.get("text"));
    expect(setup.svg.querySelector("#line line")?.getAttribute("x1")).toBe(
      "20",
    );
    expect(setup.svg.querySelector("#line line")?.getAttribute("y2")).toBe(
      "50",
    );
    expect(setup.svg.querySelector("#rect rect")?.getAttribute("x")).toBe("60");
    expect(setup.svg.querySelector("#rect rect")?.getAttribute("width")).toBe(
      "100",
    );
    expect(setup.svg.querySelector("#text text")?.getAttribute("x")).toBe(
      "200",
    );
    expect(setup.svg.querySelector("#text text")?.getAttribute("y")).toBe(
      "130",
    );
  });

  it("keeps outer identity through syntax crossfade and settles exact canonical DOM", async () => {
    const baseToken = tokenNode("term", 240);
    const nextToken = tokenNode("term", 240, { latex: "(x+3)^2" });
    const base = scene(1, [baseToken]);
    const target = scene(2, [nextToken]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    const retained = setup.elements.get("term")?.element;
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(planned(base, target));
    const timeline = capturedTimeline(timelineSpy);
    timeline.pause().time(0.5, false);

    expect(setup.elements.get("term")?.element).toBe(retained);
    expect(retained?.textContent).toBe("(x+3)^2");
    timeline.progress(1, false);
    await expect(playback.finished).resolves.toEqual({
      status: "completed",
      firstCuePresented: true,
    });
    expect(setup.elements.get("term")?.element).toBe(retained);
    expect(setup.elements.get("term")?.data).toEqual(target.nodes[0]);
    expectClean(setup.svg);
  });

  it("does not issue firstCuePresented before the first mutation crosses its barrier", async () => {
    const paint = deferred();
    const base = scene(0, []);
    const target = scene(1, [textNode("new", 40)]);
    let setup!: ReturnType<typeof harness>;
    let visibleAtBarrier = false;
    const barrier = vi.fn(() => {
      const entered = setup.svg.querySelector<SVGElement>("#new");
      visibleAtBarrier = Number(entered?.style.opacity ?? "0") > 0;
      return paint.promise;
    });
    setup = harness({ barrier });
    setup.seed(base);
    const playback = setup.executor.play(planned(base, target));
    let receipt: boolean | undefined;
    void playback.firstCuePresented.then((value) => {
      receipt = value;
    });

    await flushMicrotasks();
    expect(setup.svg.querySelector("#new")).not.toBeNull();
    expect(barrier).toHaveBeenCalledOnce();
    expect(visibleAtBarrier).toBe(true);
    expect(receipt).toBeUndefined();
    paint.resolve();
    await expect(playback.firstCuePresented).resolves.toBe(true);
  });

  it("cancels before presentation by restoring exact base nodes and viewport", async () => {
    const paints: VoidDeferred[] = [];
    const barrier = vi.fn(() => {
      const paint = deferred();
      paints.push(paint);
      return paint.promise;
    });
    const baseNode = textNode("retained", 40);
    const base = scene(4, [baseNode]);
    const target = scene(5, [
      textNode("retained", 200),
      textNode("enter", 300),
    ]);
    const setup = harness({ barrier });
    setup.seed(base);
    const identity = setup.elements.get("retained")?.element;
    const playback = setup.executor.play(planned(base, target));

    playback.cancel();
    expect(setup.elements.get("retained")?.element).toBe(identity);
    expect(setup.elements.get("retained")?.data).toEqual(baseNode);
    expect(setup.elements.has("enter")).toBe(false);
    expect(setup.readViewport()).toEqual(BASE_VIEWPORT);
    await flushMicrotasks();
    expect(barrier).toHaveBeenCalledOnce();
    paints[0].resolve();

    await expect(playback.firstCuePresented).resolves.toBe(false);
    await expect(playback.finished).resolves.toEqual({
      status: "cancelled_before_presented",
      firstCuePresented: false,
    });
    expectClean(setup.svg);
  });

  it("cancels after presentation by atomically settling the checkpoint", async () => {
    const paints: VoidDeferred[] = [];
    const barrier = vi.fn(() => {
      const paint = deferred();
      paints.push(paint);
      return paint.promise;
    });
    const base = scene(7, [textNode("move", 20)]);
    const target = scene(8, [textNode("move", 420), textNode("new", 500)]);
    const setup = harness({ barrier });
    setup.seed(base);
    const identity = setup.elements.get("move")?.element;
    const playback = setup.executor.play(planned(base, target));
    await flushMicrotasks();
    paints[0].resolve();
    await expect(playback.firstCuePresented).resolves.toBe(true);

    playback.cancel();
    expect(setup.elements.get("move")?.element).toBe(identity);
    expect(setup.elements.get("move")?.data).toEqual(target.nodes[0]);
    expect(setup.elements.get("new")?.data).toEqual(target.nodes[1]);
    expect(setup.readViewport()).toEqual(RESULT_VIEWPORT);
    expectClean(setup.svg);
    await flushMicrotasks();
    expect(barrier).toHaveBeenCalledTimes(2);
    paints[1].resolve();

    await expect(playback.finished).resolves.toEqual({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
  });

  it("fails closed and rolls back when the presentation barrier rejects", async () => {
    const paint = deferred();
    const baseNode = textNode("move", 40);
    const base = scene(2, [baseNode]);
    const target = scene(3, [textNode("move", 400), textNode("new", 500)]);
    const setup = harness({ barrier: () => paint.promise });
    setup.seed(base);
    const playback = setup.executor.play(planned(base, target));
    await flushMicrotasks();

    paint.reject(new Error("paint failed"));
    await expect(playback.firstCuePresented).resolves.toBe(false);
    await expect(playback.finished).resolves.toEqual({
      status: "failed",
      firstCuePresented: false,
      error: "paint failed",
    });
    expect(setup.elements.get("move")?.data).toEqual(baseNode);
    expect(setup.elements.has("new")).toBe(false);
    expect(setup.readViewport()).toEqual(BASE_VIEWPORT);
    expectClean(setup.svg);
  });

  it("rolls both scene and camera back if the terminal presentation barrier fails", async () => {
    const paints: VoidDeferred[] = [];
    const baseNode = textNode("move", 40);
    const base = scene(12, [baseNode]);
    const target = scene(13, [textNode("move", 440), textNode("new", 520)]);
    const setup = harness({
      barrier: () => {
        const paint = deferred();
        paints.push(paint);
        return paint.promise;
      },
    });
    setup.seed(base);
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(planned(base, target));
    const timeline = capturedTimeline(timelineSpy);
    timeline.pause();
    await flushMicrotasks();
    paints[0].resolve();
    await expect(playback.firstCuePresented).resolves.toBe(true);

    timeline.progress(1, false);
    await flushMicrotasks();
    expect(setup.elements.get("move")?.data).toEqual(target.nodes[0]);
    expect(setup.readViewport()).toEqual(RESULT_VIEWPORT);
    paints[1].reject(new Error("terminal paint failed"));

    await expect(playback.finished).resolves.toEqual({
      status: "failed",
      firstCuePresented: true,
      error: "terminal paint failed",
    });
    expect(setup.elements.get("move")?.data).toEqual(baseNode);
    expect(setup.elements.has("new")).toBe(false);
    expect(setup.readViewport()).toEqual(BASE_VIEWPORT);
    expectClean(setup.svg);
  });

  it("materializes reduced motion immediately while preserving the authored hold", async () => {
    const paints: VoidDeferred[] = [];
    const setup = harness({
      reducedMotion: true,
      barrier: () => {
        const paint = deferred();
        paints.push(paint);
        return paint.promise;
      },
    });
    const base = scene(20, [pathNode("shape", 0)]);
    const target = scene(21, [pathNode("shape", 100), textNode("label", 300)]);
    setup.seed(base);
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(
      planned(base, target, { durationMs: 1_200, holdAfterMs: 700 }),
    );
    const timeline = capturedTimeline(timelineSpy);

    expect(timeline.duration()).toBeCloseTo(0.7);
    expect(setup.elements.get("shape")?.data).toEqual(target.nodes[0]);
    expect(setup.elements.get("label")?.data).toEqual(target.nodes[1]);
    expect(setup.readViewport()).toEqual(RESULT_VIEWPORT);
    expectClean(setup.svg);
    await flushMicrotasks();
    paints[0].resolve();
    await expect(playback.firstCuePresented).resolves.toBe(true);

    timeline.progress(1, false);
    await flushMicrotasks();
    paints[1].resolve();
    await expect(playback.finished).resolves.toEqual({
      status: "completed",
      firstCuePresented: true,
    });
  });

  it("uses one timeline for phase plus hold, deterministic emphasis, and terminal cleanup", async () => {
    const base = scene(30, [pathNode("shape", 0), textNode("old", 20)]);
    const target = scene(31, [
      pathNode("shape", 60),
      {
        ...textNode("new", 300, "new", "scale"),
        presentation: { enter: "scale", exit: "fade" },
      },
    ]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(
      planned(base, target, {
        durationMs: 800,
        holdAfterMs: 400,
        emphasize: ["shape"],
      }),
    );
    const timeline = capturedTimeline(timelineSpy);
    timeline.pause().time(0.4, false);

    expect(timelineSpy).toHaveBeenCalledOnce();
    expect(timeline.duration()).toBeCloseTo(1.2);
    expect(setup.elements.get("shape")?.element.style.filter).toMatch(
      /^brightness\(1\.28/,
    );
    timeline.progress(1, false);
    await expect(playback.finished).resolves.toEqual({
      status: "completed",
      firstCuePresented: true,
    });
    expect([...setup.elements.keys()]).toEqual(["shape", "new"]);
    expect(setup.elements.get("shape")?.data).toEqual(target.nodes[0]);
    expect(setup.elements.get("new")?.data).toEqual(target.nodes[1]);
    expect(setup.readViewport()).toEqual(RESULT_VIEWPORT);
    expectClean(setup.svg);
  });

  it("reports only the certified closed cue order and presentation milestones", async () => {
    const base = scene(35, [textNode("move", 20)]);
    const target = scene(36, [textNode("move", 200), textNode("new", 320)]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    const signals: unknown[] = [];
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(planned(base, target), (signal) => {
      signals.push(signal);
    });
    const timeline = capturedTimeline(timelineSpy);
    timeline.progress(1, false);

    await expect(playback.finished).resolves.toEqual({
      status: "completed",
      firstCuePresented: true,
    });
    expect(signals).toEqual([
      { type: "cueStarted", cue: "enter" },
      { type: "cueStarted", cue: "transform" },
      { type: "cueStarted", cue: "focus" },
      { type: "firstCuePresented" },
      { type: "checkpointSettled", settlement: "completed" },
    ]);
  });

  it("settles a token morph and emphasis when cancelled during the reading hold", async () => {
    const base = scene(40, [tokenNode("term", 240)]);
    const target = scene(41, [tokenNode("term", 500, { latex: "(x+3)^2" })]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    const retained = setup.elements.get("term")?.element;
    const timelineSpy = vi.spyOn(gsap, "timeline");
    const playback = setup.executor.play(
      planned(base, target, {
        durationMs: 400,
        holdAfterMs: 1_000,
        emphasize: ["term"],
      }),
    );
    const timeline = capturedTimeline(timelineSpy).pause();
    timeline.time(0.6, false);
    await expect(playback.firstCuePresented).resolves.toBe(true);

    playback.cancel();

    await expect(playback.finished).resolves.toEqual({
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
    expect(setup.elements.get("term")?.element).toBe(retained);
    expect(setup.elements.get("term")?.data).toEqual(target.nodes[0]);
    expect(setup.readViewport()).toEqual(RESULT_VIEWPORT);
    expectClean(setup.svg);
  });

  it("rolls back when a cue renderer fails before playback starts", async () => {
    const baseNode = textNode("retained", 40);
    const base = scene(50, [baseNode]);
    const target = scene(51, [baseNode, textNode("broken", 300)]);
    const barrier = vi.fn(() => Promise.resolve());
    const setup = harness({ barrier, failOnId: "broken" });
    setup.seed(base);

    const playback = setup.executor.play(planned(base, target));

    await expect(playback.firstCuePresented).resolves.toBe(false);
    await expect(playback.finished).resolves.toEqual({
      status: "failed",
      firstCuePresented: false,
      error: "renderer failed for broken",
    });
    expect(barrier).not.toHaveBeenCalled();
    expect(setup.elements.get("retained")?.data).toEqual(baseNode);
    expect(setup.elements.has("broken")).toBe(false);
    expect(setup.readViewport()).toEqual(BASE_VIEWPORT);
    expectClean(setup.svg);
  });

  it("returns failed without mutating when DOM identity disagrees with the plan", async () => {
    const baseNode = textNode("node", 20);
    const base = scene(0, [baseNode]);
    const target = scene(1, [textNode("node", 200)]);
    const setup = harness({ barrier: () => Promise.resolve() });
    setup.seed(base);
    setup.elements
      .get("node")
      ?.element.setAttribute("data-element-id", "wrong");

    const playback = setup.executor.play(planned(base, target));

    await expect(playback.finished).resolves.toMatchObject({
      status: "failed",
      firstCuePresented: false,
      error: "The reconciler DOM identity does not match base node node",
    });
    expect(setup.elements.get("node")?.data).toEqual(baseNode);
    expect(setup.readViewport()).toEqual(BASE_VIEWPORT);
  });
});
