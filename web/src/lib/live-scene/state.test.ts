import { describe, expect, it } from "vitest";

import { createSceneState } from "./state";
import type { LineSceneNode, SceneState } from "./types";

const presentation = { enter: "draw", exit: "fade" } as const;

function line(id = "triangle-base"): LineSceneNode {
  return {
    id,
    kind: "line",
    presentation,
    points: [
      [20, 180],
      [220, 180],
    ],
    style: {
      stroke: "#3b82f6",
      strokeWidth: 3,
      opacity: 1,
      roughness: 1,
    },
  };
}

describe("createSceneState", () => {
  it("returns a deeply immutable snapshot independent from caller mutation", () => {
    const mutablePoints: [number, number][] = [
      [20, 180],
      [220, 180],
    ];
    const mutableNodes = [
      {
        ...line(),
        points: mutablePoints,
      },
    ];
    const mutable = {
      revision: 1,
      nodes: mutableNodes,
    } as unknown as SceneState;

    const snapshot = createSceneState(mutable);
    mutablePoints[0][0] = 999;
    mutableNodes.push(
      line("late-mutation") as unknown as (typeof mutableNodes)[number]
    );

    expect(snapshot.nodes).toHaveLength(1);
    expect((snapshot.nodes[0] as LineSceneNode).points[0]).toEqual([20, 180]);
    expect(Object.isFrozen(snapshot)).toBe(true);
    expect(Object.isFrozen(snapshot.nodes)).toBe(true);
    expect(Object.isFrozen(snapshot.nodes[0])).toBe(true);
    expect(Object.isFrozen((snapshot.nodes[0] as LineSceneNode).points)).toBe(true);
    expect(Object.isFrozen((snapshot.nodes[0] as LineSceneNode).points[0])).toBe(true);
    expect(Object.isFrozen(snapshot.nodes[0].presentation)).toBe(true);
    expect(Object.isFrozen(snapshot.nodes[0].style)).toBe(true);
  });

  it.each([
    [-1],
    [1.5],
    [Number.POSITIVE_INFINITY],
  ])("rejects invalid revision %s", (revision) => {
    expect(() => createSceneState({ revision, nodes: [] })).toThrow(
      "revision must be a non-negative safe integer"
    );
  });

  it.each(["1triangle", "triangle.main", "triangle base", "a".repeat(65)])(
    "rejects unsafe node id %s",
    (id) => {
      expect(() => createSceneState({ revision: 0, nodes: [line(id)] })).toThrow(
        "must match"
      );
    }
  );

  it("rejects duplicate stable IDs", () => {
    expect(() =>
      createSceneState({ revision: 0, nodes: [line(), line()] })
    ).toThrow("node id triangle-base is duplicated");
  });

  it.each([
    [Number.NaN, 180],
    [20, Number.NEGATIVE_INFINITY],
  ])("rejects non-finite point (%s, %s)", (x, y) => {
    const node = line();
    const invalid = {
      ...node,
      points: [
        [x, y],
        node.points[1],
      ],
    } as unknown as LineSceneNode;
    expect(() => createSceneState({ revision: 0, nodes: [invalid] })).toThrow("must be finite");
  });

  it("rejects invalid dimensions and presentation values", () => {
    expect(() =>
      createSceneState({
        revision: 0,
        nodes: [
          {
            id: "area-square",
            kind: "rect",
            presentation: { enter: "scale", exit: "fade" },
            x: 10,
            y: 10,
            width: 0,
            height: 80,
            style: {
              stroke: "#fff",
              strokeWidth: 2,
              fill: "none",
              opacity: 1,
              roughness: 1,
            },
          },
        ],
      })
    ).toThrow("width must be greater than zero");

    const invalidOpacity = line();
    expect(() =>
      createSceneState({
        revision: 0,
        nodes: [{ ...invalidOpacity, style: { ...invalidOpacity.style, opacity: 1.1 } }],
      })
    ).toThrow("opacity must be between zero and one");
  });

  it("accepts and freezes every supported node kind", () => {
    const snapshot = createSceneState({
      revision: 0,
      nodes: [
        line(),
        {
          id: "right-angle",
          kind: "path",
          presentation,
          points: [
            [185, 180],
            [185, 165],
            [200, 165],
          ],
          closed: false,
          style: {
            stroke: "#f59e0b",
            strokeWidth: 2,
            fill: "none",
            opacity: 1,
            roughness: 0,
          },
        },
        {
          id: "area-square",
          kind: "rect",
          presentation: { enter: "scale", exit: "fade" },
          x: 20,
          y: 20,
          width: 80,
          height: 80,
          style: {
            stroke: "#60a5fa",
            strokeWidth: 2,
            fill: "transparent",
            opacity: 0.8,
            roughness: 1,
          },
        },
        {
          id: "lesson-title",
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 20,
          y: 30,
          text: "Pythagorean theorem",
          style: {
            color: "#e2e8f0",
            fontSize: 24,
            opacity: 1,
            anchor: "start",
          },
        },
        {
          id: "theorem-equation",
          kind: "latex",
          presentation: { enter: "fade", exit: "fade" },
          x: 300,
          y: 120,
          latex: "a^2+b^2=c^2",
          style: { color: "#e2e8f0", fontSize: 28, opacity: 1 },
        },
      ],
    });

    expect(snapshot.nodes.map((node) => node.kind)).toEqual([
      "line",
      "path",
      "rect",
      "text",
      "latex",
    ]);
    expect(snapshot.nodes.every(Object.isFrozen)).toBe(true);
  });
});
