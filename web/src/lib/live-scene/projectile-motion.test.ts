import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import {
  PROJECTILE_MOTION_CHECKPOINT_IDS,
  PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
  PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  PROJECTILE_MOTION_PROBLEM_VERSION,
  PROJECTILE_MOTION_ROUTED_BEAT_VERSION,
  PROJECTILE_MOTION_STAGES,
  SUPPORTED_PROJECTILE_ANGLES_DEG,
  SUPPORTED_PROJECTILE_SPEEDS_MPS,
  decodeProjectileMotionProblemSpecV1,
  decodeProjectileMotionRouteV1,
  decodeProjectileMotionStateV1,
  decodeRoutedProjectileMotionBeatV1,
  nextProjectileMotionMainCheckpoint,
  projectileMotionCheckpointPrefix,
  projectileMotionCheckpointsThrough,
  sameProjectileMotionProblem,
} from "./projectile-motion";

function problem(speedMps = 20, angleDeg = 45): Record<string, unknown> {
  return { v: PROJECTILE_MOTION_PROBLEM_VERSION, speedMps, angleDeg };
}

function state(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    kind: "projectile_motion",
    id: "projectile",
    problemSpec: problem(),
    lastMainCheckpoint: "apex_state",
    clarifiedTopics: [],
    activeClarification: null,
    ...overrides,
  };
}

function beat(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    v: PROJECTILE_MOTION_ROUTED_BEAT_VERSION,
    beatId: "projectile-beat-1",
    componentKind: "projectile_motion",
    componentId: "projectile",
    baseProblemSpec: problem(),
    resultProblemSpec: problem(),
    route: { intent: "advance", targetStage: "flight" },
    ...overrides,
  };
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("projectile-motion problem and frontier contracts", () => {
  it("accepts exactly the nine qualified speed and angle pairs", () => {
    const accepted = SUPPORTED_PROJECTILE_SPEEDS_MPS.flatMap((speedMps) =>
      SUPPORTED_PROJECTILE_ANGLES_DEG.map((angleDeg) =>
        decodeProjectileMotionProblemSpecV1(problem(speedMps, angleDeg)),
      ),
    );

    expect(accepted).toHaveLength(9);
    expect(new Set(accepted.map(({ speedMps }) => speedMps))).toEqual(
      new Set([20, 25, 30]),
    );
    expect(new Set(accepted.map(({ angleDeg }) => angleDeg))).toEqual(
      new Set([30, 45, 60]),
    );
    accepted.forEach((decoded) => expect(Object.isFrozen(decoded)).toBe(true));
  });

  it.each([
    ["future version", "v", 2],
    ["coerced version", "v", "1"],
    ["boolean version", "v", true],
    ["coerced speed", "speedMps", "20"],
    ["fractional speed", "speedMps", 20.5],
    ["boolean speed", "speedMps", true],
    ["unsupported lower speed", "speedMps", 19],
    ["unsupported upper speed", "speedMps", 35],
    ["coerced angle", "angleDeg", "45"],
    ["fractional angle", "angleDeg", 45.5],
    ["boolean angle", "angleDeg", false],
    ["unsupported neighboring angle", "angleDeg", 50],
  ])("rejects %s", (_label, field, value) => {
    const source = problem();
    source[field] = value;
    expect(protocolCode(() => decodeProjectileMotionProblemSpecV1(source))).toBe(
      "invalid_event",
    );
  });

  it.each(["gravity", "launchHeight", "wind", "drag"])(
    "rejects client-supplied physics field %s",
    (field) => {
      expect(() =>
        decodeProjectileMotionProblemSpecV1({ ...problem(), [field]: 0 }),
      ).toThrow(new RegExp(`unknown field ${field}`));
    },
  );

  it("rejects missing, array, and class-instance problems", () => {
    const missing = problem();
    delete missing.angleDeg;
    expect(() => decodeProjectileMotionProblemSpecV1(missing)).toThrow(
      /missing field angleDeg/,
    );
    expect(() => decodeProjectileMotionProblemSpecV1([])).toThrow(/object/);

    class Problem {
      v = 1;
      speedMps = 20;
      angleDeg = 45;
    }
    expect(() => decodeProjectileMotionProblemSpecV1(new Problem())).toThrow(
      /plain object/,
    );
  });

  it("compares only the three accepted problem identity fields", () => {
    const first = decodeProjectileMotionProblemSpecV1(problem());
    const same = decodeProjectileMotionProblemSpecV1(problem());
    const other = decodeProjectileMotionProblemSpecV1(problem(20, 60));
    expect(sameProjectileMotionProblem(first, same)).toBe(true);
    expect(sameProjectileMotionProblem(first, other)).toBe(false);
  });

  it("exposes the exact six-checkpoint, four-stage progression", () => {
    expect(PROJECTILE_MOTION_MAIN_CHECKPOINTS).toEqual([
      "setup",
      "decompose_velocity",
      "trace_ascent",
      "apex_state",
      "trace_descent",
      "summary",
    ]);
    expect(PROJECTILE_MOTION_STAGES).toEqual([
      "setup",
      "launch",
      "flight",
      "solve",
    ]);
    expect(projectileMotionCheckpointsThrough("setup")).toEqual(["setup"]);
    expect(projectileMotionCheckpointsThrough("launch")).toEqual([
      "setup",
      "decompose_velocity",
    ]);
    expect(projectileMotionCheckpointsThrough("flight")).toEqual(
      PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, 5),
    );
    expect(projectileMotionCheckpointsThrough("solve")).toEqual(
      PROJECTILE_MOTION_MAIN_CHECKPOINTS,
    );
    expect(PROJECTILE_MOTION_MAIN_CHECKPOINTS.reduce<(string | null)[]>(
      (next, checkpoint) => [
        ...next,
        nextProjectileMotionMainCheckpoint(checkpoint),
      ],
      [nextProjectileMotionMainCheckpoint(null)],
    )).toEqual([...PROJECTILE_MOTION_MAIN_CHECKPOINTS, null]);
    expect(projectileMotionCheckpointPrefix("apex_state")).toEqual(
      PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(0, 4),
    );
  });

  it("locks the three clarification prerequisites and sidecar identities", () => {
    expect(PROJECTILE_MOTION_CLARIFICATION_TOPICS).toEqual([
      "horizontal_velocity",
      "apex_acceleration",
      "flight_symmetry",
    ]);
    expect(PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES).toEqual({
      horizontal_velocity: "decompose_velocity",
      apex_acceleration: "apex_state",
      flight_symmetry: "trace_descent",
    });
    expect(PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS).toEqual({
      horizontal_velocity: "horizontal_velocity_detail",
      apex_acceleration: "apex_acceleration_detail",
      flight_symmetry: "flight_symmetry_detail",
    });
    expect(PROJECTILE_MOTION_CHECKPOINT_IDS).toHaveLength(10);
  });

  it.each([null, ...PROJECTILE_MOTION_MAIN_CHECKPOINTS])(
    "decodes and deeply freezes the %s semantic frontier",
    (lastMainCheckpoint) => {
      const decoded = decodeProjectileMotionStateV1(
        state({ lastMainCheckpoint }),
      );
      expect(decoded.lastMainCheckpoint).toBe(lastMainCheckpoint);
      expect(Object.isFrozen(decoded)).toBe(true);
      expect(Object.isFrozen(decoded.problemSpec)).toBe(true);
      expect(Object.isFrozen(decoded.clarifiedTopics)).toBe(true);
    },
  );

  it("accepts canonical one-shot clarification history with one active detail", () => {
    const decoded = decodeProjectileMotionStateV1(
      state({
        lastMainCheckpoint: "summary",
        clarifiedTopics: ["horizontal_velocity", "flight_symmetry"],
        activeClarification: "flight_symmetry",
      }),
    );
    expect(decoded.clarifiedTopics).toEqual([
      "horizontal_velocity",
      "flight_symmetry",
    ]);
    expect(decoded.activeClarification).toBe("flight_symmetry");
  });

  it.each([
    [
      "duplicates",
      { clarifiedTopics: ["horizontal_velocity", "horizontal_velocity"] },
      /must be unique/,
    ],
    [
      "noncanonical order",
      { clarifiedTopics: ["apex_acceleration", "horizontal_velocity"] },
      /canonical pedagogical order/,
    ],
    [
      "premature horizontal detail",
      {
        lastMainCheckpoint: "setup",
        clarifiedTopics: ["horizontal_velocity"],
      },
      /premature/,
    ],
    [
      "premature apex detail",
      {
        lastMainCheckpoint: "trace_ascent",
        clarifiedTopics: ["apex_acceleration"],
      },
      /premature/,
    ],
    [
      "premature symmetry detail",
      {
        lastMainCheckpoint: "apex_state",
        clarifiedTopics: ["flight_symmetry"],
      },
      /premature/,
    ],
    [
      "inactive ledger topic",
      { activeClarification: "apex_acceleration" },
      /must be present in clarifiedTopics/,
    ],
  ])("rejects clarification state with %s", (_label, overrides, message) => {
    expect(() => decodeProjectileMotionStateV1(state(overrides))).toThrow(
      message,
    );
  });

  it("rejects open, coerced, cross-domain, and unsafe semantic state", () => {
    expect(() =>
      decodeProjectileMotionStateV1(state({ clarifiedTopics: "none" })),
    ).toThrow(/must be an array/);
    expect(() =>
      decodeProjectileMotionStateV1(
        state({ clarifiedTopics: [], activeClarification: "other" }),
      ),
    ).toThrow(/unsupported value/);
    expect(() =>
      decodeProjectileMotionStateV1(state({ kind: "completing_square" })),
    ).toThrow(/kind must equal projectile_motion/);
    expect(() =>
      decodeProjectileMotionStateV1(state({ id: "1projectile" })),
    ).toThrow(/unsafe identifier/);
    expect(() =>
      decodeProjectileMotionStateV1({ ...state(), serverCache: {} }),
    ).toThrow(/unknown field serverCache/);
  });
});

describe("projectile-motion route and routed-beat contracts", () => {
  it("decodes and deeply freezes all three route variants", () => {
    const advance = decodeProjectileMotionRouteV1({
      intent: "advance",
      targetStage: "flight",
    });
    const clarify = decodeProjectileMotionRouteV1({
      intent: "clarify",
      topic: "apex_acceleration",
    });
    const retarget = decodeProjectileMotionRouteV1({
      intent: "retarget",
      targetProblemSpec: problem(30, 60),
    });

    expect(advance).toEqual({ intent: "advance", targetStage: "flight" });
    expect(clarify).toEqual({
      intent: "clarify",
      topic: "apex_acceleration",
    });
    expect(retarget).toEqual({
      intent: "retarget",
      targetProblemSpec: problem(30, 60),
    });
    expect(Object.isFrozen(advance)).toBe(true);
    expect(Object.isFrozen(clarify)).toBe(true);
    expect(Object.isFrozen(retarget)).toBe(true);
    if (retarget.intent !== "retarget") throw new Error("expected retarget");
    expect(Object.isFrozen(retarget.targetProblemSpec)).toBe(true);
  });

  it.each([
    { intent: "advance" },
    { intent: "advance", targetStage: "later" },
    { intent: "advance", targetStage: "flight", topic: "flight_symmetry" },
    { intent: "clarify" },
    { intent: "clarify", topic: "other" },
    { intent: "retarget" },
    { intent: "retarget", targetProblemSpec: problem(40, 45) },
    { intent: "animate", targetStage: "solve" },
  ])("rejects open or malformed route %#", (route) => {
    expect(() => decodeProjectileMotionRouteV1(route)).toThrow();
  });

  it("decodes fresh, continuing, clarification, and retarget beats", () => {
    const fresh = decodeRoutedProjectileMotionBeatV1(
      beat({ baseProblemSpec: null }),
    );
    const continuing = decodeRoutedProjectileMotionBeatV1(beat());
    const clarification = decodeRoutedProjectileMotionBeatV1(
      beat({
        route: { intent: "clarify", topic: "apex_acceleration" },
      }),
    );
    const retarget = decodeRoutedProjectileMotionBeatV1(
      beat({
        resultProblemSpec: problem(20, 60),
        route: {
          intent: "retarget",
          targetProblemSpec: problem(20, 60),
        },
      }),
    );

    expect(fresh.baseProblemSpec).toBeNull();
    expect(continuing.route.intent).toBe("advance");
    expect(clarification.route.intent).toBe("clarify");
    expect(retarget.route.intent).toBe("retarget");
    expect(Object.isFrozen(retarget)).toBe(true);
    expect(Object.isFrozen(retarget.resultProblemSpec)).toBe(true);
    expect(Object.isFrozen(retarget.route)).toBe(true);
  });

  it.each([
    [
      "fresh clarification",
      { baseProblemSpec: null, route: { intent: "clarify", topic: "apex_acceleration" } },
      /only a fresh advance/,
    ],
    [
      "fresh retarget",
      {
        baseProblemSpec: null,
        resultProblemSpec: problem(30, 60),
        route: { intent: "retarget", targetProblemSpec: problem(30, 60) },
      },
      /only a fresh advance/,
    ],
    [
      "advance problem splice",
      { resultProblemSpec: problem(20, 60) },
      /must preserve/,
    ],
    [
      "clarification problem splice",
      {
        resultProblemSpec: problem(20, 60),
        route: { intent: "clarify", topic: "apex_acceleration" },
      },
      /must preserve/,
    ],
    [
      "same-problem retarget",
      {
        route: { intent: "retarget", targetProblemSpec: problem() },
      },
      /must change/,
    ],
    [
      "retarget result mismatch",
      {
        resultProblemSpec: problem(30, 45),
        route: { intent: "retarget", targetProblemSpec: problem(20, 60) },
      },
      /must match/,
    ],
  ])("rejects %s", (_label, overrides, message) => {
    expect(() => decodeRoutedProjectileMotionBeatV1(beat(overrides))).toThrow(
      message,
    );
  });

  it("rejects version, identity, compiler fields, and missing transition fields", () => {
    expect(() =>
      decodeRoutedProjectileMotionBeatV1(beat({ v: "1" })),
    ).toThrow(/v must equal/);
    expect(() =>
      decodeRoutedProjectileMotionBeatV1(
        beat({ componentKind: "completing_square_parametric" }),
      ),
    ).toThrow(/componentKind/);
    expect(() =>
      decodeRoutedProjectileMotionBeatV1(beat({ beatId: "1beat" })),
    ).toThrow(/unsafe identifier/);
    expect(() =>
      decodeRoutedProjectileMotionBeatV1(beat({ componentId: "c".repeat(33) })),
    ).toThrow(/unsafe identifier/);
    expect(() =>
      decodeRoutedProjectileMotionBeatV1({ ...beat(), durationMs: 1_000 }),
    ).toThrow(/unknown field durationMs/);
    const missing = beat();
    delete missing.baseProblemSpec;
    expect(() => decodeRoutedProjectileMotionBeatV1(missing)).toThrow(
      /missing field baseProblemSpec/,
    );
  });
});
