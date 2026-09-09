import { LiveSceneProtocolError } from "./patch";

export const COMPLETING_SQUARE_PROBLEM_VERSION = 1 as const;

export interface CompletingSquareProblemSpecV1 {
  readonly v: typeof COMPLETING_SQUARE_PROBLEM_VERSION;
  readonly linearCoefficient: number;
  readonly rightHandSide: number;
}

export interface CompletingSquareProblemValues {
  readonly halfCoefficient: number;
  readonly cornerValue: number;
  readonly completedRightHandSide: number;
  readonly squareRootMagnitude: number;
  readonly positiveRoot: number;
  readonly negativeRoot: number;
}

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `parametric problem ${message}`,
  );
}

function record(value: unknown): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("must be an object");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail("must be a plain object");
  }
  return value as UnknownRecord;
}

function exactKeys(value: UnknownRecord): void {
  const keys = ["v", "linearCoefficient", "rightHandSide"] as const;
  const allowed = new Set<string>(keys);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`contains unknown field ${key}`);
  }
  for (const key of keys) {
    if (!Object.hasOwn(value, key)) fail(`is missing field ${key}`);
  }
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    fail(`${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

/** Decode exactly one member of the bounded 36-equation Gate 1.6 family. */
export function decodeCompletingSquareProblemSpecV1(
  value: unknown,
): CompletingSquareProblemSpecV1 {
  const input = record(value);
  exactKeys(input);
  if (input.v !== COMPLETING_SQUARE_PROBLEM_VERSION) {
    fail(`v must equal ${COMPLETING_SQUARE_PROBLEM_VERSION}`);
  }

  const linearCoefficient = integer(
    input.linearCoefficient,
    "linearCoefficient",
    2,
    16,
  );
  if (linearCoefficient % 2 !== 0) {
    fail("linearCoefficient must be even");
  }
  const rightHandSide = integer(input.rightHandSide, "rightHandSide", 1, 80);
  const halfCoefficient = linearCoefficient / 2;
  const completed = rightHandSide + halfCoefficient ** 2;
  const magnitude = Math.sqrt(completed);
  if (!Number.isInteger(magnitude) || magnitude <= halfCoefficient || magnitude > 9) {
    fail("must complete to an integer square with h < m <= 9");
  }

  return Object.freeze({
    v: COMPLETING_SQUARE_PROBLEM_VERSION,
    linearCoefficient,
    rightHandSide,
  });
}

/** Derive display values locally from the two accepted coefficients. */
export function deriveCompletingSquareProblemValues(
  problemValue: CompletingSquareProblemSpecV1,
): CompletingSquareProblemValues {
  const problem = decodeCompletingSquareProblemSpecV1(problemValue);
  const halfCoefficient = problem.linearCoefficient / 2;
  const cornerValue = halfCoefficient ** 2;
  const completedRightHandSide = problem.rightHandSide + cornerValue;
  const squareRootMagnitude = Math.sqrt(completedRightHandSide);
  return Object.freeze({
    halfCoefficient,
    cornerValue,
    completedRightHandSide,
    squareRootMagnitude,
    positiveRoot: squareRootMagnitude - halfCoefficient,
    negativeRoot: -(squareRootMagnitude + halfCoefficient),
  });
}

export function sameCompletingSquareProblem(
  left: CompletingSquareProblemSpecV1,
  right: CompletingSquareProblemSpecV1,
): boolean {
  return (
    left.v === right.v &&
    left.linearCoefficient === right.linearCoefficient &&
    left.rightHandSide === right.rightHandSide
  );
}
