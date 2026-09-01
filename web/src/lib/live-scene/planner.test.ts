import { describe, expect, it } from "vitest";

import { planSceneTransition } from "./planner";
import { createSceneState } from "./state";
import type { LatexSceneNode, LineSceneNode, SceneNode, TextSceneNode } from "./types";

const presentation = { enter: "draw", exit: "fade" } as const;
const strokeStyle = {
  stroke: "#3b82f6",
  strokeWidth: 3,
  opacity: 1,
  roughness: 1,
} as const;

function line(id: string, endX: number): LineSceneNode {
  return {
    id,
    kind: "line",
    presentation,
    points: [
      [20, 180],
      [endX, 180],
    ],
    style: strokeStyle,
  };
}

function text(textValue: string, x = 20): TextSceneNode {
  return {
    id: "lesson-title",
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x,
    y: 30,
    text: textValue,
    style: {
      color: "#e2e8f0",
      fontSize: 24,
      opacity: 1,
      anchor: "start",
    },
  };
}

function latex(value: string): LatexSceneNode {
  return {
    id: "theorem-equation",
    kind: "latex",
    presentation: { enter: "fade", exit: "fade" },
    x: 300,
    y: 120,
    latex: value,
    style: { color: "#e2e8f0", fontSize: 28, opacity: 1 },
  };
}

describe("planSceneTransition", () => {
  it("is deterministic and returns an immutable plan", () => {
    const previous = createSceneState({ revision: 0, nodes: [] });
    const next = createSceneState({
      revision: 1,
      nodes: [line("triangle-base", 220), text("Pythagorean theorem")],
    });

    const first = planSceneTransition(previous, next);
    const second = planSceneTransition(previous, next);

    expect(first).toEqual(second);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first.steps)).toBe(true);
    expect(first.steps.every(Object.isFrozen)).toBe(true);
    expect(first.steps.map((step) => [step.type, step.id])).toEqual([
      ["enter", "triangle-base"],
      ["enter", "lesson-title"],
    ]);
  });

  it("plans removals in reverse old order, then changes in next order", () => {
    const previous = createSceneState({
      revision: 4,
      nodes: [
        line("remove-first", 120),
        line("triangle-base", 220),
        line("remove-last", 160),
        text("Old title"),
      ],
    });
    const next = createSceneState({
      revision: 5,
      nodes: [
        line("triangle-base", 260),
        latex("a^2+b^2=c^2"),
        text("New title"),
      ],
    });

    const plan = planSceneTransition(previous, next);

    expect(plan.steps.map((step) => [step.type, step.id])).toEqual([
      ["remove", "remove-last"],
      ["remove", "remove-first"],
      ["update", "triangle-base"],
      ["enter", "theorem-equation"],
      ["update", "lesson-title"],
    ]);
    expect(plan.steps[0]).toMatchObject({ effect: "fade" });
    expect(plan.steps[3]).toMatchObject({ effect: "fade" });
  });

  it("uses transform for compatible same-ID geometry and style changes", () => {
    const previous = createSceneState({ revision: 1, nodes: [line("triangle-base", 220)] });
    const changed = line("triangle-base", 260);
    const next = createSceneState({
      revision: 2,
      nodes: [{ ...changed, style: { ...changed.style, stroke: "#f59e0b" } }],
    });

    expect(planSceneTransition(previous, next).steps).toEqual([
      expect.objectContaining({
        type: "update",
        id: "triangle-base",
        transition: "transform",
      }),
    ]);
  });

  it("crossfades an incompatible path topology change", () => {
    const base = {
      id: "right-angle",
      kind: "path" as const,
      presentation,
      closed: false,
      style: { ...strokeStyle, fill: "none" },
    };
    const previous = createSceneState({
      revision: 1,
      nodes: [
        {
          ...base,
          points: [
            [180, 180],
            [180, 160],
          ],
        },
      ],
    });
    const next = createSceneState({
      revision: 2,
      nodes: [
        {
          ...base,
          points: [
            [180, 180],
            [180, 160],
            [200, 160],
          ],
        },
      ],
    });

    expect(planSceneTransition(previous, next).steps[0]).toMatchObject({
      type: "update",
      id: "right-angle",
      transition: "crossfade",
    });
  });

  it("crossfades changed text and LaTeX content while retaining stable IDs", () => {
    const previous = createSceneState({
      revision: 7,
      nodes: [text("Old title"), latex("a^2+b^2=c^2")],
    });
    const next = createSceneState({
      revision: 8,
      nodes: [text("New title"), latex("c^2=a^2+b^2")],
    });

    const plan = planSceneTransition(previous, next);

    expect(plan.steps).toEqual([
      expect.objectContaining({
        type: "update",
        id: "lesson-title",
        transition: "crossfade",
      }),
      expect.objectContaining({
        type: "update",
        id: "theorem-equation",
        transition: "crossfade",
      }),
    ]);
  });

  it("transforms text geometry when its content is unchanged", () => {
    const previous = createSceneState({ revision: 2, nodes: [text("Same title", 20)] });
    const next = createSceneState({ revision: 3, nodes: [text("Same title", 80)] });

    expect(planSceneTransition(previous, next).steps[0]).toMatchObject({
      type: "update",
      id: "lesson-title",
      transition: "transform",
    });
  });

  it("crossfades a same-ID kind change", () => {
    const previous = createSceneState({ revision: 0, nodes: [line("explanation", 220)] });
    const replacement: SceneNode = { ...text("Now in words"), id: "explanation" };
    const next = createSceneState({ revision: 1, nodes: [replacement] });

    expect(planSceneTransition(previous, next).steps[0]).toMatchObject({
      type: "update",
      id: "explanation",
      transition: "crossfade",
    });
  });

  it("omits unchanged stable nodes", () => {
    const node = line("triangle-base", 220);
    const previous = createSceneState({ revision: 10, nodes: [node] });
    const next = createSceneState({ revision: 11, nodes: [node] });

    expect(planSceneTransition(previous, next).steps).toEqual([]);
  });

  it("rejects skipped, repeated, and backward revisions", () => {
    const previous = createSceneState({ revision: 3, nodes: [] });
    for (const revision of [3, 2, 5]) {
      expect(() =>
        planSceneTransition(previous, createSceneState({ revision, nodes: [] }))
      ).toThrow(`revision ${revision} must follow 3`);
    }
  });

  it("does not expose later caller mutations through a plan", () => {
    const mutableNode = line("triangle-base", 220);
    const previous = { revision: 0, nodes: [] };
    const next = { revision: 1, nodes: [mutableNode] };
    const plan = planSceneTransition(previous, next);

    (mutableNode.points as unknown as [number[], number[]])[0][0] = 999;

    const enter = plan.steps[0];
    expect(enter.type).toBe("enter");
    if (enter.type === "enter" && enter.node.kind === "line") {
      expect(enter.node.points[0]).toEqual([20, 180]);
    }
  });
});
