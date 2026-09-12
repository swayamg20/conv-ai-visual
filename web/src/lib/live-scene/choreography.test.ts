import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import {
  CHOREOGRAPHY_CUE_ORDER,
  CHOREOGRAPHY_CUE_V2_ORDER,
  CHOREOGRAPHY_EASINGS,
  CHOREOGRAPHY_PLAN_VERSION,
  CHOREOGRAPHY_PLAN_V2_VERSION,
  COMPLETING_SQUARE_STAGES,
  MAX_CHOREOGRAPHY_CUES,
  MAX_CHOREOGRAPHY_HOLD_AFTER_MS,
  MAX_CHOREOGRAPHY_PHASE_MS,
  MAX_CHOREOGRAPHY_PLAN_MS,
  MAX_CHOREOGRAPHY_TARGET_REFERENCES,
  MAX_CHOREOGRAPHY_TARGETS_PER_CUE,
  MAX_CHOREOGRAPHY_V2_CUES,
  MAX_CHOREOGRAPHY_V2_NODE_REFERENCES,
  MIN_CHOREOGRAPHY_PHASE_MS,
  PRESENTATION_CHECKPOINT_VERSION,
  ROUTED_CHOREOGRAPHY_BEAT_VERSION,
  VIEWPORT_POSE_VERSION,
  decodeChoreographyCueV1,
  decodeChoreographyCueV2,
  decodeChoreographyPlan,
  decodeChoreographyPlanV1,
  decodeChoreographyPlanV2,
  decodePresentationCheckpointV1,
  decodeRoutedChoreographyBeatV2,
  decodeViewportPoseV1,
} from "./choreography";

function advanceBeat(stage = "solve"): Record<string, unknown> {
  return {
    v: ROUTED_CHOREOGRAPHY_BEAT_VERSION,
    beatId: "beat-solve",
    componentKind: "completing_square",
    componentId: "square-lesson",
    route: { intent: "advance", targetStage: stage },
  };
}

function clarificationBeat(): Record<string, unknown> {
  return {
    v: ROUTED_CHOREOGRAPHY_BEAT_VERSION,
    beatId: "beat-corner-detail",
    componentKind: "completing_square",
    componentId: "square-lesson",
    route: { intent: "clarify_corner" },
  };
}

function choreographyPlan(): Record<string, unknown> {
  return {
    v: CHOREOGRAPHY_PLAN_VERSION,
    phase: {
      cues: [
        { cue: "enter", targetIds: ["corner", "dimension_label"] },
        { cue: "transform", targetIds: ["strip_left", "strip_top"] },
        { cue: "emphasize", targetIds: ["corner"] },
        { cue: "focus", targetIds: ["corner", "strip_top"] },
      ],
      durationMs: 1_800,
      easing: "ease_in_out",
      holdAfterMs: 6_000,
    },
  };
}

function tracePlan(): Record<string, unknown> {
  return {
    v: CHOREOGRAPHY_PLAN_V2_VERSION,
    phase: {
      cues: [
        {
          cue: "enter",
          targetIds: ["projectile__marker", "projectile__trajectory_ascent"],
        },
        {
          cue: "trace_path",
          pathId: "projectile__trajectory_ascent",
          markerId: "projectile__marker",
        },
        {
          cue: "focus",
          targetIds: ["projectile__marker", "projectile__trajectory_ascent"],
        },
      ],
      durationMs: 2_000,
      easing: "linear",
      holdAfterMs: 800,
    },
  };
}

function pose(
  overrides: Partial<{
    x: number;
    y: number;
    width: number;
    height: number;
  }> = {},
): Record<string, unknown> {
  return {
    v: VIEWPORT_POSE_VERSION,
    x: 0,
    y: 75,
    width: 800,
    height: 450,
    ...overrides,
  };
}

function checkpoint(): Record<string, unknown> {
  return {
    v: PRESENTATION_CHECKPOINT_VERSION,
    checkpointId: "missing-corner",
    checkpointNarration: "The two strips leave one three-by-three corner.",
    baseViewports: {
      cinematic: pose(),
      compact: pose({ x: 100, y: 0, width: 600, height: 600 }),
    },
    resultViewports: {
      cinematic: pose({ x: 180, y: 105, width: 440, height: 247.5 }),
      compact: pose({ x: 180, y: 80, width: 440, height: 440 }),
    },
    transientFree: true,
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("live choreography contracts", () => {
  it.each(COMPLETING_SQUARE_STAGES)(
    "decodes and freezes the routed-only %s advance beat",
    (stage) => {
      const source = advanceBeat(stage);
      const decoded = decodeRoutedChoreographyBeatV2(source);

      expect(decoded).toEqual(source);
      expect(decoded.route).toEqual({ intent: "advance", targetStage: stage });
      expect(Object.isFrozen(decoded)).toBe(true);
      expect(Object.isFrozen(decoded.route)).toBe(true);

      (source.route as Record<string, unknown>).targetStage = "setup";
      expect(decoded.route).toEqual({ intent: "advance", targetStage: stage });
    },
  );

  it("accepts only the bounded corner clarification route", () => {
    const source = clarificationBeat();
    const decoded = decodeRoutedChoreographyBeatV2(source);

    expect(decoded).toEqual(source);
    expect(decoded.route).toEqual({ intent: "clarify_corner" });
    expect(Object.isFrozen(decoded.route)).toBe(true);

    const open = clarificationBeat();
    (open.route as Record<string, unknown>).targetStage = "complete";
    expect(() => decodeRoutedChoreographyBeatV2(open)).toThrow(
      "unknown field targetStage",
    );
  });

  it.each([
    ["v", 1],
    ["componentKind", "pythagorean_area_identity"],
    ["beatId", "1unsafe"],
    ["componentId", "c".repeat(33)],
  ])("rejects invalid routed beat field %s", (field, value) => {
    const source = advanceBeat();
    source[field as string] = value;

    expect(() => decodeRoutedChoreographyBeatV2(source)).toThrow();
  });

  it.each([
    ["narration", "Model-authored caption"],
    ["durationMs", 800],
    ["viewport", { x: 0, y: 0 }],
  ])("rejects compiler-owned routed beat field %s", (field, value) => {
    const source = advanceBeat();
    source[field as string] = value;

    expect(() => decodeRoutedChoreographyBeatV2(source)).toThrow(
      `unknown field ${field}`,
    );
  });

  it("rejects missing fields and non-plain routed data", () => {
    const missing = advanceBeat();
    delete missing.componentId;
    expect(() => decodeRoutedChoreographyBeatV2(missing)).toThrow(
      "missing field componentId",
    );

    class Route {
      intent = "advance";
      targetStage = "solve";
    }
    const nonPlain = advanceBeat();
    nonPlain.route = new Route();
    expect(() => decodeRoutedChoreographyBeatV2(nonPlain)).toThrow(
      "must be a plain object",
    );
  });

  it.each(CHOREOGRAPHY_CUE_ORDER)(
    "decodes and deeply freezes the closed %s cue",
    (cue) => {
      const source = { cue, targetIds: ["node_a", "node_b"] };
      const decoded = decodeChoreographyCueV1(source);

      expect(decoded).toEqual(source);
      expect(Object.isFrozen(decoded)).toBe(true);
      expect(Object.isFrozen(decoded.targetIds)).toBe(true);
      source.targetIds[0] = "mutated";
      expect(decoded.targetIds).toEqual(["node_a", "node_b"]);
    },
  );

  it.each([
    [{ cue: "spin", targetIds: ["node_a"] }, "unsupported value"],
    [{ cue: "enter", targetIds: [] }, "must be non-empty"],
    [{ cue: "enter", targetIds: ["node_a", "node_a"] }, "must be unique"],
    [
      { cue: "enter", targetIds: ["node_b", "node_a"] },
      "canonical lexical order",
    ],
    [{ cue: "focus", targetIds: ["#corner"] }, "unsafe identifier"],
    [{ cue: "transform", targetIds: ["node_a"], x: 100 }, "unknown field x"],
  ])("rejects an open or noncanonical cue %#", (source, message) => {
    expect(() => decodeChoreographyCueV1(source)).toThrow(message as string);
  });

  it("enforces the per-cue target budget", () => {
    const source = {
      cue: "enter",
      targetIds: Array.from(
        { length: MAX_CHOREOGRAPHY_TARGETS_PER_CUE + 1 },
        (_, index) => `node_${index.toString().padStart(2, "0")}`,
      ),
    };

    expect(protocolCode(() => decodeChoreographyCueV1(source))).toBe(
      "budget_exceeded",
    );
  });

  it("decodes and freezes the one closed V2 path-trace cue", () => {
    const source = {
      cue: "trace_path",
      pathId: "projectile__trajectory_ascent",
      markerId: "projectile__marker",
    };
    const decoded = decodeChoreographyCueV2(source);

    expect(decoded).toEqual(source);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(CHOREOGRAPHY_CUE_ORDER).not.toContain("trace_path");
    expect(CHOREOGRAPHY_CUE_V2_ORDER).toEqual([
      "enter",
      "exit",
      "transform",
      "trace_path",
      "emphasize",
      "focus",
    ]);
  });

  it.each([
    [
      {
        cue: "trace_path",
        pathId: "projectile__trajectory",
        markerId: "projectile__trajectory",
      },
      "must be distinct",
    ],
    [
      {
        cue: "trace_path",
        pathId: "#trajectory",
        markerId: "projectile__marker",
      },
      "unsafe identifier",
    ],
    [
      {
        cue: "trace_path",
        pathId: "projectile__trajectory",
        markerId: "projectile__marker",
        progress: 0.5,
      },
      "unknown field progress",
    ],
    [
      {
        cue: "trace_path",
        pathId: "projectile__trajectory",
        markerId: "projectile__marker",
        targetIds: ["projectile__marker"],
      },
      "unknown field targetIds",
    ],
  ])("rejects an open or ambiguous V2 trace cue %#", (source, message) => {
    expect(() => decodeChoreographyCueV2(source)).toThrow(message as string);
  });

  it("decodes V2 without widening either sealed V1 decoder", () => {
    const source = tracePlan();
    const decoded = decodeChoreographyPlanV2(source);

    expect(decoded).toEqual(source);
    expect(decodeChoreographyPlan(source)).toEqual(decoded);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.phase)).toBe(true);
    expect(Object.isFrozen(decoded.phase.cues)).toBe(true);
    expect(() => decodeChoreographyPlanV1(source)).toThrow();
    expect(() =>
      decodeChoreographyCueV1({
        cue: "trace_path",
        pathId: "projectile__trajectory_ascent",
        markerId: "projectile__marker",
      }),
    ).toThrow();
    expect(decodeChoreographyPlan(choreographyPlan())).toEqual(
      decodeChoreographyPlanV1(choreographyPlan()),
    );

    const noTrace = tracePlan();
    const phase = noTrace.phase as { cues: Record<string, unknown>[] };
    phase.cues = phase.cues.filter((cue) => cue.cue !== "trace_path");
    expect(decodeChoreographyPlanV2(noTrace).phase.cues).toHaveLength(2);
  });

  it("requires V2 trace ownership to remain disjoint from normal transforms", () => {
    const source = tracePlan();
    const phase = source.phase as { cues: Record<string, unknown>[] };
    phase.cues.splice(1, 0, {
      cue: "transform",
      targetIds: ["projectile__marker"],
    });

    expect(() => decodeChoreographyPlanV2(source)).toThrow(
      "trace-owned nodes must be disjoint from transform targetIds",
    );
  });

  it("enforces V2 cue ordering, count, and node-reference budgets independently", () => {
    expect(MAX_CHOREOGRAPHY_V2_CUES).toBe(6);
    const unordered = tracePlan();
    const unorderedPhase = unordered.phase as { cues: unknown[] };
    unorderedPhase.cues = [...unorderedPhase.cues].reverse();
    expect(() => decodeChoreographyPlanV2(unordered)).toThrow(
      "canonical cue order",
    );

    const tooManyCues = tracePlan();
    (tooManyCues.phase as { cues: unknown[] }).cues = Array.from(
      { length: MAX_CHOREOGRAPHY_V2_CUES + 1 },
      (_, index) => ({ cue: "enter", targetIds: [`node_${index}`] }),
    );
    expect(protocolCode(() => decodeChoreographyPlanV2(tooManyCues))).toBe(
      "budget_exceeded",
    );

    const tooManyReferences = tracePlan();
    (tooManyReferences.phase as { cues: unknown[] }).cues = [
      {
        cue: "enter",
        targetIds: Array.from({ length: 16 }, (_, index) =>
          `enter_${index.toString().padStart(2, "0")}`,
        ),
      },
      {
        cue: "exit",
        targetIds: Array.from({ length: 15 }, (_, index) =>
          `exit_${index.toString().padStart(2, "0")}`,
        ),
      },
      {
        cue: "trace_path",
        pathId: "projectile__trajectory",
        markerId: "projectile__marker",
      },
    ];
    expect(16 + 15 + 2).toBe(MAX_CHOREOGRAPHY_V2_NODE_REFERENCES + 1);
    expect(
      protocolCode(() => decodeChoreographyPlanV2(tooManyReferences)),
    ).toBe("budget_exceeded");
  });

  it.each(CHOREOGRAPHY_EASINGS)(
    "accepts the approved non-overshooting %s easing",
    (easing) => {
      const source = choreographyPlan();
      (source.phase as Record<string, unknown>).easing = easing;
      const decoded = decodeChoreographyPlanV1(source);

      expect(decoded.phase.easing).toBe(easing);
      expect(Object.isFrozen(decoded)).toBe(true);
      expect(Object.isFrozen(decoded.phase)).toBe(true);
      expect(Object.isFrozen(decoded.phase.cues)).toBe(true);
    },
  );

  it.each(["bounce", "elastic", "back", "power4.out"])(
    "rejects the unapproved easing %s",
    (easing) => {
      const source = choreographyPlan();
      (source.phase as Record<string, unknown>).easing = easing;

      expect(() => decodeChoreographyPlanV1(source)).toThrow(
        "unsupported value",
      );
    },
  );

  it("requires one canonical sequence of unique cue kinds", () => {
    const duplicate = choreographyPlan();
    (duplicate.phase as Record<string, unknown>).cues = [
      { cue: "focus", targetIds: ["corner"] },
      { cue: "focus", targetIds: ["strip_top"] },
    ];
    expect(() => decodeChoreographyPlanV1(duplicate)).toThrow(
      "cue kinds must be unique",
    );

    const unordered = choreographyPlan();
    const phase = unordered.phase as Record<string, unknown>;
    phase.cues = [...(phase.cues as unknown[])].reverse();
    expect(() => decodeChoreographyPlanV1(unordered)).toThrow(
      "canonical cue order",
    );

    const multiplePhases = {
      v: CHOREOGRAPHY_PLAN_VERSION,
      phases: [choreographyPlan().phase, choreographyPlan().phase],
    };
    expect(() => decodeChoreographyPlanV1(multiplePhases)).toThrow(
      "unknown field phases",
    );
  });

  it("enforces cue-count and phase target-reference budgets", () => {
    const tooManyCues = choreographyPlan();
    (tooManyCues.phase as Record<string, unknown>).cues = Array.from(
      { length: MAX_CHOREOGRAPHY_CUES + 1 },
      (_, index) => ({ cue: "enter", targetIds: [`node_${index}`] }),
    );
    expect(protocolCode(() => decodeChoreographyPlanV1(tooManyCues))).toBe(
      "budget_exceeded",
    );

    const tooManyReferences = choreographyPlan();
    (tooManyReferences.phase as Record<string, unknown>).cues = [
      {
        cue: "enter",
        targetIds: Array.from(
          { length: 16 },
          (_, index) => `enter_${index.toString().padStart(2, "0")}`,
        ),
      },
      {
        cue: "transform",
        targetIds: Array.from(
          { length: 16 },
          (_, index) => `transform_${index.toString().padStart(2, "0")}`,
        ),
      },
      { cue: "emphasize", targetIds: ["extra"] },
    ];
    expect(
      (
        tooManyReferences.phase as { cues: { targetIds: string[] }[] }
      ).cues.reduce((count, cue) => count + cue.targetIds.length, 0),
    ).toBe(MAX_CHOREOGRAPHY_TARGET_REFERENCES + 1);
    expect(
      protocolCode(() => decodeChoreographyPlanV1(tooManyReferences)),
    ).toBe("budget_exceeded");
  });

  it.each([
    ["durationMs", MIN_CHOREOGRAPHY_PHASE_MS - 1],
    ["durationMs", MAX_CHOREOGRAPHY_PHASE_MS + 1],
    ["durationMs", 1_200.5],
    ["holdAfterMs", -1],
    ["holdAfterMs", MAX_CHOREOGRAPHY_HOLD_AFTER_MS + 1],
  ])("rejects invalid phase timing %s=%s", (field, value) => {
    const source = choreographyPlan();
    (source.phase as Record<string, unknown>)[field as string] = value;

    expect(() => decodeChoreographyPlanV1(source)).toThrow();
  });

  it("accepts the exact total duration boundary and rejects an overflow", () => {
    const exact = choreographyPlan();
    const exactPhase = exact.phase as Record<string, unknown>;
    exactPhase.durationMs = MAX_CHOREOGRAPHY_PHASE_MS;
    exactPhase.holdAfterMs =
      MAX_CHOREOGRAPHY_PLAN_MS - MAX_CHOREOGRAPHY_PHASE_MS;
    expect(decodeChoreographyPlanV1(exact).phase).toMatchObject({
      durationMs: 6_000,
      holdAfterMs: 6_000,
    });

    const overflow = clone(exact);
    (overflow.phase as Record<string, unknown>).holdAfterMs =
      MAX_CHOREOGRAPHY_PLAN_MS - MAX_CHOREOGRAPHY_PHASE_MS + 1;
    expect(protocolCode(() => decodeChoreographyPlanV1(overflow))).toBe(
      "budget_exceeded",
    );
  });

  it("decodes and deeply freezes exact cinematic and compact viewport maps", () => {
    const source = checkpoint();
    source.checkpointNarration = "  The corner is 3 × 3 = 9.  ";
    const decoded = decodePresentationCheckpointV1(source);

    expect(decoded.checkpointNarration).toBe("The corner is 3 × 3 = 9.");
    expect(decoded.baseViewports.cinematic).toEqual(pose());
    expect(decoded.resultViewports.compact).toEqual(
      pose({ x: 180, y: 80, width: 440, height: 440 }),
    );
    expect(decoded.transientFree).toBe(true);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.baseViewports)).toBe(true);
    expect(Object.isFrozen(decoded.baseViewports.cinematic)).toBe(true);
    expect(Object.isFrozen(decoded.resultViewports.compact)).toBe(true);
  });

  it.each([
    [pose({ x: 1, width: 800 }), "board width"],
    [pose({ y: 151, height: 450 }), "board height"],
    [pose({ width: 0 }), "positive"],
    [pose({ height: Number.POSITIVE_INFINITY }), "finite"],
    [pose({ x: -1 }), "non-negative"],
  ])("rejects an invalid viewport pose %#", (source, message) => {
    expect(() => decodeViewportPoseV1(source)).toThrow(message as string);
  });

  it("accepts viewport poses that touch every board edge", () => {
    expect(decodeViewportPoseV1(pose())).toEqual(pose());
    expect(
      decodeViewportPoseV1(pose({ x: 700, y: 500, width: 100, height: 100 })),
    ).toEqual(pose({ x: 700, y: 500, width: 100, height: 100 }));
  });

  it("rejects missing or open layouts and non-terminal checkpoints", () => {
    const missing = checkpoint();
    delete (missing.resultViewports as Record<string, unknown>).compact;
    expect(() => decodePresentationCheckpointV1(missing)).toThrow(
      "missing field compact",
    );

    const open = checkpoint();
    (open.baseViewports as Record<string, unknown>).tablet = pose();
    expect(() => decodePresentationCheckpointV1(open)).toThrow(
      "unknown field tablet",
    );

    const nonTerminal = checkpoint();
    nonTerminal.transientFree = false;
    expect(() => decodePresentationCheckpointV1(nonTerminal)).toThrow(
      "transientFree must equal true",
    );
  });

  it("enforces checkpoint identity, narration, and exact top-level fields", () => {
    const badId = checkpoint();
    badId.checkpointId = "#corner";
    expect(() => decodePresentationCheckpointV1(badId)).toThrow(
      "unsafe identifier",
    );

    const longNarration = checkpoint();
    longNarration.checkpointNarration = "😀".repeat(513);
    expect(
      protocolCode(() => decodePresentationCheckpointV1(longNarration)),
    ).toBe("budget_exceeded");

    const open = checkpoint();
    open.hash = "not-authenticated-here";
    expect(() => decodePresentationCheckpointV1(open)).toThrow(
      "unknown field hash",
    );
  });
});
