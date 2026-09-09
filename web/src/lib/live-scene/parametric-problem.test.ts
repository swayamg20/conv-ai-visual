import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import {
  decodeCompletingSquareProblemSpecV1,
  deriveCompletingSquareProblemValues,
  sameCompletingSquareProblem,
} from "./parametric-problem";

function problem(
  linearCoefficient = 8,
  rightHandSide = 20,
): Record<string, unknown> {
  return { v: 1, linearCoefficient, rightHandSide };
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("parametric completing-square problem decoder", () => {
  it("accepts exactly the 36 supported problems and derives every value", () => {
    let count = 0;
    for (let half = 1; half <= 8; half += 1) {
      for (let magnitude = half + 1; magnitude <= 9; magnitude += 1) {
        const rightHandSide = magnitude ** 2 - half ** 2;
        const decoded = decodeCompletingSquareProblemSpecV1(
          problem(2 * half, rightHandSide),
        );
        expect(deriveCompletingSquareProblemValues(decoded)).toEqual({
          halfCoefficient: half,
          cornerValue: half ** 2,
          completedRightHandSide: magnitude ** 2,
          squareRootMagnitude: magnitude,
          positiveRoot: magnitude - half,
          negativeRoot: -(magnitude + half),
        });
        expect(Object.isFrozen(decoded)).toBe(true);
        count += 1;
      }
    }
    expect(count).toBe(36);
  });

  it.each([
    ["V2 problem version", problem(8, 20), "v", 2],
    ["coerced version", problem(8, 20), "v", "1"],
    ["coerced coefficient", problem(8, 20), "linearCoefficient", "8"],
    ["fractional coefficient", problem(8, 20), "linearCoefficient", 8.5],
    ["odd coefficient", problem(8, 20), "linearCoefficient", 7],
    ["coefficient below family", problem(8, 20), "linearCoefficient", 0],
    ["coefficient above family", problem(8, 20), "linearCoefficient", 18],
    ["right side below family", problem(8, 20), "rightHandSide", 0],
    ["right side above family", problem(8, 20), "rightHandSide", 81],
    ["non-square completion", problem(8, 20), "rightHandSide", 19],
    ["root does not exceed h", problem(8, 20), "rightHandSide", 1],
    ["completed root above nine", problem(2, 80), "rightHandSide", 81],
  ])("rejects %s", (_label, source, field, value) => {
    source[field] = value;
    expect(protocolCode(() => decodeCompletingSquareProblemSpecV1(source))).toBe(
      "invalid_event",
    );
  });

  it("rejects missing, unknown, array, and class-instance inputs", () => {
    const missing = problem();
    delete missing.rightHandSide;
    expect(() => decodeCompletingSquareProblemSpecV1(missing)).toThrow(
      /missing field rightHandSide/,
    );

    expect(() =>
      decodeCompletingSquareProblemSpecV1({ ...problem(), derivedRoot: 6 }),
    ).toThrow(/unknown field derivedRoot/);
    expect(() => decodeCompletingSquareProblemSpecV1([])).toThrow(/object/);

    class Problem {
      v = 1;
      linearCoefficient = 8;
      rightHandSide = 20;
    }
    expect(() => decodeCompletingSquareProblemSpecV1(new Problem())).toThrow(
      /plain object/,
    );
  });

  it("compares only validated problem identity fields", () => {
    const first = decodeCompletingSquareProblemSpecV1(problem());
    const same = decodeCompletingSquareProblemSpecV1(problem());
    const other = decodeCompletingSquareProblemSpecV1(problem(6, 7));
    expect(sameCompletingSquareProblem(first, same)).toBe(true);
    expect(sameCompletingSquareProblem(first, other)).toBe(false);
  });
});
