import { createHash, randomBytes } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { constants, readFileSync } from "node:fs";
import {
  access,
  lstat,
  mkdir,
  open,
  readdir,
  readFile,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";
import { inflateSync } from "node:zlib";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));

export const WEB_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const REPOSITORY_ROOT = path.resolve(WEB_ROOT, "..");
export const DEFAULT_ARTIFACT_ROOT = path.join(
  REPOSITORY_ROOT,
  "var",
  "projectile-motion-e2e",
);
export const MANIFEST_NAME = "manifest.json";
export const MANIFEST_DIGEST_NAME = "manifest.sha256";

const ARTIFACT_ROOT_NAME = "projectile-motion-e2e";
const SUITES = Object.freeze(["accelerated", "capture", "product-smoke"]);
const EXPECTED_REPORTS = Object.freeze({
  accelerated: Object.freeze({
    file: "projectile-motion.spec.ts",
    testCount: 16,
  }),
  capture: Object.freeze({
    file: "projectile-motion-capture.spec.ts",
    testCount: 1,
  }),
  "product-smoke": Object.freeze({
    file: "projectile-motion-product-auth.spec.ts",
    testCount: 2,
  }),
});
const FIXTURE_DIRECTORY_RELATIVE_PATH =
  "web/src/features/live-scene/fixtures/projectile-motion-v1";
const PRIMARY_FIXTURE_ID = "projectile-motion-v20-a45";
const PACKAGE_LOCK_RELATIVE_PATH = "web/package-lock.json";
const GENERATOR_RELATIVE_PATH =
  "scripts/generate_projectile_motion_fixtures.py";
const NEXT_ENV_RELATIVE_PATH = "web/next-env.d.ts";
const EXPECTED_CHECKPOINT_IDS = Object.freeze([
  "setup",
  "decompose_velocity",
  "trace_ascent",
  "apex_state",
  "trace_descent",
  "summary",
]);
const INTERRUPTION_CATEGORIES = Object.freeze([
  "path_trace",
  "marker_motion",
  "vector_morph",
  "focus",
  "equation_morph",
  "hold",
]);
const INTERRUPTION_REPETITIONS_PER_CATEGORY = 4;
const INTERRUPTION_SAMPLE_COUNT =
  INTERRUPTION_CATEGORIES.length * INTERRUPTION_REPETITIONS_PER_CATEGORY;
const INTERRUPTION_SPECS = Object.freeze({
  path_trace: Object.freeze({ checkpointId: "trace_ascent", mainIndex: 2 }),
  marker_motion: Object.freeze({ checkpointId: "trace_ascent", mainIndex: 2 }),
  vector_morph: Object.freeze({ checkpointId: "parameters_retargeted" }),
  focus: Object.freeze({ checkpointId: "trace_ascent", mainIndex: 2 }),
  equation_morph: Object.freeze({
    checkpointId: "trace_descent",
    mainIndex: 4,
  }),
  hold: Object.freeze({ checkpointId: "setup", mainIndex: 0 }),
});
const TRACE_TIP_SPECS = Object.freeze(
  ["ascent", "descent"].flatMap((segment) =>
    [0.25, 0.5, 0.75].map((progress) => [
      `trace_${segment}`,
      `projectile__trajectory_${segment}`,
      progress,
    ]),
  ),
);
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const GIT_OBJECT_PATTERN = /^[a-f0-9]{40}$/;
const MAX_JSON_BYTES = 32 * 1024 * 1024;
const MAX_ARTIFACT_BYTES = 256 * 1024 * 1024;
const MIN_RECORDING_DURATION_MS = 35_000;
const MAX_RECORDING_DURATION_MS = 45_000;
const MAX_RECORDING_SHORTFALL_MS = 50;
const MAX_RECORDING_OVERHANG_MS = 5_000;
const EXPECTED_ARTIFACT_PATHS = Object.freeze({
  video: "capture/projectile-motion.webm",
  pageScreenshot: "capture/projectile-motion-page.png",
  boardScreenshot: "capture/projectile-motion-board.png",
  contactSheet: "capture/projectile-motion-contact-sheet.png",
});

export class ProjectileMotionEvidenceError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProjectileMotionEvidenceError";
  }
}

function fail(location, message) {
  throw new ProjectileMotionEvidenceError(`${location}: ${message}`);
}

function plainObject(value, location) {
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

function nonEmptyString(value, location) {
  if (typeof value !== "string" || value.length === 0) {
    fail(location, "must be a non-empty string");
  }
  return value;
}

function finiteNumber(value, location, { integer = false, minimum } = {}) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (integer && !Number.isInteger(value)) ||
    (minimum !== undefined && value < minimum)
  ) {
    fail(
      location,
      `must be a finite${integer ? " integer" : " number"}${
        minimum === undefined ? "" : ` >= ${minimum}`
      }`,
    );
  }
  return value;
}

function exact(value, expected, location) {
  if (value !== expected) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function sameValue(actual, expected, location) {
  if (!isDeepStrictEqual(actual, expected)) {
    fail(location, "must exactly match fixture-derived evidence");
  }
}

function sameFixtureValue(
  actual,
  expected,
  location,
  { allowSemanticLineQuantization = false } = {},
) {
  if (Array.isArray(expected)) {
    const actualEntries = array(actual, location, expected.length);
    expected.forEach((entry, index) =>
      sameFixtureValue(actualEntries[index], entry, `${location}[${index}]`, {
        allowSemanticLineQuantization,
      }),
    );
    return;
  }
  if (expected !== null && typeof expected === "object") {
    const actualRecord = exactKeys(actual, Object.keys(expected), location);
    for (const [key, expectedValue] of Object.entries(expected)) {
      if (
        key === "points" &&
        (expected.kind === "path" ||
          (allowSemanticLineQuantization && expected.kind === "line"))
      ) {
        const actualPoints = array(
          actualRecord.points,
          `${location}.points`,
          expectedValue.length,
        );
        expectedValue.forEach((expectedPoint, pointIndex) => {
          const actualPoint = array(
            actualPoints[pointIndex],
            `${location}.points[${pointIndex}]`,
            2,
          );
          expectedPoint.forEach((expectedCoordinate, coordinateIndex) => {
            const coordinateLocation = `${location}.points[${pointIndex}][${coordinateIndex}]`;
            const actualCoordinate = finiteNumber(
              actualPoint[coordinateIndex],
              coordinateLocation,
            );
            const scaledEpsilon =
              Number.EPSILON *
              Math.max(
                1,
                Math.abs(actualCoordinate),
                Math.abs(expectedCoordinate),
              );
            const isSemanticLine = expected.kind === "line";
            const tolerance = isSemanticLine
              ? 0.5e-6 + scaledEpsilon
              : scaledEpsilon;
            const distance = isSemanticLine
              ? Math.min(
                  Math.abs(actualCoordinate - expectedCoordinate),
                  Math.abs(actualCoordinate - Math.fround(expectedCoordinate)),
                )
              : Math.abs(actualCoordinate - expectedCoordinate);
            if (distance > tolerance) {
              fail(
                coordinateLocation,
                isSemanticLine
                  ? `must be within ${tolerance} of the fixture coordinate or its nearest Float32 representation`
                  : `must be within scaled Number.EPSILON (${tolerance}) of the fixture coordinate`,
              );
            }
          });
        });
      } else {
        sameFixtureValue(
          actualRecord[key],
          expectedValue,
          `${location}.${key}`,
          { allowSemanticLineQuantization },
        );
      }
    }
    return;
  }
  if (!isDeepStrictEqual(actual, expected)) {
    fail(location, "must exactly match fixture-derived evidence");
  }
}

function array(value, location, length) {
  if (!Array.isArray(value)) fail(location, "must be an array");
  if (length !== undefined && value.length !== length) {
    fail(location, `must contain exactly ${length} entries`);
  }
  return value;
}

function uniqueStringArray(value, location, length) {
  const entries = array(value, location, length).map((entry, index) =>
    nonEmptyString(entry, `${location}[${index}]`),
  );
  if (new Set(entries).size !== entries.length) {
    fail(location, "must not contain duplicates");
  }
  return entries;
}

function assertSameArray(actual, expected, location) {
  if (
    actual.length !== expected.length ||
    actual.some((entry, index) => entry !== expected[index])
  ) {
    fail(
      location,
      `must equal ${JSON.stringify(expected)}; received ${JSON.stringify(actual)}`,
    );
  }
}

function canonicalJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function sha256Digest(value, location) {
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

function validateSource(value, location) {
  const source = exactKeys(value, ["gitCommit", "gitTree"], location);
  return {
    gitCommit: gitObject(source.gitCommit, `${location}.gitCommit`),
    gitTree: gitObject(source.gitTree, `${location}.gitTree`),
  };
}

function validateEnvironment(value, location) {
  const environment = exactKeys(
    value,
    [
      "nodeVersion",
      "platform",
      "arch",
      "playwrightVersion",
      "browserName",
      "browserVersion",
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
    fail(
      `${location}.playwrightVersion`,
      "must be a complete Playwright version",
    );
  }
  exact(environment.browserName, "chromium", `${location}.browserName`);
  const browserVersion = nonEmptyString(
    environment.browserVersion,
    `${location}.browserVersion`,
  );
  if (!/^\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$/.test(browserVersion)) {
    fail(`${location}.browserVersion`, "must be a Chromium version");
  }
  return {
    nodeVersion,
    platform: nonEmptyString(environment.platform, `${location}.platform`),
    arch: nonEmptyString(environment.arch, `${location}.arch`),
    playwrightVersion,
    browserName: environment.browserName,
    browserVersion,
  };
}

function normalizeRelativeArtifactPath(value, location) {
  const relative = nonEmptyString(value, location);
  if (
    relative.includes("\\") ||
    relative.startsWith("/") ||
    path.posix.normalize(relative) !== relative ||
    relative.split("/").includes("..")
  ) {
    fail(location, "must be a normalized relative POSIX path");
  }
  return relative;
}

function isInside(target, boundary) {
  return target === boundary || target.startsWith(`${boundary}${path.sep}`);
}

function artifactRootFromEnvironment() {
  const configured = process.env.PROJECTILE_MOTION_E2E_OUTPUT_DIR;
  return configured
    ? path.resolve(WEB_ROOT, configured)
    : DEFAULT_ARTIFACT_ROOT;
}

export function resolveArtifactRoot(candidate = artifactRootFromEnvironment()) {
  const resolved = path.resolve(candidate);
  if (path.basename(resolved) !== ARTIFACT_ROOT_NAME) {
    fail(
      "artifact root",
      `must end in the exact directory name ${JSON.stringify(ARTIFACT_ROOT_NAME)}`,
    );
  }
  const boundaries = [path.resolve(REPOSITORY_ROOT), path.resolve(tmpdir())];
  if (!boundaries.some((boundary) => isInside(resolved, boundary))) {
    fail(
      "artifact root",
      "must be inside this repository or the operating-system temporary directory",
    );
  }
  const forbidden = new Set([
    path.parse(resolved).root,
    REPOSITORY_ROOT,
    WEB_ROOT,
    path.resolve(process.cwd()),
    ...(process.env.HOME ? [path.resolve(process.env.HOME)] : []),
  ]);
  if (forbidden.has(resolved)) {
    fail("artifact root", `${resolved} is too broad to remove safely`);
  }
  return resolved;
}

async function rejectSymlinkPath(candidate, location) {
  try {
    const stat = await lstat(candidate);
    if (stat.isSymbolicLink()) fail(location, "must not be a symbolic link");
    return stat;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function assertSafeArtifactRoot(candidate) {
  const resolved = resolveArtifactRoot(candidate);
  const boundary = isInside(resolved, REPOSITORY_ROOT)
    ? REPOSITORY_ROOT
    : path.resolve(tmpdir());
  let cursor = resolved;
  while (isInside(cursor, boundary) && cursor !== boundary) {
    await rejectSymlinkPath(cursor, `artifact path ${cursor}`);
    cursor = path.dirname(cursor);
  }
  const boundaryReal = await realpath(boundary);
  let canonicalParent = path.dirname(resolved);
  while (true) {
    try {
      canonicalParent = await realpath(canonicalParent);
      break;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const next = path.dirname(canonicalParent);
      if (next === canonicalParent) throw error;
      canonicalParent = next;
    }
  }
  if (!isInside(canonicalParent, boundaryReal)) {
    fail("artifact root", "escapes its boundary after resolving ancestors");
  }
  return resolved;
}

async function prepareRoot(candidate, allowTemporaryRoot) {
  const artifactRoot = await assertSafeArtifactRoot(candidate);
  const configuredRoot = resolveArtifactRoot(artifactRootFromEnvironment());
  if (!allowTemporaryRoot && artifactRoot !== configuredRoot) {
    fail(
      "artifact root",
      `prepare may remove only the configured root ${configuredRoot}`,
    );
  }
  await rm(artifactRoot, { recursive: true, force: true });
  await mkdir(artifactRoot, { recursive: true });
  await Promise.all(
    SUITES.map((suite) =>
      mkdir(path.join(artifactRoot, suite), { recursive: true }),
    ),
  );
  return artifactRoot;
}

export async function prepareArtifactRoot(candidate = DEFAULT_ARTIFACT_ROOT) {
  return prepareRoot(candidate, false);
}

export async function prepareArtifactRootForTests(candidate) {
  return prepareRoot(candidate, true);
}

async function readRegularFile(
  filePath,
  location,
  maximumBytes = MAX_JSON_BYTES,
) {
  const stat = await rejectSymlinkPath(filePath, location);
  if (!stat?.isFile()) fail(location, "must be a regular file");
  if (stat.size < 1 || stat.size > maximumBytes) {
    fail(location, `must contain between 1 and ${maximumBytes} bytes`);
  }
  return readFile(filePath);
}

function parseJson(bytes, location) {
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail(location, `must be valid JSON (${error.message})`);
  }
}

function fixtureCheckpoints(fixture, location) {
  const root = plainObject(fixture, location);
  const lanes = plainObject(root.lanes, `${location}.lanes`);
  const main = plainObject(lanes.main, `${location}.lanes.main`);
  const checkpoints = array(main.events, `${location}.lanes.main.events`)
    .filter(
      (event) =>
        plainObject(event, `${location}.lanes.main.events entry`).type ===
        "projectile_choreography_scene_checkpoint",
    )
    .map((event, index) => {
      const semantic = plainObject(
        event.semantic,
        `${location}.checkpoint[${index}].semantic`,
      );
      const certificate = plainObject(
        semantic.certificate,
        `${location}.checkpoint[${index}].semantic.certificate`,
      );
      const phase = plainObject(
        plainObject(
          semantic.choreography,
          `${location}.checkpoint[${index}].semantic.choreography`,
        ).phase,
        `${location}.checkpoint[${index}].semantic.choreography.phase`,
      );
      return {
        checkpointId: nonEmptyString(
          semantic.checkpointId,
          `${location}.checkpoint[${index}].checkpointId`,
        ),
        revision: finiteNumber(
          event.resultRevision,
          `${location}.checkpoint[${index}].resultRevision`,
          { integer: true, minimum: 1 },
        ),
        certificateSha256: sha256Digest(
          certificate.certificateSha256,
          `${location}.checkpoint[${index}].certificateSha256`,
        ),
        durationMs: finiteNumber(
          phase.durationMs,
          `${location}.checkpoint[${index}].durationMs`,
          { integer: true, minimum: 0 },
        ),
        holdAfterMs: finiteNumber(
          phase.holdAfterMs,
          `${location}.checkpoint[${index}].holdAfterMs`,
          { integer: true, minimum: 0 },
        ),
      };
    });
  assertSameArray(
    checkpoints.map(({ checkpointId }) => checkpointId),
    EXPECTED_CHECKPOINT_IDS,
    `${location} checkpoint IDs`,
  );
  return checkpoints;
}

async function loadFixtureCatalog() {
  const directory = path.join(REPOSITORY_ROOT, FIXTURE_DIRECTORY_RELATIVE_PATH);
  const entries = await readdir(directory, { withFileTypes: true });
  const names = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => entry.name)
    .sort();
  if (names.length !== 5) {
    fail("fixture catalog", "must contain exactly five JSON fixtures");
  }
  const fixtures = [];
  for (const name of names) {
    const relativePath = `${FIXTURE_DIRECTORY_RELATIVE_PATH}/${name}`;
    const bytes = await readRegularFile(
      path.join(REPOSITORY_ROOT, relativePath),
      `fixture ${name}`,
    );
    const fixture = plainObject(
      parseJson(bytes, `fixture ${name}`),
      `fixture ${name}`,
    );
    exact(fixture.v, 1, `fixture ${name}.v`);
    exact(
      fixture.protocol,
      "projectile_choreography_v1",
      `fixture ${name}.protocol`,
    );
    exact(
      fixture.providerRequestCount,
      0,
      `fixture ${name}.providerRequestCount`,
    );
    const compilerVersion = nonEmptyString(
      fixture.compilerVersion,
      `fixture ${name}.compilerVersion`,
    );
    exact(
      compilerVersion,
      "murmur.projectile_motion_choreography.v1",
      `fixture ${name}.compilerVersion`,
    );
    const checkpoints = fixtureCheckpoints(fixture, `fixture ${name}`);
    fixtures.push({
      path: relativePath,
      fixtureId: nonEmptyString(fixture.fixtureId, `fixture ${name}.fixtureId`),
      compilerVersion,
      sha256: sha256(bytes),
      problemSpec: plainObject(
        fixture.problemSpec,
        `fixture ${name}.problemSpec`,
      ),
      checkpointIds: checkpoints.map(({ checkpointId }) => checkpointId),
      checkpointTimings: checkpoints.map(
        ({ checkpointId, durationMs, holdAfterMs }) => ({
          checkpointId,
          durationMs,
          holdAfterMs,
          authoredWindowMs: durationMs + holdAfterMs,
        }),
      ),
      finalRevision: checkpoints.at(-1).revision,
      certificateHeadSha256: checkpoints.at(-1).certificateSha256,
      authoredDurationMs: checkpoints.reduce(
        (total, checkpoint) =>
          total + checkpoint.durationMs + checkpoint.holdAfterMs,
        0,
      ),
    });
  }
  if (
    new Set(fixtures.map(({ fixtureId }) => fixtureId)).size !== fixtures.length
  ) {
    fail("fixture catalog", "fixture IDs must be unique");
  }
  if (!fixtures.some(({ fixtureId }) => fixtureId === PRIMARY_FIXTURE_ID)) {
    fail("fixture catalog", `must include ${PRIMARY_FIXTURE_ID}`);
  }
  return fixtures;
}

function loadRawPrimaryFixture(primaryFixture) {
  const absolute = path.join(REPOSITORY_ROOT, primaryFixture.path);
  const bytes = readFileSync(absolute);
  exact(sha256(bytes), primaryFixture.sha256, "raw primary fixture digest");
  const fixture = plainObject(
    parseJson(bytes, "raw primary fixture"),
    "raw primary fixture",
  );
  exact(
    fixture.fixtureId,
    primaryFixture.fixtureId,
    "raw primary fixture.fixtureId",
  );
  return fixture;
}

function fixtureCheckpointEvents(lane, location) {
  return array(lane.events, `${location}.events`).filter(
    (event) => event.type === "projectile_choreography_scene_checkpoint",
  );
}

function applyFixtureEvent(scene, event, location) {
  exact(event.baseRevision, scene.revision, `${location}.baseRevision`);
  const nodes = [...scene.nodes];
  const positions = new Map(nodes.map((node, index) => [node.id, index]));
  const removed = new Set();
  for (const operation of event.patch.operations) {
    if (operation.op === "remove") {
      removed.add(operation.id);
      continue;
    }
    const index = positions.get(operation.node.id);
    if (index === undefined) {
      positions.set(operation.node.id, nodes.length);
      nodes.push(operation.node);
    } else {
      nodes[index] = operation.node;
    }
  }
  return {
    revision: event.resultRevision,
    nodes: nodes.filter((node) => !removed.has(node.id)),
  };
}

function semanticSceneForEvent(event) {
  return {
    certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
    components: [event.semantic.resultComponent],
    revision: event.semantic.semanticResultRevision,
  };
}

function materializeFixtureEvents(events, location) {
  let scene = { revision: 0, nodes: [] };
  return events.map((event, index) => {
    scene = applyFixtureEvent(scene, event, `${location}[${index}]`);
    return {
      event,
      scene,
      semanticScene: semanticSceneForEvent(event),
    };
  });
}

function fixtureTruth(primaryFixture) {
  const fixture = loadRawPrimaryFixture(primaryFixture);
  const lanes = plainObject(fixture.lanes, "raw primary fixture.lanes");
  const mainLane = plainObject(lanes.main, "raw primary fixture.lanes.main");
  const main = materializeFixtureEvents(
    fixtureCheckpointEvents(mainLane, "raw primary fixture.lanes.main"),
    "raw primary fixture main checkpoints",
  );
  sameValue(
    main.at(-1).scene,
    mainLane.expectedTerminal.scene,
    "raw primary fixture main terminal scene",
  );
  sameValue(
    main.at(-1).semanticScene,
    mainLane.expectedTerminal.semanticScene,
    "raw primary fixture main terminal semantic scene",
  );

  const vectorLaneNames = [
    "clarifyApex",
    "continueAfterClarification",
    "retargetAfterSummary",
  ];
  const vectorEvents = [
    ...main.slice(0, 4).map(({ event }) => event),
    ...vectorLaneNames.flatMap((name) =>
      fixtureCheckpointEvents(
        plainObject(lanes[name], `raw primary fixture.lanes.${name}`),
        `raw primary fixture.lanes.${name}`,
      ),
    ),
  ];
  const vector = materializeFixtureEvents(
    vectorEvents,
    "raw primary fixture vector journey",
  );
  const retargetLane = plainObject(
    lanes.retargetAfterSummary,
    "raw primary fixture.lanes.retargetAfterSummary",
  );
  sameValue(
    vector.at(-1).scene,
    retargetLane.expectedTerminal.scene,
    "raw primary fixture retarget terminal scene",
  );
  sameValue(
    vector.at(-1).semanticScene,
    retargetLane.expectedTerminal.semanticScene,
    "raw primary fixture retarget terminal semantic scene",
  );
  return { main, vector };
}

function validateFixtureSource(value, primaryFixture, location) {
  const source = exactKeys(
    value,
    ["fixtureId", "compilerVersion", "fixtureSha256"],
    location,
  );
  exact(source.fixtureId, primaryFixture.fixtureId, `${location}.fixtureId`);
  exact(
    source.compilerVersion,
    primaryFixture.compilerVersion,
    `${location}.compilerVersion`,
  );
  exact(
    sha256Digest(source.fixtureSha256, `${location}.fixtureSha256`),
    primaryFixture.sha256,
    `${location}.fixtureSha256`,
  );
  return {
    fixtureId: source.fixtureId,
    compilerVersion: source.compilerVersion,
    fixtureSha256: source.fixtureSha256,
  };
}

function validateBrowser(value, location) {
  const browser = exactKeys(
    value,
    ["route", "viewport", "layout", "reducedMotion", "colorScheme"],
    location,
  );
  const route = nonEmptyString(browser.route, `${location}.route`);
  exact(
    route,
    "/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=normal&proof=none",
    `${location}.route`,
  );
  const viewport = exactKeys(
    browser.viewport,
    ["width", "height"],
    `${location}.viewport`,
  );
  const width = finiteNumber(viewport.width, `${location}.viewport.width`, {
    integer: true,
    minimum: 1,
  });
  const height = finiteNumber(viewport.height, `${location}.viewport.height`, {
    integer: true,
    minimum: 1,
  });
  exact(width, 1280, `${location}.viewport.width`);
  exact(height, 720, `${location}.viewport.height`);
  exact(browser.layout, "cinematic", `${location}.layout`);
  exact(browser.reducedMotion, false, `${location}.reducedMotion`);
  exact(browser.colorScheme, "dark", `${location}.colorScheme`);
  return {
    route,
    viewport: { width, height },
    layout: browser.layout,
    reducedMotion: browser.reducedMotion,
    colorScheme: browser.colorScheme,
  };
}

function validateBridgeCall(value, index, location) {
  const call = exactKeys(
    value,
    [
      "ordinal",
      "generation",
      "routingMode",
      "problemSpec",
      "baseRevision",
      "semanticRevision",
      "certificateHeadSha256",
      "checkpointId",
      "clarifiedTopics",
      "activeClarification",
      "requestedRoute",
    ],
    location,
  );
  exact(call.ordinal, index + 1, `${location}.ordinal`);
  finiteNumber(call.generation, `${location}.generation`, {
    integer: true,
    minimum: 1,
  });
  exact(call.routingMode, "reflex", `${location}.routingMode`);
  const problemSpec = exactKeys(
    call.problemSpec,
    ["v", "speedMps", "angleDeg"],
    `${location}.problemSpec`,
  );
  exact(problemSpec.v, 1, `${location}.problemSpec.v`);
  exact(problemSpec.speedMps, 20, `${location}.problemSpec.speedMps`);
  exact(problemSpec.angleDeg, 45, `${location}.problemSpec.angleDeg`);
  exact(call.baseRevision, 0, `${location}.baseRevision`);
  exact(call.semanticRevision, 0, `${location}.semanticRevision`);
  exact(call.certificateHeadSha256, null, `${location}.certificateHeadSha256`);
  exact(call.checkpointId, null, `${location}.checkpointId`);
  uniqueStringArray(call.clarifiedTopics, `${location}.clarifiedTopics`, 0);
  exact(call.activeClarification, null, `${location}.activeClarification`);
  const requestedRoute = exactKeys(
    call.requestedRoute,
    ["intent", "targetStage"],
    `${location}.requestedRoute`,
  );
  exact(requestedRoute.intent, "advance", `${location}.requestedRoute.intent`);
  exact(
    requestedRoute.targetStage,
    "solve",
    `${location}.requestedRoute.targetStage`,
  );
  return {
    ordinal: call.ordinal,
    generation: call.generation,
    routingMode: call.routingMode,
    problemSpec: {
      v: problemSpec.v,
      speedMps: problemSpec.speedMps,
      angleDeg: problemSpec.angleDeg,
    },
    baseRevision: call.baseRevision,
    semanticRevision: call.semanticRevision,
    certificateHeadSha256: null,
    checkpointId: null,
    clarifiedTopics: [],
    activeClarification: null,
    requestedRoute: {
      intent: requestedRoute.intent,
      targetStage: requestedRoute.targetStage,
    },
  };
}

function parsePathData(pathD, location) {
  const values = [];
  const token = /([MLZ])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/g;
  let end = 0;
  for (const match of pathD.matchAll(token)) {
    if (!/^[\s,]*$/.test(pathD.slice(end, match.index))) {
      fail(location, "must contain only M/L/Z commands and finite coordinates");
    }
    values.push(match[1] ?? Number(match[2]));
    end = (match.index ?? 0) + match[0].length;
  }
  if (!/^[\s,]*$/.test(pathD.slice(end))) {
    fail(location, "must contain only M/L/Z commands and finite coordinates");
  }
  const points = [];
  let index = 0;
  while (index < values.length && values[index] !== "Z") {
    const command = values[index];
    const x = values[index + 1];
    const y = values[index + 2];
    if (
      command !== (index === 0 ? "M" : "L") ||
      typeof x !== "number" ||
      !Number.isFinite(x) ||
      typeof y !== "number" ||
      !Number.isFinite(y)
    ) {
      fail(location, "must begin with M and continue with L vertices");
    }
    points.push([x, y]);
    index += 3;
  }
  const closed = values[index] === "Z";
  if ((closed ? index + 1 : index) !== values.length || points.length < 2) {
    fail(location, "must be one complete M/L path with an optional terminal Z");
  }
  return { points, closed };
}

function parsePolylinePath(pathD, location) {
  const parsed = parsePathData(pathD, location);
  if (parsed.closed || parsed.points.length !== 33) {
    fail(location, "must contain the verified 33-point polyline");
  }
  return parsed.points;
}

function polylineMetrics(points) {
  const segments = points.slice(1).map((point, index) => ({
    start: points[index],
    end: point,
    length: Math.hypot(
      point[0] - points[index][0],
      point[1] - points[index][1],
    ),
  }));
  const totalLength = segments.reduce(
    (total, segment) => total + segment.length,
    0,
  );
  const pointAt = (fraction) => {
    let remaining = totalLength * fraction;
    for (const segment of segments) {
      if (remaining <= segment.length) {
        const progress = segment.length === 0 ? 0 : remaining / segment.length;
        return [
          segment.start[0] + (segment.end[0] - segment.start[0]) * progress,
          segment.start[1] + (segment.end[1] - segment.start[1]) * progress,
        ];
      }
      remaining -= segment.length;
    }
    return points.at(-1);
  };
  return { totalLength, pointAt };
}

function approximate(actual, expected, location, tolerance = 0.01) {
  if (Math.abs(actual - expected) > tolerance) {
    fail(
      location,
      `must be within ${tolerance} of fixture-derived ${expected}`,
    );
  }
}

function fixturePathNode(record, id, location) {
  const node = record.scene.nodes.find((candidate) => candidate.id === id);
  if (node?.kind !== "path") fail(location, `fixture path ${id} is missing`);
  return node;
}

function validateFixturePathData(value, node, location) {
  const parsed = parsePathData(nonEmptyString(value, location), location);
  sameFixtureValue(
    { kind: "path", ...parsed },
    { kind: "path", points: node.points, closed: node.closed },
    location,
  );
  return parsed;
}

function strictInterpolationProgress(before, active, target, location) {
  if (before.length !== active.length || active.length !== target.length) {
    fail(location, "must preserve the fixture endpoint dimensions");
  }
  let progress;
  for (let index = 0; index < before.length; index += 1) {
    const delta = target[index] - before[index];
    if (Math.abs(delta) <= Number.EPSILON) {
      approximate(active[index], before[index], `${location}[${index}]`, 0.003);
      continue;
    }
    const candidate = (active[index] - before[index]) / delta;
    if (!(candidate > 0 && candidate < 1)) {
      fail(`${location}[${index}]`, "must be strictly between both endpoints");
    }
    if (progress === undefined) progress = candidate;
    else approximate(candidate, progress, `${location}[${index}]`, 0.003);
  }
  if (progress === undefined) fail(location, "fixture endpoints must differ");
  return progress;
}

function parseViewBox(value, location) {
  const text = nonEmptyString(value, location);
  const entries = text.split(" ");
  if (entries.length !== 4 || entries.some((entry) => entry.length === 0)) {
    fail(location, "must be a canonical four-number viewBox");
  }
  const values = entries.map((entry, index) =>
    finiteNumber(Number(entry), `${location}[${index}]`),
  );
  exact(text, values.join(" "), location);
  return values;
}

function viewportString(viewport) {
  return `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;
}

function parseTranslation(value, location) {
  const text = nonEmptyString(value, location);
  const match = text.match(
    /^translate\(\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*[ ,]\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*\)$/i,
  );
  if (!match)
    fail(location, "must be a two-coordinate translate(...) transform");
  return { x: Number(match[1]), y: Number(match[2]) };
}

function pointOnPolyline(points, point) {
  let offset = 0;
  let nearest = { distance: Number.POSITIVE_INFINITY, offset: 0 };
  for (let index = 1; index < points.length; index += 1) {
    const [startX, startY] = points[index - 1];
    const [endX, endY] = points[index];
    const dx = endX - startX;
    const dy = endY - startY;
    const lengthSquared = dx * dx + dy * dy;
    const ratio =
      lengthSquared === 0
        ? 0
        : Math.max(
            0,
            Math.min(
              1,
              ((point.x - startX) * dx + (point.y - startY) * dy) /
                lengthSquared,
            ),
          );
    const projectedX = startX + ratio * dx;
    const projectedY = startY + ratio * dy;
    const segmentLength = Math.sqrt(lengthSquared);
    const distance = Math.hypot(point.x - projectedX, point.y - projectedY);
    if (distance < nearest.distance) {
      nearest = { distance, offset: offset + ratio * segmentLength };
    }
    offset += segmentLength;
  }
  return nearest;
}

function validateRuntimeEvidence(value, primaryFixture, location) {
  const truth = fixtureTruth(primaryFixture);
  const runtime = exactKeys(
    value,
    [
      "scenarioId",
      "settledCheckpointIds",
      "finalCheckpointId",
      "finalRevision",
      "certificateHeadSha256",
      "caption",
      "visibleNodeIds",
      "stableNodeIds",
      "traceCueObserved",
      "markerId",
      "pathIds",
      "motionSamples",
      "pathGeometrySamples",
      "bridgeCalls",
    ],
    location,
  );
  const settledCheckpointIds = uniqueStringArray(
    runtime.settledCheckpointIds,
    `${location}.settledCheckpointIds`,
    EXPECTED_CHECKPOINT_IDS.length,
  );
  assertSameArray(
    settledCheckpointIds,
    primaryFixture.checkpointIds,
    `${location}.settledCheckpointIds`,
  );
  exact(
    runtime.finalCheckpointId,
    primaryFixture.checkpointIds.at(-1),
    `${location}.finalCheckpointId`,
  );
  exact(
    runtime.finalRevision,
    primaryFixture.finalRevision,
    `${location}.finalRevision`,
  );
  exact(
    sha256Digest(
      runtime.certificateHeadSha256,
      `${location}.certificateHeadSha256`,
    ),
    primaryFixture.certificateHeadSha256,
    `${location}.certificateHeadSha256`,
  );
  exact(runtime.traceCueObserved, true, `${location}.traceCueObserved`);
  const visibleNodeIds = uniqueStringArray(
    runtime.visibleNodeIds,
    `${location}.visibleNodeIds`,
  );
  const stableNodeIds = uniqueStringArray(
    runtime.stableNodeIds,
    `${location}.stableNodeIds`,
  );
  if (visibleNodeIds.length === 0 || stableNodeIds.length === 0) {
    fail(location, "visible and stable node evidence must not be empty");
  }
  const markerId = nonEmptyString(runtime.markerId, `${location}.markerId`);
  exact(markerId, "projectile__projectile_marker", `${location}.markerId`);
  assertSameArray(
    stableNodeIds,
    ["projectile__ground", markerId],
    `${location}.stableNodeIds`,
  );
  const pathIds = uniqueStringArray(runtime.pathIds, `${location}.pathIds`, 2);
  assertSameArray(
    pathIds,
    ["projectile__trajectory_ascent", "projectile__trajectory_descent"],
    `${location}.pathIds`,
  );
  const motionSamples = array(
    runtime.motionSamples,
    `${location}.motionSamples`,
    2,
  ).map((entry, index) => {
    const sample = exactKeys(
      entry,
      [
        "checkpointId",
        "pathId",
        "markerId",
        "dashArray",
        "dashOffset",
        "markerTransform",
      ],
      `${location}.motionSamples[${index}]`,
    );
    exact(
      sample.checkpointId,
      index === 0 ? "trace_ascent" : "trace_descent",
      `${location}.motionSamples[${index}].checkpointId`,
    );
    exact(
      sample.pathId,
      pathIds[index],
      `${location}.motionSamples[${index}].pathId`,
    );
    exact(
      sample.markerId,
      markerId,
      `${location}.motionSamples[${index}].markerId`,
    );
    const dashArray = finiteNumber(
      sample.dashArray,
      `${location}.motionSamples[${index}].dashArray`,
      { minimum: 0.001 },
    );
    const dashOffset = finiteNumber(
      sample.dashOffset,
      `${location}.motionSamples[${index}].dashOffset`,
      { minimum: 0.001 },
    );
    if (dashOffset >= dashArray) {
      fail(
        `${location}.motionSamples[${index}].dashOffset`,
        "must prove a partially traced path",
      );
    }
    const markerTransform = nonEmptyString(
      sample.markerTransform,
      `${location}.motionSamples[${index}].markerTransform`,
    );
    if (!/^translate\([^)]*\)$/.test(markerTransform)) {
      fail(
        `${location}.motionSamples[${index}].markerTransform`,
        "must be a translate(...) transform",
      );
    }
    return {
      checkpointId: sample.checkpointId,
      pathId: sample.pathId,
      markerId: sample.markerId,
      dashArray,
      dashOffset,
      markerTransform,
    };
  });
  const expectedFractions = [0, 0.25, 0.5, 0.75, 1];
  const pathGeometrySamples = array(
    runtime.pathGeometrySamples,
    `${location}.pathGeometrySamples`,
    2,
  ).map((entry, index) => {
    const geometry = exactKeys(
      entry,
      ["pathId", "pathD", "totalLength", "samples"],
      `${location}.pathGeometrySamples[${index}]`,
    );
    exact(
      geometry.pathId,
      pathIds[index],
      `${location}.pathGeometrySamples[${index}].pathId`,
    );
    const pathD = nonEmptyString(
      geometry.pathD,
      `${location}.pathGeometrySamples[${index}].pathD`,
    );
    const points = parsePolylinePath(
      pathD,
      `${location}.pathGeometrySamples[${index}].pathD`,
    );
    const fixturePath = truth.main
      .at(-1)
      .scene.nodes.find(({ id }) => id === geometry.pathId);
    if (fixturePath?.kind !== "path") {
      fail(
        `${location}.pathGeometrySamples[${index}]`,
        "fixture path is missing",
      );
    }
    sameFixtureValue(
      { kind: "path", points },
      { kind: "path", points: fixturePath.points },
      `${location}.pathGeometrySamples[${index}].pathD vertices`,
    );
    const metrics = polylineMetrics(points);
    const totalLength = finiteNumber(
      geometry.totalLength,
      `${location}.pathGeometrySamples[${index}].totalLength`,
      { minimum: 0.001 },
    );
    approximate(
      totalLength,
      metrics.totalLength,
      `${location}.pathGeometrySamples[${index}].totalLength`,
    );
    const samples = array(
      geometry.samples,
      `${location}.pathGeometrySamples[${index}].samples`,
      expectedFractions.length,
    ).map((entryValue, sampleIndex) => {
      const sample = exactKeys(
        entryValue,
        ["fraction", "x", "y", "markerTransform"],
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}]`,
      );
      exact(
        sample.fraction,
        expectedFractions[sampleIndex],
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].fraction`,
      );
      const x = finiteNumber(
        sample.x,
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].x`,
      );
      const y = finiteNumber(
        sample.y,
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].y`,
      );
      exact(
        sample.markerTransform,
        `translate(${x} ${y})`,
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].markerTransform`,
      );
      const fixturePoint = metrics.pointAt(sample.fraction);
      approximate(
        x,
        fixturePoint[0],
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].x`,
      );
      approximate(
        y,
        fixturePoint[1],
        `${location}.pathGeometrySamples[${index}].samples[${sampleIndex}].y`,
      );
      return {
        fraction: sample.fraction,
        x,
        y,
        markerTransform: sample.markerTransform,
      };
    });
    const [first, middle, last] = [points[0], points[16], points[32]];
    const twiceArea =
      (middle[0] - first[0]) * (last[1] - first[1]) -
      (middle[1] - first[1]) * (last[0] - first[0]);
    if (Math.abs(twiceArea) < 0.001) {
      fail(
        `${location}.pathGeometrySamples[${index}].pathD`,
        "fixture path must be non-collinear",
      );
    }
    return { pathId: geometry.pathId, pathD, totalLength, samples };
  });
  for (const id of [...pathIds, ...stableNodeIds]) {
    if (!visibleNodeIds.includes(id)) {
      fail(`${location}.visibleNodeIds`, `must retain ${id}`);
    }
  }
  const bridgeCalls = array(
    runtime.bridgeCalls,
    `${location}.bridgeCalls`,
    1,
  ).map((call, index) =>
    validateBridgeCall(call, index, `${location}.bridgeCalls[${index}]`),
  );
  return {
    scenarioId: exact(
      runtime.scenarioId,
      "main_solve",
      `${location}.scenarioId`,
    ),
    settledCheckpointIds,
    finalCheckpointId: runtime.finalCheckpointId,
    finalRevision: runtime.finalRevision,
    certificateHeadSha256: runtime.certificateHeadSha256,
    caption: nonEmptyString(runtime.caption, `${location}.caption`),
    visibleNodeIds,
    stableNodeIds,
    traceCueObserved: runtime.traceCueObserved,
    markerId,
    pathIds,
    motionSamples,
    pathGeometrySamples,
    bridgeCalls,
  };
}

function validateRuntime(value, primaryFixture, location) {
  const runtime = exactKeys(value, ["evidence", "evidenceSha256"], location);
  const expectedDigest = sha256(
    Buffer.from(JSON.stringify(runtime.evidence), "utf8"),
  );
  exact(
    sha256Digest(runtime.evidenceSha256, `${location}.evidenceSha256`),
    expectedDigest,
    `${location}.evidenceSha256`,
  );
  return {
    evidence: validateRuntimeEvidence(
      runtime.evidence,
      primaryFixture,
      `${location}.evidence`,
    ),
    evidenceSha256: runtime.evidenceSha256,
  };
}

function validateNetwork(value, location) {
  const network = exactKeys(
    value,
    [
      "providerRequestCount",
      "liveSceneRequests",
      "unexpectedRequests",
      "failedRequests",
    ],
    location,
  );
  exact(network.providerRequestCount, 0, `${location}.providerRequestCount`);
  const liveSceneRequests = uniqueStringArray(
    network.liveSceneRequests,
    `${location}.liveSceneRequests`,
    0,
  );
  const unexpectedRequests = uniqueStringArray(
    network.unexpectedRequests,
    `${location}.unexpectedRequests`,
    0,
  );
  const failedRequests = uniqueStringArray(
    network.failedRequests,
    `${location}.failedRequests`,
    0,
  );
  return {
    providerRequestCount: network.providerRequestCount,
    liveSceneRequests,
    unexpectedRequests,
    failedRequests,
  };
}

function validateTiming(value, primaryFixture, location) {
  const timing = exactKeys(
    value,
    [
      "startedAtMs",
      "firstMeaningfulVisualAtMs",
      "completedAtMs",
      "visualDurationMs",
      "checkpointSettlements",
    ],
    location,
  );
  const startedAtMs = finiteNumber(
    timing.startedAtMs,
    `${location}.startedAtMs`,
    {
      minimum: 0,
    },
  );
  const firstMeaningfulVisualAtMs = finiteNumber(
    timing.firstMeaningfulVisualAtMs,
    `${location}.firstMeaningfulVisualAtMs`,
    { minimum: startedAtMs },
  );
  const completedAtMs = finiteNumber(
    timing.completedAtMs,
    `${location}.completedAtMs`,
    { minimum: firstMeaningfulVisualAtMs },
  );
  const visualDurationMs = finiteNumber(
    timing.visualDurationMs,
    `${location}.visualDurationMs`,
    { minimum: 1 },
  );
  const measuredDuration = completedAtMs - firstMeaningfulVisualAtMs;
  if (Math.abs(measuredDuration - visualDurationMs) > 250) {
    fail(
      `${location}.visualDurationMs`,
      "must match completedAtMs - firstMeaningfulVisualAtMs within 250ms",
    );
  }
  const firstMeaningfulLatencyMs = firstMeaningfulVisualAtMs - startedAtMs;
  if (firstMeaningfulLatencyMs >= 300) {
    fail(
      `${location}.firstMeaningfulVisualAtMs`,
      "must be less than 300ms after the lesson starts",
    );
  }
  const acceptedMinimum = 35_000;
  const acceptedMaximum = 45_000;
  if (
    visualDurationMs < acceptedMinimum ||
    visualDurationMs > acceptedMaximum
  ) {
    fail(
      `${location}.visualDurationMs`,
      `must stay within the authored real-time envelope ${acceptedMinimum}..${acceptedMaximum}ms`,
    );
  }
  let previousAtMs = startedAtMs;
  const checkpointWindows = array(
    timing.checkpointSettlements,
    `${location}.checkpointSettlements`,
    EXPECTED_CHECKPOINT_IDS.length,
  ).map((entry, index) => {
    const settlement = exactKeys(
      entry,
      ["checkpointId", "settledAtMs"],
      `${location}.checkpointSettlements[${index}]`,
    );
    const expected = primaryFixture.checkpointTimings[index];
    exact(
      settlement.checkpointId,
      expected.checkpointId,
      `${location}.checkpointSettlements[${index}].checkpointId`,
    );
    const settledAtMs = finiteNumber(
      settlement.settledAtMs,
      `${location}.checkpointSettlements[${index}].settledAtMs`,
      { minimum: previousAtMs },
    );
    const elapsedMs = settledAtMs - previousAtMs;
    const unexplainedMs = Math.max(0, elapsedMs - expected.authoredWindowMs);
    if (unexplainedMs > 1_200) {
      fail(
        `${location}.checkpointSettlements[${index}]`,
        `contains ${unexplainedMs}ms of unexplained delay (maximum 1200ms)`,
      );
    }
    previousAtMs = settledAtMs;
    return {
      checkpointId: settlement.checkpointId,
      settledAtMs,
      elapsedMs,
      authoredWindowMs: expected.authoredWindowMs,
      unexplainedMs,
    };
  });
  const completionTailMs = completedAtMs - previousAtMs;
  if (completionTailMs < 0 || completionTailMs > 1_200) {
    fail(
      `${location}.completedAtMs`,
      "must follow the final settlement by no more than 1200ms",
    );
  }
  return {
    startedAtMs,
    firstMeaningfulVisualAtMs,
    completedAtMs,
    visualDurationMs,
    firstMeaningfulLatencyMs,
    authoredDurationMs: primaryFixture.authoredDurationMs,
    acceptedRangeMs: [acceptedMinimum, acceptedMaximum],
    maximumUnexplainedMs: 1_200,
    checkpointWindows,
    completionTailMs,
  };
}

function validateArtifactDescriptor(
  value,
  role,
  location,
  expectedPath = EXPECTED_ARTIFACT_PATHS[role],
) {
  const isVideo = role === "video";
  const descriptor = exactKeys(
    value,
    isVideo
      ? ["path", "bytes", "sha256"]
      : ["path", "bytes", "sha256", "width", "height"],
    location,
  );
  const relativePath = normalizeRelativeArtifactPath(
    descriptor.path,
    `${location}.path`,
  );
  exact(relativePath, expectedPath, `${location}.path`);
  const result = {
    path: relativePath,
    bytes: finiteNumber(descriptor.bytes, `${location}.bytes`, {
      integer: true,
      minimum: 1,
    }),
    sha256: sha256Digest(descriptor.sha256, `${location}.sha256`),
  };
  if (!isVideo) {
    result.width = finiteNumber(descriptor.width, `${location}.width`, {
      integer: true,
      minimum: 1,
    });
    result.height = finiteNumber(descriptor.height, `${location}.height`, {
      integer: true,
      minimum: 1,
    });
  }
  return result;
}

function validateExecution(value, location) {
  const execution = exactKeys(value, ["source", "environment"], location);
  return {
    source: validateSource(execution.source, `${location}.source`),
    environment: validateEnvironment(
      execution.environment,
      `${location}.environment`,
    ),
  };
}

function validateFirstMeaningfulEvidence(value, location) {
  const evidence = exactKeys(
    value,
    ["samplesMs", "p95Ms", "thresholdExclusiveMs"],
    location,
  );
  const samplesMs = array(evidence.samplesMs, `${location}.samplesMs`, 20).map(
    (sample, index) =>
      finiteNumber(sample, `${location}.samplesMs[${index}]`, { minimum: 0 }),
  );
  const sorted = [...samplesMs].sort((left, right) => left - right);
  const p95Ms = sorted[Math.ceil(sorted.length * 0.95) - 1];
  exact(evidence.p95Ms, p95Ms, `${location}.p95Ms`);
  exact(evidence.thresholdExclusiveMs, 300, `${location}.thresholdExclusiveMs`);
  if (p95Ms >= evidence.thresholdExclusiveMs) {
    fail(`${location}.p95Ms`, "must be below 300ms");
  }
  return { samplesMs, p95Ms, thresholdExclusiveMs: 300 };
}

function roundedEvidenceNumber(value, location, minimum) {
  const result = finiteNumber(
    value,
    location,
    minimum === undefined ? {} : { minimum },
  );
  if (Number(result.toFixed(3)) !== result) {
    fail(location, "must be rounded to at most three decimal places");
  }
  return result;
}

function validateCssPoint(value, location) {
  const point = exactKeys(value, ["x", "y"], location);
  roundedEvidenceNumber(point.x, location + ".x");
  roundedEvidenceNumber(point.y, location + ".y");
  return point;
}

function validateDistance(left, right, reported, location) {
  const errorCssPx = roundedEvidenceNumber(reported, location, 0);
  const expected = Math.hypot(left.x - right.x, left.y - right.y);
  if (Math.abs(errorCssPx - expected) > 0.003) {
    fail(location, "must equal the independently recomputed distance");
  }
  if (errorCssPx > 1) {
    fail(location, "must be at most one CSS pixel");
  }
}

function validateTraceTipSamples(value, location) {
  return array(value, location, TRACE_TIP_SPECS.length).map((entry, index) => {
    const itemLocation = location + "[" + index + "]";
    const sample = exactKeys(
      entry,
      [
        "checkpointId",
        "pathId",
        "localProgress",
        "dashArray",
        "dashOffset",
        "revealedProgress",
        "markerCss",
        "traceTipCss",
        "errorCssPx",
      ],
      itemLocation,
    );
    ["checkpointId", "pathId", "localProgress"].forEach((key, part) =>
      exact(
        sample[key],
        TRACE_TIP_SPECS[index][part],
        itemLocation + "." + key,
      ),
    );
    const dashArray = roundedEvidenceNumber(
      sample.dashArray,
      itemLocation + ".dashArray",
      Number.EPSILON,
    );
    const dashOffset = roundedEvidenceNumber(
      sample.dashOffset,
      itemLocation + ".dashOffset",
      0,
    );
    if (dashOffset > dashArray) {
      fail(itemLocation + ".dashOffset", "must not exceed dashArray");
    }
    const revealedProgress = roundedEvidenceNumber(
      sample.revealedProgress,
      itemLocation + ".revealedProgress",
      0,
    );
    if (Math.abs(revealedProgress - (1 - dashOffset / dashArray)) > 0.003) {
      fail(
        itemLocation + ".revealedProgress",
        "must equal the independently recomputed dash progress",
      );
    }
    if (Math.abs(revealedProgress - TRACE_TIP_SPECS[index][2]) > 0.035) {
      fail(
        itemLocation + ".revealedProgress",
        "must be within 0.035 of the requested local progress",
      );
    }
    validateDistance(
      validateCssPoint(sample.markerCss, itemLocation + ".markerCss"),
      validateCssPoint(sample.traceTipCss, itemLocation + ".traceTipCss"),
      sample.errorCssPx,
      itemLocation + ".errorCssPx",
    );
    return sample;
  });
}

function fixtureNodeSignature(node) {
  if (node.kind === "line") {
    return {
      kind: "line",
      id: node.id,
      points: node.points,
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
      strokeLinecap: "round",
      strokeLinejoin: "round",
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
  fail("raw primary fixture node", `unsupported kind ${node.kind}`);
}

function expectedSemanticDom(record) {
  const viewport = record.event.semantic.presentation.resultViewports.cinematic;
  const labels = new Set(["text", "latex", "latex_token"]);
  return {
    sourceRevision: record.scene.revision,
    viewBox: `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`,
    paintOrder: [
      ...record.scene.nodes.filter((node) => !labels.has(node.kind)),
      ...record.scene.nodes.filter((node) => labels.has(node.kind)),
    ].map(({ id }) => id),
    nodes: record.scene.nodes.map(fixtureNodeSignature),
    residueFree: true,
  };
}

function validateSemanticDomSignature(value, record, location) {
  const signature = exactKeys(
    value,
    ["sourceRevision", "viewBox", "paintOrder", "nodes", "residueFree"],
    location,
  );
  sameFixtureValue(signature, expectedSemanticDom(record), location, {
    allowSemanticLineQuantization: true,
  });
  return signature;
}

function validateCanonicalTerminal(value, truth, location) {
  const terminal = exactKeys(
    value,
    [
      "animatedSemanticDom",
      "reducedMotionSemanticDom",
      "replaySemanticDom",
      "replayProviderRequestCount",
    ],
    location,
  );
  const semanticDoms = ["animated", "reducedMotion", "replay"].map((name) =>
    validateSemanticDomSignature(
      terminal[name + "SemanticDom"],
      truth.main.at(-1),
      `${location}.${name}SemanticDom`,
    ),
  );
  for (const signature of semanticDoms.slice(1)) {
    sameValue(signature, semanticDoms[0], `${location} terminal semantic DOM`);
  }
  exact(
    terminal.replayProviderRequestCount,
    0,
    `${location}.replayProviderRequestCount`,
  );
  return terminal;
}

function validateAcceptedPrefix(value, expected, location) {
  return array(value, location, expected.length).map((entry, index) => {
    const itemLocation = `${location}[${index}]`;
    const accepted = exactKeys(
      entry,
      ["event", "scene", "semanticScene", "viewport", "layout", "presentation"],
      itemLocation,
    );
    const fixture = expected[index];
    sameFixtureValue(
      accepted.event,
      fixture.event,
      `${itemLocation}.event fixture body`,
    );
    sameFixtureValue(accepted.scene, fixture.scene, `${itemLocation}.scene`);
    sameValue(
      accepted.semanticScene,
      fixture.semanticScene,
      `${itemLocation}.semanticScene`,
    );
    exact(accepted.layout, "cinematic", `${itemLocation}.layout`);
    sameValue(
      accepted.viewport,
      fixture.event.semantic.presentation.resultViewports.cinematic,
      `${itemLocation}.viewport`,
    );
    const presentation = exactKeys(
      accepted.presentation,
      [
        "type",
        "checkpointId",
        "certificateSha256",
        "sceneRevision",
        "semanticRevision",
        "layout",
        "resultViewport",
        "settlement",
      ],
      `${itemLocation}.presentation`,
    );
    exact(
      presentation.type,
      "projectile_choreography_checkpoint_presented",
      `${itemLocation}.presentation.type`,
    );
    exact(
      presentation.checkpointId,
      fixture.event.semantic.checkpointId,
      `${itemLocation}.presentation.checkpointId`,
    );
    exact(
      presentation.certificateSha256,
      fixture.event.semantic.semanticResultCertificateSha256,
      `${itemLocation}.presentation.certificateSha256`,
    );
    exact(
      presentation.sceneRevision,
      fixture.scene.revision,
      `${itemLocation}.presentation.sceneRevision`,
    );
    exact(
      presentation.semanticRevision,
      fixture.semanticScene.revision,
      `${itemLocation}.presentation.semanticRevision`,
    );
    exact(
      presentation.layout,
      "cinematic",
      `${itemLocation}.presentation.layout`,
    );
    sameValue(
      presentation.resultViewport,
      accepted.viewport,
      `${itemLocation}.presentation.resultViewport`,
    );
    exact(
      presentation.settlement,
      fixture.settlement,
      `${itemLocation}.presentation.settlement`,
    );
    return accepted;
  });
}

function expectedAcceptedRecords(records, cancelledIndex, continuationStart) {
  return records.map((record, index) => ({
    ...record,
    event:
      continuationStart !== undefined && index >= continuationStart
        ? {
            ...record.event,
            generation: 2,
            sequence: index - continuationStart + 1,
          }
        : record.event,
    settlement:
      index === cancelledIndex ? "cancelled_to_checkpoint" : "completed",
  }));
}

function validateInterruptionPayload(value, frontier, expected, location) {
  const payload = exactKeys(
    value,
    [
      "semanticDomProjection",
      "caption",
      "phase",
      "generation",
      "visibleCheckpointId",
      "committedScene",
      "committedSemanticScene",
      "accepted",
    ],
    location,
  );
  validateSemanticDomSignature(
    payload.semanticDomProjection,
    expected.record,
    `${location}.semanticDomProjection`,
  );
  exact(
    payload.caption,
    expected.record.event.patch.narration,
    `${location}.caption`,
  );
  exact(payload.phase, frontier.phase, `${location}.phase`);
  exact(payload.generation, frontier.generation, `${location}.generation`);
  exact(
    payload.visibleCheckpointId,
    frontier.checkpointId,
    `${location}.visibleCheckpointId`,
  );
  sameFixtureValue(
    payload.committedScene,
    expected.record.scene,
    `${location}.committedScene`,
  );
  sameValue(
    payload.committedSemanticScene,
    expected.record.semanticScene,
    `${location}.committedSemanticScene`,
  );
  validateAcceptedPrefix(
    payload.accepted,
    expected.prefix,
    `${location}.accepted`,
  );
  return payload;
}

function validateInterruptionFrontier(value, expected, location) {
  const frontier = exactKeys(
    value,
    [
      "generation",
      "attempt",
      "sequence",
      "phase",
      "checkpointId",
      "revision",
      "certificateHeadSha256",
      "payload",
    ],
    location,
  );
  exact(frontier.generation, expected.generation, `${location}.generation`);
  exact(frontier.attempt, expected.attempt, `${location}.attempt`);
  const sequence = finiteNumber(frontier.sequence, `${location}.sequence`, {
    integer: true,
    minimum: expected.minimumSequence,
  });
  if (sequence > expected.maximumSequence) {
    fail(`${location}.sequence`, `must not exceed ${expected.maximumSequence}`);
  }
  exact(frontier.phase, expected.phase, `${location}.phase`);
  exact(
    frontier.checkpointId,
    expected.record.event.semantic.checkpointId,
    `${location}.checkpointId`,
  );
  exact(
    frontier.revision,
    expected.record.scene.revision,
    `${location}.revision`,
  );
  exact(
    frontier.certificateHeadSha256,
    expected.record.semanticScene.certificateHeadSha256,
    `${location}.certificateHeadSha256`,
  );
  validateInterruptionPayload(
    frontier.payload,
    frontier,
    expected,
    `${location}.payload`,
  );
  return frontier;
}

function expectedInterruptionFrontier(truth, category, terminal = false) {
  if (category === "vector_morph") {
    const prefix = expectedAcceptedRecords(
      truth.vector,
      truth.vector.length - 1,
    );
    return {
      record: prefix.at(-1),
      prefix,
      generation: 4,
      attempt: prefix.at(-1).event.attempt,
      minimumSequence: prefix.at(-1).event.sequence,
      maximumSequence: prefix.at(-1).event.sequence,
      phase: "interrupted",
    };
  }
  const mainIndex = INTERRUPTION_SPECS[category].mainIndex;
  if (terminal) {
    const prefix = expectedAcceptedRecords(
      truth.main,
      mainIndex,
      mainIndex + 1,
    );
    return {
      record: prefix.at(-1),
      prefix,
      generation: 2,
      attempt: prefix.at(-1).event.attempt,
      minimumSequence: prefix.at(-1).event.sequence,
      maximumSequence: prefix.at(-1).event.sequence,
      phase: "completed",
    };
  }
  const prefix = expectedAcceptedRecords(
    truth.main.slice(0, mainIndex + 1),
    mainIndex,
  );
  return {
    record: prefix.at(-1),
    prefix,
    generation: 1,
    attempt: prefix.at(-1).event.attempt,
    minimumSequence: prefix.at(-1).event.sequence,
    maximumSequence: truth.main.at(-1).event.sequence,
    phase: "interrupted",
  };
}

function validateActiveSurface(value, category, truth, location) {
  const surface = plainObject(value, location);
  exact(surface.kind, category, `${location}.kind`);
  exact(
    surface.checkpointId,
    INTERRUPTION_SPECS[category].checkpointId,
    `${location}.checkpointId`,
  );
  const schemas = {
    path_trace: ["kind", "checkpointId", "targetId", "dashArray", "dashOffset"],
    marker_motion: [
      "kind",
      "checkpointId",
      "targetId",
      "firstTransform",
      "secondTransform",
      "firstPoint",
      "secondPoint",
      "displacementSvgUnits",
    ],
    vector_morph: [
      "kind",
      "checkpointId",
      "targetId",
      "beforePathD",
      "firstActivePathD",
      "secondActivePathD",
      "targetPathD",
    ],
    focus: [
      "kind",
      "checkpointId",
      "targetId",
      "beforeViewBox",
      "activeViewBox",
      "targetViewBox",
    ],
    equation_morph: ["kind", "checkpointId", "targetId", "opacity"],
    hold: [
      "kind",
      "checkpointId",
      "targetId",
      "dashArray",
      "dashOffset",
      "settledMainCount",
    ],
  };
  exactKeys(surface, schemas[category], location);
  const targets = {
    path_trace: "projectile__trajectory_ascent",
    marker_motion: "projectile__projectile_marker",
    vector_morph: "projectile__velocity_resultant",
    focus: "live-choreography-board",
    equation_morph: "projectile__vertical_state",
    hold: "projectile__velocity_resultant",
  };
  exact(surface.targetId, targets[category], `${location}.targetId`);
  if (category === "path_trace" || category === "hold") {
    const arrayValue = roundedEvidenceNumber(
      surface.dashArray,
      `${location}.dashArray`,
      Number.EPSILON,
    );
    const offset = roundedEvidenceNumber(
      surface.dashOffset,
      `${location}.dashOffset`,
      0,
    );
    if (category === "path_trace" && !(offset > 0 && offset < arrayValue)) {
      fail(`${location}.dashOffset`, "must prove an active partial trace");
    }
    if (category === "hold") {
      if (offset > 0.003)
        fail(`${location}.dashOffset`, "must prove the trace is settled");
      exact(surface.settledMainCount, 0, `${location}.settledMainCount`);
    }
  } else if (category === "marker_motion") {
    const firstPoint = validateCssPoint(
      surface.firstPoint,
      `${location}.firstPoint`,
    );
    const secondPoint = validateCssPoint(
      surface.secondPoint,
      `${location}.secondPoint`,
    );
    for (const [name, point] of [
      ["first", firstPoint],
      ["second", secondPoint],
    ]) {
      const transform = parseTranslation(
        surface[`${name}Transform`],
        `${location}.${name}Transform`,
      );
      approximate(transform.x, point.x, `${location}.${name}Point.x`, 0.001);
      approximate(transform.y, point.y, `${location}.${name}Point.y`, 0.001);
    }
    const displacement = roundedEvidenceNumber(
      surface.displacementSvgUnits,
      `${location}.displacementSvgUnits`,
      0,
    );
    approximate(
      displacement,
      Math.hypot(secondPoint.x - firstPoint.x, secondPoint.y - firstPoint.y),
      `${location}.displacementSvgUnits`,
      0.003,
    );
    if (displacement <= 0.01) {
      fail(`${location}.displacementSvgUnits`, "must exceed 0.01 SVG units");
    }
    const originNode = fixturePathNode(
      truth.main[1],
      "projectile__projectile_marker",
      location,
    );
    const xs = originNode.points.map(([x]) => x);
    const ys = originNode.points.map(([, y]) => y);
    const origin = {
      x: (Math.min(...xs) + Math.max(...xs)) / 2,
      y: (Math.min(...ys) + Math.max(...ys)) / 2,
    };
    const trajectory = fixturePathNode(
      truth.main[2],
      "projectile__trajectory_ascent",
      location,
    );
    const projections = [firstPoint, secondPoint].map((point, index) => {
      const projected = pointOnPolyline(trajectory.points, {
        x: origin.x + point.x,
        y: origin.y + point.y,
      });
      if (projected.distance > 0.01) {
        fail(
          `${location}.${index === 0 ? "firstPoint" : "secondPoint"}`,
          "must place the marker on the fixture trajectory",
        );
      }
      return projected;
    });
    if (projections[1].offset <= projections[0].offset) {
      fail(location, "second marker sample must advance along the trajectory");
    }
  } else if (category === "vector_morph") {
    const id = "projectile__velocity_resultant";
    const before = validateFixturePathData(
      surface.beforePathD,
      fixturePathNode(truth.vector.at(-2), id, location),
      `${location}.beforePathD`,
    );
    const target = validateFixturePathData(
      surface.targetPathD,
      fixturePathNode(truth.vector.at(-1), id, location),
      `${location}.targetPathD`,
    );
    const active = ["firstActivePathD", "secondActivePathD"].map((key) => {
      const parsed = parsePathData(
        nonEmptyString(surface[key], `${location}.${key}`),
        `${location}.${key}`,
      );
      exact(parsed.closed, before.closed, `${location}.${key} closure`);
      array(parsed.points, `${location}.${key} points`, before.points.length);
      return parsed.points.flat();
    });
    const beforeValues = before.points.flat();
    const targetValues = target.points.flat();
    const firstProgress = strictInterpolationProgress(
      beforeValues,
      active[0],
      targetValues,
      `${location}.firstActivePathD`,
    );
    const secondProgress = strictInterpolationProgress(
      beforeValues,
      active[1],
      targetValues,
      `${location}.secondActivePathD`,
    );
    if (secondProgress <= firstProgress) {
      fail(location, "active vector samples must be distinct and ordered");
    }
  } else if (category === "focus") {
    const checkpoint = truth.main[2].event.semantic.presentation;
    const before = viewportString(checkpoint.baseViewports.cinematic);
    const target = viewportString(checkpoint.resultViewports.cinematic);
    exact(surface.beforeViewBox, before, `${location}.beforeViewBox`);
    exact(surface.targetViewBox, target, `${location}.targetViewBox`);
    strictInterpolationProgress(
      parseViewBox(before, `${location}.beforeViewBox`),
      parseViewBox(surface.activeViewBox, `${location}.activeViewBox`),
      parseViewBox(target, `${location}.targetViewBox`),
      `${location}.activeViewBox`,
    );
  } else {
    const opacity = roundedEvidenceNumber(
      surface.opacity,
      `${location}.opacity`,
      0,
    );
    if (opacity <= 0 || opacity >= 1)
      fail(`${location}.opacity`, "must prove an active equation morph");
  }
  return surface;
}

function validateInterruptionEvidence(value, truth, location) {
  const evidence = exactKeys(
    value,
    [
      "categories",
      "repetitionsPerCategory",
      "staleWindowMs",
      "thresholdExclusiveMs",
      "p95Ms",
      "trials",
    ],
    location,
  );
  assertSameArray(
    uniqueStringArray(
      evidence.categories,
      location + ".categories",
      INTERRUPTION_CATEGORIES.length,
    ),
    INTERRUPTION_CATEGORIES,
    location + ".categories",
  );
  exact(
    evidence.repetitionsPerCategory,
    INTERRUPTION_REPETITIONS_PER_CATEGORY,
    location + ".repetitionsPerCategory",
  );
  exact(evidence.staleWindowMs, 2_000, location + ".staleWindowMs");
  exact(evidence.thresholdExclusiveMs, 150, location + ".thresholdExclusiveMs");

  const settleSamples = [];
  array(
    evidence.trials,
    location + ".trials",
    INTERRUPTION_SAMPLE_COUNT,
  ).forEach((entry, index) => {
    const itemLocation = location + ".trials[" + index + "]";
    const trial = exactKeys(
      entry,
      [
        "ordinal",
        "category",
        "requestedAtMs",
        "settledAtMs",
        "staleObservedAtMs",
        "staleDomMutationCount",
        "staleRuntimePublicationCount",
        "activeSurface",
        "immediate",
        "afterStaleWindow",
        "terminal",
      ],
      itemLocation,
    );
    exact(trial.ordinal, index + 1, itemLocation + ".ordinal");
    const expectedCategory =
      INTERRUPTION_CATEGORIES[
        Math.floor(index / INTERRUPTION_REPETITIONS_PER_CATEGORY)
      ];
    exact(trial.category, expectedCategory, itemLocation + ".category");
    const requestedAtMs = roundedEvidenceNumber(
      trial.requestedAtMs,
      `${itemLocation}.requestedAtMs`,
      0,
    );
    const settledAtMs = roundedEvidenceNumber(
      trial.settledAtMs,
      `${itemLocation}.settledAtMs`,
      requestedAtMs,
    );
    settleSamples.push(Number((settledAtMs - requestedAtMs).toFixed(3)));
    const staleObservedAtMs = roundedEvidenceNumber(
      trial.staleObservedAtMs,
      `${itemLocation}.staleObservedAtMs`,
      settledAtMs,
    );
    if (staleObservedAtMs - settledAtMs < evidence.staleWindowMs) {
      fail(
        `${itemLocation}.staleObservedAtMs`,
        "must observe at least 2000ms after settlement",
      );
    }
    exact(
      trial.staleDomMutationCount,
      0,
      `${itemLocation}.staleDomMutationCount`,
    );
    exact(
      trial.staleRuntimePublicationCount,
      0,
      `${itemLocation}.staleRuntimePublicationCount`,
    );
    validateActiveSurface(
      trial.activeSurface,
      trial.category,
      truth,
      `${itemLocation}.activeSurface`,
    );
    const interruptedExpected = expectedInterruptionFrontier(
      truth,
      trial.category,
    );
    const immediate = validateInterruptionFrontier(
      trial.immediate,
      interruptedExpected,
      `${itemLocation}.immediate`,
    );
    const after = validateInterruptionFrontier(
      trial.afterStaleWindow,
      interruptedExpected,
      `${itemLocation}.afterStaleWindow`,
    );
    sameValue(after, immediate, itemLocation + " stale-window frontier");
    const terminal = validateInterruptionFrontier(
      trial.terminal,
      expectedInterruptionFrontier(truth, trial.category, true),
      `${itemLocation}.terminal`,
    );
    if (trial.category === "vector_morph") {
      sameValue(terminal, immediate, itemLocation + " vector-morph terminal");
    }
  });
  const ordered = settleSamples.sort((left, right) => left - right);
  const p95Ms = ordered[Math.ceil(ordered.length * 0.95) - 1];
  exact(evidence.p95Ms, p95Ms, location + ".p95Ms");
  if (p95Ms >= 150) fail(location + ".p95Ms", "must be below 150ms");
  return evidence;
}

function validateMotionBoundaryEvidence(value, truth, location) {
  const evidence = exactKeys(
    value,
    ["traceTipSamples", "canonicalTerminal", "interruption"],
    location,
  );
  validateTraceTipSamples(
    evidence.traceTipSamples,
    location + ".traceTipSamples",
  );
  validateCanonicalTerminal(
    evidence.canonicalTerminal,
    truth,
    location + ".canonicalTerminal",
  );
  validateInterruptionEvidence(
    evidence.interruption,
    truth,
    location + ".interruption",
  );
  return evidence;
}
function validateAcceleratedObservation(value, primaryFixture, location) {
  const root = exactKeys(
    value,
    ["v", "gate", "execution", "firstMeaningful", "motionBoundary"],
    location,
  );
  exact(root.v, 1, `${location}.v`);
  exact(root.gate, "1.7", `${location}.gate`);
  return {
    v: root.v,
    gate: root.gate,
    execution: validateExecution(root.execution, `${location}.execution`),
    firstMeaningful: validateFirstMeaningfulEvidence(
      root.firstMeaningful,
      `${location}.firstMeaningful`,
    ),
    motionBoundary: validateMotionBoundaryEvidence(
      root.motionBoundary,
      fixtureTruth(primaryFixture),
      `${location}.motionBoundary`,
    ),
  };
}

export function validateAcceleratedObservationForTests(value, primaryFixture) {
  return validateAcceleratedObservation(
    value,
    primaryFixture,
    "accelerated observations",
  );
}

export function validateCaptureObservationForTests(value, primaryFixture) {
  return validateCaptureObservation(
    value,
    primaryFixture,
    "capture observations",
  );
}

function validateCaptureObservation(value, primaryFixture, location) {
  const root = exactKeys(
    value,
    [
      "v",
      "gate",
      "execution",
      "fixtureSource",
      "browser",
      "runtime",
      "network",
      "timing",
      "artifacts",
    ],
    location,
  );
  exact(root.v, 1, `${location}.v`);
  exact(root.gate, "1.7", `${location}.gate`);
  const artifacts = exactKeys(
    root.artifacts,
    [
      "video",
      "pageScreenshot",
      "boardScreenshot",
      "contactSheet",
      "checkpointScreenshots",
    ],
    `${location}.artifacts`,
  );
  const checkpointScreenshots = array(
    artifacts.checkpointScreenshots,
    `${location}.artifacts.checkpointScreenshots`,
    EXPECTED_CHECKPOINT_IDS.length,
  ).map((entry, index) => {
    const screenshot = exactKeys(
      entry,
      ["checkpointId", "path", "bytes", "sha256", "width", "height"],
      `${location}.artifacts.checkpointScreenshots[${index}]`,
    );
    const checkpointId = EXPECTED_CHECKPOINT_IDS[index];
    exact(
      screenshot.checkpointId,
      checkpointId,
      `${location}.artifacts.checkpointScreenshots[${index}].checkpointId`,
    );
    const ordinal = String(index + 1).padStart(2, "0");
    const descriptor = validateArtifactDescriptor(
      {
        path: screenshot.path,
        bytes: screenshot.bytes,
        sha256: screenshot.sha256,
        width: screenshot.width,
        height: screenshot.height,
      },
      "checkpointScreenshot",
      `${location}.artifacts.checkpointScreenshots[${index}]`,
      `capture/checkpoints/${ordinal}-${checkpointId}.png`,
    );
    return { checkpointId, ...descriptor };
  });
  const video = validateArtifactDescriptor(
    artifacts.video,
    "video",
    `${location}.artifacts.video`,
  );
  const pageScreenshot = validateArtifactDescriptor(
    artifacts.pageScreenshot,
    "pageScreenshot",
    `${location}.artifacts.pageScreenshot`,
  );
  const boardScreenshot = validateArtifactDescriptor(
    artifacts.boardScreenshot,
    "boardScreenshot",
    `${location}.artifacts.boardScreenshot`,
  );
  const contactSheet = validateArtifactDescriptor(
    artifacts.contactSheet,
    "contactSheet",
    `${location}.artifacts.contactSheet`,
  );
  const [firstCheckpointScreenshot] = checkpointScreenshots;
  for (const [index, screenshot] of checkpointScreenshots.entries()) {
    exact(
      screenshot.width,
      firstCheckpointScreenshot.width,
      `${location}.artifacts.checkpointScreenshots[${index}].width`,
    );
    exact(
      screenshot.height,
      firstCheckpointScreenshot.height,
      `${location}.artifacts.checkpointScreenshots[${index}].height`,
    );
  }
  exact(
    contactSheet.width,
    firstCheckpointScreenshot.width * 2,
    `${location}.artifacts.contactSheet.width`,
  );
  exact(
    contactSheet.height,
    firstCheckpointScreenshot.height * 3,
    `${location}.artifacts.contactSheet.height`,
  );
  const finalCheckpointScreenshot = checkpointScreenshots.at(-1);
  exact(
    boardScreenshot.sha256,
    finalCheckpointScreenshot.sha256,
    `${location}.artifacts.boardScreenshot.sha256`,
  );
  exact(
    boardScreenshot.width,
    finalCheckpointScreenshot.width,
    `${location}.artifacts.boardScreenshot.width`,
  );
  exact(
    boardScreenshot.height,
    finalCheckpointScreenshot.height,
    `${location}.artifacts.boardScreenshot.height`,
  );
  return {
    v: root.v,
    gate: root.gate,
    execution: validateExecution(root.execution, `${location}.execution`),
    fixtureSource: validateFixtureSource(
      root.fixtureSource,
      primaryFixture,
      `${location}.fixtureSource`,
    ),
    browser: validateBrowser(root.browser, `${location}.browser`),
    runtime: validateRuntime(
      root.runtime,
      primaryFixture,
      `${location}.runtime`,
    ),
    network: validateNetwork(root.network, `${location}.network`),
    timing: validateTiming(root.timing, primaryFixture, `${location}.timing`),
    artifacts: {
      video,
      pageScreenshot,
      boardScreenshot,
      contactSheet,
      checkpointScreenshots,
    },
  };
}

function reportTests(report) {
  const tests = [];
  const visitSuite = (suite) => {
    const value = plainObject(suite, "Playwright suite");
    for (const spec of array(value.specs ?? [], "Playwright suite.specs")) {
      const specValue = plainObject(spec, "Playwright spec");
      for (const test of array(
        specValue.tests ?? [],
        "Playwright spec.tests",
      )) {
        tests.push({
          file: specValue.file ?? value.file,
          spec: specValue,
          test: plainObject(test, "Playwright test"),
        });
      }
    }
    for (const child of array(value.suites ?? [], "Playwright suite.suites")) {
      visitSuite(child);
    }
  };
  for (const suite of array(report.suites, "Playwright report.suites")) {
    visitSuite(suite);
  }
  return tests;
}

function validateReport(value, expectedSuite, location) {
  const report = plainObject(value, location);
  const config = plainObject(report.config, `${location}.config`);
  const metadata = plainObject(config.metadata, `${location}.config.metadata`);
  exact(metadata.gate, "1.7", `${location}.config.metadata.gate`);
  exact(metadata.suite, expectedSuite, `${location}.config.metadata.suite`);
  if (metadata.actualWorkers !== undefined) {
    exact(
      metadata.actualWorkers,
      1,
      `${location}.config.metadata.actualWorkers`,
    );
  }
  const source = validateSource(
    metadata.source,
    `${location}.config.metadata.source`,
  );
  const environment = validateEnvironment(
    metadata.environment,
    `${location}.config.metadata.environment`,
  );
  exact(
    config.version,
    environment.playwrightVersion,
    `${location}.config.version`,
  );
  if (array(report.errors ?? [], `${location}.errors`).length > 0) {
    fail(`${location}.errors`, "must be empty");
  }
  const tests = reportTests(report);
  if (tests.length === 0) fail(location, "must contain at least one test");
  const expectedReport = EXPECTED_REPORTS[expectedSuite];
  exact(tests.length, expectedReport.testCount, `${location} test count`);
  for (const [index, entry] of tests.entries()) {
    const testLocation = `${location}.tests[${index}]`;
    exact(
      path.basename(nonEmptyString(entry.file, `${testLocation}.file`)),
      expectedReport.file,
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
    const results = array(entry.test.results, `${testLocation}.results`);
    if (results.length !== 1) {
      fail(
        `${testLocation}.results`,
        "must contain exactly one no-retry result",
      );
    }
    exact(results[0].status, "passed", `${testLocation}.results[0].status`);
  }
  return {
    suite: expectedSuite,
    source,
    environment,
    testCount: tests.length,
  };
}

export function validateReportForTests(value, expectedSuite) {
  return validateReport(value, expectedSuite, `${expectedSuite} report`);
}

function commandRaw(executable, args) {
  try {
    return execFileSync(executable, args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const stderr = error?.stderr?.toString().trim();
    throw new ProjectileMotionEvidenceError(
      `${executable} ${args.join(" ")} failed${stderr ? `: ${stderr}` : ""}`,
    );
  }
}

function command(executable, args) {
  return commandRaw(executable, args).trim();
}

function expectedNextDevContents(tracked) {
  const replacements = [
    [
      'import "./.next/types/routes.d.ts";',
      'import "./.next/dev/types/routes.d.ts";',
    ],
    [
      'import "./.next/types/root-params.d.ts";',
      'import "./.next/dev/types/root-params.d.ts";',
    ],
  ];
  let expected = tracked;
  for (const [source, generated] of replacements) {
    if (expected.split(source).length !== 2) return null;
    expected = expected.replace(source, generated);
  }
  return expected;
}

function relevantDirtyStatus(status, trackedNextEnv, workingNextEnv) {
  let entries = status.split("\n").filter(Boolean);
  const generatedStatus = ` M ${NEXT_ENV_RELATIVE_PATH}`;
  if (entries.includes(generatedStatus)) {
    const expected = expectedNextDevContents(trackedNextEnv);
    if (expected !== null && workingNextEnv === expected) {
      entries = entries.filter((entry) => entry !== generatedStatus);
    }
  }
  return entries.join("\n");
}

export function relevantDirtyStatusForTests(
  status,
  trackedNextEnv,
  workingNextEnv,
) {
  return relevantDirtyStatus(status, trackedNextEnv, workingNextEnv);
}

function gitProvenance() {
  const repository = command("git", [
    "-C",
    REPOSITORY_ROOT,
    "rev-parse",
    "--show-toplevel",
  ]);
  exact(path.resolve(repository), REPOSITORY_ROOT, "git repository root");
  const status = commandRaw("git", [
    "-C",
    REPOSITORY_ROOT,
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
    "--",
    "backend/murmur/live_scene",
    GENERATOR_RELATIVE_PATH,
    "web",
    ".github/workflows/ci.yml",
  ]).replace(/\n$/, "");
  const dirty = relevantDirtyStatus(
    status,
    commandRaw("git", [
      "-C",
      REPOSITORY_ROOT,
      "show",
      `HEAD:${NEXT_ENV_RELATIVE_PATH}`,
    ]),
    readFileSync(path.join(REPOSITORY_ROOT, NEXT_ENV_RELATIVE_PATH), "utf8"),
  );
  if (dirty)
    fail("git", `relevant projectile sources are not committed:\n${dirty}`);
  const gitCommit = gitObject(
    command("git", ["-C", REPOSITORY_ROOT, "rev-parse", "HEAD^{commit}"]),
    "git commit",
  );
  const gitTree = gitObject(
    command("git", ["-C", REPOSITORY_ROOT, "rev-parse", "HEAD^{tree}"]),
    "git tree",
  );
  const expected =
    process.env.PROJECTILE_MOTION_EXPECTED_SHA ?? process.env.GITHUB_SHA;
  if (expected !== undefined) {
    const label =
      process.env.PROJECTILE_MOTION_EXPECTED_SHA !== undefined
        ? "PROJECTILE_MOTION_EXPECTED_SHA"
        : "GITHUB_SHA";
    exact(gitObject(expected, label), gitCommit, label);
  }
  return { gitCommit, gitTree };
}

async function walkSourceFiles(root, predicate) {
  const files = [];
  const visit = async (directory) => {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) =>
      left.name.localeCompare(right.name),
    )) {
      const absolute = path.join(directory, entry.name);
      if (entry.isSymbolicLink())
        fail("runtime sources", `${absolute} is a symbolic link`);
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile() && predicate(absolute)) files.push(absolute);
    }
  };
  await visit(root);
  return files;
}

async function runtimeSourceEvidence() {
  const roots = [
    path.join(REPOSITORY_ROOT, "backend/murmur/live_scene"),
    path.join(WEB_ROOT, "src/lib/live-scene"),
    path.join(WEB_ROOT, "src/features/live-scene"),
  ];
  const paths = [path.join(REPOSITORY_ROOT, GENERATOR_RELATIVE_PATH)];
  for (const root of roots) {
    paths.push(
      ...(await walkSourceFiles(
        root,
        (absolute) =>
          path.basename(absolute).includes("projectile") &&
          /\.(?:py|ts|tsx)$/.test(absolute),
      )),
    );
  }
  const entries = [];
  for (const absolute of [...new Set(paths)].sort()) {
    const bytes = await readRegularFile(absolute, `runtime source ${absolute}`);
    entries.push({
      path: path.relative(REPOSITORY_ROOT, absolute).split(path.sep).join("/"),
      bytes: bytes.length,
      sha256: sha256(bytes),
    });
  }
  return {
    files: entries,
    sha256: sha256(Buffer.from(canonicalJson(entries))),
  };
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngDimensions(bytes, location) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 45 || !bytes.subarray(0, 8).equals(signature)) {
    fail(location, "must be a PNG image");
  }
  let offset = 8;
  let dimensions;
  let bitDepth;
  let colorType;
  const imageData = [];
  let sawEnd = false;
  let sawPalette = false;
  let sawImageData = false;
  let endedImageData = false;
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length)
      fail(location, "contains a truncated PNG chunk");
    const length = bytes.readUInt32BE(offset);
    const chunkEnd = offset + 12 + length;
    if (chunkEnd > bytes.length)
      fail(location, "contains a truncated PNG chunk");
    const typeBytes = bytes.subarray(offset + 4, offset + 8);
    const type = typeBytes.toString("ascii");
    if (!/^[A-Za-z]{4}$/.test(type)) {
      fail(location, "contains an invalid PNG chunk type");
    }
    if (
      (typeBytes[0] & 0x20) === 0 &&
      !["IHDR", "PLTE", "IDAT", "IEND"].includes(type)
    ) {
      fail(location, `contains unsupported critical chunk ${type}`);
    }
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    const recordedCrc = bytes.readUInt32BE(offset + 8 + length);
    const actualCrc = crc32(Buffer.concat([typeBytes, data]));
    if (recordedCrc !== actualCrc) fail(location, `${type} has an invalid CRC`);
    if (!dimensions) {
      if (type !== "IHDR" || length !== 13) {
        fail(location, "must begin with one 13-byte IHDR chunk");
      }
      const width = data.readUInt32BE(0);
      const height = data.readUInt32BE(4);
      if (width < 1 || height < 1) fail(location, "has invalid dimensions");
      bitDepth = data[8];
      colorType = data[9];
      const legalBitDepths = {
        0: [1, 2, 4, 8, 16],
        2: [8, 16],
        3: [1, 2, 4, 8],
        4: [8, 16],
        6: [8, 16],
      };
      if (!legalBitDepths[colorType]?.includes(bitDepth)) {
        fail(location, "uses an invalid PNG color type and bit depth");
      }
      if (data[10] !== 0 || data[11] !== 0 || data[12] !== 0) {
        fail(
          location,
          "uses unsupported PNG compression, filtering, or interlace",
        );
      }
      dimensions = { width, height };
    } else if (type === "IHDR") {
      fail(location, "must contain exactly one IHDR chunk");
    }
    if (type === "PLTE") {
      if (sawPalette || sawImageData) {
        fail(location, "contains a duplicate or out-of-order PLTE chunk");
      }
      if ([0, 4].includes(colorType)) {
        fail(location, "contains a PLTE chunk forbidden by its color type");
      }
      if (length < 3 || length > 768 || length % 3 !== 0) {
        fail(location, "contains an invalid PLTE chunk");
      }
      if (colorType === 3 && length / 3 > 2 ** bitDepth) {
        fail(location, "contains too many palette entries for its bit depth");
      }
      sawPalette = true;
    }
    if (type === "IDAT") {
      if (endedImageData)
        fail(location, "contains non-consecutive IDAT chunks");
      if (colorType === 3 && !sawPalette) {
        fail(location, "must contain PLTE before indexed image data");
      }
      sawImageData = true;
      imageData.push(data);
    } else if (sawImageData && type !== "IEND") {
      endedImageData = true;
    }
    if (type === "IEND") {
      if (!sawImageData || length !== 0 || chunkEnd !== bytes.length) {
        fail(location, "must end exactly at an empty IEND chunk");
      }
      sawEnd = true;
    }
    offset = chunkEnd;
  }
  if (!sawEnd || imageData.length === 0) {
    fail(location, "must contain image data and a terminal IEND chunk");
  }
  try {
    const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[colorType];
    const rowBytes = Math.ceil((dimensions.width * channels * bitDepth) / 8);
    const decodedBytes = dimensions.height * (1 + rowBytes);
    if (
      !Number.isSafeInteger(decodedBytes) ||
      decodedBytes > MAX_ARTIFACT_BYTES
    ) {
      fail(location, "has unsafe decoded image dimensions");
    }
    const decoded = inflateSync(Buffer.concat(imageData), {
      maxOutputLength: decodedBytes + 1,
    });
    if (decoded.length !== decodedBytes) {
      fail(location, "contains an incomplete or overlong decoded image");
    }
    for (let row = 0; row < dimensions.height; row += 1) {
      if (decoded[row * (rowBytes + 1)] > 4) {
        fail(location, `contains invalid filter type in decoded row ${row}`);
      }
    }
  } catch (error) {
    if (error instanceof ProjectileMotionEvidenceError) throw error;
    fail(location, `contains invalid compressed image data (${error.message})`);
  }
  return dimensions;
}

function assertWebm(bytes, location) {
  const contains = (needle) => bytes.indexOf(Buffer.from(needle)) >= 0;
  if (
    bytes.length < 4_096 ||
    !bytes.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])) ||
    !bytes
      .subarray(0, Math.min(bytes.length, 4_096))
      .includes(Buffer.from("webm")) ||
    !contains([0x18, 0x53, 0x80, 0x67]) ||
    !contains([0x16, 0x54, 0xae, 0x6b]) ||
    !contains([0x1f, 0x43, 0xb6, 0x75])
  ) {
    fail(location, "must be a complete-looking WebM recording");
  }
}

async function locateFfmpeg() {
  const executableName =
    process.platform === "darwin"
      ? "ffmpeg-mac"
      : process.platform === "win32"
        ? "ffmpeg-win64.exe"
        : "ffmpeg-linux";
  const roots = [];
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    roots.push(
      process.env.PLAYWRIGHT_BROWSERS_PATH === "0"
        ? path.join(WEB_ROOT, "node_modules/playwright-core/.local-browsers")
        : path.resolve(process.env.PLAYWRIGHT_BROWSERS_PATH),
    );
  }
  roots.push(
    path.join(WEB_ROOT, "node_modules/playwright-core/.local-browsers"),
    process.platform === "darwin"
      ? path.join(homedir(), "Library/Caches/ms-playwright")
      : process.platform === "win32"
        ? path.join(process.env.LOCALAPPDATA ?? homedir(), "ms-playwright")
        : path.join(homedir(), ".cache/ms-playwright"),
  );
  for (const root of [...new Set(roots)]) {
    let entries;
    try {
      entries = await readdir(root, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") continue;
      throw error;
    }
    for (const entry of entries
      .filter(
        (candidate) =>
          candidate.isDirectory() && candidate.name.startsWith("ffmpeg-"),
      )
      .sort((left, right) => right.name.localeCompare(left.name))) {
      const executable = path.join(root, entry.name, executableName);
      try {
        await access(executable, constants.X_OK);
        return executable;
      } catch {
        // Search the next Playwright installation.
      }
    }
  }
  fail(
    "WebM decoder",
    "Playwright ffmpeg is unavailable; run `npx playwright install chromium` first",
  );
}

async function decodeWebm(filePath, location) {
  const ffmpeg = await locateFfmpeg();
  try {
    const run = (args, captureOutput = false) =>
      execFileSync(ffmpeg, args, {
        stdio: ["ignore", captureOutput ? "pipe" : "ignore", "pipe"],
        timeout: 120_000,
        maxBuffer: 64 * 1024 * 1024,
      });
    const probe = spawnSync(
      ffmpeg,
      [
        "-v",
        "error",
        "-nostats",
        "-i",
        filePath,
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-progress",
        "pipe:2",
        "-f",
        "webm",
        "-",
      ],
      {
        stdio: ["ignore", "ignore", "pipe"],
        timeout: 120_000,
        maxBuffer: 4 * 1024 * 1024,
      },
    );
    if (probe.error) throw probe.error;
    if (probe.status !== 0) {
      const error = new Error(`ffmpeg exited with status ${probe.status}`);
      error.stderr = probe.stderr;
      throw error;
    }
    const durationMs = parseFfmpegProgressDuration(
      probe.stderr.toString("utf8"),
      `${location} duration probe`,
    );
    for (const [frameName, seek] of [
      ["first", []],
      ["near-final", ["-sseof", "-1"]],
    ]) {
      const frame = run(
        [
          "-v",
          "error",
          ...seek,
          "-i",
          filePath,
          "-frames:v",
          "1",
          "-f",
          "image2",
          "-vcodec",
          "png",
          "-",
        ],
        true,
      );
      pngDimensions(frame, `${location} decoded ${frameName} frame`);
    }
    return durationMs;
  } catch (error) {
    if (error instanceof ProjectileMotionEvidenceError) throw error;
    const stderr = error?.stderr?.toString().trim();
    fail(
      location,
      `must decode as a complete WebM video${stderr ? ` (${stderr})` : ""}`,
    );
  }
}

function parseFfmpegProgressDuration(progress, location) {
  const lines = progress.trim().split(/\r?\n/);
  const terminalState = [...lines]
    .reverse()
    .find((line) => line.startsWith("progress="));
  exact(terminalState, "progress=end", `${location}.progress`);
  const values = lines
    .filter((line) => line.startsWith("out_time_us="))
    .map((line) => line.slice("out_time_us=".length));
  if (values.length === 0 || !/^\d+$/.test(values.at(-1))) {
    fail(
      `${location}.out_time_us`,
      "must contain a terminal integer timestamp",
    );
  }
  const microseconds = Number(values.at(-1));
  if (!Number.isSafeInteger(microseconds) || microseconds <= 0) {
    fail(`${location}.out_time_us`, "must be a positive safe integer");
  }
  return Math.round(microseconds / 1_000);
}

function validateRecordingDuration(durationMs, location) {
  if (
    durationMs < MIN_RECORDING_DURATION_MS ||
    durationMs > MAX_RECORDING_DURATION_MS
  ) {
    fail(
      location,
      `must be ${MIN_RECORDING_DURATION_MS}-${MAX_RECORDING_DURATION_MS}ms; received ${durationMs}ms`,
    );
  }
  return durationMs;
}

function validateRecordingTiming(
  durationMs,
  captureVisualDurationMs,
  location,
) {
  const captureTimingDeltaMs = durationMs - captureVisualDurationMs;
  if (
    captureTimingDeltaMs < -MAX_RECORDING_SHORTFALL_MS ||
    captureTimingDeltaMs > MAX_RECORDING_OVERHANG_MS
  ) {
    fail(
      location,
      `must be no more than ${MAX_RECORDING_SHORTFALL_MS}ms shorter or ${MAX_RECORDING_OVERHANG_MS}ms longer than capture visual timing; received ${captureTimingDeltaMs}ms delta`,
    );
  }
  return {
    durationMs,
    captureTimingDeltaMs,
    maximumShortfallMs: MAX_RECORDING_SHORTFALL_MS,
    maximumOverhangMs: MAX_RECORDING_OVERHANG_MS,
  };
}

export async function locateFfmpegForTests() {
  return locateFfmpeg();
}

export async function validateWebmForTests(candidate) {
  const bytes = await readRegularFile(candidate, "test WebM");
  assertWebm(bytes, "test WebM");
  const durationMs = validateRecordingDuration(
    await decodeWebm(candidate, "test WebM"),
    "test WebM duration",
  );
  return { bytes: bytes.length, sha256: sha256(bytes), durationMs };
}

export async function probeWebmDurationForTests(candidate) {
  return decodeWebm(candidate, "test WebM");
}

export function validateRecordingTimingForTests(
  durationMs,
  captureVisualDurationMs,
) {
  return validateRecordingTiming(
    validateRecordingDuration(durationMs, "test WebM duration"),
    finiteNumber(captureVisualDurationMs, "test capture visual duration", {
      minimum: 0,
    }),
    "test WebM capture timing",
  );
}

async function verifyObservedArtifacts(
  artifactRoot,
  observation,
  { skipVideoDecode = false } = {},
) {
  const verified = {};
  const entries = [
    ["video", observation.artifacts.video],
    ["pageScreenshot", observation.artifacts.pageScreenshot],
    ["boardScreenshot", observation.artifacts.boardScreenshot],
    ["contactSheet", observation.artifacts.contactSheet],
    ...observation.artifacts.checkpointScreenshots.map((descriptor, index) => [
      `checkpoint-${index + 1}`,
      descriptor,
    ]),
  ];
  for (const [role, descriptor] of entries) {
    const bytes = await readRegularFile(
      path.join(artifactRoot, ...descriptor.path.split("/")),
      `artifact ${role}`,
      MAX_ARTIFACT_BYTES,
    );
    exact(bytes.length, descriptor.bytes, `artifact ${role} byte count`);
    exact(sha256(bytes), descriptor.sha256, `artifact ${role} digest`);
    if (role === "video") {
      assertWebm(bytes, `artifact ${role}`);
      let durationMs = observation.timing.visualDurationMs;
      if (!skipVideoDecode) {
        durationMs = validateRecordingDuration(
          await decodeWebm(
            path.join(artifactRoot, ...descriptor.path.split("/")),
            `artifact ${role}`,
          ),
          `artifact ${role} duration`,
        );
      }
      verified[role] = {
        ...descriptor,
        mediaType: "video/webm",
        ...validateRecordingTiming(
          durationMs,
          observation.timing.visualDurationMs,
          `artifact ${role} capture timing`,
        ),
      };
    } else {
      const dimensions = pngDimensions(bytes, `artifact ${role}`);
      exact(dimensions.width, descriptor.width, `artifact ${role} width`);
      exact(dimensions.height, descriptor.height, `artifact ${role} height`);
      verified[role] = { ...descriptor, mediaType: "image/png" };
    }
  }
  return {
    video: verified.video,
    pageScreenshot: verified.pageScreenshot,
    boardScreenshot: verified.boardScreenshot,
    contactSheet: verified.contactSheet,
    checkpointScreenshots: observation.artifacts.checkpointScreenshots.map(
      (_, index) => verified[`checkpoint-${index + 1}`],
    ),
  };
}

async function artifactInventory(artifactRoot) {
  const ignored = new Set([
    MANIFEST_NAME,
    MANIFEST_DIGEST_NAME,
    ".manifest-finalize.lock",
  ]);
  const files = [];
  const visit = async (directory) => {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) =>
      left.name.localeCompare(right.name),
    )) {
      const absolute = path.join(directory, entry.name);
      const relative = path
        .relative(artifactRoot, absolute)
        .split(path.sep)
        .join("/");
      if (entry.isSymbolicLink())
        fail("artifact inventory", `${relative} is a symbolic link`);
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile() && !ignored.has(relative)) {
        const bytes = await readRegularFile(
          absolute,
          `artifact inventory ${relative}`,
          MAX_ARTIFACT_BYTES,
        );
        files.push({
          path: relative,
          bytes: bytes.length,
          sha256: sha256(bytes),
        });
      } else if (!entry.isFile()) {
        fail(
          "artifact inventory",
          `${relative} must be a regular file or directory`,
        );
      }
    }
  };
  await visit(artifactRoot);
  return files;
}

function sameRecord(actual, expected, location) {
  exact(canonicalJson(actual), canonicalJson(expected), location);
}

async function buildManifestInternal(artifactRoot, provenance, options = {}) {
  const fixtures = await loadFixtureCatalog();
  const primaryFixture = fixtures.find(
    ({ fixtureId }) => fixtureId === PRIMARY_FIXTURE_ID,
  );
  const acceleratedObservationsBytes = await readRegularFile(
    path.join(artifactRoot, "accelerated/observations.json"),
    "accelerated observations",
  );
  const acceleratedObservations = validateAcceleratedObservation(
    parseJson(acceleratedObservationsBytes, "accelerated observations"),
    primaryFixture,
    "accelerated observations",
  );
  const observationsPath = path.join(artifactRoot, "capture/observations.json");
  const observationsBytes = await readRegularFile(
    observationsPath,
    "capture observations",
  );
  const observations = validateCaptureObservation(
    parseJson(observationsBytes, "capture observations"),
    primaryFixture,
    "capture observations",
  );

  const reports = {};
  const evidenceSources = [
    ["accelerated observations", acceleratedObservations.execution.source],
    ["capture observations", observations.execution.source],
  ];
  const evidenceEnvironments = [
    ["accelerated observations", acceleratedObservations.execution.environment],
    ["capture observations", observations.execution.environment],
  ];
  for (const suite of SUITES) {
    const relativePath = `${suite}/report.json`;
    const bytes = await readRegularFile(
      path.join(artifactRoot, suite, "report.json"),
      `${suite} report`,
    );
    const validated = validateReport(
      parseJson(bytes, `${suite} report`),
      suite,
      `${suite} report`,
    );
    reports[suite] = {
      path: relativePath,
      sha256: sha256(bytes),
      testCount: validated.testCount,
    };
    evidenceSources.push([`${suite} report`, validated.source]);
    evidenceEnvironments.push([`${suite} report`, validated.environment]);
  }
  for (const [location, source] of evidenceSources) {
    sameRecord(source, provenance, `${location} source provenance`);
  }
  const environment = evidenceEnvironments[0][1];
  for (const [location, value] of evidenceEnvironments) {
    sameRecord(value, environment, `${location} environment provenance`);
  }
  exact(environment.nodeVersion, process.version, "finalizer Node.js version");
  exact(environment.platform, process.platform, "finalizer platform");
  exact(environment.arch, process.arch, "finalizer architecture");

  const observedArtifacts = await verifyObservedArtifacts(
    artifactRoot,
    observations,
    options,
  );
  const inventory = await artifactInventory(artifactRoot);
  const observedDescriptors = [
    observedArtifacts.video,
    observedArtifacts.pageScreenshot,
    observedArtifacts.boardScreenshot,
    observedArtifacts.contactSheet,
    ...observedArtifacts.checkpointScreenshots,
  ];
  for (const descriptor of observedDescriptors) {
    const entry = inventory.find(
      ({ path: candidate }) => candidate === descriptor.path,
    );
    if (!entry) fail("artifact inventory", `is missing ${descriptor.path}`);
    exact(
      entry.sha256,
      descriptor.sha256,
      `artifact inventory ${descriptor.path}`,
    );
  }
  const runtimeSources = await runtimeSourceEvidence();
  const packageLockBytes = await readRegularFile(
    path.join(REPOSITORY_ROOT, PACKAGE_LOCK_RELATIVE_PATH),
    "package lock",
  );
  const generator = runtimeSources.files.find(
    ({ path: relativePath }) => relativePath === GENERATOR_RELATIVE_PATH,
  );
  if (!generator) fail("runtime sources", "fixture generator is not bound");

  return {
    schemaVersion: 1,
    gate: "1.7",
    source: {
      ...provenance,
      packageLock: {
        path: PACKAGE_LOCK_RELATIVE_PATH,
        sha256: sha256(packageLockBytes),
      },
      fixtureGenerator: generator,
      runtimeSources,
    },
    fixtures: {
      selectedFixtureId: PRIMARY_FIXTURE_ID,
      catalogSha256: sha256(Buffer.from(canonicalJson(fixtures))),
      catalog: fixtures,
    },
    environment: {
      nodeVersion: environment.nodeVersion,
      platform: environment.platform,
      arch: environment.arch,
      playwrightVersion: environment.playwrightVersion,
      browser: {
        name: environment.browserName,
        version: environment.browserVersion,
      },
      browserEvidenceSha256: sha256(
        Buffer.from(
          canonicalJson({ environment, browser: observations.browser }),
        ),
      ),
    },
    evidence: {
      reports,
      observations: {
        accelerated: {
          path: "accelerated/observations.json",
          sha256: sha256(acceleratedObservationsBytes),
        },
        capture: {
          path: "capture/observations.json",
          sha256: sha256(observationsBytes),
          fixtureSource: observations.fixtureSource,
        },
      },
      firstMeaningful: acceleratedObservations.firstMeaningful,
      motionBoundary: acceleratedObservations.motionBoundary,
      runtime: {
        ...observations.runtime,
        recordSha256: sha256(Buffer.from(canonicalJson(observations.runtime))),
      },
      timing: observations.timing,
      network: {
        ...observations.network,
        sha256: sha256(Buffer.from(canonicalJson(observations.network))),
      },
      recording: {
        browser: observations.browser,
        video: observedArtifacts.video,
        pageScreenshot: observedArtifacts.pageScreenshot,
        boardScreenshot: observedArtifacts.boardScreenshot,
        contactSheet: observedArtifacts.contactSheet,
        checkpointScreenshots: observedArtifacts.checkpointScreenshots,
      },
    },
    artifacts: inventory,
  };
}

async function assertExistingRoot(candidate) {
  const artifactRoot = await assertSafeArtifactRoot(candidate);
  const stat = await rejectSymlinkPath(artifactRoot, "artifact root");
  if (!stat?.isDirectory())
    fail("artifact root", "must be an existing directory");
  return artifactRoot;
}

export async function buildManifest(candidate = artifactRootFromEnvironment()) {
  const artifactRoot = await assertExistingRoot(candidate);
  return buildManifestInternal(artifactRoot, gitProvenance());
}

export async function buildManifestForTests(candidate, provenance) {
  const artifactRoot = await assertExistingRoot(candidate);
  return buildManifestInternal(
    artifactRoot,
    validateSource(provenance, "test provenance"),
    { skipVideoDecode: true },
  );
}

async function atomicWrite(destination, bytes) {
  const token = randomBytes(18).toString("hex");
  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${token}.tmp`,
  );
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rejectSymlinkPath(destination, "manifest destination");
    await rename(temporary, destination);
  } finally {
    if (handle) await handle.close().catch(() => undefined);
    await rm(temporary, { force: true });
  }
}

async function writeManifestInternal(candidate, provenance, options = {}) {
  const artifactRoot = await assertExistingRoot(candidate);
  const lockPath = path.join(artifactRoot, ".manifest-finalize.lock");
  let lock;
  try {
    try {
      lock = await open(lockPath, "wx", 0o600);
    } catch (error) {
      if (error?.code === "EEXIST") {
        fail(
          "manifest finalization",
          "another finalizer already holds the lock",
        );
      }
      throw error;
    }
    const manifest = await buildManifestInternal(
      artifactRoot,
      provenance,
      options,
    );
    const manifestBytes = Buffer.from(canonicalJson(manifest));
    const digest = sha256(manifestBytes);
    await atomicWrite(path.join(artifactRoot, MANIFEST_NAME), manifestBytes);
    await atomicWrite(
      path.join(artifactRoot, MANIFEST_DIGEST_NAME),
      Buffer.from(`${digest}  ${MANIFEST_NAME}\n`),
    );
    return { artifactRoot, manifest, digest };
  } finally {
    if (lock) {
      await lock.close().catch(() => undefined);
      await rm(lockPath, { force: true });
    }
  }
}

export async function writeManifest(candidate = artifactRootFromEnvironment()) {
  return writeManifestInternal(candidate, gitProvenance());
}

export async function writeManifestForTests(candidate, provenance) {
  return writeManifestInternal(
    candidate,
    validateSource(provenance, "test provenance"),
    { skipVideoDecode: true },
  );
}

async function validateManifestInternal(candidate, provenance, options = {}) {
  const artifactRoot = await assertExistingRoot(candidate);
  const [manifestBytes, digestBytes] = await Promise.all([
    readRegularFile(path.join(artifactRoot, MANIFEST_NAME), "manifest"),
    readRegularFile(
      path.join(artifactRoot, MANIFEST_DIGEST_NAME),
      "manifest digest",
    ),
  ]);
  const digest = sha256(manifestBytes);
  exact(
    digestBytes.toString("utf8"),
    `${digest}  ${MANIFEST_NAME}\n`,
    "manifest digest",
  );
  const recorded = parseJson(manifestBytes, "manifest");
  const expected = await buildManifestInternal(
    artifactRoot,
    provenance,
    options,
  );
  sameRecord(recorded, expected, "manifest content");
  return { artifactRoot, manifest: expected, digest };
}

export async function validateManifest(
  candidate = artifactRootFromEnvironment(),
) {
  return validateManifestInternal(candidate, gitProvenance());
}

export async function validateManifestForTests(candidate, provenance) {
  return validateManifestInternal(
    candidate,
    validateSource(provenance, "test provenance"),
    { skipVideoDecode: true },
  );
}

export async function fixtureCatalogForTests() {
  return loadFixtureCatalog();
}

export async function pathExistsForTests(candidate) {
  try {
    await access(candidate, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}
