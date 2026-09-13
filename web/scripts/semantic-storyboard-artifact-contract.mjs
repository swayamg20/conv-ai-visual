import { createHash } from "node:crypto";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";

import {
  validateAcceleratedInterruptionEvidence,
  validatePartialRecordInterruptionEvidence,
} from "./semantic-storyboard-interruption-contract.mjs";
import { validateProviderFreeLatencyEvidence } from "./semantic-storyboard-latency-contract.mjs";

export const GATE = "1.8";
export const PROTOCOL = "projectile_comparison_storyboard_v1";
export const SUITES = Object.freeze([
  "accelerated",
  "capture",
  "product-smoke",
]);
export const EXPECTED_REPORTS = Object.freeze({
  accelerated: Object.freeze({
    file: "semantic-storyboard.spec.ts",
    testCount: 6,
  }),
  capture: Object.freeze({
    file: "semantic-storyboard-capture.spec.ts",
    testCount: 1,
  }),
  "product-smoke": Object.freeze({
    file: "semantic-storyboard-product-auth.spec.ts",
    testCount: 3,
  }),
});

export const CAPTURE_FILES = Object.freeze({
  observation: "semantic-storyboard-normal-speed-observation.json",
  video: "semantic-storyboard-normal-speed.webm",
  fullPage: "semantic-storyboard-normal-speed-full.png",
  boardOnly: "semantic-storyboard-normal-speed-board.png",
  contactSheet: "semantic-storyboard-normal-speed-contact-sheet.png",
});

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const GIT_OBJECT_PATTERN = /^[a-f0-9]{40}$/;

export class SemanticStoryboardEvidenceError extends Error {
  constructor(message) {
    super(message);
    this.name = "SemanticStoryboardEvidenceError";
  }
}

export function fail(location, message) {
  throw new SemanticStoryboardEvidenceError(`${location}: ${message}`);
}

export function plainObject(value, location) {
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

export function exactKeys(value, keys, location) {
  const record = plainObject(value, location);
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((entry, index) => entry !== expected[index])
  ) {
    fail(
      location,
      `must contain exactly keys ${expected.join(", ")}; received ${actual.join(", ")}`,
    );
  }
  return record;
}

export function array(value, location, length) {
  if (!Array.isArray(value)) fail(location, "must be an array");
  if (length !== undefined && value.length !== length) {
    fail(location, `must contain exactly ${length} entries`);
  }
  return value;
}

export function nonEmptyString(value, location) {
  if (typeof value !== "string" || value.length === 0) {
    fail(location, "must be a non-empty string");
  }
  return value;
}

export function finiteNumber(
  value,
  location,
  { integer = false, minimum, maximum } = {},
) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (integer && !Number.isInteger(value)) ||
    (minimum !== undefined && value < minimum) ||
    (maximum !== undefined && value > maximum)
  ) {
    fail(location, "must be a bounded finite number");
  }
  return value;
}

export function exact(value, expected, location) {
  if (!isDeepStrictEqual(value, expected)) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function exactRounded(value, expected, location) {
  finiteNumber(value, location);
  finiteNumber(expected, `${location} expected`);
  if (Math.abs(value - expected) > 0.001) {
    fail(location, `must equal ${expected} within serialized timer precision`);
  }
  return value;
}

export function canonicalJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function sha256Digest(value, location) {
  if (!SHA256_PATTERN.test(nonEmptyString(value, location))) {
    fail(location, "must be a lowercase 64-character SHA-256 digest");
  }
  return value;
}

function gitObject(value, location) {
  if (!GIT_OBJECT_PATTERN.test(nonEmptyString(value, location))) {
    fail(location, "must be a lowercase 40-character Git object ID");
  }
  return value;
}

export function validateSource(value, location = "source") {
  const source = exactKeys(value, ["gitCommit", "gitTree"], location);
  return Object.freeze({
    gitCommit: gitObject(source.gitCommit, `${location}.gitCommit`),
    gitTree: gitObject(source.gitTree, `${location}.gitTree`),
  });
}

export function validateEnvironment(value, location = "environment") {
  const environment = exactKeys(
    value,
    [
      "arch",
      "browserName",
      "browserVersion",
      "nodeVersion",
      "platform",
      "playwrightVersion",
    ],
    location,
  );
  const nodeVersion = nonEmptyString(
    environment.nodeVersion,
    `${location}.nodeVersion`,
  );
  if (!/^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(nodeVersion)) {
    fail(`${location}.nodeVersion`, "must be a complete Node.js version");
  }
  const playwrightVersion = nonEmptyString(
    environment.playwrightVersion,
    `${location}.playwrightVersion`,
  );
  if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(playwrightVersion)) {
    fail(`${location}.playwrightVersion`, "must be a complete version");
  }
  exact(environment.browserName, "chromium", `${location}.browserName`);
  const browserVersion = nonEmptyString(
    environment.browserVersion,
    `${location}.browserVersion`,
  );
  if (!/^\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$/.test(browserVersion)) {
    fail(`${location}.browserVersion`, "must be a Chromium version");
  }
  return Object.freeze({
    nodeVersion,
    platform: nonEmptyString(environment.platform, `${location}.platform`),
    arch: nonEmptyString(environment.arch, `${location}.arch`),
    playwrightVersion,
    browserName: "chromium",
    browserVersion,
  });
}

function reportTests(report, location) {
  const tests = [];
  const visit = (suite, suiteLocation) => {
    const value = plainObject(suite, suiteLocation);
    for (const [index, spec] of array(
      value.specs ?? [],
      `${suiteLocation}.specs`,
    ).entries()) {
      const specValue = plainObject(spec, `${suiteLocation}.specs[${index}]`);
      for (const [testIndex, test] of array(
        specValue.tests ?? [],
        `${suiteLocation}.specs[${index}].tests`,
      ).entries()) {
        tests.push({
          file: specValue.file ?? value.file,
          test: plainObject(
            test,
            `${suiteLocation}.specs[${index}].tests[${testIndex}]`,
          ),
        });
      }
    }
    for (const [index, child] of array(
      value.suites ?? [],
      `${suiteLocation}.suites`,
    ).entries()) {
      visit(child, `${suiteLocation}.suites[${index}]`);
    }
  };
  for (const [index, suite] of array(
    report.suites,
    `${location}.suites`,
  ).entries()) {
    visit(suite, `${location}.suites[${index}]`);
  }
  return tests;
}

function reportJsonAttachment(tests, name, location) {
  const matches = [];
  for (const [testIndex, entry] of tests.entries()) {
    for (const [resultIndex, result] of array(
      entry.test.results,
      `${location}.tests[${testIndex}].results`,
    ).entries()) {
      for (const [attachmentIndex, attachmentValue] of array(
        result.attachments ?? [],
        `${location}.tests[${testIndex}].results[${resultIndex}].attachments`,
      ).entries()) {
        const attachment = plainObject(
          attachmentValue,
          `${location}.tests[${testIndex}].results[${resultIndex}].attachments[${attachmentIndex}]`,
        );
        if (attachment.name === name) matches.push(attachment);
      }
    }
  }
  exact(matches.length, 1, `${location} ${name} attachment count`);
  const attachment = exactKeys(
    matches[0],
    ["body", "contentType", "name"],
    `${location} ${name} attachment`,
  );
  exact(attachment.name, name, `${location} ${name} attachment name`);
  exact(
    attachment.contentType,
    "application/json",
    `${location} ${name} attachment contentType`,
  );
  const encoded = nonEmptyString(
    attachment.body,
    `${location} ${name} attachment body`,
  );
  if (encoded.length > 1_400_000 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) {
    fail(`${location} ${name} attachment body`, "must be bounded base64");
  }
  const bytes = Buffer.from(encoded, "base64");
  exact(
    bytes.toString("base64"),
    encoded,
    `${location} ${name} attachment base64`,
  );
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail(`${location} ${name} attachment`, `must be JSON (${error.message})`);
  }
}

export function validateReport(value, suite, location = `${suite} report`) {
  if (!SUITES.includes(suite)) fail(location, "has an unknown suite");
  const report = plainObject(value, location);
  const config = plainObject(report.config, `${location}.config`);
  const metadata = plainObject(config.metadata, `${location}.config.metadata`);
  const metadataKeys = Object.keys(metadata).filter(
    (key) => key !== "actualWorkers",
  );
  exact(
    metadataKeys.sort(),
    ["environment", "gate", "protocol", "source", "suite"],
    `${location}.config.metadata keys`,
  );
  if (metadata.actualWorkers !== undefined) {
    exact(
      metadata.actualWorkers,
      1,
      `${location}.config.metadata.actualWorkers`,
    );
  }
  exact(metadata.gate, GATE, `${location}.config.metadata.gate`);
  exact(metadata.protocol, PROTOCOL, `${location}.config.metadata.protocol`);
  exact(metadata.suite, suite, `${location}.config.metadata.suite`);
  const source = validateSource(
    metadata.source,
    `${location}.config.metadata.source`,
  );
  const environment = validateEnvironment(
    metadata.environment,
    `${location}.config.metadata.environment`,
  );
  exact(config.version, environment.playwrightVersion, `${location}.version`);
  exact(
    array(report.errors ?? [], `${location}.errors`).length,
    0,
    `${location}.errors`,
  );

  const expected = EXPECTED_REPORTS[suite];
  const tests = reportTests(report, location);
  exact(tests.length, expected.testCount, `${location} test count`);
  for (const [index, entry] of tests.entries()) {
    const testLocation = `${location}.tests[${index}]`;
    exact(
      path.basename(nonEmptyString(entry.file, `${testLocation}.file`)),
      expected.file,
      `${testLocation}.file`,
    );
    exact(
      entry.test.expectedStatus,
      "passed",
      `${testLocation}.expectedStatus`,
    );
    if (entry.test.status !== undefined) {
      exact(entry.test.status, "expected", `${testLocation}.status`);
    }
    const results = array(entry.test.results, `${testLocation}.results`, 1);
    exact(results[0].status, "passed", `${testLocation}.results[0].status`);
  }
  const interruption =
    suite === "accelerated"
      ? validateAcceleratedInterruptionEvidence(
          reportJsonAttachment(
            tests,
            "semantic-storyboard-interruption-observation",
            location,
          ),
          `${location} interruption attachment`,
        )
      : suite === "product-smoke"
        ? validatePartialRecordInterruptionEvidence(
            reportJsonAttachment(
              tests,
              "semantic-storyboard-partial-record-interruption-observation",
              location,
            ),
            `${location} partial-record attachment`,
          )
        : null;
  const latency =
    suite === "accelerated"
      ? validateProviderFreeLatencyEvidence(
          reportJsonAttachment(
            tests,
            "semantic-storyboard-provider-free-latency-observation",
            location,
          ),
          `${location} provider-free latency attachment`,
        )
      : null;
  return Object.freeze({
    source,
    environment,
    testCount: tests.length,
    interruption,
    latency,
  });
}

function normalizedFileName(value, location) {
  const fileName = nonEmptyString(value, location);
  if (
    path.basename(fileName) !== fileName ||
    fileName.includes("\\") ||
    fileName === "." ||
    fileName === ".."
  ) {
    fail(location, "must be a filename without a directory");
  }
  return fileName;
}

function artifactDescriptor(value, location, expectedFile, image) {
  const keys = image
    ? ["bytes", "fileName", "height", "sha256", "width"]
    : ["bytes", "fileName", "sha256"];
  const descriptor = exactKeys(value, keys, location);
  const fileName = normalizedFileName(
    descriptor.fileName,
    `${location}.fileName`,
  );
  if (expectedFile) exact(fileName, expectedFile, `${location}.fileName`);
  const result = {
    fileName,
    bytes: finiteNumber(descriptor.bytes, `${location}.bytes`, {
      integer: true,
      minimum: 1,
    }),
    sha256: sha256Digest(descriptor.sha256, `${location}.sha256`),
  };
  if (image) {
    result.width = finiteNumber(descriptor.width, `${location}.width`, {
      integer: true,
      minimum: 1,
    });
    result.height = finiteNumber(descriptor.height, `${location}.height`, {
      integer: true,
      minimum: 1,
    });
  }
  return Object.freeze(result);
}

function stageObservation(value, location) {
  const stage = exactKeys(
    value,
    [
      "acceptedAngles",
      "acceptedSpeed",
      "caption",
      "certificateHead",
      "completionDetail",
      "completionReason",
      "generation",
      "lastRoute",
      "layout",
      "nodeIds",
      "phase",
      "programSha256",
      "rendererTrusted",
      "sceneRevision",
      "semanticRevision",
      "sessionStatus",
      "settledBeatCount",
      "viewBox",
      "visibleCheckpointId",
    ],
    location,
  );
  for (const key of [
    "acceptedAngles",
    "acceptedSpeed",
    "caption",
    "certificateHead",
    "completionDetail",
    "completionReason",
    "lastRoute",
    "layout",
    "phase",
    "programSha256",
    "rendererTrusted",
    "sessionStatus",
    "viewBox",
    "visibleCheckpointId",
  ]) {
    nonEmptyString(stage[key], `${location}.${key}`);
  }
  for (const key of [
    "generation",
    "sceneRevision",
    "semanticRevision",
    "settledBeatCount",
  ]) {
    finiteNumber(stage[key], `${location}.${key}`, {
      integer: true,
      minimum: 0,
    });
  }
  const nodeIds = array(stage.nodeIds, `${location}.nodeIds`).map(
    (entry, index) => nonEmptyString(entry, `${location}.nodeIds[${index}]`),
  );
  if (nodeIds.length === 0 || new Set(nodeIds).size !== nodeIds.length) {
    fail(`${location}.nodeIds`, "must be a non-empty unique list");
  }
  return stage;
}

function terminalFixtureForStage(fixtures, stage, location) {
  const matches = fixtures.flatMap((fixture) => {
    const programs = fixture.programs.map((program) => ({
      fixture,
      program,
      baseProgram: null,
      modelCheckpointIds: program.checkpointIds,
    }));
    const continuations = fixture.continuations.map((program) => {
      const baseProgram = program.fromProgramId
        ? fixture.programs.find(
            (candidate) => candidate.programId === program.fromProgramId,
          )
        : fixture.acceptedPrefixMalformedTail?.scenarioId ===
            program.fromScenarioId
          ? fixture.acceptedPrefixMalformedTail
          : undefined;
      if (!baseProgram) {
        fail(
          location,
          `continuation ${program.scenarioId} has no base program`,
        );
      }
      return {
        fixture,
        program,
        baseProgram,
        modelCheckpointIds: [
          ...baseProgram.checkpointIds.slice(0, program.fromPrefixCount),
          ...program.checkpointIds,
        ],
      };
    });
    return [...programs, ...continuations].filter(
      ({ program }) =>
        program.expectedTerminal?.frontier?.programSha256 ===
        stage.programSha256,
    );
  });
  if (matches.length !== 1) {
    fail(location, "must select exactly one fixture program by programSha256");
  }
  return matches[0];
}

function validateTerminalSnapshot(value, expected, stage, location) {
  const snapshot = exactKeys(
    value,
    [
      "controls",
      "lastRoute",
      "orchestrationError",
      "pendingDirector",
      "problemSpec",
      "progress",
      "runtime",
      "status",
    ],
    location,
  );
  exact(snapshot.status, "paused", `${location}.status`);
  exact(snapshot.lastRoute, "director", `${location}.lastRoute`);
  exact(snapshot.pendingDirector, false, `${location}.pendingDirector`);
  exact(snapshot.orchestrationError, null, `${location}.orchestrationError`);
  exact(
    snapshot.problemSpec,
    expected.fixture.problemSpec,
    `${location}.problemSpec`,
  );
  const runtime = plainObject(snapshot.runtime, `${location}.runtime`);
  exact(runtime.phase, "completed", `${location}.runtime.phase`);
  exact(runtime.rendererTrusted, true, `${location}.runtime.rendererTrusted`);
  exact(
    runtime.committedScene,
    expected.program.expectedTerminal.scene,
    `${location}.runtime.committedScene`,
  );
  exact(
    runtime.committedSemanticScene,
    expected.program.expectedTerminal.semanticScene,
    `${location}.runtime.committedSemanticScene`,
  );
  exact(
    runtime.committedScene.revision,
    stage.sceneRevision,
    `${location}.runtime scene join`,
  );
  exact(
    runtime.committedSemanticScene.revision,
    stage.semanticRevision,
    `${location}.runtime semantic join`,
  );
  exact(
    runtime.committedSemanticScene.certificateHeadSha256,
    stage.certificateHead,
    `${location}.runtime certificate join`,
  );
  return snapshot;
}

function runnerCall(value, index, expected, fixture, location) {
  const call = exactKeys(
    value,
    [
      "acceptedRecordCount",
      "baseRevision",
      "certificateHeadSha256",
      "generation",
      "observedAtMs",
      "ordinal",
      "problemSpec",
      "prompt",
      "routingMode",
      "semanticRevision",
    ],
    location,
  );
  exact(call.ordinal, index + 1, `${location}.ordinal`);
  finiteNumber(call.observedAtMs, `${location}.observedAtMs`, { minimum: 0 });
  exact(call.generation, index + 1, `${location}.generation`);
  exact(call.routingMode, expected.routingMode, `${location}.routingMode`);
  exact(call.prompt, expected.prompt, `${location}.prompt`);
  exact(call.problemSpec, fixture.problemSpec, `${location}.problemSpec`);
  exact(call.baseRevision, expected.baseRevision, `${location}.baseRevision`);
  exact(
    call.semanticRevision,
    expected.semanticRevision,
    `${location}.semanticRevision`,
  );
  exact(
    call.certificateHeadSha256,
    expected.certificateHeadSha256,
    `${location}.certificateHeadSha256`,
  );
  exact(
    call.acceptedRecordCount,
    expected.acceptedRecordCount,
    `${location}.acceptedRecordCount`,
  );
  return call;
}

function expectedRunnerCalls(selected) {
  const primary = selected.baseProgram ?? selected.program;
  const calls = [
    {
      routingMode: "reflex",
      prompt: null,
      baseRevision: 0,
      semanticRevision: 0,
      certificateHeadSha256: null,
      acceptedRecordCount: 0,
    },
    {
      routingMode: "director",
      prompt: primary.prompt,
      baseRevision: 1,
      semanticRevision: 1,
      certificateHeadSha256:
        selected.fixture.anchor.expectedTerminal.frontier.certificateHeadSha256,
      acceptedRecordCount: 0,
    },
  ];
  if (selected.baseProgram) {
    calls.push({
      routingMode: "director",
      prompt: selected.program.prompt,
      baseRevision: selected.program.baseScene.revision,
      semanticRevision: selected.program.baseSemanticScene.revision,
      certificateHeadSha256:
        selected.program.baseSemanticScene.certificateHeadSha256,
      acceptedRecordCount: selected.program.fromPrefixCount,
    });
  }
  return calls;
}

function compactTimelineObservation(value, index, expectedIds, location) {
  const entry = exactKeys(
    value,
    [
      "acceptedCheckpointIds",
      "generation",
      "observedAtMs",
      "phase",
      "rendererTrusted",
      "sceneRevision",
      "semanticRevision",
      "status",
    ],
    location,
  );
  finiteNumber(entry.observedAtMs, `${location}.observedAtMs`, { minimum: 0 });
  finiteNumber(entry.generation, `${location}.generation`, {
    integer: true,
    minimum: 0,
  });
  finiteNumber(entry.sceneRevision, `${location}.sceneRevision`, {
    integer: true,
    minimum: 0,
  });
  finiteNumber(entry.semanticRevision, `${location}.semanticRevision`, {
    integer: true,
    minimum: 0,
  });
  if (typeof entry.rendererTrusted !== "boolean") {
    fail(`${location}.rendererTrusted`, "must be a boolean");
  }
  nonEmptyString(entry.status, `${location}.status`);
  nonEmptyString(entry.phase, `${location}.phase`);
  const accepted = array(
    entry.acceptedCheckpointIds,
    `${location}.acceptedCheckpointIds`,
  ).map((id, checkpointIndex) =>
    nonEmptyString(id, `${location}.acceptedCheckpointIds[${checkpointIndex}]`),
  );
  exact(
    accepted,
    expectedIds.slice(0, accepted.length),
    `${location}.acceptedCheckpointIds`,
  );
  if (
    entry.sceneRevision > accepted.length ||
    entry.semanticRevision > accepted.length
  ) {
    fail(
      location,
      `observation ${index} advances beyond its accepted checkpoint prefix`,
    );
  }
  return entry;
}

function acceptedTimelineEntry(
  value,
  index,
  expectedId,
  expectedGeneration,
  location,
) {
  const entry = exactKeys(
    value,
    [
      "checkpointId",
      "generation",
      "observedAtMs",
      "ordinal",
      "sceneRevision",
      "semanticRevision",
    ],
    location,
  );
  exact(entry.ordinal, index, `${location}.ordinal`);
  exact(entry.checkpointId, expectedId, `${location}.checkpointId`);
  exact(entry.generation, expectedGeneration, `${location}.generation`);
  finiteNumber(entry.observedAtMs, `${location}.observedAtMs`, { minimum: 0 });
  exact(entry.sceneRevision, index + 1, `${location}.sceneRevision`);
  exact(entry.semanticRevision, index + 1, `${location}.semanticRevision`);
  return entry;
}

function checkpointEvents(lane) {
  return lane.events.filter(
    (event) => event.type === "semantic_storyboard_scene_checkpoint",
  );
}

function authoredVisualDuration(selected) {
  const events = selected.baseProgram
    ? [
        ...checkpointEvents(selected.baseProgram).slice(
          0,
          selected.program.fromPrefixCount,
        ),
        ...checkpointEvents(selected.program),
      ]
    : checkpointEvents(selected.program);
  return events.reduce((total, event) => {
    const phase = event.transition.checkpoint.choreography.phase;
    return total + phase.durationMs + phase.holdAfterMs;
  }, 0);
}

function expectedCheckpointGenerations(selected) {
  const primaryCount = selected.baseProgram
    ? selected.program.fromPrefixCount
    : selected.program.checkpointIds.length;
  return [
    1,
    ...Array.from({ length: primaryCount }, () => 2),
    ...(selected.baseProgram
      ? Array.from({ length: selected.program.checkpointIds.length }, () => 3)
      : []),
  ];
}

function expectedPaintOrder(nodes) {
  const isLabel = (node) =>
    node.kind === "text" ||
    node.kind === "latex" ||
    node.kind === "latex_token";
  return [
    ...nodes.filter((node) => !isLabel(node)).map((node) => node.id),
    ...nodes.filter(isLabel).map((node) => node.id),
  ];
}

function expectedDomNode(node) {
  if (node.kind === "line") {
    return {
      kind: "line",
      id: node.id,
      points: node.points.map((point) =>
        point.map((coordinate) => Number(coordinate.toFixed(6))),
      ),
      stroke: node.style.stroke,
      strokeWidth: node.style.strokeWidth,
      opacity: node.style.opacity,
    };
  }
  if (node.kind === "path") {
    return {
      kind: "path",
      id: node.id,
      points: node.points,
      closed: node.closed,
      fill: node.style.fill,
      stroke: node.style.stroke,
      strokeWidth: node.style.strokeWidth,
      opacity: node.style.opacity,
    };
  }
  if (node.kind === "latex_token") {
    return {
      kind: "latex_token",
      id: node.id,
      x: node.x,
      y: node.y,
      width: node.width,
      height: node.height,
      anchor: node.anchor,
      latex: node.latex,
      color: node.style.color,
      fontSize: node.style.fontSize,
      opacity: node.style.opacity,
    };
  }
  fail(
    "capture observation.dom.signature.nodes",
    `unsupported node kind ${node.kind}`,
  );
}

function validateDom(value, expected, stage, location) {
  const dom = exactKeys(value, ["mismatches", "signature"], location);
  exact(
    array(dom.mismatches, `${location}.mismatches`).length,
    0,
    `${location}.mismatches`,
  );
  const signature = exactKeys(
    dom.signature,
    ["nodes", "paintOrder", "residueFree", "sourceRevision", "viewBox"],
    `${location}.signature`,
  );
  exact(
    signature.sourceRevision,
    expected.scene.revision,
    `${location}.signature.sourceRevision`,
  );
  exact(signature.viewBox, stage.viewBox, `${location}.signature.viewBox`);
  exact(signature.residueFree, true, `${location}.signature.residueFree`);
  exact(
    signature.paintOrder,
    expectedPaintOrder(expected.scene.nodes),
    `${location}.signature.paintOrder`,
  );
  exact(
    signature.nodes,
    expected.scene.nodes.map(expectedDomNode),
    `${location}.signature.nodes`,
  );
  return dom;
}

function validateCaptureArtifacts(value, expectedCheckpointIds, location) {
  const artifacts = exactKeys(
    value,
    ["boardOnly", "checkpoints", "contactSheet", "fullPage", "video"],
    location,
  );
  const checkpoints = array(
    artifacts.checkpoints,
    `${location}.checkpoints`,
    expectedCheckpointIds.length,
  ).map((entry, index) => {
    const checkpoint = exactKeys(
      entry,
      [
        "bytes",
        "checkpointId",
        "fileName",
        "height",
        "ordinal",
        "sha256",
        "width",
      ],
      `${location}.checkpoints[${index}]`,
    );
    exact(
      checkpoint.ordinal,
      index,
      `${location}.checkpoints[${index}].ordinal`,
    );
    exact(
      checkpoint.checkpointId,
      expectedCheckpointIds[index],
      `${location}.checkpoints[${index}].checkpointId`,
    );
    return Object.freeze({
      ordinal: index,
      checkpointId: checkpoint.checkpointId,
      ...artifactDescriptor(
        {
          fileName: checkpoint.fileName,
          bytes: checkpoint.bytes,
          sha256: checkpoint.sha256,
          width: checkpoint.width,
          height: checkpoint.height,
        },
        `${location}.checkpoints[${index}]`,
        `semantic-storyboard-normal-speed-checkpoint-${String(index).padStart(2, "0")}-${checkpoint.checkpointId}.png`,
        true,
      ),
    });
  });
  const fullPage = artifactDescriptor(
    artifacts.fullPage,
    `${location}.fullPage`,
    CAPTURE_FILES.fullPage,
    true,
  );
  const boardOnly = artifactDescriptor(
    artifacts.boardOnly,
    `${location}.boardOnly`,
    CAPTURE_FILES.boardOnly,
    true,
  );
  const contactSheet = artifactDescriptor(
    artifacts.contactSheet,
    `${location}.contactSheet`,
    CAPTURE_FILES.contactSheet,
    true,
  );
  exact(fullPage.width, 1_280, `${location}.fullPage.width`);
  if (fullPage.height < 720) {
    fail(
      `${location}.fullPage.height`,
      "must contain the complete 1280x720 viewport",
    );
  }
  const firstCheckpoint = checkpoints[0];
  if (!firstCheckpoint) fail(`${location}.checkpoints`, "must not be empty");
  for (const [index, checkpoint] of checkpoints.entries()) {
    exact(
      checkpoint.width,
      firstCheckpoint.width,
      `${location}.checkpoints[${index}].width`,
    );
    exact(
      checkpoint.height,
      firstCheckpoint.height,
      `${location}.checkpoints[${index}].height`,
    );
  }
  exact(boardOnly.width, firstCheckpoint.width, `${location}.boardOnly.width`);
  exact(
    boardOnly.height,
    firstCheckpoint.height,
    `${location}.boardOnly.height`,
  );
  exact(
    contactSheet.width,
    firstCheckpoint.width * 2,
    `${location}.contactSheet.width`,
  );
  exact(
    contactSheet.height,
    firstCheckpoint.height * Math.ceil(checkpoints.length / 2),
    `${location}.contactSheet.height`,
  );
  return Object.freeze({
    video: artifactDescriptor(
      artifacts.video,
      `${location}.video`,
      CAPTURE_FILES.video,
      false,
    ),
    fullPage,
    boardOnly,
    contactSheet,
    checkpoints,
  });
}

export function validateCaptureObservation(
  value,
  fixtures,
  location = "capture observation",
) {
  const observation = exactKeys(
    value,
    [
      "artifacts",
      "dom",
      "route",
      "runner",
      "schemaVersion",
      "terminal",
      "timeline",
      "timing",
      "viewport",
    ],
    location,
  );
  exact(observation.schemaVersion, 1, `${location}.schemaVersion`);
  const route = exactKeys(
    observation.route,
    ["path", "query"],
    `${location}.route`,
  );
  exact(route.path, "/e2e/semantic-storyboard", `${location}.route.path`);
  const query = nonEmptyString(route.query, `${location}.route.query`);
  const parameters = new URLSearchParams(
    query.startsWith("?") ? query.slice(1) : query,
  );
  const queryRecord = Object.fromEntries(parameters);
  exact(parameters.size, 4, `${location}.route.query parameter count`);
  exact(
    queryRecord,
    {
      layout: "cinematic",
      motion: "real",
      proof: "none",
      speed: "normal",
    },
    `${location}.route.query`,
  );
  const viewport = exactKeys(
    observation.viewport,
    ["height", "width"],
    `${location}.viewport`,
  );
  exact(viewport, { width: 1_280, height: 720 }, `${location}.viewport`);

  const terminal = exactKeys(
    observation.terminal,
    ["snapshot", "stage"],
    `${location}.terminal`,
  );
  const stage = stageObservation(terminal.stage, `${location}.terminal.stage`);
  exact(stage.phase, "completed", `${location}.terminal.stage.phase`);
  exact(
    stage.sessionStatus,
    "paused",
    `${location}.terminal.stage.sessionStatus`,
  );
  exact(
    stage.rendererTrusted,
    "true",
    `${location}.terminal.stage.rendererTrusted`,
  );
  exact(stage.layout, "cinematic", `${location}.terminal.stage.layout`);
  exact(
    stage.completionReason,
    "model_stop",
    `${location}.terminal.stage.completionReason`,
  );
  const selected = terminalFixtureForStage(
    fixtures,
    stage,
    `${location}.terminal.stage`,
  );
  const expectedFrontier = selected.program.expectedTerminal.frontier;
  exact(
    selected.modelCheckpointIds.length,
    5,
    `${location}.terminal canonical model beat count`,
  );
  exact(
    stage.acceptedSpeed,
    String(selected.fixture.problemSpec.speedMps),
    `${location}.terminal.stage.acceptedSpeed`,
  );
  exact(
    stage.acceptedAngles,
    selected.fixture.problemSpec.anglesDeg.join(":"),
    `${location}.terminal.stage.acceptedAngles`,
  );
  exact(
    stage.sceneRevision,
    expectedFrontier.revision,
    `${location}.terminal.stage.sceneRevision`,
  );
  exact(
    stage.semanticRevision,
    expectedFrontier.revision,
    `${location}.terminal.stage.semanticRevision`,
  );
  exact(
    stage.certificateHead,
    expectedFrontier.certificateHeadSha256,
    `${location}.terminal.stage.certificateHead`,
  );
  exact(
    stage.settledBeatCount,
    selected.modelCheckpointIds.length,
    `${location}.terminal.stage.settledBeatCount`,
  );
  const snapshot = validateTerminalSnapshot(
    terminal.snapshot,
    selected,
    stage,
    `${location}.terminal.snapshot`,
  );

  const runner = exactKeys(
    observation.runner,
    ["calls", "runnerCallCount"],
    `${location}.runner`,
  );
  const expectedCalls = expectedRunnerCalls(selected);
  const calls = array(
    runner.calls,
    `${location}.runner.calls`,
    expectedCalls.length,
  ).map((call, index) =>
    runnerCall(
      call,
      index,
      expectedCalls[index],
      selected.fixture,
      `${location}.runner.calls[${index}]`,
    ),
  );
  exact(
    runner.runnerCallCount,
    calls.length,
    `${location}.runner.runnerCallCount`,
  );
  for (let index = 1; index < calls.length; index += 1) {
    if (calls[index].observedAtMs < calls[index - 1].observedAtMs) {
      fail(
        `${location}.runner.calls[${index}].observedAtMs`,
        "must be monotonic",
      );
    }
  }

  const timeline = exactKeys(
    observation.timeline,
    ["acceptedCheckpointTimeline", "observations"],
    `${location}.timeline`,
  );
  const observations = array(
    timeline.observations,
    `${location}.timeline.observations`,
  );
  if (observations.length === 0)
    fail(`${location}.timeline.observations`, "must not be empty");
  const expectedCheckpointIds = [
    ...selected.fixture.anchor.checkpointIds,
    ...selected.modelCheckpointIds,
  ];
  const compactObservations = observations.map((entry, index) =>
    compactTimelineObservation(
      entry,
      index,
      expectedCheckpointIds,
      `${location}.timeline.observations[${index}]`,
    ),
  );
  for (let index = 1; index < compactObservations.length; index += 1) {
    if (
      compactObservations[index].observedAtMs <
      compactObservations[index - 1].observedAtMs
    ) {
      fail(
        `${location}.timeline.observations[${index}].observedAtMs`,
        "must be monotonic",
      );
    }
  }
  const acceptedTimeline = array(
    timeline.acceptedCheckpointTimeline,
    `${location}.timeline.acceptedCheckpointTimeline`,
    expectedCheckpointIds.length,
  );
  const checkpointGenerations = expectedCheckpointGenerations(selected);
  const validatedTimeline = acceptedTimeline.map((entry, index) =>
    acceptedTimelineEntry(
      entry,
      index,
      expectedCheckpointIds[index],
      checkpointGenerations[index],
      `${location}.timeline.acceptedCheckpointTimeline[${index}]`,
    ),
  );
  for (let index = 1; index < validatedTimeline.length; index += 1) {
    if (
      validatedTimeline[index].observedAtMs <
      validatedTimeline[index - 1].observedAtMs
    ) {
      fail(
        `${location}.timeline.acceptedCheckpointTimeline[${index}].observedAtMs`,
        "must be monotonic",
      );
    }
  }

  const timing = exactKeys(
    observation.timing,
    [
      "anchorPaintMs",
      "anchorPostPaintAcceptanceMs",
      "anchorToFirstModelPostPaintMs",
      "authoredVisualDurationMs",
      "callToStartedEventMs",
      "checkpointEventToPostPaint",
      "directorDispatchToFirstModelPostPaintMs",
      "directorDispatchToFirstVisibleMs",
      "elapsedMs",
      "firstModelCheckpointEventToFirstVisibleMs",
      "firstModelVisibleAtMs",
      "observedFiveBeatSequenceMs",
      "postPaintToNextBeatVisibleGapsMs",
      "startedAtMs",
      "terminalAtMs",
      "transportCheckpointArrivalGapsMs",
      "unavailableMetrics",
      "videoCrop",
    ],
    `${location}.timing`,
  );
  for (const key of [
    "anchorPaintMs",
    "anchorPostPaintAcceptanceMs",
    "anchorToFirstModelPostPaintMs",
    "authoredVisualDurationMs",
    "directorDispatchToFirstModelPostPaintMs",
    "directorDispatchToFirstVisibleMs",
    "elapsedMs",
    "firstModelCheckpointEventToFirstVisibleMs",
    "firstModelVisibleAtMs",
    "observedFiveBeatSequenceMs",
    "startedAtMs",
    "terminalAtMs",
  ]) {
    finiteNumber(timing[key], `${location}.timing.${key}`, { minimum: 0 });
  }
  exact(
    Number((timing.terminalAtMs - timing.startedAtMs).toFixed(3)),
    timing.elapsedMs,
    `${location}.timing.elapsedMs`,
  );
  const anchor = validatedTimeline[0];
  const firstModel = validatedTimeline[1];
  const firstDirector = calls[1];
  exactRounded(
    timing.anchorPostPaintAcceptanceMs,
    Number((anchor.observedAtMs - timing.startedAtMs).toFixed(3)),
    `${location}.timing.anchorPostPaintAcceptanceMs`,
  );
  if (timing.anchorPaintMs > timing.anchorPostPaintAcceptanceMs) {
    fail(
      `${location}.timing.anchorPaintMs`,
      "must not follow anchor post-paint acceptance",
    );
  }
  exact(
    timing.anchorToFirstModelPostPaintMs,
    Number((firstModel.observedAtMs - anchor.observedAtMs).toFixed(3)),
    `${location}.timing.anchorToFirstModelPostPaintMs`,
  );
  exact(
    timing.directorDispatchToFirstModelPostPaintMs,
    Number((firstModel.observedAtMs - firstDirector.observedAtMs).toFixed(3)),
    `${location}.timing.directorDispatchToFirstModelPostPaintMs`,
  );
  exact(
    timing.authoredVisualDurationMs,
    authoredVisualDuration(selected),
    `${location}.timing.authoredVisualDurationMs`,
  );
  exact(
    timing.authoredVisualDurationMs,
    8_459,
    `${location}.timing canonical authored duration`,
  );
  exactRounded(
    timing.observedFiveBeatSequenceMs,
    Number((timing.terminalAtMs - firstDirector.observedAtMs).toFixed(3)),
    `${location}.timing.observedFiveBeatSequenceMs`,
  );
  if (
    timing.observedFiveBeatSequenceMs < timing.authoredVisualDurationMs - 250 ||
    timing.observedFiveBeatSequenceMs > timing.authoredVisualDurationMs + 2_000
  ) {
    fail(
      `${location}.timing.observedFiveBeatSequenceMs`,
      "must remain within the authored-duration observation window",
    );
  }
  const videoCrop = exactKeys(
    timing.videoCrop,
    [
      "directorDispatchAtEpochMs",
      "durationMs",
      "leadInMs",
      "pageTimeOriginMs",
      "recordingStartedAtEpochMs",
      "startMs",
      "tailMs",
    ],
    `${location}.timing.videoCrop`,
  );
  finiteNumber(
    videoCrop.recordingStartedAtEpochMs,
    `${location}.timing.videoCrop.recordingStartedAtEpochMs`,
    { integer: true, minimum: 1 },
  );
  for (const key of [
    "pageTimeOriginMs",
    "directorDispatchAtEpochMs",
    "startMs",
    "durationMs",
  ]) {
    finiteNumber(videoCrop[key], `${location}.timing.videoCrop.${key}`, {
      minimum: 0,
    });
  }
  exact(videoCrop.leadInMs, 100, `${location}.timing.videoCrop.leadInMs`);
  exact(videoCrop.tailMs, 300, `${location}.timing.videoCrop.tailMs`);
  exactRounded(
    videoCrop.directorDispatchAtEpochMs,
    videoCrop.pageTimeOriginMs + firstDirector.observedAtMs,
    `${location}.timing.videoCrop.directorDispatchAtEpochMs`,
  );
  exactRounded(
    videoCrop.startMs,
    Math.max(
      0,
      videoCrop.directorDispatchAtEpochMs -
        videoCrop.recordingStartedAtEpochMs -
        videoCrop.leadInMs,
    ),
    `${location}.timing.videoCrop.startMs`,
  );
  exactRounded(
    videoCrop.durationMs,
    timing.observedFiveBeatSequenceMs + videoCrop.leadInMs + videoCrop.tailMs,
    `${location}.timing.videoCrop.durationMs`,
  );
  exactRounded(
    timing.directorDispatchToFirstVisibleMs,
    Number(
      (timing.firstModelVisibleAtMs - firstDirector.observedAtMs).toFixed(3),
    ),
    `${location}.timing.directorDispatchToFirstVisibleMs`,
  );
  if (timing.directorDispatchToFirstVisibleMs >= 2_000) {
    fail(
      `${location}.timing.directorDispatchToFirstVisibleMs`,
      "must be below 2000ms",
    );
  }
  const callToStarted = array(
    timing.callToStartedEventMs,
    `${location}.timing.callToStartedEventMs`,
    calls.length,
  );
  for (const [index, value] of callToStarted.entries()) {
    const entry = exactKeys(
      value,
      ["durationMs", "generation", "ordinal", "routingMode"],
      `${location}.timing.callToStartedEventMs[${index}]`,
    );
    exact(
      entry.ordinal,
      calls[index].ordinal,
      `${location}.timing.callToStartedEventMs[${index}].ordinal`,
    );
    exact(
      entry.generation,
      calls[index].generation,
      `${location}.timing.callToStartedEventMs[${index}].generation`,
    );
    exact(
      entry.routingMode,
      calls[index].routingMode,
      `${location}.timing.callToStartedEventMs[${index}].routingMode`,
    );
    finiteNumber(
      entry.durationMs,
      `${location}.timing.callToStartedEventMs[${index}].durationMs`,
      { minimum: 0 },
    );
  }
  const eventToPaint = array(
    timing.checkpointEventToPostPaint,
    `${location}.timing.checkpointEventToPostPaint`,
    expectedCheckpointIds.length,
  ).map((value, index) => {
    const entry = exactKeys(
      value,
      ["acceptedAtMs", "checkpointId", "durationMs", "eventAtMs"],
      `${location}.timing.checkpointEventToPostPaint[${index}]`,
    );
    exact(
      entry.checkpointId,
      expectedCheckpointIds[index],
      `${location}.timing.checkpointEventToPostPaint[${index}].checkpointId`,
    );
    exact(
      entry.acceptedAtMs,
      validatedTimeline[index].observedAtMs,
      `${location}.timing.checkpointEventToPostPaint[${index}].acceptedAtMs`,
    );
    finiteNumber(
      entry.eventAtMs,
      `${location}.timing.checkpointEventToPostPaint[${index}].eventAtMs`,
      { minimum: 0 },
    );
    finiteNumber(
      entry.durationMs,
      `${location}.timing.checkpointEventToPostPaint[${index}].durationMs`,
      { minimum: 0 },
    );
    exactRounded(
      entry.durationMs,
      Number((entry.acceptedAtMs - entry.eventAtMs).toFixed(3)),
      `${location}.timing.checkpointEventToPostPaint[${index}].durationMs`,
    );
    return entry;
  });
  exactRounded(
    timing.firstModelCheckpointEventToFirstVisibleMs,
    Number(
      (timing.firstModelVisibleAtMs - eventToPaint[1].eventAtMs).toFixed(3),
    ),
    `${location}.timing.firstModelCheckpointEventToFirstVisibleMs`,
  );
  if (timing.firstModelVisibleAtMs > eventToPaint[1].acceptedAtMs) {
    fail(
      `${location}.timing.firstModelVisibleAtMs`,
      "must precede first-model post-paint acceptance",
    );
  }
  const postPaintToVisible = array(
    timing.postPaintToNextBeatVisibleGapsMs,
    `${location}.timing.postPaintToNextBeatVisibleGapsMs`,
    expectedCheckpointIds.length - 1,
  );
  for (const [index, value] of postPaintToVisible.entries()) {
    const gapLocation = `${location}.timing.postPaintToNextBeatVisibleGapsMs[${index}]`;
    const gap = exactKeys(
      value,
      [
        "durationMs",
        "fromCheckpointId",
        "nextVisibleAtMs",
        "postPaintAcceptedAtMs",
        "toCheckpointId",
      ],
      gapLocation,
    );
    exact(
      gap.fromCheckpointId,
      expectedCheckpointIds[index],
      `${gapLocation}.fromCheckpointId`,
    );
    exact(
      gap.toCheckpointId,
      expectedCheckpointIds[index + 1],
      `${gapLocation}.toCheckpointId`,
    );
    exact(
      gap.postPaintAcceptedAtMs,
      validatedTimeline[index].observedAtMs,
      `${gapLocation}.postPaintAcceptedAtMs`,
    );
    finiteNumber(gap.nextVisibleAtMs, `${gapLocation}.nextVisibleAtMs`, {
      minimum: 0,
    });
    finiteNumber(gap.durationMs, `${gapLocation}.durationMs`, { minimum: 0 });
    exactRounded(
      gap.durationMs,
      Number((gap.nextVisibleAtMs - gap.postPaintAcceptedAtMs).toFixed(3)),
      `${gapLocation}.durationMs`,
    );
    if (gap.nextVisibleAtMs > validatedTimeline[index + 1].observedAtMs) {
      fail(
        `${gapLocation}.nextVisibleAtMs`,
        "must not follow matching post-paint acceptance",
      );
    }
    if (
      index > 0 &&
      gap.nextVisibleAtMs < postPaintToVisible[index - 1].nextVisibleAtMs
    ) {
      fail(`${gapLocation}.nextVisibleAtMs`, "must be monotonic");
    }
  }
  exactRounded(
    postPaintToVisible[0].nextVisibleAtMs,
    timing.firstModelVisibleAtMs,
    `${location}.timing.postPaintToNextBeatVisibleGapsMs[0].nextVisibleAtMs`,
  );
  const validateArrivalGaps = (values, key, timestamps) => {
    const gaps = array(
      values,
      `${location}.timing.${key}`,
      expectedCheckpointIds.length - 1,
    );
    for (const [index, value] of gaps.entries()) {
      const gap = exactKeys(
        value,
        ["durationMs", "fromCheckpointId", "toCheckpointId"],
        `${location}.timing.${key}[${index}]`,
      );
      exact(
        gap.fromCheckpointId,
        expectedCheckpointIds[index],
        `${location}.timing.${key}[${index}].fromCheckpointId`,
      );
      exact(
        gap.toCheckpointId,
        expectedCheckpointIds[index + 1],
        `${location}.timing.${key}[${index}].toCheckpointId`,
      );
      exact(
        gap.durationMs,
        Number((timestamps[index + 1] - timestamps[index]).toFixed(3)),
        `${location}.timing.${key}[${index}].durationMs`,
      );
    }
  };
  validateArrivalGaps(
    timing.transportCheckpointArrivalGapsMs,
    "transportCheckpointArrivalGapsMs",
    eventToPaint.map((entry) => entry.eventAtMs),
  );
  exact(
    timing.unavailableMetrics,
    [
      "server_provider_first_byte",
      "record_complete_to_verification",
      "verification_duration",
      "post_paint_barrier_duration",
    ],
    `${location}.timing.unavailableMetrics`,
  );

  const dom = validateDom(
    observation.dom,
    selected.program.expectedTerminal,
    stage,
    `${location}.dom`,
  );
  const artifacts = validateCaptureArtifacts(
    observation.artifacts,
    expectedCheckpointIds,
    `${location}.artifacts`,
  );
  return Object.freeze({
    artifacts,
    selectedFixtureId: selected.fixture.fixtureId,
    selectedProgramId:
      selected.program.programId ?? selected.program.scenarioId,
    expectedCheckpointIds: Object.freeze(expectedCheckpointIds),
    stage,
    snapshot,
    timing,
    dom,
  });
}
