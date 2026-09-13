import { isDeepStrictEqual } from "node:util";

const CLOCK = "browser_performance_now";
const THRESHOLD_MS = 150;
const TRIALS = 4;
const ACCELERATED_SURFACES = Object.freeze([
  "provider_wait",
  "draw",
  "trace_path",
  "marker_movement",
  "relationship_morph",
  "camera_focus",
  "hold",
  "post_paint_barrier",
  "replay",
]);
const ALL_SURFACES = Object.freeze([...ACCELERATED_SURFACES, "partial_record"]);

function fail(location, message) {
  throw new Error(`${location}: ${message}`);
}

function object(value, location) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    fail(location, "must be a plain object");
  }
  return value;
}

function exactKeys(value, keys, location) {
  const record = object(value, location);
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    fail(location, `must contain exactly keys ${expected.join(", ")}`);
  }
  return record;
}

function array(value, length, location) {
  if (!Array.isArray(value) || value.length !== length) {
    fail(location, `must contain exactly ${length} entries`);
  }
  return value;
}

function number(value, location, { integer = false } = {}) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    (integer && !Number.isInteger(value))
  ) {
    fail(location, "must be a non-negative finite number");
  }
  return value;
}

function exact(value, expected, location) {
  if (!isDeepStrictEqual(value, expected)) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function roundedEqual(value, expected, location) {
  number(value, location);
  if (Math.abs(value - expected) > 0.001_001) {
    fail(location, `must equal ${expected} within browser timer precision`);
  }
}

function root(value, location) {
  const evidence = exactKeys(
    value,
    ["clock", "results", "schemaVersion", "thresholdMs", "unavailableSurfaces"],
    location,
  );
  exact(evidence.schemaVersion, 1, `${location}.schemaVersion`);
  exact(evidence.clock, CLOCK, `${location}.clock`);
  exact(evidence.thresholdMs, THRESHOLD_MS, `${location}.thresholdMs`);
  exact(evidence.unavailableSurfaces, [], `${location}.unavailableSurfaces`);
  return evidence;
}

function latencySummary(value, surface, location) {
  const result = exactKeys(
    value,
    ["latenciesMs", "maxMs", "p95Ms", "surface", "trials"],
    location,
  );
  exact(result.surface, surface, `${location}.surface`);
  exact(result.trials, TRIALS, `${location}.trials`);
  const latenciesMs = array(
    result.latenciesMs,
    TRIALS,
    `${location}.latenciesMs`,
  ).map((entry, index) => number(entry, `${location}.latenciesMs[${index}]`));
  const maximum = Math.max(...latenciesMs);
  exact(result.p95Ms, maximum, `${location}.p95Ms`);
  exact(result.maxMs, maximum, `${location}.maxMs`);
  if (maximum >= THRESHOLD_MS) {
    fail(location, `must remain below ${THRESHOLD_MS}ms`);
  }
  return Object.freeze({
    surface,
    trials: TRIALS,
    latenciesMs: Object.freeze(latenciesMs),
    p95Ms: maximum,
    maxMs: maximum,
  });
}

export function validateAcceleratedInterruptionEvidence(
  value,
  location = "accelerated interruption evidence",
) {
  const evidence = root(value, location);
  const results = array(
    evidence.results,
    ACCELERATED_SURFACES.length,
    `${location}.results`,
  ).map((entry, index) =>
    latencySummary(
      entry,
      ACCELERATED_SURFACES[index],
      `${location}.results[${index}]`,
    ),
  );
  return Object.freeze({
    schemaVersion: 1,
    clock: CLOCK,
    thresholdMs: THRESHOLD_MS,
    results: Object.freeze(results),
    unavailableSurfaces: Object.freeze([]),
  });
}

export function validatePartialRecordInterruptionEvidence(
  value,
  location = "partial-record interruption evidence",
) {
  const evidence = root(value, location);
  const [rawResult] = array(evidence.results, 1, `${location}.results`);
  const result = exactKeys(
    rawResult,
    [
      "abortCount",
      "latenciesMs",
      "maxMs",
      "observations",
      "p95Ms",
      "partialChunkCount",
      "requestCount",
      "surface",
      "trials",
    ],
    `${location}.results[0]`,
  );
  exact(result.surface, "partial_record", `${location}.results[0].surface`);
  exact(result.trials, TRIALS, `${location}.results[0].trials`);
  exact(result.abortCount, TRIALS, `${location}.results[0].abortCount`);
  exact(
    result.partialChunkCount,
    TRIALS,
    `${location}.results[0].partialChunkCount`,
  );
  exact(result.requestCount, TRIALS * 2, `${location}.results[0].requestCount`);
  const observations = array(
    result.observations,
    TRIALS,
    `${location}.results[0].observations`,
  ).map((entry, index) => {
    const observation = exactKeys(
      entry,
      ["latencyMs", "requestedAtMs", "settledAtMs", "trial"],
      `${location}.results[0].observations[${index}]`,
    );
    exact(
      observation.trial,
      index + 1,
      `${location}.results[0].observations[${index}].trial`,
    );
    number(
      observation.requestedAtMs,
      `${location}.results[0].observations[${index}].requestedAtMs`,
    );
    number(
      observation.settledAtMs,
      `${location}.results[0].observations[${index}].settledAtMs`,
    );
    number(
      observation.latencyMs,
      `${location}.results[0].observations[${index}].latencyMs`,
    );
    roundedEqual(
      observation.latencyMs,
      Number((observation.settledAtMs - observation.requestedAtMs).toFixed(3)),
      `${location}.results[0].observations[${index}].latencyMs`,
    );
    if (
      index > 0 &&
      observation.requestedAtMs < result.observations[index - 1].requestedAtMs
    ) {
      fail(
        `${location}.results[0].observations[${index}].requestedAtMs`,
        "must be monotonic",
      );
    }
    return Object.freeze({ ...observation });
  });
  const latenciesMs = array(
    result.latenciesMs,
    TRIALS,
    `${location}.results[0].latenciesMs`,
  ).map((entry, index) =>
    number(entry, `${location}.results[0].latenciesMs[${index}]`),
  );
  exact(
    latenciesMs,
    observations.map(({ latencyMs }) => latencyMs),
    `${location}.results[0].latenciesMs`,
  );
  const maximum = Math.max(...latenciesMs);
  exact(result.p95Ms, maximum, `${location}.results[0].p95Ms`);
  exact(result.maxMs, maximum, `${location}.results[0].maxMs`);
  if (maximum >= THRESHOLD_MS) {
    fail(`${location}.results[0]`, `must remain below ${THRESHOLD_MS}ms`);
  }
  return Object.freeze({
    schemaVersion: 1,
    clock: CLOCK,
    thresholdMs: THRESHOLD_MS,
    results: Object.freeze([
      Object.freeze({
        surface: "partial_record",
        trials: TRIALS,
        observations: Object.freeze(observations),
        latenciesMs: Object.freeze(latenciesMs),
        p95Ms: maximum,
        maxMs: maximum,
        abortCount: TRIALS,
        requestCount: TRIALS * 2,
        partialChunkCount: TRIALS,
      }),
    ]),
    unavailableSurfaces: Object.freeze([]),
  });
}

export function combineInterruptionEvidence(accelerated, partial) {
  const results = [...accelerated.results, ...partial.results];
  exact(
    results.map(({ surface }) => surface),
    ALL_SURFACES,
    "combined interruption surfaces",
  );
  exact(accelerated.clock, partial.clock, "combined interruption clock");
  exact(
    accelerated.thresholdMs,
    partial.thresholdMs,
    "combined interruption threshold",
  );
  return Object.freeze({
    schemaVersion: 1,
    clock: CLOCK,
    thresholdMs: THRESHOLD_MS,
    results: Object.freeze(results),
    unavailableSurfaces: Object.freeze([]),
  });
}
