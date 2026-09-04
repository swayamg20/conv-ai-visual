/** @vitest-environment happy-dom */

import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { gsap } from "gsap";
import { afterEach, describe, expect, it } from "vitest";

import { SVGCanvas } from "@/components/svg-canvas";
import { createSceneState, planSceneTransition } from "@/lib/live-scene";

import type { SVGCanvasHandle } from "./types";

const actEnvironment = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT: boolean;
};
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  document.body.replaceChildren();
});

describe("SVGCanvas", () => {
  it("renders direct and caller-controlled scenes through its public handle", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });
    act(() => {
      canvas.current?.render([
        {
          action: "text",
          id: "answer",
          text: "x = 4",
          x: 13,
          y: 31,
          animate_style: "none",
        },
      ]);
    });

    const rendered = host.querySelector<SVGGElement>("[data-element-id='answer']");
    expect(rendered?.querySelector("text")?.textContent).toBe("x = 4");
    expect(rendered?.querySelector("text")?.getAttribute("x")).toBe("20");
    expect(rendered?.querySelector("text")?.getAttribute("y")).toBe("40");

    const timeline = canvas.current?.createPausedSequence({
      steps: [
        {
          action: "text",
          target_id: "sequence-answer",
          text: "2 + 2 = 4",
          x: 100,
          y: 100,
          animate_style: "none",
        },
      ],
    });
    act(() => {
      timeline?.progress(1);
    });
    expect(
      host.querySelector("[data-element-id='sequence-answer'] text")?.textContent
    ).toBe("2 + 2 = 4");

    await act(async () => root.unmount());
  });

  it("cancels active and future drawing work without clearing visible ink", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });
    act(() => {
      canvas.current?.render([
        {
          action: "text",
          id: "committed-ink",
          text: "Keep me",
          x: 20,
          y: 40,
        },
      ]);
    });
    const committed = host.querySelector<SVGGElement>(
      "[data-element-id='committed-ink']"
    );
    expect(committed).not.toBeNull();
    expect(gsap.getTweensOf(committed as SVGGElement).length).toBeGreaterThan(0);

    const future = canvas.current?.createPausedSequence({
      steps: [
        {
          action: "text",
          target_id: "stale-future",
          text: "Do not reveal",
          x: 40,
          y: 80,
        },
      ],
    });

    act(() => {
      canvas.current?.cancelMotion();
    });

    expect(gsap.getTweensOf(committed as SVGGElement)).toHaveLength(0);
    expect(future?.isActive()).toBe(false);
    expect(host.querySelector("[data-element-id='committed-ink']")).not.toBeNull();
    expect(host.querySelector("[data-element-id='stale-future']")).toBeNull();

    await act(async () => root.unmount());
  });

  it("owns queued motion-plan work and keeps the accepted scene on cancellation", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const foundation = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "visible-now",
          kind: "text",
          x: 20,
          y: 40,
          text: "Visible now",
          presentation: { enter: "none", exit: "fade" },
          style: { color: "#fff", fontSize: 18, opacity: 1, anchor: "start" },
        },
        {
          id: "stale-later",
          kind: "text",
          x: 20,
          y: 80,
          text: "Stale later",
          presentation: { enter: "fade", exit: "fade" },
          style: { color: "#fff", fontSize: 18, opacity: 1, anchor: "start" },
        },
      ],
    });

    const playback = canvas.current?.playMotionPlan(
      planSceneTransition(empty, foundation),
      { staggerMs: 10_000 }
    );
    act(() => {
      playback?.cancel();
    });

    await expect(playback?.finished).resolves.toMatchObject({
      status: "cancelled",
      appliedStepIds: ["visible-now"],
    });
    expect(host.querySelector("[data-element-id='visible-now']")).not.toBeNull();
    expect(host.querySelector("[data-element-id='stale-later']")).toBeNull();

    await act(async () => root.unmount());
  });

  it("commits one canonical DOM identity when a same-id update is interrupted", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const oldScene = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "stable-title",
          kind: "text",
          x: 20,
          y: 40,
          text: "Old title",
          presentation: { enter: "none", exit: "fade" },
          style: { color: "#fff", fontSize: 18, opacity: 1, anchor: "start" },
        },
      ],
    });
    const oldTitle = oldScene.nodes[0];
    if (oldTitle.kind !== "text") throw new Error("Expected a text fixture");
    const newScene = createSceneState({
      revision: 2,
      nodes: [
        {
          ...oldTitle,
          text: "New title",
        },
      ],
    });

    let initial: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      initial = canvas.current?.playMotionPlan(planSceneTransition(empty, oldScene));
    });
    await expect(initial?.finished).resolves.toMatchObject({ status: "completed" });
    const before = host.querySelector("[data-element-id='stable-title']");
    let update: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      update = canvas.current?.playMotionPlan(planSceneTransition(oldScene, newScene));
      update?.cancel();
    });

    await expect(update?.finished).resolves.toMatchObject({
      status: "cancelled",
      appliedStepIds: ["stable-title"],
    });
    const stableElements = host.querySelectorAll("[data-element-id='stable-title']");
    expect(stableElements).toHaveLength(1);
    expect(stableElements[0]).not.toBe(before);
    expect(stableElements[0].querySelector("text")?.textContent).toBe("New title");
    expect(getComputedStyle(stableElements[0]).opacity).toBe("1");
    expect(host.querySelector("[data-element-id='stable-title--outgoing']")).toBeNull();

    await act(async () => root.unmount());
  });

  it("updates compatible geometry in place and reports it as materialized", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const first = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "moving-title",
          kind: "text",
          x: 20,
          y: 40,
          text: "Same title",
          presentation: { enter: "none", exit: "fade" },
          style: { color: "#fff", fontSize: 18, opacity: 1, anchor: "start" },
        },
      ],
    });
    const firstTitle = first.nodes[0];
    if (firstTitle.kind !== "text") throw new Error("Expected a text fixture");
    const moved = createSceneState({
      revision: 2,
      nodes: [{ ...firstTitle, x: 80 }],
    });

    let initial: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      initial = canvas.current?.playMotionPlan(planSceneTransition(empty, first));
    });
    await initial?.finished;
    const before = host.querySelector("[data-element-id='moving-title']");
    before?.setAttribute("clip-path", "url(#stale-reveal)");
    before?.setAttribute(
      "style",
      `${before.getAttribute("style") ?? ""}; clip-path: url(#stale-reveal)`
    );
    let update: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      update = canvas.current?.playMotionPlan(planSceneTransition(first, moved));
      update?.cancel();
    });

    await expect(update?.finished).resolves.toMatchObject({
      status: "cancelled",
      appliedStepIds: ["moving-title"],
    });
    const after = host.querySelector("[data-element-id='moving-title']");
    expect(after).toBe(before);
    expect(after?.querySelector("text")?.getAttribute("x")).toBe("80");
    expect(after?.getAttribute("clip-path")).toBeNull();
    expect((after as SVGElement | null)?.style.transform).toBe("");
    expect((after as SVGElement | null)?.style.transformOrigin).toBe("");
    expect((after as SVGElement | null)?.style.clipPath).toBe("");
    expect(gsap.getProperty(after as Element, "scale")).toBe(1);

    await act(async () => root.unmount());
  });

  it("commits an interrupted removal to its canonical terminal state", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const visible = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "retained-note",
          kind: "text",
          x: 20,
          y: 40,
          text: "Keep this ink",
          presentation: { enter: "none", exit: "fade" },
          style: { color: "#fff", fontSize: 18, opacity: 1, anchor: "start" },
        },
      ],
    });
    const removed = createSceneState({ revision: 2, nodes: [] });

    let initial: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      initial = canvas.current?.playMotionPlan(planSceneTransition(empty, visible));
    });
    await initial?.finished;
    const before = host.querySelector("[data-element-id='retained-note']");
    let removal: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      removal = canvas.current?.playMotionPlan(planSceneTransition(visible, removed));
      removal?.cancel();
    });

    await expect(removal?.finished).resolves.toMatchObject({
      status: "cancelled",
      appliedStepIds: ["retained-note"],
    });
    const after = host.querySelector("[data-element-id='retained-note']");
    expect(before).not.toBeNull();
    expect(after).toBeNull();

    await act(async () => root.unmount());
  });

  it("clears synchronously before a new scene starts", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });
    act(() => {
      canvas.current?.render([
        {
          action: "text",
          id: "stale-callout",
          text: "Old branch",
          x: 20,
          y: 40,
        },
      ]);
      canvas.current?.clear();
      canvas.current?.render([
        {
          action: "text",
          id: "fresh-board",
          text: "Fresh branch",
          x: 20,
          y: 40,
          animate_style: "none",
        },
      ]);
    });

    expect(host.querySelector("[data-element-id='stale-callout']")).toBeNull();
    expect(host.querySelector("[data-element-id='fresh-board']")).not.toBeNull();

    await act(async () => root.unmount());
  });

  it("restores an authored theme fill when draw motion is cancelled", async () => {
    document.documentElement.style.setProperty("--test-fill", "38 91% 55%");
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const scene = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "filled-triangle",
          kind: "path",
          points: [
            [40, 180],
            [160, 40],
            [280, 180],
          ],
          closed: true,
          presentation: { enter: "draw", exit: "fade" },
          style: {
            stroke: "#ffffff",
            strokeWidth: 4,
            fill: "hsl(var(--test-fill))",
            opacity: 1,
            roughness: 0,
          },
        },
      ],
    });

    let playback: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      playback = canvas.current?.playMotionPlan(planSceneTransition(empty, scene));
      playback?.cancel();
    });

    await expect(playback?.finished).resolves.toMatchObject({
      status: "cancelled",
      appliedStepIds: ["filled-triangle"],
    });
    const group = host.querySelector<SVGGElement>("#filled-triangle");
    const path = host.querySelector<SVGPathElement>("#filled-triangle path");
    expect(group?.style.opacity).toBe("1");
    expect(group?.getAttribute("clip-path")).toBeNull();
    expect(path?.getAttribute("fill")).toBe("hsl(var(--test-fill))");
    expect(path?.style.fill).toBe("");
    expect(path?.getAttribute("stroke-dasharray")).toBeNull();
    expect(path?.getAttribute("stroke-dashoffset")).toBeNull();
    expect(path?.style.strokeDasharray).toBe("");
    expect(path?.style.strokeDashoffset).toBe("");

    document.documentElement.style.removeProperty("--test-fill");
    await act(async () => root.unmount());
  });

  it("resolves CSS variable colors before running an emphasis tween", async () => {
    document.documentElement.style.setProperty("--test-stroke", "252 36% 64%");
    document.documentElement.style.setProperty("--test-highlight", "38 91% 55%");
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });

    const empty = createSceneState({ revision: 0, nodes: [] });
    const scene = createSceneState({
      revision: 1,
      nodes: [
        {
          id: "css-colored-angle",
          kind: "path",
          points: [
            [20, 60],
            [20, 20],
            [60, 20],
          ],
          closed: false,
          presentation: { enter: "none", exit: "fade" },
          style: {
            stroke: "hsl(var(--test-stroke))",
            strokeWidth: 4,
            fill: "none",
            opacity: 1,
            roughness: 0,
          },
        },
      ],
    });

    let playback: ReturnType<SVGCanvasHandle["playMotionPlan"]> | undefined;
    act(() => {
      playback = canvas.current?.playMotionPlan(planSceneTransition(empty, scene));
    });
    await playback?.finished;

    const path = host.querySelector("[data-element-id='css-colored-angle'] path");
    expect(path?.getAttribute("stroke")).toBe("hsl(var(--test-stroke))");
    expect(() => {
      act(() => {
        canvas.current?.emphasizeElement(
          "css-colored-angle",
          "hsl(var(--test-highlight))"
        );
        gsap.ticker.tick();
      });
    }).not.toThrow();

    act(() => canvas.current?.cancelMotion());
    expect(path?.getAttribute("stroke")).toBe("hsl(var(--test-stroke))");

    document.documentElement.style.removeProperty("--test-stroke");
    document.documentElement.style.removeProperty("--test-highlight");
    await act(async () => root.unmount());
  });
});
