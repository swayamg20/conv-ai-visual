import { describe, expect, it } from "vitest";

import { createSceneState } from "./state";
import type { SceneNode, SceneState, TextSceneNode } from "./types";
import {
  applyLiveScenePatch,
  applyScenePatch,
  decodeLiveScenePatchEvent,
  LIVE_SCENE_MAX_NARRATION_LENGTH,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LIVE_SCENE_MAX_PATH_POINTS,
  LIVE_SCENE_MAX_TEXT_LENGTH,
  LiveSceneProtocolError,
  parseLiveScenePatchEvent,
} from "./patch";

const presentation = { enter: "fade", exit: "fade" } as const;

function textNode(id = "lesson-title", text = "Build a triangle"): TextSceneNode {
  return {
    id,
    kind: "text",
    presentation,
    x: 400,
    y: 64,
    text,
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 28,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function rawEvent(
  operations: unknown[] = [{ op: "put", node: textNode() }],
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    type: "scene_patch",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: 0,
    resultRevision: 1,
    patch: {
      v: 1,
      patchId: "patch-1",
      narration: "Draw the title.",
      operations,
    },
    ...overrides,
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function errorCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

function percentile(samples: readonly number[], percentileValue: number): number {
  const sorted = [...samples].sort((left, right) => left - right);
  const index = Math.max(0, Math.ceil(sorted.length * percentileValue) - 1);
  return sorted[index];
}

describe("live scene patch protocol", () => {
  it("decodes the exact server-authoritative envelope and freezes trusted output", () => {
    const event = decodeLiveScenePatchEvent(rawEvent());

    expect(event).toMatchObject({
      type: "scene_patch",
      generation: 1,
      attempt: 1,
      sequence: 1,
      baseRevision: 0,
      resultRevision: 1,
      patch: { v: 1, patchId: "patch-1", narration: "Draw the title." },
    });
    expect(Object.isFrozen(event)).toBe(true);
    expect(Object.isFrozen(event.patch)).toBe(true);
    expect(Object.isFrozen(event.patch.operations)).toBe(true);
    expect(Object.isFrozen(event.patch.operations[0])).toBe(true);
    expect(Object.isFrozen((event.patch.operations[0] as { node: SceneNode }).node)).toBe(
      true
    );

    expect(parseLiveScenePatchEvent(JSON.stringify(rawEvent()))).toEqual(event);
    expect(errorCode(() => parseLiveScenePatchEvent("{not-json"))).toBe("invalid_json");

    const padded = clone(rawEvent());
    (padded.patch as { narration: string; operations: { node: { text: string } }[] })
      .narration = "  Draw the title.  ";
    (padded.patch as { operations: { node: { text: string } }[] }).operations[0]
      .node.text = "  Build a triangle  ";
    const normalized = decodeLiveScenePatchEvent(padded);
    expect(normalized.patch.narration).toBe("Draw the title.");
    expect(
      (normalized.patch.operations[0] as { node: TextSceneNode }).node.text
    ).toBe("Build a triangle");
  });

  it("accepts every Gate 0 node kind and safe paint grammar", () => {
    const nodes: SceneNode[] = [
      {
        id: "triangle-base",
        kind: "line",
        presentation: { enter: "draw", exit: "fade" },
        points: [
          [100, 400],
          [500, 400],
        ],
        style: { stroke: "#AABBCC", strokeWidth: 4, opacity: 1, roughness: 0.5 },
      },
      {
        id: "triangle-angle",
        kind: "path",
        presentation: { enter: "draw", exit: "none" },
        points: [
          [460, 400],
          [460, 360],
          [500, 360],
        ],
        closed: false,
        style: {
          stroke: "hsl(var(--amber))",
          strokeWidth: 3,
          opacity: 1,
          roughness: 0,
          fill: "none",
        },
      },
      {
        id: "area-square",
        kind: "rect",
        presentation: { enter: "scale", exit: "fade" },
        x: 100,
        y: 100,
        width: 120,
        height: 120,
        style: {
          stroke: "hsl(var(--lavender))",
          strokeWidth: 2,
          opacity: 0.8,
          roughness: 1,
          fill: "transparent",
        },
      },
      textNode(),
      {
        id: "equation-pythagoras",
        kind: "latex",
        presentation,
        x: 400,
        y: 500,
        latex: "a^2+b^2=c^2",
        style: { color: "hsl(var(--sage))", fontSize: 32, opacity: 1 },
      },
    ];
    const event = decodeLiveScenePatchEvent(
      rawEvent(nodes.map((node) => ({ op: "put", node })))
    );
    const applied = applyLiveScenePatch(createSceneState({ revision: 0, nodes: [] }), event);

    expect(applied.scene.nodes.map((node) => node.kind)).toEqual([
      "line",
      "path",
      "rect",
      "text",
      "latex",
    ]);
    expect(applied.plan.steps.map((step) => step.id)).toEqual(
      nodes.map((node) => node.id)
    );
    expect(
      applyScenePatch(createSceneState({ revision: 0, nodes: [] }), event)
    ).toEqual(applied.scene);
  });

  it("atomically puts, replaces, and removes nodes while retaining stable identity", () => {
    const initial = createSceneState({
      revision: 2,
      nodes: [textNode("stable-title", "Old title"), textNode("remove-me", "Old note")],
    });
    const event = decodeLiveScenePatchEvent(
      rawEvent(
        [
          { op: "put", node: textNode("stable-title", "New title") },
          { op: "remove", id: "remove-me" },
          { op: "put", node: textNode("new-note", "New note") },
        ],
        { baseRevision: 2, resultRevision: 3 }
      )
    );

    const applied = applyLiveScenePatch(initial, event);

    expect(initial.nodes.map((node) => node.id)).toEqual(["stable-title", "remove-me"]);
    expect(applied.scene.revision).toBe(3);
    expect(applied.scene.nodes.map((node) => node.id)).toEqual([
      "stable-title",
      "new-note",
    ]);
    expect(applied.plan.steps.map((step) => [step.type, step.id])).toEqual([
      ["remove", "remove-me"],
      ["update", "stable-title"],
      ["enter", "new-note"],
    ]);
  });

  it("rejects unknown fields at every protocol layer", () => {
    const cases = [
      (event: Record<string, unknown>) => Object.assign(event, { extra: true }),
      (event: Record<string, unknown>) =>
        Object.assign(event.patch as Record<string, unknown>, { extra: true }),
      (event: Record<string, unknown>) =>
        Object.assign(
          ((event.patch as { operations: Record<string, unknown>[] }).operations[0]),
          { extra: true }
        ),
      (event: Record<string, unknown>) =>
        Object.assign(
          ((event.patch as { operations: { node: Record<string, unknown> }[] }).operations[0]
            .node),
          { extra: true }
        ),
      (event: Record<string, unknown>) =>
        Object.assign(
          ((event.patch as { operations: { node: { presentation: Record<string, unknown> } }[] })
            .operations[0].node.presentation),
          { extra: true }
        ),
      (event: Record<string, unknown>) =>
        Object.assign(
          ((event.patch as { operations: { node: { style: Record<string, unknown> } }[] })
            .operations[0].node.style),
          { fontFamily: "serif" }
        ),
    ];

    for (const mutate of cases) {
      const event = clone(rawEvent());
      mutate(event);
      expect(() => decodeLiveScenePatchEvent(event)).toThrow("unknown field");
    }
  });

  it("rejects unsafe paint, coordinates, geometry, and text budgets", () => {
    const unsafePaint = clone(rawEvent());
    const paintNode = (
      unsafePaint.patch as {
        operations: { node: { style: Record<string, unknown> } }[];
      }
    ).operations[0].node;
    paintNode.style.color = "url(javascript:alert(1))";
    expect(errorCode(() => decodeLiveScenePatchEvent(unsafePaint))).toBe("invalid_node");

    const outOfBounds = clone(rawEvent());
    const outOfBoundsNode = (
      outOfBounds.patch as { operations: { node: { x: number } }[] }
    ).operations[0].node;
    outOfBoundsNode.x = 801;
    expect(errorCode(() => decodeLiveScenePatchEvent(outOfBounds))).toBe("invalid_node");

    const overflowingRect = rawEvent([
      {
        op: "put",
        node: {
          id: "overflowing-rect",
          kind: "rect",
          presentation,
          x: 790,
          y: 10,
          width: 20,
          height: 20,
          style: {
            stroke: "#FFFFFF",
            strokeWidth: 1,
            opacity: 1,
            roughness: 0,
            fill: "none",
          },
        },
      },
    ]);
    expect(errorCode(() => decodeLiveScenePatchEvent(overflowingRect))).toBe(
      "invalid_node"
    );

    const tooManyPoints = rawEvent([
      {
        op: "put",
        node: {
          id: "long-path",
          kind: "path",
          presentation,
          points: Array.from({ length: LIVE_SCENE_MAX_PATH_POINTS + 1 }, () => [1, 1]),
          closed: false,
          style: {
            stroke: "#FFFFFF",
            strokeWidth: 1,
            opacity: 1,
            roughness: 0,
            fill: "none",
          },
        },
      },
    ]);
    expect(errorCode(() => decodeLiveScenePatchEvent(tooManyPoints))).toBe(
      "budget_exceeded"
    );

    const longText = clone(rawEvent());
    const longTextNode = (
      longText.patch as { operations: { node: { text: string } }[] }
    ).operations[0].node;
    longTextNode.text = "x".repeat(LIVE_SCENE_MAX_TEXT_LENGTH + 1);
    expect(errorCode(() => decodeLiveScenePatchEvent(longText))).toBe(
      "budget_exceeded"
    );

    const longNarration = clone(rawEvent());
    (longNarration.patch as { narration: string }).narration = "x".repeat(
      LIVE_SCENE_MAX_NARRATION_LENGTH + 1
    );
    expect(errorCode(() => decodeLiveScenePatchEvent(longNarration))).toBe(
      "budget_exceeded"
    );

    const unicodeBoundary = clone(rawEvent());
    (unicodeBoundary.patch as { narration: string }).narration = "😀".repeat(
      LIVE_SCENE_MAX_NARRATION_LENGTH
    );
    expect(() => decodeLiveScenePatchEvent(unicodeBoundary)).not.toThrow();

    const unicodeOverflow = clone(rawEvent());
    (unicodeOverflow.patch as { narration: string }).narration = "😀".repeat(
      LIVE_SCENE_MAX_NARRATION_LENGTH + 1
    );
    expect(errorCode(() => decodeLiveScenePatchEvent(unicodeOverflow))).toBe(
      "budget_exceeded"
    );
  });

  it("enforces operation, target, node-count, and no-op budgets", () => {
    const tooManyOperations = Array.from(
      { length: LIVE_SCENE_MAX_PATCH_OPERATIONS + 1 },
      (_, index) => ({ op: "put", node: textNode(`node-${index}`, `Node ${index}`) })
    );
    expect(
      errorCode(() => decodeLiveScenePatchEvent(rawEvent(tooManyOperations)))
    ).toBe("budget_exceeded");

    expect(() =>
      decodeLiveScenePatchEvent(
        rawEvent([
          { op: "put", node: textNode("same-target") },
          { op: "remove", id: "same-target" },
        ])
      )
    ).toThrow("more than once");

    const fullScene = createSceneState({
      revision: 0,
      nodes: Array.from({ length: LIVE_SCENE_MAX_NODES }, (_, index) =>
        textNode(`node-${index}`, `Node ${index}`)
      ),
    });
    const overflow = decodeLiveScenePatchEvent(
      rawEvent([{ op: "put", node: textNode("one-too-many") }])
    );
    expect(errorCode(() => applyLiveScenePatch(fullScene, overflow))).toBe(
      "budget_exceeded"
    );

    const alreadyOversized = createSceneState({
      revision: 0,
      nodes: [
        ...fullScene.nodes,
        textNode("already-one-too-many", "Already too many"),
      ],
    });
    const shrink = decodeLiveScenePatchEvent(
      rawEvent([{ op: "remove", id: "already-one-too-many" }])
    );
    expect(errorCode(() => applyLiveScenePatch(alreadyOversized, shrink))).toBe(
      "budget_exceeded"
    );

    const unchanged = createSceneState({ revision: 0, nodes: [textNode()] });
    const noOp = decodeLiveScenePatchEvent(rawEvent([{ op: "put", node: textNode() }]));
    expect(errorCode(() => applyLiveScenePatch(unchanged, noOp))).toBe("invalid_patch");
  });

  it("rejects absent removals and revision mismatches without changing accepted state", () => {
    const accepted = createSceneState({ revision: 4, nodes: [textNode()] });
    const before = JSON.stringify(accepted);
    const absent = decodeLiveScenePatchEvent(
      rawEvent([{ op: "remove", id: "missing-node" }], {
        baseRevision: 4,
        resultRevision: 5,
      })
    );

    expect(errorCode(() => applyLiveScenePatch(accepted, absent))).toBe("invalid_patch");
    expect(JSON.stringify(accepted)).toBe(before);

    const stale = decodeLiveScenePatchEvent(
      rawEvent([{ op: "put", node: textNode("new-node") }], {
        baseRevision: 3,
        resultRevision: 4,
      })
    );
    expect(errorCode(() => applyLiveScenePatch(accepted, stale))).toBe(
      "revision_mismatch"
    );
    expect(JSON.stringify(accepted)).toBe(before);

    expect(
      errorCode(() =>
        decodeLiveScenePatchEvent(rawEvent(undefined, { resultRevision: 2 }))
      )
    ).toBe("revision_mismatch");
  });

  it("validates authoritative generation, attempt, and sequence", () => {
    for (const [field, value] of [
      ["generation", 0],
      ["attempt", 0],
      ["attempt", 3],
      ["sequence", 0],
      ["sequence", 1.5],
      ["sequence", 9],
    ] as const) {
      expect(errorCode(() => decodeLiveScenePatchEvent(rawEvent(undefined, { [field]: value })))).toBe(
        "invalid_event"
      );
    }
  });

  it("decodes, applies, and plans a maximum-complexity patch under 16ms p95", () => {
    const accepted = createSceneState({
      revision: 0,
      nodes: Array.from(
        { length: LIVE_SCENE_MAX_NODES - LIVE_SCENE_MAX_PATCH_OPERATIONS },
        (_, index) => textNode(`existing-${index}`, `Existing node ${index}`)
      ),
    });
    const maximumPatch = JSON.stringify(
      rawEvent(
        Array.from({ length: LIVE_SCENE_MAX_PATCH_OPERATIONS }, (_, operationIndex) => ({
          op: "put",
          node: {
            id: `path-${operationIndex}`,
            kind: "path",
            presentation,
            points: Array.from({ length: LIVE_SCENE_MAX_PATH_POINTS }, (_, pointIndex) => [
              pointIndex * 6,
              (operationIndex * 31 + pointIndex * 3) % 600,
            ]),
            closed: false,
            style: {
              stroke: "#AABBCC",
              strokeWidth: 4,
              opacity: 1,
              roughness: 1,
              fill: "none",
            },
          },
        }))
      )
    );
    const measure = (): number => {
      const startedAt = performance.now();
      const event = parseLiveScenePatchEvent(maximumPatch);
      const applied = applyLiveScenePatch(accepted, event);
      const elapsedMs = performance.now() - startedAt;
      expect(applied.scene.nodes).toHaveLength(LIVE_SCENE_MAX_NODES);
      expect(applied.plan.steps).toHaveLength(LIVE_SCENE_MAX_PATCH_OPERATIONS);
      return elapsedMs;
    };

    for (let index = 0; index < 20; index += 1) measure();
    const samples = Array.from({ length: 200 }, measure);
    const p95Ms = percentile(samples, 0.95);

    console.info(`Gate 1 max-patch decode/apply/plan p95: ${p95Ms.toFixed(3)}ms`);
    expect(p95Ms).toBeLessThan(16);
  });
});
