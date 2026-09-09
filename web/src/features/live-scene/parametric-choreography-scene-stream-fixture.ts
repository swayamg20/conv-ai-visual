import boundaryLowFixtureValue from "./fixtures/completing-square-parametric-b2-c80.v3.json";
import primaryFixtureValue from "./fixtures/completing-square-parametric-b8-c20.v3.json";
import boundaryHighFixtureValue from "./fixtures/completing-square-parametric-b16-c17.v3.json";

import {
  decodeParametricChoreographyRequestV3,
  type ParametricChoreographyRequestV3,
} from "@/lib/live-scene/parametric-choreography-request";

import { createFixtureSseResponse } from "./fixture-sse";
import {
  createParametricChoreographyFixtureCatalog,
  decodeParametricChoreographyFixtureMode,
  PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES,
  type ParametricChoreographyFixtureBatch,
  type ParametricChoreographyFixtureCatalog,
  type ParametricChoreographyFixtureMode,
} from "./parametric-choreography-fixture-catalog";
import {
  failParametricChoreographyFixture,
  ParametricChoreographyFixtureError,
} from "./parametric-choreography-fixture-schema";
import {
  consumeParametricChoreographySceneStreamResponse,
  type ParametricChoreographySceneStreamRunner,
} from "./parametric-choreography-model-stream";

export {
  PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES,
  ParametricChoreographyFixtureError,
};
export type {
  ParametricChoreographyFixtureBatch,
  ParametricChoreographyFixtureMode,
} from "./parametric-choreography-fixture-catalog";
export type { ParametricChoreographyFixtureErrorCode } from "./parametric-choreography-fixture-schema";

export interface ParametricChoreographyFixtureRunnerOptions {
  readonly mode?: ParametricChoreographyFixtureMode;
  readonly eventDelayMs?: number;
  readonly chunkDelayMs?: number;
  /** Injectable only so malformed-envelope behavior can be tested locally. */
  readonly fixtureValues?: readonly unknown[];
}

const DEFAULT_FIXTURE_VALUES = Object.freeze([
  boundaryLowFixtureValue,
  primaryFixtureValue,
  boundaryHighFixtureValue,
]);
const DEFAULT_CATALOG = createParametricChoreographyFixtureCatalog(
  DEFAULT_FIXTURE_VALUES,
);

function catalogFor(
  fixtureValues: readonly unknown[] | undefined,
): ParametricChoreographyFixtureCatalog {
  return fixtureValues
    ? createParametricChoreographyFixtureCatalog(fixtureValues)
    : DEFAULT_CATALOG;
}

function decodeRequest(value: unknown): ParametricChoreographyRequestV3 {
  try {
    return decodeParametricChoreographyRequestV3(value);
  } catch (error) {
    return failParametricChoreographyFixture(
      "request_mismatch",
      `request failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
}

function nonnegativeDelay(
  value: number | undefined,
  fallback: number,
  field: string,
): number {
  const delay = value ?? fallback;
  if (!Number.isFinite(delay) || delay < 0) {
    throw new TypeError(`${field} must be finite and nonnegative`);
  }
  return delay;
}

/** Select one exact provider-free fixture lane without opening a stream. */
export function createParametricChoreographyFixtureBatch(
  request: ParametricChoreographyRequestV3,
  options: Pick<
    ParametricChoreographyFixtureRunnerOptions,
    "mode" | "fixtureValues"
  > = {},
): ParametricChoreographyFixtureBatch {
  const mode = decodeParametricChoreographyFixtureMode(
    options.mode ?? PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES[0],
  );
  return catalogFor(options.fixtureValues).select(decodeRequest(request), mode);
}

/** Exercise the production V3 byte decoder with deterministic, provider-free SSE. */
export function createParametricChoreographyFixtureRunner(
  options: ParametricChoreographyFixtureRunnerOptions = {},
): ParametricChoreographySceneStreamRunner {
  const mode = decodeParametricChoreographyFixtureMode(
    options.mode ?? PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES[0],
  );
  const eventDelayMs = nonnegativeDelay(
    options.eventDelayMs,
    24,
    "eventDelayMs",
  );
  const chunkDelayMs = nonnegativeDelay(
    options.chunkDelayMs,
    1,
    "chunkDelayMs",
  );
  const catalog = catalogFor(options.fixtureValues);

  return async ({ request, signal, onEvent }) => {
    const batch = catalog.select(decodeRequest(request), mode);
    const response = createFixtureSseResponse(batch.events, signal, {
      eventDelayMs,
      chunkDelayMs,
      idPrefix: `parametric-fixture-${batch.fixtureId}-${batch.lane}`,
      holdOpenUntilAbort: batch.holdOpenUntilAbort,
    });
    await consumeParametricChoreographySceneStreamResponse(response, onEvent);
  };
}
