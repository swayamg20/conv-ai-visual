import { createHash, randomBytes } from "node:crypto";
import {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { constants, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));

export const WEB_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const REPOSITORY_ROOT = path.resolve(WEB_ROOT, "..");
export const DEFAULT_ARTIFACT_ROOT = path.join(
  REPOSITORY_ROOT,
  "var",
  "live-choreography",
);
export const MANIFEST_NAME = "manifest.json";
export const MANIFEST_DIGEST_NAME = "manifest.sha256";

const FIXTURE_RELATIVE_PATH =
  "web/src/features/live-scene/fixtures/completing-the-square.v1.json";
const COMPILER_RELATIVE_PATH =
  "backend/murmur/live_scene/completing_square_compiler.py";
const GENERATOR_RELATIVE_PATH = "scripts/generate_live_choreography_fixture.py";
const PACKAGE_LOCK_RELATIVE_PATH = "web/package-lock.json";
const NEXT_ENV_RELATIVE_PATH = "web/next-env.d.ts";
const MAX_JSON_BYTES = 16 * 1024 * 1024;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const GIT_OBJECT_ID_PATTERN = /^[a-f0-9]{40}$/;
const EXPECTED_CHECKPOINT_IDS = Object.freeze([
  "problem",
  "area_model",
  "split_linear_term",
  "rearrange_halves",
  "missing_corner",
  "balance_and_complete",
  "factor_square",
  "solve_roots",
]);
const EXPECTED_CORNER_DETAIL_IDS = Object.freeze([
  "square-lesson__corner_calc",
  "square-lesson__corner_dim_h",
  "square-lesson__corner_dim_v",
]);
const EXPECTED_RESPONSIVE_VIEWPORTS = Object.freeze([
  Object.freeze({ width: 320, height: 568 }),
  Object.freeze({ width: 375, height: 812 }),
]);
const INTERRUPTION_DEFINITIONS = Object.freeze([
  Object.freeze({
    category: "move",
    checkpointOrdinal: 4,
    cue: "transform",
    timing: "motion",
  }),
  Object.freeze({
    category: "token_morph",
    checkpointOrdinal: 7,
    cue: "transform",
    timing: "motion",
  }),
  Object.freeze({
    category: "camera_focus",
    checkpointOrdinal: 4,
    cue: "focus",
    timing: "motion",
  }),
  Object.freeze({
    category: "emphasis",
    checkpointOrdinal: 6,
    cue: "emphasize",
    timing: "motion",
  }),
  Object.freeze({
    category: "authored_hold",
    checkpointOrdinal: 4,
    cue: "hold",
    timing: "hold",
  }),
]);
const EXPECTED_ACCELERATED_TESTS = Object.freeze([
  "starts meaningful choreography under 100ms p95 over twenty fresh runs",
  "settles the exact eight-checkpoint lesson without replacing retained ink or calling a model",
  "reduced motion reaches the same certified final state",
  "keeps the compact 375x812 stage inside the viewport",
  "keeps the compact 320x568 stage inside the viewport",
  "interrupts twenty active checkpoints, proves stale-window stability, and replays every frontier",
]);
const EXPECTED_CAPTURE_TESTS = Object.freeze([
  "records the complete 1x cinematic choreography",
  "captures all eight quiescent checkpoints and a two-by-four contact sheet",
]);
const RELEVANT_GIT_PATHS = Object.freeze([
  "backend/murmur",
  GENERATOR_RELATIVE_PATH,
  "web",
  ".github/workflows/ci.yml",
]);

export class EvidenceError extends Error {
  constructor(message) {
    super(message);
    this.name = "EvidenceError";
  }
}

function fail(location, message) {
  throw new EvidenceError(`${location}: ${message}`);
}

function svgPaintLayer(kind) {
  return kind === "text" || kind === "latex" || kind === "latex_token" ? 1 : 0;
}

function orderNodeIdsForSvgPaint(nodes) {
  return nodes
    .map((node, insertionIndex) => ({ ...node, insertionIndex }))
    .sort(
      (left, right) =>
        svgPaintLayer(left.kind) - svgPaintLayer(right.kind) ||
        left.insertionIndex - right.insertionIndex,
    )
    .map((node) => node.id);
}

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function object(value, location) {
  if (!isPlainObject(value)) fail(location, "must be a plain object");
  return value;
}

function array(value, location, length) {
  if (!Array.isArray(value)) fail(location, "must be an array");
  if (length !== undefined && value.length !== length) {
    fail(location, `must contain exactly ${length} entries`);
  }
  return value;
}

function exactKeys(value, expected, location) {
  const record = object(value, location);
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  if (!sameArray(actual, wanted)) {
    fail(
      location,
      `must contain exactly keys ${wanted.join(", ")}; received ${actual.join(", ")}`,
    );
  }
  return record;
}

function string(value, location) {
  if (typeof value !== "string" || value.length === 0) {
    fail(location, "must be a non-empty string");
  }
  return value;
}

function number(value, location, { integer = false, minimum } = {}) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (integer && !Number.isInteger(value)) ||
    (minimum !== undefined && value < minimum)
  ) {
    const requirements = [
      integer ? "integer" : "finite number",
      minimum === undefined ? "" : `>= ${minimum}`,
    ]
      .filter(Boolean)
      .join(" and ");
    fail(location, `must be a ${requirements}`);
  }
  return value;
}

function literal(value, expected, location) {
  if (value !== expected) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function stringArray(value, location, length) {
  const values = array(value, location, length).map((entry, index) =>
    string(entry, `${location}[${index}]`),
  );
  if (new Set(values).size !== values.length) {
    fail(location, "must not contain duplicate IDs");
  }
  return values;
}

function numericSamples(value, location) {
  return array(value, location, 20).map((entry, index) =>
    number(entry, `${location}[${index}]`, { minimum: 0 }),
  );
}

function sameArray(left, right) {
  return (
    left.length === right.length &&
    left.every((entry, index) => entry === right[index])
  );
}

function assertArrayEqual(actual, expected, location) {
  if (!sameArray(actual, expected)) {
    fail(
      location,
      `must equal ${JSON.stringify(expected)}; received ${JSON.stringify(actual)}`,
    );
  }
}

function assertDeepEqual(actual, expected, location) {
  if (!isDeepStrictEqual(actual, expected)) {
    fail(location, "must be deeply equal");
  }
}

function canonicalJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function nearestRankP95(samples) {
  const values = numericSamples(samples, "samples");
  return [...values].sort((left, right) => left - right)[
    Math.ceil(values.length * 0.95) - 1
  ];
}

function rounded(value) {
  return Number(value.toFixed(3));
}

function gitObjectId(value, location) {
  if (!GIT_OBJECT_ID_PATTERN.test(string(value, location))) {
    fail(location, "must be a lowercase 40-character Git object ID");
  }
  return value;
}

function validateSource(value, location) {
  const source = exactKeys(value, ["gitCommit", "gitTree"], location);
  return {
    gitCommit: gitObjectId(source.gitCommit, `${location}.gitCommit`),
    gitTree: gitObjectId(source.gitTree, `${location}.gitTree`),
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
  const nodeVersion = string(
    environment.nodeVersion,
    `${location}.nodeVersion`,
  );
  if (!/^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(nodeVersion)) {
    fail(`${location}.nodeVersion`, "must be a full Node.js version");
  }
  const playwrightVersion = string(
    environment.playwrightVersion,
    `${location}.playwrightVersion`,
  );
  if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(playwrightVersion)) {
    fail(`${location}.playwrightVersion`, "must be a full Playwright version");
  }
  literal(environment.browserName, "chromium", `${location}.browserName`);
  const browserVersion = string(
    environment.browserVersion,
    `${location}.browserVersion`,
  );
  if (!/^\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$/.test(browserVersion)) {
    fail(`${location}.browserVersion`, "must be a Chromium version");
  }
  return {
    nodeVersion,
    platform: string(environment.platform, `${location}.platform`),
    arch: string(environment.arch, `${location}.arch`),
    playwrightVersion,
    browserName: environment.browserName,
    browserVersion,
  };
}

function assertSameRecord(actual, expected, location) {
  literal(canonicalJson(actual), canonicalJson(expected), location);
}

export function verifyEvidenceProvenance(
  evidenceSources,
  finalizeSource,
  evidenceEnvironments,
  finalizeEnvironment = {
    nodeVersion: process.version,
    platform: process.platform,
    arch: process.arch,
  },
) {
  const source = validateSource(finalizeSource, "finalize source");
  for (const [location, value] of evidenceSources) {
    assertSameRecord(validateSource(value, location), source, location);
  }

  const [firstEnvironment, ...remainingEnvironments] = evidenceEnvironments;
  if (!firstEnvironment) fail("environment", "no browser evidence supplied");
  const environment = validateEnvironment(
    firstEnvironment[1],
    firstEnvironment[0],
  );
  for (const [location, value] of remainingEnvironments) {
    assertSameRecord(
      validateEnvironment(value, location),
      environment,
      location,
    );
  }
  literal(
    environment.nodeVersion,
    finalizeEnvironment.nodeVersion,
    "environment.nodeVersion at finalize",
  );
  literal(
    environment.platform,
    finalizeEnvironment.platform,
    "environment.platform at finalize",
  );
  literal(
    environment.arch,
    finalizeEnvironment.arch,
    "environment.arch at finalize",
  );
  return { source, environment };
}

function viewportString(viewport, location) {
  const value = exactKeys(
    viewport,
    ["v", "x", "y", "width", "height"],
    location,
  );
  literal(value.v, 1, `${location}.v`);
  for (const key of ["x", "y", "width", "height"]) {
    number(value[key], `${location}.${key}`);
  }
  if (value.width <= 0 || value.height <= 0) {
    fail(location, "width and height must be positive");
  }
  return `${value.x} ${value.y} ${value.width} ${value.height}`;
}

function runGitRaw(arguments_) {
  try {
    return execFileSync("git", ["-C", REPOSITORY_ROOT, ...arguments_], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const stderr = error?.stderr?.toString().trim();
    throw new EvidenceError(
      `git ${arguments_.join(" ")} failed${stderr ? `: ${stderr}` : ""}`,
    );
  }
}

function runGit(arguments_) {
  return runGitRaw(arguments_).trim();
}

function expectedNextDevGeneratedContents(trackedContents) {
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
  let expected = trackedContents;
  for (const [trackedImport, generatedImport] of replacements) {
    if (expected.split(trackedImport).length !== 2) return null;
    expected = expected.replace(trackedImport, generatedImport);
  }
  return expected;
}

function relevantDirtyStatus(status, trackedNextEnv, workingNextEnv) {
  const entries = status.split("\n").filter(Boolean);
  const nextEnvStatus = ` M ${NEXT_ENV_RELATIVE_PATH}`;
  if (!entries.includes(nextEnvStatus)) return entries.join("\n");
  const expected = expectedNextDevGeneratedContents(trackedNextEnv);
  if (expected === null || workingNextEnv !== expected) {
    return entries.join("\n");
  }
  return entries.filter((entry) => entry !== nextEnvStatus).join("\n");
}

export function inspectRelevantGitStatusForTests(
  status,
  trackedNextEnv,
  workingNextEnv,
) {
  return relevantDirtyStatus(status, trackedNextEnv, workingNextEnv);
}

function validateExpectedSha(commitSha, expectedSha, label) {
  if (expectedSha === undefined) return;
  gitObjectId(expectedSha, label);
  literal(expectedSha, commitSha, label);
}

export function validateGithubShaForTests(commitSha, githubSha) {
  validateExpectedSha(commitSha, githubSha, "GITHUB_SHA");
}

export function validateExpectedShaForTests(commitSha, expectedSha, label) {
  validateExpectedSha(commitSha, expectedSha, label);
}

function gitProvenance() {
  const repository = runGit(["rev-parse", "--show-toplevel"]);
  if (path.resolve(repository) !== REPOSITORY_ROOT) {
    fail(
      "git",
      `expected repository root ${REPOSITORY_ROOT}, received ${repository}`,
    );
  }
  const status = runGitRaw([
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
    "--",
    ...RELEVANT_GIT_PATHS,
  ]).replace(/\n$/, "");
  const nextEnvPath = path.join(REPOSITORY_ROOT, NEXT_ENV_RELATIVE_PATH);
  const dirty = relevantDirtyStatus(
    status,
    runGitRaw(["show", `HEAD:${NEXT_ENV_RELATIVE_PATH}`]),
    readFileSync(nextEnvPath, "utf8"),
  );
  if (dirty) {
    fail("git", `relevant choreography sources are not committed:\n${dirty}`);
  }
  const commitSha = runGit(["rev-parse", "HEAD"]);
  const treeSha = runGit(["rev-parse", "HEAD^{tree}"]);
  gitObjectId(commitSha, "git HEAD");
  gitObjectId(treeSha, "git tree");
  const expectedSha =
    process.env.CHOREOGRAPHY_EXPECTED_SHA ?? process.env.GITHUB_SHA;
  validateExpectedSha(
    commitSha,
    expectedSha,
    process.env.CHOREOGRAPHY_EXPECTED_SHA !== undefined
      ? "CHOREOGRAPHY_EXPECTED_SHA"
      : "GITHUB_SHA",
  );
  return { commitSha, treeSha };
}

function artifactRootFromEnvironment() {
  const configured = process.env.CHOREOGRAPHY_E2E_ARTIFACT_DIR;
  return configured
    ? path.resolve(WEB_ROOT, configured)
    : DEFAULT_ARTIFACT_ROOT;
}

export function resolveArtifactRoot(candidate = artifactRootFromEnvironment()) {
  const resolved = path.resolve(candidate);
  if (path.basename(resolved) !== "live-choreography") {
    fail(
      "artifact root",
      'must end in the exact directory name "live-choreography"',
    );
  }
  const forbidden = new Set([
    path.parse(resolved).root,
    REPOSITORY_ROOT,
    WEB_ROOT,
    path.resolve(process.cwd()),
  ]);
  if (process.env.HOME) forbidden.add(path.resolve(process.env.HOME));
  if (forbidden.has(resolved)) {
    fail("artifact root", `${resolved} is too broad to remove safely`);
  }
  return resolved;
}

function isPathWithin(target, base) {
  return target === base || target.startsWith(`${base}${path.sep}`);
}

function artifactRootBoundary(resolved) {
  if (isPathWithin(resolved, path.resolve(REPOSITORY_ROOT))) {
    return "repository";
  }
  if (isPathWithin(resolved, path.resolve(tmpdir()))) return "temporary";
  fail(
    "artifact root",
    "must be inside this repository or the operating-system temporary directory",
  );
}

function assertCanonicalArtifactRootBoundary(
  requestedRoot,
  canonicalRoot,
  canonicalRepository,
  canonicalTemporaryRoot,
) {
  const boundary = artifactRootBoundary(requestedRoot);
  const canonicalBoundary =
    boundary === "repository" ? canonicalRepository : canonicalTemporaryRoot;
  if (!isPathWithin(canonicalRoot, canonicalBoundary)) {
    fail(
      "artifact root",
      `${requestedRoot} escapes its ${boundary} boundary after realpath`,
    );
  }
}

/** Test-only pure boundary check for realpath escape scenarios. */
export async function assertArtifactRootBoundaryForTests(
  requestedRoot,
  canonicalRoot,
) {
  const [canonicalRepository, canonicalTemporaryRoot] = await Promise.all([
    realpath(REPOSITORY_ROOT),
    realpath(tmpdir()),
  ]);
  assertCanonicalArtifactRootBoundary(
    path.resolve(requestedRoot),
    path.resolve(canonicalRoot),
    canonicalRepository,
    canonicalTemporaryRoot,
  );
}

async function inspectExistingArtifactRoot(candidate) {
  const requestedRoot = resolveArtifactRoot(candidate);
  let before;
  try {
    before = await lstat(requestedRoot);
  } catch (error) {
    if (error?.code === "ENOENT") {
      fail("artifact root", `missing directory ${requestedRoot}`);
    }
    throw error;
  }
  if (before.isSymbolicLink()) {
    fail("artifact root", `${requestedRoot} must not be a symbolic link`);
  }
  if (!before.isDirectory()) {
    fail("artifact root", `${requestedRoot} must be a directory`);
  }

  const [canonicalRoot, canonicalRepository, canonicalTemporaryRoot] =
    await Promise.all([
      realpath(requestedRoot),
      realpath(REPOSITORY_ROOT),
      realpath(tmpdir()),
    ]);
  assertCanonicalArtifactRootBoundary(
    requestedRoot,
    canonicalRoot,
    canonicalRepository,
    canonicalTemporaryRoot,
  );

  const after = await lstat(requestedRoot);
  if (
    after.isSymbolicLink() ||
    !after.isDirectory() ||
    after.dev !== before.dev ||
    after.ino !== before.ino
  ) {
    fail("artifact root", `${requestedRoot} changed while it was inspected`);
  }
  return {
    requestedRoot,
    canonicalRoot,
    device: after.dev,
    inode: after.ino,
  };
}

async function assertArtifactRootSnapshot(snapshot) {
  let information;
  try {
    information = await lstat(snapshot.requestedRoot);
  } catch (error) {
    if (error?.code === "ENOENT") {
      fail("artifact root", `${snapshot.requestedRoot} disappeared`);
    }
    throw error;
  }
  if (
    information.isSymbolicLink() ||
    !information.isDirectory() ||
    information.dev !== snapshot.device ||
    information.ino !== snapshot.inode ||
    (await realpath(snapshot.requestedRoot)) !== snapshot.canonicalRoot
  ) {
    fail("artifact root", `${snapshot.requestedRoot} changed after validation`);
  }
}

async function rejectSymlinkAt(location, label) {
  try {
    const information = await lstat(location);
    if (information.isSymbolicLink()) {
      fail(label, `${location} must not be a symbolic link`);
    }
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function prepareArtifactRootAt(candidate, allowTemporaryTestRoot) {
  const artifactRoot = resolveArtifactRoot(candidate);
  if (
    !allowTemporaryTestRoot &&
    path.resolve(artifactRoot) !== path.resolve(DEFAULT_ARTIFACT_ROOT)
  ) {
    fail("artifact root", `prepare may remove only ${DEFAULT_ARTIFACT_ROOT}`);
  }
  const parent = path.dirname(artifactRoot);
  await mkdir(parent, { recursive: true });
  await rejectSymlinkAt(parent, "artifact parent");
  const canonicalParent = await realpath(parent);
  const canonicalRoot = path.join(canonicalParent, path.basename(artifactRoot));
  if (path.basename(canonicalRoot) !== "live-choreography") {
    fail("artifact root", "canonical target has an unexpected directory name");
  }
  const canonicalRepository = await realpath(REPOSITORY_ROOT);
  const canonicalTemporaryRoot = await realpath(tmpdir());
  const permittedRoot = allowTemporaryTestRoot
    ? isPathWithin(canonicalRoot, canonicalTemporaryRoot)
    : canonicalRoot ===
      path.join(canonicalRepository, "var", "live-choreography");
  if (!permittedRoot) {
    fail(
      "artifact root",
      allowTemporaryTestRoot
        ? "test preparation is allowed only inside the operating-system temporary directory"
        : `prepare may remove only ${DEFAULT_ARTIFACT_ROOT}`,
    );
  }
  await rejectSymlinkAt(artifactRoot, "artifact root");
  await rm(artifactRoot, { recursive: true, force: true });
  await Promise.all([
    mkdir(path.join(artifactRoot, "accelerated"), { recursive: true }),
    mkdir(path.join(artifactRoot, "capture", "checkpoints"), {
      recursive: true,
    }),
  ]);
  return artifactRoot;
}

export async function prepareArtifactRoot(candidate = DEFAULT_ARTIFACT_ROOT) {
  return prepareArtifactRootAt(candidate, false);
}

/** Test-only escape hatch: it permits exact-name roots under the OS temp directory. */
export async function prepareArtifactRootForTests(candidate) {
  return prepareArtifactRootAt(candidate, true);
}

async function readBytes(filePath, location) {
  let before;
  try {
    before = await lstat(filePath);
  } catch (error) {
    if (error?.code === "ENOENT") fail(location, `missing file ${filePath}`);
    throw error;
  }
  if (before.isSymbolicLink()) {
    fail(location, `${filePath} must not be a symbolic link`);
  }
  if (!before.isFile()) {
    fail(location, `${filePath} must be a regular file`);
  }

  let handle;
  try {
    handle = await open(
      filePath,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
  } catch (error) {
    if (error?.code === "ENOENT") fail(location, `missing file ${filePath}`);
    if (error?.code === "ELOOP") {
      fail(location, `${filePath} must not be a symbolic link`);
    }
    throw error;
  }
  try {
    const information = await handle.stat();
    if (
      !information.isFile() ||
      information.dev !== before.dev ||
      information.ino !== before.ino
    ) {
      fail(location, `${filePath} changed while it was opened`);
    }
    return await handle.readFile();
  } finally {
    await handle.close();
  }
}

function parseJsonBytes(bytes, location) {
  if (bytes.length === 0 || bytes.length > MAX_JSON_BYTES) {
    fail(location, `JSON size must be between 1 and ${MAX_JSON_BYTES} bytes`);
  }
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail(location, `invalid JSON: ${error.message}`);
  }
}

function fixtureCheckpoints(fixture) {
  const root = object(fixture, "fixture");
  literal(root.v, 1, "fixture.v");
  const fixtureId = string(root.fixtureId, "fixture.fixtureId");
  const compilerVersion = string(
    root.compilerVersion,
    "fixture.compilerVersion",
  );
  const transcript = object(root.transcript, "fixture.transcript");
  literal(
    transcript.providerRequestCount,
    0,
    "fixture.transcript.providerRequestCount",
  );
  literal(transcript.checkpointCount, 8, "fixture.transcript.checkpointCount");
  const transcriptIds = stringArray(
    transcript.checkpointIds,
    "fixture.transcript.checkpointIds",
    8,
  );
  assertArrayEqual(
    transcriptIds,
    EXPECTED_CHECKPOINT_IDS,
    "fixture.transcript.checkpointIds",
  );

  const events = array(root.events, "fixture.events").filter(
    (event) =>
      object(event, "fixture.events[]").type ===
      "choreography_scene_checkpoint",
  );
  if (events.length !== 8) {
    fail("fixture.events", "must contain exactly eight main checkpoint events");
  }

  const activeNodes = new Map();
  const checkpoints = events.map((eventValue, index) => {
    const location = `fixture.events.checkpoint[${index}]`;
    const event = object(eventValue, location);
    const semantic = object(event.semantic, `${location}.semantic`);
    const presentation = object(
      semantic.presentation,
      `${location}.semantic.presentation`,
    );
    const choreography = object(
      semantic.choreography,
      `${location}.semantic.choreography`,
    );
    const phase = object(
      choreography.phase,
      `${location}.semantic.choreography.phase`,
    );
    const certificate = object(
      semantic.certificate,
      `${location}.semantic.certificate`,
    );
    const certificateBody = object(
      certificate.body,
      `${location}.semantic.certificate.body`,
    );
    const checkpointId = string(
      semantic.checkpointId,
      `${location}.checkpointId`,
    );
    literal(
      checkpointId,
      EXPECTED_CHECKPOINT_IDS[index],
      `${location}.checkpointId`,
    );
    literal(event.sequence, index + 1, `${location}.sequence`);
    literal(event.baseRevision, index, `${location}.baseRevision`);
    literal(event.resultRevision, index + 1, `${location}.resultRevision`);
    literal(presentation.v, 1, `${location}.presentation.v`);
    literal(
      presentation.checkpointId,
      checkpointId,
      `${location}.presentation.checkpointId`,
    );
    literal(
      presentation.transientFree,
      true,
      `${location}.presentation.transientFree`,
    );
    literal(choreography.v, 1, `${location}.choreography.v`);

    for (const [operationIndex, operationValue] of array(
      object(event.patch, `${location}.patch`).operations,
      `${location}.patch.operations`,
    ).entries()) {
      const operation = object(
        operationValue,
        `${location}.patch.operations[${operationIndex}]`,
      );
      if (operation.op === "put") {
        const node = object(
          operation.node,
          `${location}.patch.operations[${operationIndex}].node`,
        );
        const id = string(
          node.id,
          `${location}.patch.operations[${operationIndex}].node.id`,
        );
        const kind = string(
          node.kind,
          `${location}.patch.operations[${operationIndex}].node.kind`,
        );
        activeNodes.set(id, { id, kind });
      } else if (operation.op === "remove") {
        const id = string(
          operation.id,
          `${location}.patch.operations[${operationIndex}].id`,
        );
        if (!activeNodes.delete(id)) {
          fail(location, `removes absent node ${id}`);
        }
      } else {
        fail(
          `${location}.patch.operations[${operationIndex}].op`,
          "unknown operation",
        );
      }
    }
    const activeIds = orderNodeIdsForSvgPaint([...activeNodes.values()]);

    const cues = array(phase.cues, `${location}.phase.cues`).map(
      (cueValue, cueIndex) => {
        const cue = exactKeys(
          cueValue,
          ["cue", "targetIds"],
          `${location}.phase.cues[${cueIndex}]`,
        );
        return {
          cue: string(cue.cue, `${location}.phase.cues[${cueIndex}].cue`),
          targetIds: stringArray(
            cue.targetIds,
            `${location}.phase.cues[${cueIndex}].targetIds`,
          ),
        };
      },
    );
    const durationMs = number(
      phase.durationMs,
      `${location}.phase.durationMs`,
      {
        integer: true,
        minimum: 0,
      },
    );
    const holdAfterMs = number(
      phase.holdAfterMs,
      `${location}.phase.holdAfterMs`,
      { integer: true, minimum: 0 },
    );
    const certificateSha256 = string(
      certificate.certificateSha256,
      `${location}.certificate.certificateSha256`,
    );
    if (!SHA256_PATTERN.test(certificateSha256)) {
      fail(
        `${location}.certificate.certificateSha256`,
        "must be lowercase SHA-256",
      );
    }
    literal(
      certificateBody.compilerVersion,
      compilerVersion,
      `${location}.certificate.compilerVersion`,
    );
    literal(
      certificateBody.checkpointId,
      checkpointId,
      `${location}.certificate.checkpointId`,
    );

    const baseViewports = object(
      presentation.baseViewports,
      `${location}.presentation.baseViewports`,
    );
    const resultViewports = object(
      presentation.resultViewports,
      `${location}.presentation.resultViewports`,
    );
    return {
      ordinal: index + 1,
      sequence: event.sequence,
      baseRevision: event.baseRevision,
      resultRevision: event.resultRevision,
      checkpointId,
      certificateSha256,
      caption: string(
        presentation.checkpointNarration,
        `${location}.presentation.checkpointNarration`,
      ),
      nodeIds: [...activeIds],
      viewports: {
        cinematic: {
          base: viewportString(
            baseViewports.cinematic,
            `${location}.baseViewports.cinematic`,
          ),
          result: viewportString(
            resultViewports.cinematic,
            `${location}.resultViewports.cinematic`,
          ),
        },
        compact: {
          base: viewportString(
            baseViewports.compact,
            `${location}.baseViewports.compact`,
          ),
          result: viewportString(
            resultViewports.compact,
            `${location}.resultViewports.compact`,
          ),
        },
      },
      phase: {
        durationMs,
        holdAfterMs,
        easing: string(phase.easing, `${location}.phase.easing`),
        cues,
      },
    };
  });

  for (const layout of ["cinematic", "compact"]) {
    for (let index = 1; index < checkpoints.length; index += 1) {
      literal(
        checkpoints[index].viewports[layout].base,
        checkpoints[index - 1].viewports[layout].result,
        `fixture checkpoint ${index + 1} ${layout} base viewport`,
      );
    }
  }
  const authoredDurationMs = number(
    transcript.authoredDurationMs,
    "fixture.transcript.authoredDurationMs",
    { integer: true, minimum: 0 },
  );
  const computedDurationMs = checkpoints.reduce(
    (total, checkpoint) =>
      total + checkpoint.phase.durationMs + checkpoint.phase.holdAfterMs,
    0,
  );
  literal(
    computedDurationMs,
    authoredDurationMs,
    "fixture.transcript.authoredDurationMs",
  );

  const adaptive = object(
    root.adaptiveTranscript,
    "fixture.adaptiveTranscript",
  );
  literal(
    adaptive.baseCheckpointId,
    "missing_corner",
    "fixture.adaptiveTranscript.baseCheckpointId",
  );
  assertArrayEqual(
    stringArray(
      adaptive.checkpointIds,
      "fixture.adaptiveTranscript.checkpointIds",
      4,
    ),
    ["corner_detail", "balance_and_complete", "factor_square", "solve_roots"],
    "fixture.adaptiveTranscript.checkpointIds",
  );
  const adaptiveCheckpoints = array(
    adaptive.events,
    "fixture.adaptiveTranscript.events",
  ).filter(
    (event) =>
      object(event, "fixture.adaptiveTranscript.events[]").type ===
      "choreography_scene_checkpoint",
  );
  if (adaptiveCheckpoints.length !== 4) {
    fail(
      "fixture.adaptiveTranscript.events",
      "must contain exactly four checkpoints",
    );
  }
  const cornerEvent = object(
    adaptiveCheckpoints[0],
    "fixture.adaptive.corner_detail",
  );
  const cornerSemantic = object(
    cornerEvent.semantic,
    "fixture.adaptive.corner_detail.semantic",
  );
  literal(
    cornerSemantic.checkpointId,
    "corner_detail",
    "fixture.adaptive.corner_detail.checkpointId",
  );
  const cornerNodeIds = array(
    object(cornerEvent.patch, "fixture.adaptive.corner_detail.patch")
      .operations,
    "fixture.adaptive.corner_detail.patch.operations",
    3,
  ).map((operation, index) => {
    const value = object(
      operation,
      `fixture.adaptive.corner_detail.patch[${index}]`,
    );
    literal(
      value.op,
      "put",
      `fixture.adaptive.corner_detail.patch[${index}].op`,
    );
    return string(
      object(value.node, `fixture.adaptive.corner_detail.patch[${index}].node`)
        .id,
      `fixture.adaptive.corner_detail.patch[${index}].node.id`,
    );
  });
  assertArrayEqual(
    cornerNodeIds,
    EXPECTED_CORNER_DETAIL_IDS,
    "fixture.adaptive.corner_detail.nodeIds",
  );
  const cornerPresentation = object(
    cornerSemantic.presentation,
    "fixture.adaptive.corner_detail.presentation",
  );
  const cornerCertificateSha256 = string(
    object(
      cornerSemantic.certificate,
      "fixture.adaptive.corner_detail.certificate",
    ).certificateSha256,
    "fixture.adaptive.corner_detail.certificate.certificateSha256",
  );
  if (!SHA256_PATTERN.test(cornerCertificateSha256)) {
    fail(
      "fixture.adaptive.corner_detail.certificate.certificateSha256",
      "must be lowercase SHA-256",
    );
  }

  return {
    fixtureId,
    compilerVersion,
    authoredDurationMs,
    providerRequestCount: transcript.providerRequestCount,
    checkpoints,
    adaptive: {
      baseCheckpointId: adaptive.baseCheckpointId,
      checkpointIds: [...adaptive.checkpointIds],
      authoredDurationMs: number(
        adaptive.authoredDurationMs,
        "fixture.adaptiveTranscript.authoredDurationMs",
        { integer: true, minimum: 0 },
      ),
      cornerDetail: {
        checkpointId: "corner_detail",
        certificateSha256: cornerCertificateSha256,
        caption: string(
          cornerPresentation.checkpointNarration,
          "fixture.adaptive.corner_detail.presentation.checkpointNarration",
        ),
        nodeIds: cornerNodeIds,
        cinematicResultViewport: viewportString(
          object(
            cornerPresentation.resultViewports,
            "fixture.adaptive.corner_detail.resultViewports",
          ).cinematic,
          "fixture.adaptive.corner_detail.resultViewports.cinematic",
        ),
      },
    },
  };
}

export function deriveFixtureEvidence(fixture) {
  return fixtureCheckpoints(fixture);
}

export function deriveRuntimeEvidence(lesson) {
  return lesson.checkpoints
    .flatMap((checkpoint) => {
      const common = {
        generation: 1,
        attempt: 1,
        sequence: checkpoint.sequence,
        checkpointId: checkpoint.checkpointId,
        certificateSha256: checkpoint.certificateSha256,
      };
      return [
        ...checkpoint.phase.cues.map(({ cue }) => ({
          type: "cueStarted",
          ...common,
          cue,
        })),
        { type: "firstCuePresented", ...common },
        {
          type: "checkpointSettled",
          ...common,
          settlement: "completed",
        },
      ];
    })
    .map((event, index) => ({ ordinal: index + 1, ...event }));
}

function validateExpectedEvidence(value, expected, location) {
  const observed = array(value, location, expected.length);
  observed.forEach((eventValue, index) => {
    const expectedEvent = expected[index];
    const eventLocation = `${location}[${index}]`;
    const event = exactKeys(
      eventValue,
      Object.keys(expectedEvent),
      eventLocation,
    );
    for (const [key, expectedValue] of Object.entries(expectedEvent)) {
      literal(event[key], expectedValue, `${eventLocation}.${key}`);
    }
  });
  return expected;
}

function validateRuntimeEvidence(value, lesson, location) {
  return validateExpectedEvidence(
    value,
    deriveRuntimeEvidence(lesson),
    location,
  );
}

/** Test-only wrapper for the checkpoint-derived runtime trace validator. */
export function validateRuntimeEvidenceForTests(value, lesson) {
  return validateRuntimeEvidence(value, lesson, "runtime evidence");
}

function validateDomIdentity(observation, expectedNodeIds, location) {
  const nodeIds = stringArray(observation.nodeIds, `${location}.nodeIds`);
  assertArrayEqual(nodeIds, expectedNodeIds, `${location}.nodeIds`);
  const identities = object(observation.domIdentity, `${location}.domIdentity`);
  assertArrayEqual(
    Object.keys(identities),
    nodeIds,
    `${location}.domIdentity keys`,
  );
  const tokens = nodeIds.map((id) =>
    number(identities[id], `${location}.domIdentity.${id}`, {
      integer: true,
      minimum: 1,
    }),
  );
  if (new Set(tokens).size !== tokens.length) {
    fail(`${location}.domIdentity`, "must assign one unique token per node");
  }
  return { nodeIds, domIdentity: identities };
}

function validateSettledCheckpoint(observationValue, expected, location) {
  const observation = exactKeys(
    observationValue,
    [
      "ordinal",
      "checkpointId",
      "caption",
      "viewBox",
      "nodeIds",
      "domIdentity",
      "rendererTrusted",
      "transientResidueCount",
    ],
    location,
  );
  literal(observation.ordinal, expected.ordinal, `${location}.ordinal`);
  literal(
    observation.checkpointId,
    expected.checkpointId,
    `${location}.checkpointId`,
  );
  literal(observation.caption, expected.caption, `${location}.caption`);
  literal(
    observation.viewBox,
    expected.viewports.cinematic.result,
    `${location}.viewBox`,
  );
  literal(observation.rendererTrusted, true, `${location}.rendererTrusted`);
  literal(
    observation.transientResidueCount,
    0,
    `${location}.transientResidueCount`,
  );
  const identity = validateDomIdentity(observation, expected.nodeIds, location);
  return { ...observation, ...identity };
}

function validateIdentityContinuity(
  observations,
  location,
  earlierRunTokenOwners = new Map(),
) {
  const tokenOwners = new Map(earlierRunTokenOwners);
  const earlierRunTokens = new Set(earlierRunTokenOwners.keys());
  for (let index = 0; index < observations.length; index += 1) {
    const observation = observations[index];
    for (const id of observation.nodeIds) {
      const token = observation.domIdentity[id];
      if (earlierRunTokens.has(token)) {
        fail(
          `${location}[${index}].domIdentity.${id}`,
          "reuses a DOM token from the earlier run",
        );
      }
      const owner = tokenOwners.get(token);
      if (owner !== undefined && owner !== id) {
        fail(
          `${location}[${index}].domIdentity.${id}`,
          `reuses token already assigned to ${owner}`,
        );
      }
      tokenOwners.set(token, id);
    }

    if (index === 0) continue;
    const previous = observations[index - 1];
    const current = observation;
    for (const id of previous.nodeIds) {
      if (
        current.domIdentity[id] !== undefined &&
        current.domIdentity[id] !== previous.domIdentity[id]
      ) {
        fail(
          `${location}[${index}].domIdentity.${id}`,
          "changed for a retained SVG node",
        );
      }
    }
  }
  return tokenOwners;
}

function expectedInterruptionCases(lesson) {
  return INTERRUPTION_DEFINITIONS.flatMap((definition) =>
    Array.from({ length: 4 }, (_, repeat) => ({
      ...definition,
      label: `${definition.category}-${repeat + 1}`,
      checkpoint: lesson.checkpoints[definition.checkpointOrdinal - 1],
    })),
  );
}

function cueTargetIds(checkpoint, cue, location) {
  if (cue === "hold") return [];
  const matches = checkpoint.phase.cues.filter((entry) => entry.cue === cue);
  if (matches.length !== 1) {
    fail(location, `fixture must contain exactly one ${cue} cue`);
  }
  return matches[0].targetIds;
}

function interruptedEvidence(lesson, checkpoint) {
  return deriveRuntimeEvidence(lesson)
    .filter((event) => event.sequence <= checkpoint.sequence)
    .map((event) =>
      event.sequence === checkpoint.sequence &&
      event.type === "checkpointSettled"
        ? { ...event, settlement: "cancelled_to_checkpoint" }
        : event,
    );
}

function validateStageSnapshot(value, target, location) {
  const stage = exactKeys(
    value,
    ["caption", "viewBox", "nodeIds", "domIdentity", "canonicalSvg"],
    location,
  );
  literal(stage.caption, target.caption, `${location}.caption`);
  literal(stage.viewBox, target.viewBox, `${location}.viewBox`);
  const identity = validateDomIdentity(stage, target.nodeIds, location);
  assertDeepEqual(
    stage.domIdentity,
    target.domIdentity,
    `${location}.domIdentity against target`,
  );
  return {
    caption: stage.caption,
    viewBox: stage.viewBox,
    nodeIds: identity.nodeIds,
    domIdentity: stage.domIdentity,
    canonicalSvg: string(stage.canonicalSvg, `${location}.canonicalSvg`),
  };
}

function validateInterruptedStability(
  value,
  checkpoint,
  target,
  expectedEvidence,
  location,
) {
  const snapshot = exactKeys(
    value,
    ["stage", "frontier", "evidence"],
    location,
  );
  const stage = validateStageSnapshot(
    snapshot.stage,
    target,
    `${location}.stage`,
  );
  const frontier = exactKeys(
    snapshot.frontier,
    [
      "phase",
      "checkpointId",
      "settledMainCount",
      "rendererTrusted",
      "waitingFor",
    ],
    `${location}.frontier`,
  );
  literal(frontier.phase, "interrupted", `${location}.frontier.phase`);
  literal(
    frontier.checkpointId,
    checkpoint.checkpointId,
    `${location}.frontier.checkpointId`,
  );
  literal(
    frontier.settledMainCount,
    checkpoint.ordinal,
    `${location}.frontier.settledMainCount`,
  );
  literal(
    frontier.rendererTrusted,
    true,
    `${location}.frontier.rendererTrusted`,
  );
  literal(frontier.waitingFor, null, `${location}.frontier.waitingFor`);
  const evidence = validateExpectedEvidence(
    snapshot.evidence,
    expectedEvidence,
    `${location}.evidence`,
  );
  return { stage, frontier: { ...frontier }, evidence };
}

function validateInterruptionCase(value, expected, lesson, index) {
  const location = `accelerated.adaptive.interruptionCases[${index}]`;
  const observation = exactKeys(
    value,
    [
      "label",
      "category",
      "checkpointId",
      "sequence",
      "cue",
      "cueTargetIds",
      "timing",
      "authoredDurationMs",
      "authoredHoldAfterMs",
      "trigger",
      "delayAfterPresentedMs",
      "activeRevision",
      "requestedAtMs",
      "settledAtMs",
      "settleMs",
      "target",
      "evidenceBefore",
      "evidenceAfter",
      "staleWindowMs",
      "stabilityBefore",
      "stabilityAfter",
      "staleStable",
    ],
    location,
  );
  const checkpoint = expected.checkpoint;
  literal(observation.label, expected.label, `${location}.label`);
  literal(observation.category, expected.category, `${location}.category`);
  literal(
    observation.checkpointId,
    checkpoint.checkpointId,
    `${location}.checkpointId`,
  );
  literal(observation.sequence, checkpoint.sequence, `${location}.sequence`);
  literal(observation.cue, expected.cue, `${location}.cue`);
  const expectedTargets = cueTargetIds(
    checkpoint,
    expected.cue,
    `${location}.cueTargetIds`,
  );
  assertArrayEqual(
    stringArray(
      observation.cueTargetIds,
      `${location}.cueTargetIds`,
      expectedTargets.length,
    ),
    expectedTargets,
    `${location}.cueTargetIds`,
  );
  literal(observation.timing, expected.timing, `${location}.timing`);
  literal(
    observation.authoredDurationMs,
    checkpoint.phase.durationMs,
    `${location}.authoredDurationMs`,
  );
  literal(
    observation.authoredHoldAfterMs,
    checkpoint.phase.holdAfterMs,
    `${location}.authoredHoldAfterMs`,
  );
  const expectedTrigger =
    expected.timing === "hold"
      ? "afterFirstCuePresentedDelay"
      : "firstCuePresented";
  literal(observation.trigger, expectedTrigger, `${location}.trigger`);
  literal(
    observation.delayAfterPresentedMs,
    expected.timing === "hold"
      ? Math.ceil(checkpoint.phase.durationMs / 16) + 50
      : 0,
    `${location}.delayAfterPresentedMs`,
  );
  literal(
    observation.activeRevision,
    checkpoint.resultRevision,
    `${location}.activeRevision`,
  );
  const requestedAtMs = number(
    observation.requestedAtMs,
    `${location}.requestedAtMs`,
    { minimum: 0 },
  );
  const settledAtMs = number(
    observation.settledAtMs,
    `${location}.settledAtMs`,
    { minimum: 0 },
  );
  if (settledAtMs < requestedAtMs) {
    fail(`${location}.settledAtMs`, "must not precede requestedAtMs");
  }
  const settleMs = number(observation.settleMs, `${location}.settleMs`, {
    minimum: 0,
  });
  if (Math.abs(settledAtMs - requestedAtMs - settleMs) > 0.00101) {
    fail(
      `${location}.settleMs`,
      "must equal settledAtMs - requestedAtMs within serialized precision",
    );
  }

  const target = validateSettledCheckpoint(
    observation.target,
    checkpoint,
    `${location}.target`,
  );
  const expectedAfter = interruptedEvidence(lesson, checkpoint);
  const expectedBefore = expectedAfter.slice(0, -1);
  const evidenceBefore = validateExpectedEvidence(
    observation.evidenceBefore,
    expectedBefore,
    `${location}.evidenceBefore`,
  );
  const evidenceAfter = validateExpectedEvidence(
    observation.evidenceAfter,
    expectedAfter,
    `${location}.evidenceAfter`,
  );
  const targetSettlements = evidenceAfter.filter(
    (event) =>
      event.sequence === checkpoint.sequence &&
      event.type === "checkpointSettled",
  );
  if (
    targetSettlements.length !== 1 ||
    targetSettlements[0].settlement !== "cancelled_to_checkpoint" ||
    evidenceAfter.at(-1) !== targetSettlements[0]
  ) {
    fail(
      `${location}.evidenceAfter`,
      "must end once in cancelled_to_checkpoint for the target",
    );
  }
  if (evidenceAfter.some((event) => event.sequence > checkpoint.sequence)) {
    fail(`${location}.evidenceAfter`, "must contain no later sequence");
  }
  literal(observation.staleWindowMs, 2_000, `${location}.staleWindowMs`);
  literal(observation.staleStable, true, `${location}.staleStable`);
  const stabilityBefore = validateInterruptedStability(
    observation.stabilityBefore,
    checkpoint,
    target,
    expectedAfter,
    `${location}.stabilityBefore`,
  );
  const stabilityAfter = validateInterruptedStability(
    observation.stabilityAfter,
    checkpoint,
    target,
    expectedAfter,
    `${location}.stabilityAfter`,
  );
  assertDeepEqual(
    stabilityAfter,
    stabilityBefore,
    `${location}.stabilityAfter`,
  );

  return {
    label: observation.label,
    category: observation.category,
    checkpointId: observation.checkpointId,
    sequence: observation.sequence,
    cue: observation.cue,
    cueTargetIds: [...observation.cueTargetIds],
    timing: observation.timing,
    authoredDurationMs: observation.authoredDurationMs,
    authoredHoldAfterMs: observation.authoredHoldAfterMs,
    trigger: observation.trigger,
    delayAfterPresentedMs: observation.delayAfterPresentedMs,
    activeRevision: observation.activeRevision,
    requestedAtMs,
    settledAtMs,
    settleMs,
    target,
    evidenceBefore,
    evidenceAfter,
    staleWindowMs: 2_000,
    stabilityBefore,
    stabilityAfter,
    staleStable: true,
  };
}

function validateReplayViewport(value, expected, location) {
  const viewport = exactKeys(
    value,
    ["v", "x", "y", "width", "height"],
    location,
  );
  literal(viewportString(viewport, location), expected, location);
  return { ...viewport };
}

function validateReplayCheckpoint(value, expected, live, lesson, index) {
  const location = `accelerated.adaptive.replay.replayedCheckpoints[${index}]`;
  const checkpoint = exactKeys(
    value,
    [
      "ordinal",
      "checkpointId",
      "certificateSha256",
      "caption",
      "viewport",
      "nodeIds",
      "domIdentity",
      "rendererTrusted",
      "cueTrace",
    ],
    location,
  );
  literal(checkpoint.ordinal, expected.ordinal, `${location}.ordinal`);
  literal(
    checkpoint.checkpointId,
    expected.checkpointId,
    `${location}.checkpointId`,
  );
  literal(
    checkpoint.certificateSha256,
    expected.certificateSha256,
    `${location}.certificateSha256`,
  );
  literal(checkpoint.caption, expected.caption, `${location}.caption`);
  literal(checkpoint.caption, live.caption, `${location}.caption against live`);
  const viewport = validateReplayViewport(
    checkpoint.viewport,
    expected.viewports.cinematic.result,
    `${location}.viewport`,
  );
  const identity = validateDomIdentity(checkpoint, expected.nodeIds, location);
  assertArrayEqual(
    identity.nodeIds,
    live.nodeIds,
    `${location}.nodeIds against live`,
  );
  literal(checkpoint.rendererTrusted, true, `${location}.rendererTrusted`);
  const cueTrace = validateExpectedEvidence(
    checkpoint.cueTrace,
    deriveRuntimeEvidence(lesson).filter(
      (event) => event.sequence === expected.sequence,
    ),
    `${location}.cueTrace`,
  );
  return {
    ordinal: checkpoint.ordinal,
    checkpointId: checkpoint.checkpointId,
    certificateSha256: checkpoint.certificateSha256,
    caption: checkpoint.caption,
    viewport,
    nodeIds: identity.nodeIds,
    domIdentity: checkpoint.domIdentity,
    rendererTrusted: true,
    cueTrace,
  };
}

function validateReplay(value, lesson) {
  const location = "accelerated.adaptive.replay";
  const replay = exactKeys(
    value,
    [
      "liveCheckpoints",
      "replayedCheckpoints",
      "checkpointIds",
      "certificateSha256s",
      "liveEvidence",
      "replayEvidence",
      "finalCanonicalSvgMatches",
      "equivalent",
    ],
    location,
  );
  const liveCheckpoints = array(
    replay.liveCheckpoints,
    `${location}.liveCheckpoints`,
    lesson.checkpoints.length,
  ).map((checkpoint, index) =>
    validateSettledCheckpoint(
      checkpoint,
      lesson.checkpoints[index],
      `${location}.liveCheckpoints[${index}]`,
    ),
  );
  const liveTokenOwners = validateIdentityContinuity(
    liveCheckpoints,
    `${location}.liveCheckpoints`,
  );
  const replayedCheckpoints = array(
    replay.replayedCheckpoints,
    `${location}.replayedCheckpoints`,
    lesson.checkpoints.length,
  ).map((checkpoint, index) =>
    validateReplayCheckpoint(
      checkpoint,
      lesson.checkpoints[index],
      liveCheckpoints[index],
      lesson,
      index,
    ),
  );
  validateIdentityContinuity(
    replayedCheckpoints,
    `${location}.replayedCheckpoints`,
    liveTokenOwners,
  );
  assertArrayEqual(
    stringArray(
      replay.checkpointIds,
      `${location}.checkpointIds`,
      lesson.checkpoints.length,
    ),
    lesson.checkpoints.map((checkpoint) => checkpoint.checkpointId),
    `${location}.checkpointIds`,
  );
  assertArrayEqual(
    stringArray(
      replay.certificateSha256s,
      `${location}.certificateSha256s`,
      lesson.checkpoints.length,
    ),
    lesson.checkpoints.map((checkpoint) => checkpoint.certificateSha256),
    `${location}.certificateSha256s`,
  );
  const liveEvidence = validateRuntimeEvidence(
    replay.liveEvidence,
    lesson,
    `${location}.liveEvidence`,
  );
  const replayEvidence = validateRuntimeEvidence(
    replay.replayEvidence,
    lesson,
    `${location}.replayEvidence`,
  );
  assertDeepEqual(
    replayEvidence,
    liveEvidence,
    `${location}.replayEvidence against liveEvidence`,
  );
  literal(
    replay.finalCanonicalSvgMatches,
    true,
    `${location}.finalCanonicalSvgMatches`,
  );
  literal(replay.equivalent, true, `${location}.equivalent`);
  return {
    liveCheckpoints,
    replayedCheckpoints,
    checkpointIds: [...replay.checkpointIds],
    certificateSha256s: [...replay.certificateSha256s],
    liveEvidence,
    replayEvidence,
    finalCanonicalSvgMatches: true,
    equivalent: true,
  };
}

function validateAdaptive(value, lesson, location = "accelerated.adaptive") {
  const adaptive = exactKeys(
    value,
    [
      "interruptionSettleMsSamples",
      "interruptionSettleP95Ms",
      "interruptionCases",
      "cornerDetailNodeIds",
      "replay",
      "replayEquivalent",
      "liveSceneRequests",
      "unexpectedRequests",
    ],
    location,
  );
  const interruptionSamples = numericSamples(
    adaptive.interruptionSettleMsSamples,
    `${location}.interruptionSettleMsSamples`,
  );
  const expectedCases = expectedInterruptionCases(lesson);
  const caseValues = array(
    adaptive.interruptionCases,
    `${location}.interruptionCases`,
    expectedCases.length,
  );
  const labels = caseValues.map((entry, index) =>
    string(
      object(entry, `${location}.interruptionCases[${index}]`).label,
      `${location}.interruptionCases[${index}].label`,
    ),
  );
  if (new Set(labels).size !== labels.length) {
    fail(`${location}.interruptionCases`, "labels must be unique");
  }
  const interruptionCases = caseValues.map((entry, index) =>
    validateInterruptionCase(entry, expectedCases[index], lesson, index),
  );
  for (const definition of INTERRUPTION_DEFINITIONS) {
    const count = interruptionCases.filter(
      (entry) => entry.category === definition.category,
    ).length;
    literal(count, 4, `${location}.interruptionCases.${definition.category}`);
  }
  assertArrayEqual(
    interruptionSamples,
    interruptionCases.map((entry) => entry.settleMs),
    `${location}.interruptionSettleMsSamples`,
  );
  const interruptionP95Ms = rounded(nearestRankP95(interruptionSamples));
  literal(
    adaptive.interruptionSettleP95Ms,
    interruptionP95Ms,
    `${location}.interruptionSettleP95Ms`,
  );
  if (interruptionP95Ms >= 150) {
    fail(`${location}.interruptionSettleP95Ms`, "must be below 150 ms");
  }
  assertArrayEqual(
    stringArray(
      adaptive.cornerDetailNodeIds,
      `${location}.cornerDetailNodeIds`,
      3,
    ),
    lesson.adaptive.cornerDetail.nodeIds,
    `${location}.cornerDetailNodeIds`,
  );
  const replay = validateReplay(adaptive.replay, lesson);
  literal(adaptive.replayEquivalent, true, `${location}.replayEquivalent`);
  const requests = validateProviderFreeRequests(adaptive, location);
  return {
    interruptionSampleCount: interruptionSamples.length,
    interruptionSamplesMs: interruptionSamples,
    interruptionP95Ms,
    interruptionThresholdExclusiveMs: 150,
    interruptionCases,
    cornerDetailNodeIds: [...adaptive.cornerDetailNodeIds],
    replay,
    replayEquivalent: true,
    requests,
  };
}

/** Test-only wrapper for the complete accelerated adaptive evidence contract. */
export function validateAdaptiveEvidenceForTests(value, lesson) {
  return validateAdaptive(value, lesson);
}

function validateRequestList(value, location) {
  const requests = array(value, location);
  if (requests.length !== 0) fail(location, "must be empty");
  return [];
}

function validateProviderFreeRequests(value, location) {
  const requests = object(value, location);
  return {
    liveSceneRequests: validateRequestList(
      requests.liveSceneRequests,
      `${location}.liveSceneRequests`,
    ),
    unexpectedRequests: validateRequestList(
      requests.unexpectedRequests,
      `${location}.unexpectedRequests`,
    ),
  };
}

function providerFreeRequestCounts(requests) {
  return {
    liveSceneRequestCount: requests.liveSceneRequests.length,
    unexpectedRequestCount: requests.unexpectedRequests.length,
  };
}

/** Test-only wrapper around the request evidence accepted by production validators. */
export function validateProviderFreeRequestsForTests(value) {
  const requests = exactKeys(
    value,
    ["liveSceneRequests", "unexpectedRequests"],
    "provider-free requests",
  );
  return validateProviderFreeRequests(requests, "provider-free requests");
}

function validateNetwork(value, location) {
  const network = exactKeys(
    value,
    [
      "requestCount",
      "resourceTypeCounts",
      "liveSceneRequests",
      "unexpectedRequests",
      "failedRequests",
    ],
    location,
  );
  number(network.requestCount, `${location}.requestCount`, {
    integer: true,
    minimum: 0,
  });
  const counts = object(
    network.resourceTypeCounts,
    `${location}.resourceTypeCounts`,
  );
  let total = 0;
  for (const [resourceType, count] of Object.entries(counts)) {
    string(resourceType, `${location}.resourceTypeCounts key`);
    total += number(count, `${location}.resourceTypeCounts.${resourceType}`, {
      integer: true,
      minimum: 0,
    });
  }
  literal(total, network.requestCount, `${location}.resourceTypeCounts total`);
  validateProviderFreeRequests(network, location);
  validateRequestList(network.failedRequests, `${location}.failedRequests`);
  return network;
}

function validateAccelerated(value, lesson) {
  const root = exactKeys(
    value,
    [
      "v",
      "source",
      "environment",
      "latency",
      "cinematic",
      "reducedMotion",
      "responsive",
      "adaptive",
    ],
    "accelerated observations",
  );
  literal(root.v, 1, "accelerated observations.v");
  const source = validateSource(root.source, "accelerated observations.source");
  const environment = validateEnvironment(
    root.environment,
    "accelerated observations.environment",
  );

  const latency = exactKeys(
    root.latency,
    [
      "firstMeaningfulVisualMs",
      "p95Ms",
      "liveSceneRequests",
      "unexpectedRequests",
    ],
    "accelerated.latency",
  );
  const firstMeaningfulVisualMs = numericSamples(
    latency.firstMeaningfulVisualMs,
    "accelerated.latency.firstMeaningfulVisualMs",
  );
  const firstVisibleP95Ms = rounded(nearestRankP95(firstMeaningfulVisualMs));
  literal(latency.p95Ms, firstVisibleP95Ms, "accelerated.latency.p95Ms");
  if (firstVisibleP95Ms >= 100) {
    fail("accelerated.latency.p95Ms", "must be below 100 ms");
  }
  const latencyRequests = validateProviderFreeRequests(
    latency,
    "accelerated.latency",
  );

  const cinematic = exactKeys(
    root.cinematic,
    ["checkpoints", "liveSceneRequests", "unexpectedRequests"],
    "accelerated.cinematic",
  );
  const cinematicCheckpoints = array(
    cinematic.checkpoints,
    "accelerated.cinematic.checkpoints",
    8,
  ).map((checkpoint, index) =>
    validateSettledCheckpoint(
      checkpoint,
      lesson.checkpoints[index],
      `accelerated.cinematic.checkpoints[${index}]`,
    ),
  );
  validateIdentityContinuity(
    cinematicCheckpoints,
    "accelerated.cinematic.checkpoints",
  );
  const cinematicRequests = validateProviderFreeRequests(
    cinematic,
    "accelerated.cinematic",
  );

  const reduced = exactKeys(
    root.reducedMotion,
    [
      "final",
      "equivalentToCinematic",
      "liveSceneRequests",
      "unexpectedRequests",
    ],
    "accelerated.reducedMotion",
  );
  const reducedFinal = validateSettledCheckpoint(
    reduced.final,
    lesson.checkpoints.at(-1),
    "accelerated.reducedMotion.final",
  );
  literal(
    reduced.equivalentToCinematic,
    true,
    "accelerated.reducedMotion.equivalentToCinematic",
  );
  const reducedRequests = validateProviderFreeRequests(
    reduced,
    "accelerated.reducedMotion",
  );
  for (const key of ["caption", "viewBox", "nodeIds"]) {
    if (
      JSON.stringify(reducedFinal[key]) !==
      JSON.stringify(cinematicCheckpoints.at(-1)[key])
    ) {
      fail(
        `accelerated.reducedMotion.final.${key}`,
        "drifts from cinematic final state",
      );
    }
  }

  const responsiveValues = array(root.responsive, "accelerated.responsive", 2);
  const responsiveRequestEvidence = [];
  const responsive = responsiveValues
    .map((entryValue, index) => {
      const location = `accelerated.responsive[${index}]`;
      const entry = exactKeys(
        entryValue,
        [
          "viewport",
          "documentWidth",
          "bodyWidth",
          "stage",
          "finalViewBox",
          "liveSceneRequests",
          "unexpectedRequests",
        ],
        location,
      );
      const viewport = exactKeys(
        entry.viewport,
        ["width", "height"],
        `${location}.viewport`,
      );
      const width = number(viewport.width, `${location}.viewport.width`, {
        integer: true,
        minimum: 1,
      });
      const height = number(viewport.height, `${location}.viewport.height`, {
        integer: true,
        minimum: 1,
      });
      const documentWidth = number(
        entry.documentWidth,
        `${location}.documentWidth`,
        {
          minimum: 0,
        },
      );
      const bodyWidth = number(entry.bodyWidth, `${location}.bodyWidth`, {
        minimum: 0,
      });
      if (documentWidth > width || bodyWidth > width) {
        fail(location, "contains horizontal page overflow");
      }
      const stage = exactKeys(
        entry.stage,
        ["x", "y", "width", "height"],
        `${location}.stage`,
      );
      for (const key of ["x", "y", "width", "height"]) {
        number(stage[key], `${location}.stage.${key}`);
      }
      if (
        stage.x < -0.5 ||
        stage.y < -0.5 ||
        stage.width <= 0 ||
        stage.height <= 0 ||
        stage.x + stage.width > width + 0.5 ||
        stage.y + stage.height > height + 0.5
      ) {
        fail(location, "stage exceeds the tested viewport");
      }
      literal(
        entry.finalViewBox,
        lesson.checkpoints.at(-1).viewports.compact.result,
        `${location}.finalViewBox`,
      );
      const requests = validateProviderFreeRequests(entry, location);
      responsiveRequestEvidence.push({
        viewport: { width, height },
        ...providerFreeRequestCounts(requests),
      });
      return {
        viewport: { width, height },
        documentWidth,
        bodyWidth,
        stage: { ...stage },
        finalViewBox: entry.finalViewBox,
      };
    })
    .sort((left, right) => left.viewport.width - right.viewport.width);
  if (
    JSON.stringify(responsive.map((entry) => entry.viewport)) !==
    JSON.stringify(EXPECTED_RESPONSIVE_VIEWPORTS)
  ) {
    fail(
      "accelerated.responsive",
      "must cover exactly 320x568 and 375x812 once each",
    );
  }

  const adaptive = validateAdaptive(root.adaptive, lesson);

  return {
    source,
    environment,
    latency: {
      sampleCount: firstMeaningfulVisualMs.length,
      samplesMs: firstMeaningfulVisualMs,
      p95Ms: firstVisibleP95Ms,
      thresholdExclusiveMs: 100,
    },
    cinematicCheckpoints,
    reducedMotion: {
      equivalentToCinematic: true,
      finalCheckpointId: reducedFinal.checkpointId,
      finalViewBox: reducedFinal.viewBox,
      finalNodeIds: reducedFinal.nodeIds,
    },
    responsive,
    adaptive,
    requests: {
      latency: providerFreeRequestCounts(latencyRequests),
      cinematic: providerFreeRequestCounts(cinematicRequests),
      reducedMotion: providerFreeRequestCounts(reducedRequests),
      responsive: responsiveRequestEvidence.sort(
        (left, right) => left.viewport.width - right.viewport.width,
      ),
      adaptive: providerFreeRequestCounts(adaptive.requests),
    },
  };
}

function validateObservedArtifact(value, expectedPath, location, dimensions) {
  const expectedKeys = dimensions
    ? ["path", "bytes", "sha256", "width", "height"]
    : ["path", "bytes", "sha256"];
  const artifact = exactKeys(value, expectedKeys, location);
  literal(artifact.path, expectedPath, `${location}.path`);
  number(artifact.bytes, `${location}.bytes`, { integer: true, minimum: 1 });
  if (!SHA256_PATTERN.test(string(artifact.sha256, `${location}.sha256`))) {
    fail(`${location}.sha256`, "must be lowercase SHA-256");
  }
  if (dimensions) {
    literal(artifact.width, dimensions.width, `${location}.width`);
    literal(artifact.height, dimensions.height, `${location}.height`);
  }
  return artifact;
}

function validateCaptureCheckpoint(value, expected, index) {
  const location = `capture.checkpointCapture.checkpoints[${index}]`;
  const checkpoint = exactKeys(
    value,
    [
      "ordinal",
      "checkpointId",
      "caption",
      "viewBox",
      "nodeIds",
      "domIdentity",
      "rendererTrusted",
      "transientResidueCount",
      "gateOpenedAtMs",
      "capturedAtMs",
      "gateToCaptureMs",
      "cues",
      "baseViewport",
      "resultViewport",
      "screenshot",
    ],
    location,
  );
  const settled = validateSettledCheckpoint(
    Object.fromEntries(
      Object.entries(checkpoint).filter(([key]) =>
        [
          "ordinal",
          "checkpointId",
          "caption",
          "viewBox",
          "nodeIds",
          "domIdentity",
          "rendererTrusted",
          "transientResidueCount",
        ].includes(key),
      ),
    ),
    expected,
    location,
  );
  const openedAtMs = number(
    checkpoint.gateOpenedAtMs,
    `${location}.gateOpenedAtMs`,
    {
      minimum: 0,
    },
  );
  const capturedAtMs = number(
    checkpoint.capturedAtMs,
    `${location}.capturedAtMs`,
    {
      minimum: 0,
    },
  );
  const delta = number(
    checkpoint.gateToCaptureMs,
    `${location}.gateToCaptureMs`,
    {
      minimum: 0,
    },
  );
  if (
    capturedAtMs < openedAtMs ||
    Math.abs(capturedAtMs - openedAtMs - delta) > 1
  ) {
    fail(
      `${location}.gateToCaptureMs`,
      "does not match capture minus gate timestamps",
    );
  }
  assertArrayEqual(
    stringArray(checkpoint.cues, `${location}.cues`),
    expected.phase.cues.map((cue) => cue.cue),
    `${location}.cues`,
  );
  literal(
    checkpoint.baseViewport,
    expected.viewports.cinematic.base,
    `${location}.baseViewport`,
  );
  literal(
    checkpoint.resultViewport,
    expected.viewports.cinematic.result,
    `${location}.resultViewport`,
  );
  const screenshotPath = `capture/checkpoints/${String(index + 1).padStart(2, "0")}-${expected.checkpointId}.png`;
  const screenshot = validateObservedArtifact(
    checkpoint.screenshot,
    screenshotPath,
    `${location}.screenshot`,
    { width: 1280, height: 720 },
  );
  return {
    ...settled,
    openedAtMs,
    capturedAtMs,
    gateToCaptureMs: delta,
    screenshot,
  };
}

const MAX_UNEXPLAINED_PACING_GAP_MS = 1_200;
const MAX_EARLY_SETTLEMENT_JITTER_MS = 250;

function validateRealTimePacing(
  { firstVisibleAtMs, completedAtMs, settlements, phaseTransitions },
  lesson,
) {
  const expectedTransitions = [
    { phase: "idle", checkpointId: "none", settledMainCount: 0 },
    { phase: "connecting", checkpointId: "none", settledMainCount: 0 },
    { phase: "streaming", checkpointId: "none", settledMainCount: 0 },
    { phase: "completing", checkpointId: "none", settledMainCount: 0 },
    ...lesson.checkpoints.map((checkpoint, index) => ({
      phase:
        index === lesson.checkpoints.length - 1 ? "completed" : "completing",
      checkpointId: checkpoint.checkpointId,
      settledMainCount: index + 1,
    })),
  ];
  if (phaseTransitions.length !== expectedTransitions.length) {
    fail(
      "capture.realTime.phaseTransitions",
      `must contain exactly ${expectedTransitions.length} closed transitions`,
    );
  }
  phaseTransitions.forEach((transition, index) => {
    const location = `capture.realTime.phaseTransitions[${index}]`;
    const expected = expectedTransitions[index];
    literal(transition.phase, expected.phase, `${location}.phase`);
    literal(
      transition.checkpointId,
      expected.checkpointId,
      `${location}.checkpointId`,
    );
    literal(
      transition.settledMainCount,
      expected.settledMainCount,
      `${location}.settledMainCount`,
    );
    if (index > 0 && transition.atMs <= phaseTransitions[index - 1].atMs) {
      fail(`${location}.atMs`, "must increase strictly");
    }
    if (transition.atMs > completedAtMs + 1) {
      fail(`${location}.atMs`, "must not occur after completion");
    }
  });

  return settlements.map((settlement, index) => {
    const checkpoint = lesson.checkpoints[index];
    const transition = phaseTransitions[index + 4];
    literal(
      transition.atMs,
      settlement.atMs,
      `capture.realTime.phaseTransitions[${index + 4}].atMs`,
    );
    const startedAtMs =
      index === 0 ? firstVisibleAtMs : settlements[index - 1].atMs;
    const observedMs = settlement.atMs - startedAtMs;
    const authoredMs =
      checkpoint.phase.durationMs + checkpoint.phase.holdAfterMs;
    const unexplainedMs = observedMs - authoredMs;
    if (unexplainedMs < -MAX_EARLY_SETTLEMENT_JITTER_MS) {
      fail(
        `capture.realTime.settlements[${index}].atMs`,
        `settles ${Math.abs(unexplainedMs).toFixed(1)} ms before its authored motion and hold`,
      );
    }
    if (unexplainedMs > MAX_UNEXPLAINED_PACING_GAP_MS) {
      fail(
        `capture.realTime.settlements[${index}].atMs`,
        `contains an unexplained ${unexplainedMs.toFixed(1)} ms gap beyond its authored motion and hold`,
      );
    }
    return {
      checkpointId: checkpoint.checkpointId,
      authoredMs,
      observedMs,
      unexplainedMs,
    };
  });
}

/** Test-only wrapper for the closed real-time phase and pacing validator. */
export function validateRealTimePacingForTests(evidence, lesson) {
  return validateRealTimePacing(evidence, lesson);
}

function validateCapture(value, lesson) {
  const root = exactKeys(
    value,
    [
      "v",
      "gate",
      "fixtureId",
      "compilerVersion",
      "generatedAt",
      "source",
      "environment",
      "realTime",
      "checkpointCapture",
    ],
    "capture observations",
  );
  literal(root.v, 1, "capture.v");
  literal(root.gate, "1.5", "capture.gate");
  literal(root.fixtureId, lesson.fixtureId, "capture.fixtureId");
  literal(
    root.compilerVersion,
    lesson.compilerVersion,
    "capture.compilerVersion",
  );
  const generatedAt = string(root.generatedAt, "capture.generatedAt");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(generatedAt)) {
    fail("capture.generatedAt", "must be an ISO-8601 UTC timestamp");
  }
  const source = validateSource(root.source, "capture observations.source");
  const environment = validateEnvironment(
    root.environment,
    "capture observations.environment",
  );

  const realTime = exactKeys(
    root.realTime,
    [
      "route",
      "layout",
      "viewport",
      "fixtureAuthoredDurationMs",
      "firstMeaningfulVisualAtMs",
      "completedAtMs",
      "visualDurationMs",
      "settlements",
      "phaseTransitions",
      "longFrames",
      "longTasks",
      "finalCheckpoint",
      "runtimeEvidence",
      "network",
      "video",
    ],
    "capture.realTime",
  );
  literal(
    realTime.route,
    "/e2e/choreography?layout=cinematic&motion=real&pace=auto&timing=real",
    "capture.realTime.route",
  );
  literal(realTime.layout, "cinematic", "capture.realTime.layout");
  const videoViewport = exactKeys(
    realTime.viewport,
    ["width", "height"],
    "capture.realTime.viewport",
  );
  literal(videoViewport.width, 1280, "capture.realTime.viewport.width");
  literal(videoViewport.height, 720, "capture.realTime.viewport.height");
  literal(
    realTime.fixtureAuthoredDurationMs,
    lesson.authoredDurationMs,
    "capture.realTime.fixtureAuthoredDurationMs",
  );
  const firstVisibleAtMs = number(
    realTime.firstMeaningfulVisualAtMs,
    "capture.realTime.firstMeaningfulVisualAtMs",
    { minimum: 0 },
  );
  const completedAtMs = number(
    realTime.completedAtMs,
    "capture.realTime.completedAtMs",
    {
      minimum: 0,
    },
  );
  const visualDurationMs = number(
    realTime.visualDurationMs,
    "capture.realTime.visualDurationMs",
    { minimum: 0 },
  );
  if (visualDurationMs < 60_000 || visualDurationMs > 90_000) {
    fail(
      "capture.realTime.visualDurationMs",
      "must be between 60000 and 90000 ms",
    );
  }
  if (Math.abs(completedAtMs - firstVisibleAtMs - visualDurationMs) > 1) {
    fail(
      "capture.realTime.visualDurationMs",
      "does not match completion minus first visual",
    );
  }
  if (Math.abs(visualDurationMs - lesson.authoredDurationMs) > 2_000) {
    fail(
      "capture.realTime.visualDurationMs",
      "drifts more than 2000 ms from authored timing",
    );
  }
  const settlements = array(
    realTime.settlements,
    "capture.realTime.settlements",
    8,
  ).map((entryValue, index) => {
    const location = `capture.realTime.settlements[${index}]`;
    const entry = exactKeys(
      entryValue,
      ["ordinal", "checkpointId", "phase", "atMs"],
      location,
    );
    literal(entry.ordinal, index + 1, `${location}.ordinal`);
    literal(
      entry.checkpointId,
      lesson.checkpoints[index].checkpointId,
      `${location}.checkpointId`,
    );
    literal(
      entry.phase,
      index === 7 ? "completed" : "completing",
      `${location}.phase`,
    );
    number(entry.atMs, `${location}.atMs`, { minimum: 0 });
    if (index > 0 && entry.atMs <= realTime.settlements[index - 1].atMs) {
      fail(`${location}.atMs`, "must increase strictly");
    }
    return { ...entry };
  });
  if (Math.abs(settlements.at(-1).atMs - completedAtMs) > 1) {
    fail(
      "capture.realTime.completedAtMs",
      "does not match the final settlement",
    );
  }
  const phaseTransitions = array(
    realTime.phaseTransitions,
    "capture.realTime.phaseTransitions",
  ).map((entryValue, index) => {
    const location = `capture.realTime.phaseTransitions[${index}]`;
    const entry = exactKeys(
      entryValue,
      ["phase", "checkpointId", "settledMainCount", "atMs"],
      location,
    );
    string(entry.phase, `${location}.phase`);
    string(entry.checkpointId, `${location}.checkpointId`);
    number(entry.settledMainCount, `${location}.settledMainCount`, {
      integer: true,
      minimum: 0,
    });
    number(entry.atMs, `${location}.atMs`, { minimum: 0 });
    return { ...entry };
  });
  const transitionCheckpointIds = phaseTransitions
    .filter((entry) => entry.settledMainCount > 0)
    .map((entry) => entry.checkpointId);
  assertArrayEqual(
    transitionCheckpointIds,
    EXPECTED_CHECKPOINT_IDS,
    "capture.realTime.phaseTransitions checkpoint sequence",
  );
  const timingWindows = validateRealTimePacing(
    { firstVisibleAtMs, completedAtMs, settlements, phaseTransitions },
    lesson,
  );
  const samples = (name) =>
    array(realTime[name], `capture.realTime.${name}`).map(
      (entryValue, index) => {
        const location = `capture.realTime.${name}[${index}]`;
        const entry = exactKeys(
          entryValue,
          ["startTimeMs", "durationMs"],
          location,
        );
        number(entry.startTimeMs, `${location}.startTimeMs`, { minimum: 0 });
        number(entry.durationMs, `${location}.durationMs`, { minimum: 0 });
        return { ...entry };
      },
    );
  const longFrames = samples("longFrames");
  const longTasks = samples("longTasks");
  const finalCheckpoint = validateSettledCheckpoint(
    realTime.finalCheckpoint,
    lesson.checkpoints.at(-1),
    "capture.realTime.finalCheckpoint",
  );
  const realTimeRuntimeEvidence = validateRuntimeEvidence(
    realTime.runtimeEvidence,
    lesson,
    "capture.realTime.runtimeEvidence",
  );
  const realNetwork = validateNetwork(
    realTime.network,
    "capture.realTime.network",
  );
  const video = validateObservedArtifact(
    realTime.video,
    "capture/live-choreography.webm",
    "capture.realTime.video",
  );

  const checkpointCapture = exactKeys(
    root.checkpointCapture,
    [
      "route",
      "layout",
      "viewport",
      "network",
      "runtimeEvidence",
      "checkpoints",
      "contactSheet",
    ],
    "capture.checkpointCapture",
  );
  literal(
    checkpointCapture.route,
    "/e2e/choreography?layout=cinematic&motion=real&pace=step&timing=accelerated",
    "capture.checkpointCapture.route",
  );
  literal(
    checkpointCapture.layout,
    "cinematic",
    "capture.checkpointCapture.layout",
  );
  const stillViewport = exactKeys(
    checkpointCapture.viewport,
    ["width", "height"],
    "capture.checkpointCapture.viewport",
  );
  literal(
    stillViewport.width,
    1280,
    "capture.checkpointCapture.viewport.width",
  );
  literal(
    stillViewport.height,
    720,
    "capture.checkpointCapture.viewport.height",
  );
  const stillNetwork = validateNetwork(
    checkpointCapture.network,
    "capture.checkpointCapture.network",
  );
  const checkpointRuntimeEvidence = validateRuntimeEvidence(
    checkpointCapture.runtimeEvidence,
    lesson,
    "capture.checkpointCapture.runtimeEvidence",
  );
  const checkpoints = array(
    checkpointCapture.checkpoints,
    "capture.checkpointCapture.checkpoints",
    8,
  ).map((entry, index) =>
    validateCaptureCheckpoint(entry, lesson.checkpoints[index], index),
  );
  validateIdentityContinuity(
    checkpoints,
    "capture.checkpointCapture.checkpoints",
  );
  const contactSheet = validateObservedArtifact(
    checkpointCapture.contactSheet,
    "capture/live-choreography-contact-sheet.png",
    "capture.checkpointCapture.contactSheet",
    { width: 1280, height: 1576 },
  );

  return {
    generatedAt,
    source,
    environment,
    realTime: {
      route: realTime.route,
      layout: realTime.layout,
      viewport: { ...videoViewport },
      firstMeaningfulVisualAtMs: firstVisibleAtMs,
      completedAtMs,
      visualDurationMs,
      settlements,
      phaseTransitions,
      timingWindows,
      longFrames,
      longTasks,
      finalCheckpoint,
      runtimeEvidence: realTimeRuntimeEvidence,
      network: realNetwork,
      video,
    },
    checkpointCapture: {
      route: checkpointCapture.route,
      layout: checkpointCapture.layout,
      viewport: { ...stillViewport },
      checkpoints,
      runtimeEvidence: checkpointRuntimeEvidence,
      network: stillNetwork,
      contactSheet,
    },
  };
}

function collectReportTests(suites, files, tests, location = "report.suites") {
  for (const [suiteIndex, suiteValue] of array(suites, location).entries()) {
    const suiteLocation = `${location}[${suiteIndex}]`;
    const suite = object(suiteValue, suiteLocation);
    if (suite.file)
      files.add(path.basename(string(suite.file, `${suiteLocation}.file`)));
    for (const [specIndex, specValue] of array(
      suite.specs ?? [],
      `${suiteLocation}.specs`,
    ).entries()) {
      const specLocation = `${suiteLocation}.specs[${specIndex}]`;
      const spec = object(specValue, specLocation);
      literal(spec.ok, true, `${specLocation}.ok`);
      const title = string(spec.title, `${specLocation}.title`);
      for (const [testIndex, testValue] of array(
        spec.tests,
        `${specLocation}.tests`,
      ).entries()) {
        const testLocation = `${specLocation}.tests[${testIndex}]`;
        const reportTest = object(testValue, testLocation);
        literal(reportTest.status, "expected", `${testLocation}.status`);
        literal(
          reportTest.expectedStatus,
          "passed",
          `${testLocation}.expectedStatus`,
        );
        const results = array(reportTest.results, `${testLocation}.results`);
        if (
          results.length === 0 ||
          !results.some(
            (result) =>
              object(result, `${testLocation}.results[]`).status === "passed",
          ) ||
          results.some(
            (result) =>
              object(result, `${testLocation}.results[]`).status !== "passed",
          )
        ) {
          fail(`${testLocation}.results`, "must contain only passing attempts");
        }
        tests.push(title);
      }
    }
    collectReportTests(
      suite.suites ?? [],
      files,
      tests,
      `${suiteLocation}.suites`,
    );
  }
}

function validatePlaywrightCi(value, source, location) {
  const ci = exactKeys(
    value,
    ["commitHref", "commitHash", "buildHref"],
    location,
  );
  literal(ci.commitHash, source.gitCommit, `${location}.commitHash`);
  let commitUrl;
  let buildUrl;
  try {
    commitUrl = new URL(string(ci.commitHref, `${location}.commitHref`));
    buildUrl = new URL(string(ci.buildHref, `${location}.buildHref`));
  } catch {
    fail(location, "must contain absolute GitHub URLs");
  }
  for (const [name, url] of [
    ["commitHref", commitUrl],
    ["buildHref", buildUrl],
  ]) {
    if (
      url.protocol !== "https:" ||
      url.hostname !== "github.com" ||
      url.username !== "" ||
      url.password !== "" ||
      url.search !== "" ||
      url.hash !== ""
    ) {
      fail(`${location}.${name}`, "must be a canonical github.com HTTPS URL");
    }
  }
  const escapedCommit = source.gitCommit.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const commitMatch = commitUrl.pathname.match(
    new RegExp(`^/([^/]+)/([^/]+)/commit/${escapedCommit}/?$`),
  );
  if (!commitMatch) {
    fail(
      `${location}.commitHref`,
      "must identify the exact report source commit",
    );
  }
  const [, owner, repository] = commitMatch;
  if (
    !new RegExp(`^/${owner}/${repository}/actions/runs/[1-9][0-9]*/?$`).test(
      buildUrl.pathname,
    )
  ) {
    fail(
      `${location}.buildHref`,
      "must identify a workflow run in the same GitHub repository",
    );
  }
  return {
    commitHref: commitUrl.href.replace(/\/$/, ""),
    commitHash: ci.commitHash,
    buildHref: buildUrl.href.replace(/\/$/, ""),
  };
}

/** Test-only wrapper for Playwright's optional GitHub report provenance. */
export function validatePlaywrightCiForTests(value, source) {
  return validatePlaywrightCi(value, source, "report.config.metadata.ci");
}

function validateReport(
  value,
  expectedFile,
  expectedTests,
  expectedSuite,
  location,
) {
  const report = object(value, location);
  const config = object(report.config, `${location}.config`);
  const playwrightVersion = string(
    config.version,
    `${location}.config.version`,
  );
  const metadataValue = object(config.metadata, `${location}.config.metadata`);
  const metadata = exactKeys(
    metadataValue,
    [
      "gate",
      "suite",
      "source",
      "environment",
      "actualWorkers",
      ...(Object.hasOwn(metadataValue, "ci") ? ["ci"] : []),
    ],
    `${location}.config.metadata`,
  );
  literal(metadata.gate, "1.5", `${location}.config.metadata.gate`);
  literal(metadata.suite, expectedSuite, `${location}.config.metadata.suite`);
  literal(
    metadata.actualWorkers,
    1,
    `${location}.config.metadata.actualWorkers`,
  );
  const source = validateSource(
    metadata.source,
    `${location}.config.metadata.source`,
  );
  const environment = validateEnvironment(
    metadata.environment,
    `${location}.config.metadata.environment`,
  );
  literal(
    environment.playwrightVersion,
    playwrightVersion,
    `${location}.config.metadata.environment.playwrightVersion`,
  );
  const ci = metadata.ci
    ? validatePlaywrightCi(
        metadata.ci,
        source,
        `${location}.config.metadata.ci`,
      )
    : null;
  const stats = object(report.stats, `${location}.stats`);
  const expected = number(stats.expected, `${location}.stats.expected`, {
    integer: true,
    minimum: expectedTests.length,
  });
  literal(stats.skipped, 0, `${location}.stats.skipped`);
  literal(stats.unexpected, 0, `${location}.stats.unexpected`);
  literal(stats.flaky, 0, `${location}.stats.flaky`);
  const durationMs = number(stats.duration, `${location}.stats.duration`, {
    minimum: 0,
  });
  const files = new Set();
  const tests = [];
  collectReportTests(report.suites, files, tests, `${location}.suites`);
  assertArrayEqual(
    [...files].sort(),
    [expectedFile],
    `${location} suite files`,
  );
  literal(tests.length, expected, `${location} passing test count`);
  assertArrayEqual(tests, expectedTests, `${location} passing tests`);
  return {
    file: expectedFile,
    testCount: tests.length,
    durationMs,
    tests,
    source,
    environment,
    ci,
  };
}

function pngDimensions(bytes, location) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (
    bytes.length < 33 ||
    !bytes.subarray(0, 8).equals(signature) ||
    bytes.readUInt32BE(8) !== 13 ||
    bytes.subarray(12, 16).toString("ascii") !== "IHDR"
  ) {
    fail(location, "is not a valid PNG header with an IHDR chunk");
  }
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  if (width <= 0 || height <= 0) fail(location, "has invalid PNG dimensions");
  return { width, height };
}

function validateArtifactRelativePath(relativePath, role) {
  if (
    path.isAbsolute(relativePath) ||
    relativePath
      .split("/")
      .some((segment) => segment === ".." || segment === "")
  ) {
    fail(`artifact ${role}`, "path must be a normalized relative path");
  }
}

function inspectArtifact(
  relativePath,
  role,
  mediaType,
  expectedObservation,
  dimensions,
  bytes,
) {
  validateArtifactRelativePath(relativePath, role);
  const details = {
    role,
    path: relativePath,
    mediaType,
    bytes: bytes.length,
    sha256: sha256(bytes),
  };
  if (mediaType === "image/png") {
    Object.assign(details, pngDimensions(bytes, `artifact ${role}`));
  } else if (
    mediaType === "video/webm" &&
    (bytes.length < 1024 ||
      !bytes.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])))
  ) {
    fail(`artifact ${role}`, "is not a WebM/EBML file");
  }
  if (dimensions) {
    literal(details.width, dimensions.width, `artifact ${role} width`);
    literal(details.height, dimensions.height, `artifact ${role} height`);
  }
  if (expectedObservation) {
    for (const key of [
      "path",
      "bytes",
      "sha256",
      ...(dimensions ? ["width", "height"] : []),
    ]) {
      literal(
        details[key],
        expectedObservation[key],
        `artifact ${role} ${key}`,
      );
    }
  }
  return details;
}

async function exactCheckpointArtifactNames(artifactRoot) {
  const checkpointRoot = path.join(artifactRoot, "capture", "checkpoints");
  let entries;
  try {
    entries = await readdir(checkpointRoot, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT")
      fail("checkpoint artifacts", "directory is missing");
    throw error;
  }
  const names = entries.map((entry) => {
    if (!entry.isFile())
      fail("checkpoint artifacts", `${entry.name} must be a regular file`);
    return entry.name;
  });
  const expected = EXPECTED_CHECKPOINT_IDS.map(
    (checkpointId, index) =>
      `${String(index + 1).padStart(2, "0")}-${checkpointId}.png`,
  );
  assertArrayEqual(names.sort(), [...expected].sort(), "checkpoint artifacts");
}

export async function assertSingleWebmArtifact(artifactRoot) {
  const webmPaths = [];
  const visit = async (directory) => {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(absolute);
      } else if (entry.isSymbolicLink()) {
        fail("artifact bundle", `${absolute} must not be a symbolic link`);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".webm")) {
        webmPaths.push(path.relative(artifactRoot, absolute));
      } else if (!entry.isFile()) {
        fail(
          "artifact bundle",
          `${absolute} must be a regular file or directory`,
        );
      }
    }
  };
  await visit(artifactRoot);
  assertArrayEqual(
    webmPaths.sort(),
    ["capture/live-choreography.webm"],
    "WebM artifacts",
  );
}

function stableIdentityEvidence(checkpoints) {
  return {
    verified: true,
    checkpoints: checkpoints.map((checkpoint) => ({
      ordinal: checkpoint.ordinal,
      checkpointId: checkpoint.checkpointId,
      nodeIds: [...checkpoint.nodeIds],
      domIdentity: Object.fromEntries(
        checkpoint.nodeIds.map((id) => [id, checkpoint.domIdentity[id]]),
      ),
    })),
    retainedTransitions: checkpoints.slice(1).map((checkpoint, index) => {
      const previous = checkpoints[index];
      const retainedIds = previous.nodeIds.filter((id) =>
        checkpoint.nodeIds.includes(id),
      );
      return {
        from: previous.checkpointId,
        to: checkpoint.checkpointId,
        retainedIds,
        identitiesPreserved: true,
      };
    }),
  };
}

function interruptionManifestEvidence(adaptive, lesson) {
  return {
    caseCount: adaptive.interruptionCases.length,
    categoryCounts: Object.fromEntries(
      INTERRUPTION_DEFINITIONS.map(({ category }) => [
        category,
        adaptive.interruptionCases.filter(
          (entry) => entry.category === category,
        ).length,
      ]),
    ),
    staleWindowMs: 2_000,
    staleStable: true,
    cases: adaptive.interruptionCases.map((entry) => ({
      label: entry.label,
      category: entry.category,
      checkpointId: entry.checkpointId,
      sequence: entry.sequence,
      certificateSha256:
        lesson.checkpoints[entry.sequence - 1].certificateSha256,
      cue: entry.cue,
      cueTargetIds: entry.cueTargetIds,
      timing: entry.timing,
      authoredDurationMs: entry.authoredDurationMs,
      authoredHoldAfterMs: entry.authoredHoldAfterMs,
      trigger: entry.trigger,
      delayAfterPresentedMs: entry.delayAfterPresentedMs,
      activeRevision: entry.activeRevision,
      requestedAtMs: entry.requestedAtMs,
      settledAtMs: entry.settledAtMs,
      settleMs: entry.settleMs,
      targetRendererTrusted: true,
      targetTransientResidueCount: 0,
      evidenceBeforeEventCount: entry.evidenceBefore.length,
      evidenceAfterEventCount: entry.evidenceAfter.length,
      evidenceAfterSha256: sha256(
        Buffer.from(canonicalJson(entry.evidenceAfter)),
      ),
      settlement: "cancelled_to_checkpoint",
      laterSequenceCount: 0,
      staleWindowMs: entry.staleWindowMs,
      staleStable: entry.staleStable,
      stabilitySnapshotSha256: sha256(
        Buffer.from(canonicalJson(entry.stabilityBefore)),
      ),
    })),
  };
}

function replayManifestEvidence(replay) {
  return {
    checkpointCount: replay.replayedCheckpoints.length,
    checkpointIds: replay.checkpointIds,
    certificateSha256s: replay.certificateSha256s,
    checkpoints: replay.replayedCheckpoints.map((checkpoint) => ({
      ordinal: checkpoint.ordinal,
      checkpointId: checkpoint.checkpointId,
      certificateSha256: checkpoint.certificateSha256,
      caption: checkpoint.caption,
      viewport: checkpoint.viewport,
      nodeIds: checkpoint.nodeIds,
      rendererTrusted: checkpoint.rendererTrusted,
      cueTrace: checkpoint.cueTrace,
    })),
    stableIds: stableIdentityEvidence(replay.replayedCheckpoints),
    liveEvidenceEventCount: replay.liveEvidence.length,
    replayEvidenceEventCount: replay.replayEvidence.length,
    liveEvidenceSha256: sha256(Buffer.from(canonicalJson(replay.liveEvidence))),
    replayEvidenceSha256: sha256(
      Buffer.from(canonicalJson(replay.replayEvidence)),
    ),
    evidenceEquivalent: true,
    finalCanonicalSvgMatches: replay.finalCanonicalSvgMatches,
    equivalent: replay.equivalent,
  };
}

async function buildManifestFromSnapshot(rootSnapshot) {
  await assertArtifactRootSnapshot(rootSnapshot);
  const artifactRoot = rootSnapshot.canonicalRoot;
  await Promise.all([
    exactCheckpointArtifactNames(artifactRoot),
    assertSingleWebmArtifact(artifactRoot),
  ]);

  const fixturePath = path.join(REPOSITORY_ROOT, FIXTURE_RELATIVE_PATH);
  const compilerPath = path.join(REPOSITORY_ROOT, COMPILER_RELATIVE_PATH);
  const generatorPath = path.join(REPOSITORY_ROOT, GENERATOR_RELATIVE_PATH);
  const packageLockPath = path.join(
    REPOSITORY_ROOT,
    PACKAGE_LOCK_RELATIVE_PATH,
  );
  const [
    fixtureBytes,
    compilerBytes,
    generatorBytes,
    packageLockBytes,
    acceleratedBytes,
    captureBytes,
    acceleratedReportBytes,
    captureReportBytes,
  ] = await Promise.all([
    readBytes(fixturePath, "fixture"),
    readBytes(compilerPath, "compiler source"),
    readBytes(generatorPath, "fixture generator"),
    readBytes(packageLockPath, "package lock"),
    readBytes(
      path.join(artifactRoot, "accelerated", "observations.json"),
      "accelerated observations",
    ),
    readBytes(
      path.join(artifactRoot, "capture", "observations.json"),
      "capture observations",
    ),
    readBytes(
      path.join(artifactRoot, "accelerated", "report.json"),
      "accelerated report",
    ),
    readBytes(
      path.join(artifactRoot, "capture", "report.json"),
      "capture report",
    ),
  ]);
  const fixture = parseJsonBytes(fixtureBytes, "fixture");
  const lesson = fixtureCheckpoints(fixture);
  const accelerated = validateAccelerated(
    parseJsonBytes(acceleratedBytes, "accelerated observations"),
    lesson,
  );
  const capture = validateCapture(
    parseJsonBytes(captureBytes, "capture observations"),
    lesson,
  );
  const acceleratedReport = validateReport(
    parseJsonBytes(acceleratedReportBytes, "accelerated report"),
    "live-choreography.spec.ts",
    EXPECTED_ACCELERATED_TESTS,
    "accelerated",
    "accelerated report",
  );
  const captureReport = validateReport(
    parseJsonBytes(captureReportBytes, "capture report"),
    "live-choreography-capture.spec.ts",
    EXPECTED_CAPTURE_TESTS,
    "capture",
    "capture report",
  );

  const git = gitProvenance();
  const verifiedProvenance = verifyEvidenceProvenance(
    [
      ["accelerated observations.source", accelerated.source],
      ["capture observations.source", capture.source],
      ["accelerated report source", acceleratedReport.source],
      ["capture report source", captureReport.source],
    ],
    { gitCommit: git.commitSha, gitTree: git.treeSha },
    [
      ["accelerated observations.environment", accelerated.environment],
      ["capture observations.environment", capture.environment],
      ["accelerated report environment", acceleratedReport.environment],
      ["capture report environment", captureReport.environment],
    ],
  );

  const artifactSnapshots = new Map([
    ["accelerated/observations.json", acceleratedBytes],
    ["accelerated/report.json", acceleratedReportBytes],
    ["capture/observations.json", captureBytes],
    ["capture/report.json", captureReportBytes],
  ]);
  const consumedArtifactPaths = new Set();
  const artifacts = [];
  const pushArtifact = async (
    relativePath,
    role,
    mediaType,
    expectedObservation,
    dimensions,
  ) => {
    validateArtifactRelativePath(relativePath, role);
    if (consumedArtifactPaths.has(relativePath)) {
      fail(`artifact ${role}`, `${relativePath} is referenced more than once`);
    }
    consumedArtifactPaths.add(relativePath);
    const bytes =
      artifactSnapshots.get(relativePath) ??
      (await readBytes(
        path.join(artifactRoot, ...relativePath.split("/")),
        `artifact ${role}`,
      ));
    artifacts.push(
      inspectArtifact(
        relativePath,
        role,
        mediaType,
        expectedObservation,
        dimensions,
        bytes,
      ),
    );
  };
  await pushArtifact(
    "accelerated/observations.json",
    "accelerated-observations",
    "application/json",
  );
  await pushArtifact(
    "accelerated/report.json",
    "accelerated-playwright-report",
    "application/json",
  );
  await pushArtifact(
    "capture/observations.json",
    "capture-observations",
    "application/json",
  );
  await pushArtifact(
    "capture/report.json",
    "capture-playwright-report",
    "application/json",
  );
  for (const checkpoint of capture.checkpointCapture.checkpoints) {
    await pushArtifact(
      checkpoint.screenshot.path,
      `checkpoint-${checkpoint.ordinal}-${checkpoint.checkpointId}`,
      "image/png",
      checkpoint.screenshot,
      { width: 1280, height: 720 },
    );
  }
  await pushArtifact(
    capture.checkpointCapture.contactSheet.path,
    "checkpoint-contact-sheet",
    "image/png",
    capture.checkpointCapture.contactSheet,
    { width: 1280, height: 1576 },
  );
  await pushArtifact(
    capture.realTime.video.path,
    "real-time-stage-video",
    "video/webm",
    capture.realTime.video,
  );
  artifacts.sort((left, right) => left.path.localeCompare(right.path));

  const requestCases = [
    { case: "accelerated-latency", ...accelerated.requests.latency },
    { case: "accelerated-cinematic", ...accelerated.requests.cinematic },
    {
      case: "accelerated-reduced-motion",
      ...accelerated.requests.reducedMotion,
    },
    ...accelerated.requests.responsive.map((entry) => ({
      case: `accelerated-responsive-${entry.viewport.width}x${entry.viewport.height}`,
      liveSceneRequestCount: entry.liveSceneRequestCount,
      unexpectedRequestCount: entry.unexpectedRequestCount,
    })),
    {
      case: "accelerated-adaptive-replay",
      ...accelerated.requests.adaptive,
    },
    {
      case: "capture-real-time",
      ...providerFreeRequestCounts(capture.realTime.network),
    },
    {
      case: "capture-checkpoints",
      ...providerFreeRequestCounts(capture.checkpointCapture.network),
    },
  ];
  const liveSceneRequestCount = requestCases.reduce(
    (total, entry) => total + entry.liveSceneRequestCount,
    0,
  );
  const unexpectedRequestCount = requestCases.reduce(
    (total, entry) => total + entry.unexpectedRequestCount,
    0,
  );
  literal(liveSceneRequestCount, 0, "aggregate live-scene request count");
  literal(unexpectedRequestCount, 0, "aggregate unexpected request count");

  return {
    schemaVersion: 1,
    gate: "1.5",
    source: {
      gitCommit: git.commitSha,
      gitTree: git.treeSha,
      packageLock: {
        path: PACKAGE_LOCK_RELATIVE_PATH,
        sha256: sha256(packageLockBytes),
      },
      fixture: {
        path: FIXTURE_RELATIVE_PATH,
        sha256: sha256(fixtureBytes),
        fixtureId: lesson.fixtureId,
        schemaVersion: fixture.v,
      },
      compiler: {
        version: lesson.compilerVersion,
        path: COMPILER_RELATIVE_PATH,
        sha256: sha256(compilerBytes),
      },
      fixtureGenerator: {
        path: GENERATOR_RELATIVE_PATH,
        sha256: sha256(generatorBytes),
      },
    },
    environment: {
      nodeVersion: verifiedProvenance.environment.nodeVersion,
      platform: verifiedProvenance.environment.platform,
      arch: verifiedProvenance.environment.arch,
      playwrightVersion: verifiedProvenance.environment.playwrightVersion,
      browser: {
        name: verifiedProvenance.environment.browserName,
        version: verifiedProvenance.environment.browserVersion,
      },
    },
    lesson: {
      checkpointCount: lesson.checkpoints.length,
      checkpointIds: lesson.checkpoints.map(
        (checkpoint) => checkpoint.checkpointId,
      ),
      authoredDurationMs: lesson.authoredDurationMs,
      providerRequestCount: lesson.providerRequestCount,
      checkpoints: lesson.checkpoints,
      adaptive: lesson.adaptive,
    },
    evidence: {
      browserRuns: {
        accelerated: acceleratedReport,
        capture: captureReport,
      },
      runtimeTrace: {
        eventCount: capture.realTime.runtimeEvidence.length,
        events: capture.realTime.runtimeEvidence,
        realTimeMatchesFixture: true,
        checkpointCaptureMatchesFixture: true,
      },
      timing: {
        firstMeaningfulVisual: accelerated.latency,
        interruption: {
          sampleCount: accelerated.adaptive.interruptionSampleCount,
          samplesMs: accelerated.adaptive.interruptionSamplesMs,
          p95Ms: accelerated.adaptive.interruptionP95Ms,
          thresholdExclusiveMs:
            accelerated.adaptive.interruptionThresholdExclusiveMs,
        },
        realTime: {
          authoredDurationMs: lesson.authoredDurationMs,
          firstMeaningfulVisualAtMs: capture.realTime.firstMeaningfulVisualAtMs,
          completedAtMs: capture.realTime.completedAtMs,
          visualDurationMs: capture.realTime.visualDurationMs,
          acceptedRangeMs: [60_000, 90_000],
          settlements: capture.realTime.settlements,
          phaseTransitions: capture.realTime.phaseTransitions,
          checkpointWindows: capture.realTime.timingWindows,
          maximumUnexplainedGapMs: MAX_UNEXPLAINED_PACING_GAP_MS,
          longFrames: capture.realTime.longFrames,
          longTasks: capture.realTime.longTasks,
        },
      },
      requests: {
        fixtureProviderRequestCount: lesson.providerRequestCount,
        capturedBrowserResourceRequestCount:
          capture.realTime.network.requestCount +
          capture.checkpointCapture.network.requestCount,
        liveSceneRequestCount,
        unexpectedRequestCount,
        cases: requestCases,
      },
      recording: {
        route: capture.realTime.route,
        layout: capture.realTime.layout,
        viewport: capture.realTime.viewport,
        stageOnly: true,
        video: capture.realTime.video.path,
        contactSheet: capture.checkpointCapture.contactSheet.path,
      },
      stableIds: stableIdentityEvidence(accelerated.cinematicCheckpoints),
      reducedMotion: accelerated.reducedMotion,
      responsive: accelerated.responsive,
      adaptive: {
        cornerDetailCheckpointId: lesson.adaptive.cornerDetail.checkpointId,
        cornerDetailCaption: lesson.adaptive.cornerDetail.caption,
        cornerDetailNodeIds: accelerated.adaptive.cornerDetailNodeIds,
        cornerDetailViewport:
          lesson.adaptive.cornerDetail.cinematicResultViewport,
        interruption: interruptionManifestEvidence(
          accelerated.adaptive,
          lesson,
        ),
        replay: replayManifestEvidence(accelerated.adaptive.replay),
        replayEquivalent: accelerated.adaptive.replayEquivalent,
        requests: providerFreeRequestCounts(accelerated.adaptive.requests),
      },
      checkpointCapture: capture.checkpointCapture.checkpoints.map(
        (checkpoint) => ({
          ordinal: checkpoint.ordinal,
          checkpointId: checkpoint.checkpointId,
          gateOpenedAtMs: checkpoint.openedAtMs,
          capturedAtMs: checkpoint.capturedAtMs,
          gateToCaptureMs: checkpoint.gateToCaptureMs,
          screenshot: checkpoint.screenshot.path,
        }),
      ),
    },
    artifacts,
  };
}

export async function buildManifest(candidate) {
  const rootSnapshot = await inspectExistingArtifactRoot(candidate);
  const manifest = await buildManifestFromSnapshot(rootSnapshot);
  await assertArtifactRootSnapshot(rootSnapshot);
  return manifest;
}

async function withFinalizationLock(artifactRoot, action) {
  const lockPath = path.join(artifactRoot, ".manifest-finalize.lock");
  let handle;
  try {
    try {
      handle = await open(lockPath, "wx", 0o600);
    } catch (error) {
      if (error?.code === "EEXIST") {
        fail(
          "manifest finalization",
          "another finalizer already holds the lock",
        );
      }
      throw error;
    }
    await handle.writeFile(`${process.pid}\n`);
    await handle.sync();
    return await action();
  } finally {
    if (handle) {
      await handle.close().catch(() => undefined);
      await rm(lockPath, { force: true });
    }
  }
}

/** Test-only helper for duplicate-finalizer lock coverage. */
export async function withFinalizationLockForTests(artifactRoot, action) {
  return withFinalizationLock(artifactRoot, action);
}

export async function writeManifest(candidate) {
  const rootSnapshot = await inspectExistingArtifactRoot(candidate);
  const artifactRoot = rootSnapshot.canonicalRoot;
  return withFinalizationLock(artifactRoot, async () => {
    await assertArtifactRootSnapshot(rootSnapshot);
    const manifest = await buildManifestFromSnapshot(rootSnapshot);
    const manifestBytes = Buffer.from(canonicalJson(manifest));
    const digest = sha256(manifestBytes);
    await assertArtifactRootSnapshot(rootSnapshot);
    await atomicWrite(path.join(artifactRoot, MANIFEST_NAME), manifestBytes);
    await assertArtifactRootSnapshot(rootSnapshot);
    await atomicWrite(
      path.join(artifactRoot, MANIFEST_DIGEST_NAME),
      Buffer.from(`${digest}  ${MANIFEST_NAME}\n`),
    );
    await assertArtifactRootSnapshot(rootSnapshot);
    return { artifactRoot, manifest, digest };
  });
}

function atomicTemporaryPath(destination, token) {
  if (!/^[a-zA-Z0-9_-]{8,128}$/.test(token)) {
    fail("atomic write", "temporary token has an invalid shape");
  }
  return path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${token}.tmp`,
  );
}

async function atomicWriteWithTokenFactory(destination, bytes, tokenFactory) {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const temporary = atomicTemporaryPath(destination, tokenFactory());
    let handle;
    let created = false;
    try {
      try {
        handle = await open(temporary, "wx", 0o600);
        created = true;
      } catch (error) {
        if (error?.code === "EEXIST") continue;
        throw error;
      }
      await handle.writeFile(bytes);
      await handle.sync();
      await handle.close();
      handle = undefined;
      await rejectSymlinkAt(destination, "atomic destination");
      await rename(temporary, destination);
      return;
    } finally {
      if (handle) await handle.close().catch(() => undefined);
      if (created) await rm(temporary, { force: true });
    }
  }
  fail("atomic write", "could not reserve an exclusive temporary file");
}

async function atomicWrite(destination, bytes) {
  return atomicWriteWithTokenFactory(destination, bytes, () =>
    randomBytes(18).toString("hex"),
  );
}

/** Test-only injection point for deterministic exclusive-create collision tests. */
export async function atomicWriteForTests(destination, bytes, tokens) {
  let index = 0;
  return atomicWriteWithTokenFactory(destination, bytes, () => {
    const token = tokens[index];
    index += 1;
    return token ?? "missing_test_token";
  });
}

export async function validateManifest(candidate) {
  const rootSnapshot = await inspectExistingArtifactRoot(candidate);
  const artifactRoot = rootSnapshot.canonicalRoot;
  const manifestPath = path.join(artifactRoot, MANIFEST_NAME);
  const digestPath = path.join(artifactRoot, MANIFEST_DIGEST_NAME);
  const [manifestBytes, digestBytes] = await Promise.all([
    readBytes(manifestPath, "manifest"),
    readBytes(digestPath, "manifest digest"),
  ]);
  const digest = sha256(manifestBytes);
  const expectedSidecar = `${digest}  ${MANIFEST_NAME}\n`;
  literal(digestBytes.toString("utf8"), expectedSidecar, "manifest digest");
  const recorded = parseJsonBytes(manifestBytes, "manifest");
  const expected = await buildManifestFromSnapshot(rootSnapshot);
  literal(canonicalJson(recorded), canonicalJson(expected), "manifest content");
  await assertArtifactRootSnapshot(rootSnapshot);
  return { artifactRoot, manifest: expected, digest };
}
