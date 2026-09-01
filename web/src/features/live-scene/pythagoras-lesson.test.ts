import { describe, expect, it } from "vitest";

import { planSceneTransition } from "@/lib/live-scene";

import {
  createRightAngleExplanationScene,
  PYTHAGORAS_EMPTY_SCENE,
  PYTHAGORAS_FOUNDATION_SCENE,
  PYTHAGORAS_SEMANTIC_IDS,
  PYTHAGORAS_THEOREM_SCENE,
} from "./pythagoras-lesson";

describe("Pythagoras live-scene lesson", () => {
  it("uses unique deterministic semantic IDs", () => {
    expect(new Set(PYTHAGORAS_SEMANTIC_IDS).size).toBe(
      PYTHAGORAS_SEMANTIC_IDS.length
    );
    expect(
      PYTHAGORAS_SEMANTIC_IDS.every((id) =>
        /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(id)
      )
    ).toBe(true);

    const first = planSceneTransition(
      PYTHAGORAS_EMPTY_SCENE,
      PYTHAGORAS_FOUNDATION_SCENE
    );
    const replay = planSceneTransition(
      PYTHAGORAS_EMPTY_SCENE,
      PYTHAGORAS_FOUNDATION_SCENE
    );

    expect(replay).toEqual(first);
    expect(first.steps.map((step) => step.id)).toEqual(
      PYTHAGORAS_FOUNDATION_SCENE.nodes.map((node) => node.id)
    );
  });

  it("branches before the theorem without leaking the queued equation", () => {
    const focused = createRightAngleExplanationScene(
      PYTHAGORAS_FOUNDATION_SCENE
    );
    const plan = planSceneTransition(PYTHAGORAS_FOUNDATION_SCENE, focused);

    expect(focused.revision).toBe(2);
    expect(focused.nodes.some((node) => node.id === "equation-pythagoras")).toBe(
      false
    );
    expect(focused.nodes.some((node) => node.id === "triangle-side-a")).toBe(true);
    expect(plan.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "update",
          id: "triangle-right-angle",
        }),
        expect.objectContaining({
          type: "enter",
          id: "angle-callout-title",
        }),
      ])
    );
    expect(plan.steps.some((step) => step.id === "equation-pythagoras")).toBe(
      false
    );
  });

  it("retains an equation that was already committed before a late question", () => {
    const focused = createRightAngleExplanationScene(PYTHAGORAS_THEOREM_SCENE);
    const plan = planSceneTransition(PYTHAGORAS_THEOREM_SCENE, focused);

    expect(focused.revision).toBe(3);
    expect(focused.nodes.some((node) => node.id === "equation-pythagoras")).toBe(
      true
    );
    expect(plan.steps.some((step) => step.id === "equation-pythagoras")).toBe(
      false
    );
  });
});
