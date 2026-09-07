import { gsap } from "gsap";

import type { SVGPrimitiveRenderer } from "@/features/canvas/primitives";
import type { CanvasOperation, SVGElementData } from "@/features/canvas/types";
import type { SceneNode, SceneState } from "@/lib/live-scene";
import { createSceneState } from "@/lib/live-scene/state";

export interface SvgNodeReconcilerContext {
  readonly elements: Map<string, SVGElementData>;
  getSvg(): SVGSVGElement | null;
  getRenderer(): SVGPrimitiveRenderer | null;
  invalidate(): void;
}

export interface SvgNodeSnapshot {
  readonly element: SVGElement;
  readonly clone: SVGElement;
  readonly parent: Node | null;
  readonly nextSibling: Node | null;
}

export interface SvgNodeReconciler {
  create(node: SceneNode, domId?: string): SVGElement | null;
  remember(node: SceneNode, element: SVGElement): void;
  forget(id: string, expectedElement?: SVGElement): void;
  capture(element: SVGElement): SvgNodeSnapshot;
  restore(snapshot: SvgNodeSnapshot): void;
  settle(element: SVGElement, node: SceneNode, clearDrawResidue?: boolean): void;
  /** Materialize exact terminal attributes, identities, and canonical scene order. */
  reconcile(scene: SceneState): void;
}

function sceneNodeOperation(
  node: Exclude<SceneNode, { kind: "latex" | "latex_token" }>,
): CanvasOperation {
  switch (node.kind) {
    case "line":
      return {
        action: "line",
        id: node.id,
        points: node.points.map(([x, y]) => [x, y]) as [number, number][],
        color: node.style.stroke,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "path":
      return {
        action: "path",
        id: node.id,
        points: node.points.map(([x, y]) => [x, y]) as [number, number][],
        color: node.style.stroke,
        fill: node.style.fill,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "rect":
      return {
        action: "rect",
        id: node.id,
        x: node.x,
        y: node.y,
        width: node.width,
        height: node.height,
        color: node.style.stroke,
        fill: node.style.fill === "none" ? undefined : node.style.fill,
        stroke_width: node.style.strokeWidth,
        roughness: node.style.roughness,
      };
    case "text":
      return {
        action: "text",
        id: node.id,
        x: node.x,
        y: node.y,
        text: node.text,
        color: node.style.color,
        font_size: node.style.fontSize,
        font_family: node.style.fontFamily,
      };
  }
}

function position(node: SceneNode): { x: number; y: number } {
  return "x" in node ? { x: node.x, y: node.y } : { x: 0, y: 0 };
}

function elementData(node: SceneNode, element: SVGElement): SVGElementData {
  return {
    element,
    id: node.id,
    type: node.kind,
    ...position(node),
    data: node,
  };
}

function createPathElement(
  node: Extract<SceneNode, { kind: "path" }>,
  domId: string,
): SVGElement {
  const namespace = "http://www.w3.org/2000/svg";
  const group = document.createElementNS(namespace, "g");
  group.setAttribute("id", domId);
  group.setAttribute("data-element-id", domId);
  const path = document.createElementNS(namespace, "path");
  path.setAttribute(
    "d",
    `${node.points
      .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`)
      .join(" ")}${node.closed ? " Z" : ""}`,
  );
  path.setAttribute("fill", node.style.fill);
  path.setAttribute("stroke", node.style.stroke);
  path.setAttribute("stroke-width", String(node.style.strokeWidth));
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  group.appendChild(path);
  return group;
}

function copyElement(target: SVGElement, source: SVGElement): void {
  gsap.killTweensOf(target);
  gsap.set(target, { clearProps: "all" });
  for (const attribute of Array.from(target.attributes)) {
    target.removeAttribute(attribute.name);
  }
  for (const attribute of Array.from(source.attributes)) {
    target.setAttribute(attribute.name, attribute.value);
  }
  target.replaceChildren(
    ...Array.from(source.childNodes, (child) => child.cloneNode(true)),
  );
}

function restorePosition(snapshot: SvgNodeSnapshot): void {
  const { element, parent, nextSibling } = snapshot;
  if (!parent) return;
  const anchor = nextSibling?.parentNode === parent ? nextSibling : null;
  if (element.parentNode === parent && element.nextSibling === anchor) return;
  parent.insertBefore(element, anchor);
}

function clearPresentationResidue(element: SVGElement): void {
  for (const target of [
    element,
    ...element.querySelectorAll<SVGElement>("*"),
  ]) {
    target.removeAttribute("clip-path");
    target.style.removeProperty("clip-path");
    target.removeAttribute("filter");
    target.style.removeProperty("filter");
  }
  element.removeAttribute("transform");
  gsap.set(element, { clearProps: "transform,transformOrigin" });
  element.style.removeProperty("transform");
  element.style.removeProperty("transform-origin");
}

function clearDrawResidue(element: SVGElement): void {
  element.querySelectorAll<SVGElement>("path").forEach((path) => {
    path.removeAttribute("stroke-dasharray");
    path.removeAttribute("stroke-dashoffset");
    path.style.removeProperty("stroke-dasharray");
    path.style.removeProperty("stroke-dashoffset");
  });
}

/** Create the sole DOM reconciliation surface shared by legacy and choreographed motion. */
export function createSvgNodeReconciler(
  context: SvgNodeReconcilerContext,
): SvgNodeReconciler {
  const create = (node: SceneNode, domId = node.id): SVGElement | null => {
    const renderer = context.getRenderer();
    if (!renderer) return null;
    if (node.kind === "latex") {
      return renderer.drawLatex({
        type: "latex",
        id: domId,
        latex: node.latex,
        x: node.x,
        y: node.y,
        font_size: node.style.fontSize,
        color: node.style.color,
      });
    }
    if (node.kind === "latex_token") {
      return renderer.drawLatexToken({
        type: "latex_token",
        id: domId,
        latex: node.latex,
        x: node.x,
        y: node.y,
        width: node.width,
        height: node.height,
        anchor: node.anchor,
        font_size: node.style.fontSize,
        color: node.style.color,
      });
    }
    if (node.kind === "path") return createPathElement(node, domId);
    const element = renderer.draw({ ...sceneNodeOperation(node), id: domId });
    if (element && node.kind === "text") {
      element.querySelector("text")?.setAttribute("text-anchor", node.style.anchor);
    }
    return element;
  };

  const remember = (node: SceneNode, element: SVGElement): void => {
    context.elements.set(node.id, elementData(node, element));
    context.invalidate();
  };

  const forget = (id: string, expectedElement?: SVGElement): void => {
    if (!expectedElement || context.elements.get(id)?.element === expectedElement) {
      context.elements.delete(id);
    }
    context.invalidate();
  };

  const capture = (element: SVGElement): SvgNodeSnapshot => ({
    element,
    clone: element.cloneNode(true) as SVGElement,
    parent: element.parentNode,
    nextSibling: element.nextSibling,
  });

  const restore = (snapshot: SvgNodeSnapshot): void => {
    copyElement(snapshot.element, snapshot.clone);
    restorePosition(snapshot);
  };

  const settle = (
    element: SVGElement,
    node: SceneNode,
    shouldClearDrawResidue = false,
  ): void => {
    clearPresentationResidue(element);
    if (shouldClearDrawResidue) clearDrawResidue(element);
    gsap.set(element, { opacity: node.style.opacity });
  };

  const reconcile = (scene: SceneState): void => {
    const svg = context.getSvg();
    if (!svg) throw new Error("The SVG canvas is unavailable");
    const targetScene = createSceneState(scene);
    const rendered = targetScene.nodes.map((node) => {
      const element = create(node);
      if (!element) throw new Error(`Could not render target: ${node.id}`);
      return { node, element };
    });
    const originalEntries = [...context.elements.entries()];
    const snapshots = originalEntries.map(([, data]) => capture(data.element));
    const originalElements = new Set(originalEntries.map(([, data]) => data.element));

    try {
      const targetIds = new Set(targetScene.nodes.map((node) => node.id));
      for (const [id, data] of context.elements) {
        if (!targetIds.has(id)) {
          gsap.killTweensOf(data.element);
          data.element.remove();
        }
      }

      const ordered: [string, SVGElementData][] = [];
      rendered.forEach(({ node, element: canonical }) => {
        const retained = context.elements.get(node.id)?.element;
        const element = retained ?? canonical;
        if (retained) copyElement(retained, canonical);
        else svg.appendChild(element);
        settle(element, node, true);
        ordered.push([node.id, elementData(node, element)]);
      });
      ordered.forEach(([, data]) => svg.appendChild(data.element));
      context.elements.clear();
      ordered.forEach(([id, data]) => context.elements.set(id, data));
      context.invalidate();
    } catch (error) {
      rendered.forEach(({ element }) => {
        if (!originalElements.has(element)) element.remove();
      });
      for (const data of context.elements.values()) {
        if (!originalElements.has(data.element)) data.element.remove();
      }
      [...snapshots].reverse().forEach(restore);
      context.elements.clear();
      originalEntries.forEach(([id, data]) => context.elements.set(id, data));
      context.invalidate();
      throw error;
    }
  };

  return Object.freeze({ create, remember, forget, capture, restore, settle, reconcile });
}
