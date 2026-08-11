import { describe, expect, it } from "vitest";

import { compileScene } from "./compiler";
import { LayoutState } from "./layout";
import type { SDLScene, Viewport } from "./types";

const viewport: Viewport = { width: 800, height: 600 };

describe("LayoutState", () => {
  it("resolves positions relative to registered elements", () => {
    const layout = new LayoutState(viewport);
    layout.registerElement("base", { x: 100, y: 80, width: 200, height: 120 });

    expect(layout.resolvePosition({ below: "base", gap: 20 })).toEqual({ x: 100, y: 220 });
    expect(layout.resolvePosition({ rightOf: "base", gap: 15 })).toEqual({ x: 315, y: 80 });
  });

  it("clears registered element and cursor state on reset", () => {
    const layout = new LayoutState(viewport);
    layout.registerElement("base", { x: 100, y: 80, width: 200, height: 120 });
    layout.reset();

    expect(layout.resolveNamedElement("base")).toBeNull();
    expect(layout.resolvePosition(undefined)).toEqual({ x: 300, y: 40 });
  });
});

describe("compileScene", () => {
  it("compiles component commands and resolves semantic highlights", () => {
    const scene: SDLScene = {
      steps: [
        {
          say: "Draw the triangle.",
          show: {
            component: "right_triangle",
            id: "triangle",
            props: { sides: ["base", "height", "hypotenuse"] },
            position: "center",
          },
        },
        { say: "Focus on the hypotenuse.", highlight: "hypotenuse" },
      ],
    };

    const plan = compileScene(scene, viewport);
    const hypotenuse = plan.steps[0].commands.find(
      (command) => command.label === "hypotenuse"
    );

    expect(hypotenuse?.animate_style).toBe("draw");
    expect(plan.steps[1].commands).toEqual([
      {
        action: "highlight",
        target_id: hypotenuse?.target_id,
        highlight_color: "#f59e0b",
      },
    ]);
  });

  it("emits clear before rendering the next component", () => {
    const scene: SDLScene = {
      steps: [
        {
          say: "Start over.",
          clear: true,
          show: { component: "label", props: { text: "Fresh scene" } },
        },
      ],
    };

    const [step] = compileScene(scene, viewport).steps;

    expect(step.commands[0]).toEqual({ action: "clear" });
    expect(step.commands[1]).toMatchObject({
      action: "text",
      text: "Fresh scene",
      animate_style: "fade",
    });
  });
});
