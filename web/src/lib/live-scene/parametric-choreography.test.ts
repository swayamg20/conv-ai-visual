import { describe, expect, it } from "vitest";

import {
  PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION,
  decodeParametricCompletingSquareStateV1,
  decodeRoutedChoreographyBeatV3,
} from "./parametric-choreography";

function problem(): Record<string, unknown> {
  return { v: 1, linearCoefficient: 8, rightHandSide: 20 };
}

function beat(): Record<string, unknown> {
  return {
    v: ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION,
    beatId: "beat-complete",
    componentKind: "completing_square_parametric",
    componentId: "lesson",
    problemSpec: problem(),
    route: { intent: "advance", targetStage: "complete" },
  };
}

function state(): Record<string, unknown> {
  return {
    kind: "completing_square_parametric",
    id: "lesson",
    problemSpec: problem(),
    lastMainCheckpoint: "missing_corner",
    cornerClarified: false,
  };
}

describe("parametric choreography V3 contracts", () => {
  it("decodes and deeply freezes both closed route variants", () => {
    const advance = decodeRoutedChoreographyBeatV3(beat());
    expect(advance.route).toEqual({
      intent: "advance",
      targetStage: "complete",
    });
    expect(Object.isFrozen(advance)).toBe(true);
    expect(Object.isFrozen(advance.route)).toBe(true);
    expect(Object.isFrozen(advance.problemSpec)).toBe(true);

    const clarification = beat();
    clarification.route = { intent: "clarify_corner" };
    expect(decodeRoutedChoreographyBeatV3(clarification).route).toEqual({
      intent: "clarify_corner",
    });
  });

  it.each([
    ["V2 version", "v", 2],
    ["coerced version", "v", "3"],
    ["V2 component kind", "componentKind", "completing_square"],
    ["unsafe beat id", "beatId", "1beat"],
    ["long component id", "componentId", "c".repeat(33)],
  ])("rejects %s", (_label, field, value) => {
    const source = beat();
    source[field] = value;
    expect(() => decodeRoutedChoreographyBeatV3(source)).toThrow();
  });

  it("rejects compiler-owned fields and V2/V3 beat splices", () => {
    expect(() =>
      decodeRoutedChoreographyBeatV3({ ...beat(), durationMs: 400 }),
    ).toThrow(/unknown field durationMs/);

    const missingProblem = beat();
    delete missingProblem.problemSpec;
    expect(() => decodeRoutedChoreographyBeatV3(missingProblem)).toThrow(
      /missing field problemSpec/,
    );

    const v2Beat = beat();
    v2Beat.v = 2;
    v2Beat.componentKind = "completing_square";
    delete v2Beat.problemSpec;
    expect(() => decodeRoutedChoreographyBeatV3(v2Beat)).toThrow();
  });

  it("enforces exact route discriminators", () => {
    for (const route of [
      { intent: "advance" },
      { intent: "advance", targetStage: "later" },
      { intent: "clarify_corner", targetStage: "complete" },
      { intent: "draw_anything" },
    ]) {
      expect(() => decodeRoutedChoreographyBeatV3({ ...beat(), route })).toThrow();
    }
  });

  it.each(PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS)(
    "decodes the %s V3 frontier",
    (lastMainCheckpoint) => {
      const decoded = decodeParametricCompletingSquareStateV1({
        ...state(),
        lastMainCheckpoint,
        cornerClarified:
          PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
            lastMainCheckpoint,
          ) >=
          PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
            "missing_corner",
          ),
      });
      expect(decoded.lastMainCheckpoint).toBe(lastMainCheckpoint);
      expect(Object.isFrozen(decoded)).toBe(true);
      expect(Object.isFrozen(decoded.problemSpec)).toBe(true);
    },
  );

  it("rejects non-boolean clarification, premature clarification, and V2 state", () => {
    expect(() =>
      decodeParametricCompletingSquareStateV1({
        ...state(),
        cornerClarified: 1,
      }),
    ).toThrow(/must be a boolean/);
    expect(() =>
      decodeParametricCompletingSquareStateV1({
        ...state(),
        lastMainCheckpoint: "area_model",
        cornerClarified: true,
      }),
    ).toThrow(/requires the missing_corner frontier/);
    expect(() =>
      decodeParametricCompletingSquareStateV1({
        ...state(),
        kind: "completing_square",
      }),
    ).toThrow(/kind must equal completing_square_parametric/);
  });
});
