import unequalFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a45.v1.json";
import complementaryFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a60.v1.json";
import comparisonFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a45-a60.v1.json";

import {
  decodeSemanticStoryboardRequestV1,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";

import { createFixtureSseResponse } from "./fixture-sse";
import {
  createSemanticStoryboardFixtureCatalog,
  type SemanticStoryboardFixtureBatch,
  type SemanticStoryboardFixtureCatalog,
} from "./semantic-storyboard-fixture-catalog";
import {
  failSemanticStoryboardFixture,
  SemanticStoryboardFixtureError,
} from "./semantic-storyboard-fixture-schema";
import {
  consumeSemanticStoryboardSceneStreamResponse,
  type SemanticStoryboardSceneStreamRunner,
} from "./semantic-storyboard-model-stream";

export { SemanticStoryboardFixtureError };
export type { SemanticStoryboardFixtureBatch } from "./semantic-storyboard-fixture-catalog";
export type { SemanticStoryboardFixtureErrorCode } from "./semantic-storyboard-fixture-schema";

export interface SemanticStoryboardFixtureRunnerOptions {
  readonly eventDelayMs?: number;
  readonly chunkDelayMs?: number;
  /** Injectable only for strict malformed-envelope tests. */
  readonly fixtureValues?: readonly unknown[];
}

const DEFAULT_FIXTURE_VALUES = Object.freeze([
  unequalFixtureValue,
  complementaryFixtureValue,
  comparisonFixtureValue,
]);
const DEFAULT_CATALOG = createSemanticStoryboardFixtureCatalog(
  DEFAULT_FIXTURE_VALUES,
);

function catalogFor(
  fixtureValues: readonly unknown[] | undefined,
): SemanticStoryboardFixtureCatalog {
  return fixtureValues
    ? createSemanticStoryboardFixtureCatalog(fixtureValues)
    : DEFAULT_CATALOG;
}

function decodeRequest(value: unknown): SemanticStoryboardRequestV1 {
  try {
    return decodeSemanticStoryboardRequestV1(value);
  } catch (error) {
    return failSemanticStoryboardFixture(
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

/** Select one exact generated story without hard-coding a semantic beat order. */
export function createSemanticStoryboardFixtureBatch(
  requestValue: SemanticStoryboardRequestV1,
  options: Pick<SemanticStoryboardFixtureRunnerOptions, "fixtureValues"> = {},
): SemanticStoryboardFixtureBatch {
  return catalogFor(options.fixtureValues).select(decodeRequest(requestValue));
}

/** Exercise the production strict SSE decoder through an abortable local stream. */
export function createSemanticStoryboardFixtureRunner(
  options: SemanticStoryboardFixtureRunnerOptions = {},
): SemanticStoryboardSceneStreamRunner {
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
    const batch = catalog.select(decodeRequest(request));
    const response = createFixtureSseResponse(batch.events, signal, {
      eventDelayMs,
      chunkDelayMs,
      idPrefix: `semantic-storyboard-fixture-${batch.fixtureId}-${batch.scenarioId}`,
      holdOpenUntilAbort: batch.holdOpenUntilAbort,
    });
    await consumeSemanticStoryboardSceneStreamResponse(response, onEvent);
  };
}
