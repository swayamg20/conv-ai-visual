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
import { createSceneState } from "@/lib/live-scene";
import type { SceneNode } from "@/lib/live-scene";

import {
  createSvgNodeReconciler,
  orderSceneNodesForSvgPaint,
} from "./svg-node-reconciler";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const presentation = { enter: "fade", exit: "fade" } as const;
const stroke = {
  stroke: "#f59e0b",
  strokeWidth: 2,
  opacity: 0.8,
  roughness: 0,
} as const;

function group(id: string, childName = "path"): SVGElement {
  const element = document.createElementNS(SVG_NAMESPACE, "g");
  element.setAttribute("id", id);
  element.setAttribute("data-element-id", id);
  element.appendChild(document.createElementNS(SVG_NAMESPACE, childName));
  return element;
}

function renderer(
  calls: string[] = [],
  failOnId?: string,
): SVGPrimitiveRenderer {
  const create = (
    id: string,
    kind: string,
    child: string,
  ): SVGElement | null => {
    calls.push(`${kind}:${id}`);
    if (id === failOnId) return null;
    return group(id, child);
  };
  return {
    draw(operation: CanvasOperation) {
      const element = create(
        operation.id ?? "generated",
        operation.action,
        operation.action === "text" ? "text" : operation.action,
      );
      const text = element?.querySelector("text");
      if (text) {
        text.textContent = operation.text ?? "";
        text.setAttribute("x", String(operation.x ?? 0));
      }
      return element;
    },
    drawLatex(operation: LatexOperation) {
      return create(operation.id, "latex", "foreignObject") as SVGElement;
    },
    drawLatexToken(operation: LatexTokenOperation) {
      const element = create(
        operation.id,
        "latex_token",
        "foreignObject",
      ) as SVGElement;
      const token = element.querySelector("foreignObject");
      token?.setAttribute("x", String(operation.x));
      token?.setAttribute("width", String(operation.width));
      return element;
    },
    drawFunctionPlot: () => null,
  };
}

function nodes(): SceneNode[] {
  return [
    {
      id: "line",
      kind: "line",
      presentation,
      points: [
        [10, 20],
        [30, 40],
      ],
      style: stroke,
    },
    {
      id: "path",
      kind: "path",
      presentation,
      points: [
        [10, 20],
        [30, 40],
        [50, 20],
      ],
      closed: true,
      style: { ...stroke, fill: "transparent" },
    },
    {
      id: "rect",
      kind: "rect",
      presentation,
      x: 20,
      y: 30,
      width: 80,
      height: 90,
      style: { ...stroke, fill: "none" },
    },
    textNode("text", "Label", 60),
    {
      id: "latex",
      kind: "latex",
      presentation,
      x: 100,
      y: 120,
      latex: "x^2",
      style: { color: "#ffffff", fontSize: 32, opacity: 1 },
    },
    {
      id: "token",
      kind: "latex_token",
      presentation,
      x: 180,
      y: 120,
      width: 60,
      height: 40,
      anchor: "middle",
      latex: "+6x",
      style: { color: "#ffffff", fontSize: 28, opacity: 1 },
    },
  ];
}

function textNode(id: string, text: string, x = 20): SceneNode {
  return {
    id,
    kind: "text",
    presentation,
    x,
    y: 30,
    text,
    style: {
      color: "#ffffff",
      fontSize: 24,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function harness(
  options: {
    renderer?: SVGPrimitiveRenderer;
    replayElements?: Map<string, SVGElement>;
    svg?: SVGSVGElement | null;
  } = {},
) {
  const svg =
    options.svg === undefined
      ? document.createElementNS(SVG_NAMESPACE, "svg")
      : options.svg;
  if (svg) document.body.appendChild(svg);
  const elements = new Map<string, SVGElementData>();
  const invalidate = vi.fn();
  const nodeRenderer = options.renderer ?? renderer();
  const reconciler = createSvgNodeReconciler({
    elements,
    getSvg: () => svg,
    getRenderer: () => nodeRenderer,
    ...(options.replayElements
      ? {
          getDetachedReplayElement: (id: string) => {
            const element = options.replayElements?.get(id) ?? null;
            return element && element.parentNode === null ? element : null;
          },
        }
      : {}),
    invalidate,
  });
  return { elements, invalidate, reconciler, svg };
}

afterEach(() => {
  gsap.globalTimeline.clear();
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("SVG node reconciler", () => {
  it("creates every closed scene-node kind through one narrow surface", () => {
    const calls: string[] = [];
    const { reconciler } = harness({ renderer: renderer(calls) });
    const created = nodes().map((node) =>
      reconciler.create(node, `dom-${node.id}`),
    );

    expect(created.every(Boolean)).toBe(true);
    expect(calls).toEqual([
      "line:dom-line",
      "rect:dom-rect",
      "text:dom-text",
      "latex:dom-latex",
      "latex_token:dom-token",
    ]);
    expect(created[1]?.querySelector("path")?.getAttribute("d")).toBe(
      "M10,20 L30,40 L50,20 Z",
    );
    expect(created[3]?.querySelector("text")?.getAttribute("text-anchor")).toBe(
      "middle",
    );
    expect(Object.keys(reconciler).sort()).toEqual([
      "capture",
      "create",
      "forget",
      "reconcile",
      "remember",
      "restore",
      "settle",
    ]);
  });

  it("owns canonical element metadata and guarded removal", () => {
    const { elements, invalidate, reconciler } = harness();
    const node = textNode("label", "Label", 75);
    const element = reconciler.create(node) as SVGElement;

    reconciler.remember(node, element);
    expect(elements.get("label")).toMatchObject({
      element,
      id: "label",
      type: "text",
      x: 75,
      y: 30,
      data: node,
    });
    reconciler.forget("label", group("different"));
    expect(elements.has("label")).toBe(true);
    reconciler.forget("label", element);
    expect(elements.has("label")).toBe(false);
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("restores an exact DOM snapshot and sibling position", () => {
    const { reconciler, svg } = harness();
    if (!svg) throw new Error("missing SVG fixture");
    const before = group("before");
    const target = group("target", "text");
    const after = group("after");
    target.setAttribute("data-state", "before");
    target.querySelector("text")!.textContent = "Before";
    svg.append(before, target, after);
    const snapshot = reconciler.capture(target);

    target.setAttribute("data-state", "after");
    target.querySelector("text")!.textContent = "After";
    target.style.opacity = "0.2";
    svg.appendChild(target);
    reconciler.restore(snapshot);

    expect(target.getAttribute("data-state")).toBe("before");
    expect(target.querySelector("text")?.textContent).toBe("Before");
    expect(target.getAttribute("style")).toBeNull();
    expect(Array.from(svg.children, (child) => child.id)).toEqual([
      "before",
      "target",
      "after",
    ]);
  });

  it("settles opacity and removes transient clip, transform, filter, and draw residue", () => {
    const { reconciler } = harness();
    const node = nodes()[1];
    const element = reconciler.create(node) as SVGElement;
    const path = element.querySelector("path") as SVGPathElement;
    element.setAttribute("clip-path", "url(#clip)");
    element.setAttribute("filter", "url(#glow)");
    element.setAttribute("transform", "translate(20 0)");
    path.setAttribute("clip-path", "url(#child-clip)");
    path.setAttribute("filter", "url(#child-glow)");
    path.setAttribute("stroke-dasharray", "12 4");
    path.style.strokeDashoffset = "8";

    reconciler.settle(element, node, true);

    expect(element.style.opacity).toBe("0.8");
    expect(element.getAttribute("clip-path")).toBeNull();
    expect(element.getAttribute("filter")).toBeNull();
    expect(element.style.transform).toBe("");
    expect(path.getAttribute("clip-path")).toBeNull();
    expect(path.getAttribute("filter")).toBeNull();
    expect(path.getAttribute("stroke-dasharray")).toBeNull();
    expect(path.style.strokeDashoffset).toBe("");
  });

  it("reconciles retained identities, exact attributes, stale cleanup, and paint order", () => {
    const { elements, reconciler, svg } = harness();
    if (!svg) throw new Error("missing SVG fixture");
    const first = textNode("first", "First");
    const second = textNode("second", "Old second");
    const stale = textNode("stale", "Remove me");
    const seeded = [first, stale, second].map((node) => {
      const element = reconciler.create(node) as SVGElement;
      svg.appendChild(element);
      reconciler.remember(node, element);
      return element;
    });
    seeded[2].setAttribute("clip-path", "url(#stale)");
    const target = createSceneState({
      revision: 2,
      nodes: [textNode("second", "New second"), first, nodes()[1]],
    });

    reconciler.reconcile(target);

    expect([...elements.keys()]).toEqual(["second", "first", "path"]);
    expect(
      Array.from(svg.children, (child) =>
        child.getAttribute("data-element-id"),
      ),
    ).toEqual(["path", "second", "first"]);
    expect(elements.get("second")?.element).toBe(seeded[2]);
    expect(elements.get("first")?.element).toBe(seeded[0]);
    expect(seeded[1].isConnected).toBe(false);
    expect(seeded[2].querySelector("text")?.textContent).toBe("New second");
    expect(seeded[2].getAttribute("clip-path")).toBeNull();
    target.nodes.forEach((node) =>
      expect(elements.get(node.id)?.data).toEqual(node),
    );
  });

  it("reuses only detached canonical Replay identities and refreshes their exact content", () => {
    const parked = group("stable", "text");
    parked.setAttribute("data-stale", "remove");
    parked.setAttribute("style", "opacity: 0.2; transform: scale(0.7)");
    parked.querySelector("text")!.textContent = "Stale";
    const replayElements = new Map([["stable", parked]]);
    const { elements, reconciler, svg } = harness({ replayElements });
    if (!svg) throw new Error("missing SVG fixture");
    const current = textNode("stable", "Canonical", 75);

    const replayed = reconciler.create(current) as SVGElement;

    expect(replayed).toBe(parked);
    expect(replayed.getAttribute("data-stale")).toBeNull();
    expect(replayed.getAttribute("style")).toBeNull();
    expect(replayed.querySelector("text")?.textContent).toBe("Canonical");
    expect(replayed.querySelector("text")?.getAttribute("x")).toBe("75");

    svg.appendChild(replayed);
    reconciler.remember(current, replayed);
    expect(replayElements.get("stable")).toBe(parked);
    expect(reconciler.create(textNode("stable", "While live"))).not.toBe(
      parked,
    );
    expect(reconciler.create(current, "stable--incoming")).not.toBe(parked);

    replayed.remove();
    reconciler.forget("stable", replayed);
    expect(reconciler.create(textNode("stable", "Re-entered"))).toBe(parked);
    expect(elements.has("stable")).toBe(false);
  });

  it("does not mutate a parked Replay identity when canonical rendering fails", () => {
    const parked = group("boom", "text");
    parked.querySelector("text")!.textContent = "Keep";
    const { reconciler } = harness({
      renderer: renderer([], "boom"),
      replayElements: new Map([["boom", parked]]),
    });

    expect(reconciler.create(textNode("boom", "Replace"))).toBeNull();
    expect(parked.querySelector("text")?.textContent).toBe("Keep");
    expect(parked.parentNode).toBeNull();
  });

  it("keeps lexical area labels above opaque geometry during entry and exact replay", () => {
    const { elements, reconciler, svg } = harness();
    if (!svg) throw new Error("missing SVG fixture");
    const labels = [
      {
        id: "area_3x",
        kind: "latex_token",
        presentation,
        x: 300,
        y: 285,
        width: 50,
        height: 48,
        anchor: "middle",
        latex: "3x",
        style: { color: "#ffffff", fontSize: 24, opacity: 1 },
      },
      {
        id: "area_x2",
        kind: "latex_token",
        presentation,
        x: 300,
        y: 285,
        width: 70,
        height: 48,
        anchor: "middle",
        latex: "x^2",
        style: { color: "#ffffff", fontSize: 24, opacity: 1 },
      },
    ] satisfies SceneNode[];
    const geometry = {
      id: "filled_square",
      kind: "path",
      presentation,
      points: [
        [210, 210],
        [390, 210],
        [390, 390],
        [210, 390],
      ],
      closed: true,
      style: { ...stroke, fill: "#16171c" },
    } satisfies SceneNode;
    const lexicalScene = createSceneState({
      revision: 2,
      nodes: [...labels, geometry],
    });

    for (const node of lexicalScene.nodes) {
      const element = reconciler.create(node) as SVGElement;
      svg.appendChild(element);
      reconciler.remember(node, element);
    }

    expect([...elements.keys()]).toEqual([
      "area_3x",
      "area_x2",
      "filled_square",
    ]);
    expect(
      Array.from(svg.children, (child) =>
        child.getAttribute("data-element-id"),
      ),
    ).toEqual(["filled_square", "area_3x", "area_x2"]);
    const identities = new Map(
      [...elements].map(([id, data]) => [id, data.element]),
    );

    reconciler.reconcile(lexicalScene);
    reconciler.reconcile(lexicalScene);

    expect(
      Array.from(svg.children, (child) =>
        child.getAttribute("data-element-id"),
      ),
    ).toEqual(["filled_square", "area_3x", "area_x2"]);
    identities.forEach((element, id) => {
      expect(elements.get(id)?.element).toBe(element);
    });
    expect(
      orderSceneNodesForSvgPaint(lexicalScene.nodes).map((node) => node.id),
    ).toEqual(["filled_square", "area_3x", "area_x2"]);
    expect(lexicalScene.nodes.map((node) => node.id)).toEqual([
      "area_3x",
      "area_x2",
      "filled_square",
    ]);
  });

  it("does not mutate retained DOM when canonical rendering fails", () => {
    const { elements, reconciler, svg } = harness({
      renderer: renderer([], "boom"),
    });
    if (!svg) throw new Error("missing SVG fixture");
    const retained = textNode("retained", "Before");
    const element = reconciler.create(retained) as SVGElement;
    svg.appendChild(element);
    reconciler.remember(retained, element);
    const before = svg.innerHTML;

    expect(() =>
      reconciler.reconcile(
        createSceneState({
          revision: 1,
          nodes: [textNode("retained", "After"), textNode("boom", "Fail")],
        }),
      ),
    ).toThrow("Could not render target: boom");
    expect(svg.innerHTML).toBe(before);
    expect(elements.get("retained")?.element).toBe(element);
    expect(elements.get("retained")?.data).toEqual(retained);
  });
});
