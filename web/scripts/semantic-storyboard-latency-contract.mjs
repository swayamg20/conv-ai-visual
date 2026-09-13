import { isDeepStrictEqual } from "node:util";

const CLOCK = "browser_performance_now";
const FRESH_CONTEXT_COUNT = 20;
const ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=reduced&speed=accelerated&proof=none";
const BOUNDARIES = Object.freeze({
  submitToAnchorVisible:
    "button submit -> firstCuePresented-backed anchor stage publication",
  directorDispatchToFirstModelVisible:
    "fixture Director invocation -> firstCuePresented-backed model stage publication",
  firstModelCheckpointEventToVisible:
    "first immediately-presentable model checkpoint callback -> firstCuePresented-backed matching stage publication",
  postPaintAcceptanceToNextBeatVisible:
    "accepted checkpoint subscriber publication after executor settlement -> next firstCuePresented-backed stage publication",
});
const THRESHOLDS_MS = Object.freeze({
  submitToAnchorVisibleP95: 300,
  directorDispatchToFirstModelVisibleP95: 2_000,
  firstModelCheckpointEventToVisibleP95: 250,
  postPaintAcceptanceToNextBeatVisibleP95: 1_000,
});
const METRICS = Object.freeze([
  ["submitToAnchorVisible", "submitToAnchorVisibleP95"],
  [
    "directorDispatchToFirstModelVisible",
    "directorDispatchToFirstModelVisibleP95",
  ],
  [
    "firstModelCheckpointEventToVisible",
    "firstModelCheckpointEventToVisibleP95",
  ],
  [
    "postPaintAcceptanceToNextBeatVisible",
    "postPaintAcceptanceToNextBeatVisibleP95",
  ],
]);

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

function exact(value, expected, location) {
  if (!isDeepStrictEqual(value, expected)) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function array(value, location, length) {
  if (!Array.isArray(value)) fail(location, "must be an array");
  if (length !== undefined && value.length !== length) {
    fail(location, `must contain exactly ${length} entries`);
  }
  return value;
}

function number(value, location, { integer = false } = {}) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > Number.MAX_SAFE_INTEGER ||
    (integer && !Number.isInteger(value))
  ) {
    fail(location, "must be a non-negative finite number");
  }
  return value;
}

function string(value, location) {
  if (typeof value !== "string" || value.length === 0 || value.length > 256) {
    fail(location, "must be a bounded non-empty string");
  }
  return value;
}

function roundedEqual(value, expected, location) {
  number(value, location);
  if (Math.abs(value - expected) > 0.001_001) {
    fail(location, `must equal ${expected} within browser timer precision`);
  }
  return value;
}

function duration(start, end, durationMs, location) {
  number(start, `${location}.start`);
  number(end, `${location}.end`);
  if (end < start) fail(location, "must not move backwards in time");
  return roundedEqual(
    durationMs,
    Number((end - start).toFixed(3)),
    `${location}.durationMs`,
  );
}

function nearestRankP95(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.ceil(sorted.length * 0.95) - 1];
}

function validateStatistics(value, samples, thresholdMs, location) {
  const statistics = exactKeys(
    value,
    ["maxMs", "p95Ms", "sampleCount"],
    location,
  );
  exact(statistics.sampleCount, samples.length, `${location}.sampleCount`);
  const p95Ms = nearestRankP95(samples);
  const maxMs = Math.max(...samples);
  roundedEqual(statistics.p95Ms, p95Ms, `${location}.p95Ms`);
  roundedEqual(statistics.maxMs, maxMs, `${location}.maxMs`);
  if (statistics.p95Ms >= thresholdMs) {
    fail(`${location}.p95Ms`, `must remain below ${thresholdMs}ms`);
  }
  return Object.freeze({ sampleCount: samples.length, p95Ms, maxMs });
}

export function validateProviderFreeLatencyEvidence(
  value,
  location = "provider-free latency evidence",
) {
  const evidence = exactKeys(
    value,
    [
      "boundaries",
      "clock",
      "freshContextCount",
      "route",
      "schemaVersion",
      "statistics",
      "thresholdsMs",
      "trials",
    ],
    location,
  );
  exact(evidence.schemaVersion, 1, `${location}.schemaVersion`);
  exact(evidence.clock, CLOCK, `${location}.clock`);
  exact(
    evidence.freshContextCount,
    FRESH_CONTEXT_COUNT,
    `${location}.freshContextCount`,
  );
  exact(evidence.route, ROUTE, `${location}.route`);
  exact(evidence.boundaries, BOUNDARIES, `${location}.boundaries`);
  exact(evidence.thresholdsMs, THRESHOLDS_MS, `${location}.thresholdsMs`);

  let checkpointIds;
  const samples = {
    submitToAnchorVisible: [],
    directorDispatchToFirstModelVisible: [],
    firstModelCheckpointEventToVisible: [],
    postPaintAcceptanceToNextBeatVisible: [],
  };
  const trials = array(
    evidence.trials,
    `${location}.trials`,
    FRESH_CONTEXT_COUNT,
  ).map((rawTrial, index) => {
    const trialLocation = `${location}.trials[${index}]`;
    const trial = exactKeys(
      rawTrial,
      [
        "anchorVisibleAtMs",
        "directorDispatchAtMs",
        "directorDispatchToFirstModelVisibleMs",
        "firstModelCheckpointEventAtMs",
        "firstModelCheckpointEventToVisibleMs",
        "firstModelVisibleAtMs",
        "ordinal",
        "postPaintAcceptanceToNextBeatVisible",
        "submitAtMs",
        "submitToAnchorVisibleMs",
      ],
      trialLocation,
    );
    exact(trial.ordinal, index + 1, `${trialLocation}.ordinal`);
    for (const key of [
      "submitAtMs",
      "anchorVisibleAtMs",
      "directorDispatchAtMs",
      "firstModelVisibleAtMs",
      "firstModelCheckpointEventAtMs",
    ]) {
      number(trial[key], `${trialLocation}.${key}`);
    }
    if (trial.directorDispatchAtMs < trial.submitAtMs) {
      fail(`${trialLocation}.directorDispatchAtMs`, "must follow submit");
    }
    if (
      trial.firstModelCheckpointEventAtMs < trial.directorDispatchAtMs ||
      trial.firstModelCheckpointEventAtMs > trial.firstModelVisibleAtMs
    ) {
      fail(
        `${trialLocation}.firstModelCheckpointEventAtMs`,
        "must fall between Director dispatch and first model visibility",
      );
    }
    samples.submitToAnchorVisible.push(
      duration(
        trial.submitAtMs,
        trial.anchorVisibleAtMs,
        trial.submitToAnchorVisibleMs,
        `${trialLocation}.submitToAnchorVisible`,
      ),
    );
    samples.directorDispatchToFirstModelVisible.push(
      duration(
        trial.directorDispatchAtMs,
        trial.firstModelVisibleAtMs,
        trial.directorDispatchToFirstModelVisibleMs,
        `${trialLocation}.directorDispatchToFirstModelVisible`,
      ),
    );
    samples.firstModelCheckpointEventToVisible.push(
      duration(
        trial.firstModelCheckpointEventAtMs,
        trial.firstModelVisibleAtMs,
        trial.firstModelCheckpointEventToVisibleMs,
        `${trialLocation}.firstModelCheckpointEventToVisible`,
      ),
    );

    const rawPairs = array(
      trial.postPaintAcceptanceToNextBeatVisible,
      `${trialLocation}.postPaintAcceptanceToNextBeatVisible`,
    );
    if (rawPairs.length === 0 || rawPairs.length > 32) {
      fail(
        `${trialLocation}.postPaintAcceptanceToNextBeatVisible`,
        "must contain between 1 and 32 checkpoint joins",
      );
    }
    const pairs = rawPairs.map((rawPair, pairIndex) => {
      const pairLocation = `${trialLocation}.postPaintAcceptanceToNextBeatVisible[${pairIndex}]`;
      const pair = exactKeys(
        rawPair,
        [
          "durationMs",
          "fromCheckpointId",
          "nextVisibleAtMs",
          "postPaintAcceptedAtMs",
          "toCheckpointId",
        ],
        pairLocation,
      );
      string(pair.fromCheckpointId, `${pairLocation}.fromCheckpointId`);
      string(pair.toCheckpointId, `${pairLocation}.toCheckpointId`);
      if (pair.fromCheckpointId === pair.toCheckpointId) {
        fail(pairLocation, "must advance to a different checkpoint");
      }
      const previousVisibleAtMs =
        pairIndex === 0
          ? trial.anchorVisibleAtMs
          : rawPairs[pairIndex - 1].nextVisibleAtMs;
      const previousCheckpointId =
        pairIndex === 0
          ? pair.fromCheckpointId
          : rawPairs[pairIndex - 1].toCheckpointId;
      exact(
        pair.fromCheckpointId,
        previousCheckpointId,
        `${pairLocation}.fromCheckpointId`,
      );
      number(
        pair.postPaintAcceptedAtMs,
        `${pairLocation}.postPaintAcceptedAtMs`,
      );
      number(pair.nextVisibleAtMs, `${pairLocation}.nextVisibleAtMs`);
      if (pair.postPaintAcceptedAtMs < previousVisibleAtMs) {
        fail(
          `${pairLocation}.postPaintAcceptedAtMs`,
          "must follow publication of the accepted checkpoint",
        );
      }
      samples.postPaintAcceptanceToNextBeatVisible.push(
        duration(
          pair.postPaintAcceptedAtMs,
          pair.nextVisibleAtMs,
          pair.durationMs,
          pairLocation,
        ),
      );
      return Object.freeze({ ...pair });
    });
    roundedEqual(
      pairs[0].nextVisibleAtMs,
      trial.firstModelVisibleAtMs,
      `${trialLocation}.firstModelVisibleAtMs`,
    );
    const trialCheckpointIds = [
      pairs[0].fromCheckpointId,
      ...pairs.map(({ toCheckpointId }) => toCheckpointId),
    ];
    if (new Set(trialCheckpointIds).size !== trialCheckpointIds.length) {
      fail(`${trialLocation} checkpoint IDs`, "must be unique");
    }
    if (checkpointIds === undefined) checkpointIds = trialCheckpointIds;
    else
      exact(
        trialCheckpointIds,
        checkpointIds,
        `${trialLocation} checkpoint IDs`,
      );
    return Object.freeze({
      ...trial,
      postPaintAcceptanceToNextBeatVisible: pairs,
    });
  });

  const rawStatistics = exactKeys(
    evidence.statistics,
    METRICS.map(([metric]) => metric),
    `${location}.statistics`,
  );
  const statistics = {};
  for (const [metric, threshold] of METRICS) {
    statistics[metric] = validateStatistics(
      rawStatistics[metric],
      samples[metric],
      THRESHOLDS_MS[threshold],
      `${location}.statistics.${metric}`,
    );
  }
  return Object.freeze({
    schemaVersion: 1,
    clock: CLOCK,
    freshContextCount: FRESH_CONTEXT_COUNT,
    route: ROUTE,
    boundaries: BOUNDARIES,
    thresholdsMs: THRESHOLDS_MS,
    statistics: Object.freeze(statistics),
    checkpointIds: Object.freeze(checkpointIds),
    trials: Object.freeze(trials),
  });
}
