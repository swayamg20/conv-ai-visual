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
import { inflateSync } from "node:zlib";

import {
  CAPTURE_FILES,
  GATE,
  PROTOCOL,
  SUITES,
  canonicalJson,
  exact,
  exactKeys,
  fail,
  finiteNumber,
  nonEmptyString,
  plainObject,
  sha256,
  validateCaptureObservation,
  validateEnvironment,
  validateReport,
  validateSource,
} from "./semantic-storyboard-artifact-contract.mjs";
import { combineInterruptionEvidence } from "./semantic-storyboard-interruption-contract.mjs";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));

export const WEB_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const REPOSITORY_ROOT = path.resolve(WEB_ROOT, "..");
export const DEFAULT_ARTIFACT_ROOT = path.join(
  REPOSITORY_ROOT,
  "var",
  "semantic-storyboard-e2e",
);
export const MANIFEST_NAME = "manifest.json";
export const MANIFEST_DIGEST_NAME = "manifest.sha256";

const ARTIFACT_ROOT_NAME = "semantic-storyboard-e2e";
const FIXTURE_DIRECTORY = path.join(
  WEB_ROOT,
  "src/features/live-scene/fixtures/semantic-storyboard-v1",
);
const GENERATOR_RELATIVE_PATH =
  "scripts/generate_semantic_storyboard_fixtures.py";
const PACKAGE_LOCK_RELATIVE_PATH = "web/package-lock.json";
const NEXT_ENV_RELATIVE_PATH = "web/next-env.d.ts";
const MAX_JSON_BYTES = 32 * 1024 * 1024;
const MAX_PNG_BYTES = 64 * 1024 * 1024;
const MAX_DECODED_PNG_BYTES = 128 * 1024 * 1024;
const MAX_ARTIFACT_BYTES = 256 * 1024 * 1024;
const MAX_TOTAL_ARTIFACT_BYTES = 512 * 1024 * 1024;
const MAX_VIDEO_CROP_SHORTFALL_MS = 250;
const MAX_VIDEO_CROP_OVERHANG_MS = 1_000;
const SEALED_PRIOR_FIXTURE_BASELINE = "gate-1.8-prior-fixtures-v1";
const SEALED_PRIOR_FIXTURES = Object.freeze({
  "web/src/features/live-scene/fixtures/completing-the-square.v1.json":
    "1df951328558d537347625419f9ccf16b454d06f45bd108fc79e86f89ee98887",
  "web/src/features/live-scene/fixtures/completing-square-parametric-b2-c80.v3.json":
    "963653acddb638ddef66297b7597a59301791eef4e51a9f5a2fb2e6bbc9868db",
  "web/src/features/live-scene/fixtures/completing-square-parametric-b8-c20.v3.json":
    "8c6271c14ce49909db7a46cd01e8c07563121dacefd9b057ae869bc36f0e9538",
  "web/src/features/live-scene/fixtures/completing-square-parametric-b16-c17.v3.json":
    "8e582ca138ed0976550208c452451a3cd9cd59399bd6b7ede070c7820aa1f7a4",
  "web/src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a30.v1.json":
    "1f0e3822c5c467054c5f5bdf14163203d070da1c333e989c3797c078b0e9ae43",
  "web/src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json":
    "e822f774606881ad816344415d93e3ea2cbf295e9f9fd2b02224ef0d26ee215c",
  "web/src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a60.v1.json":
    "54238cdbce8db9d5224ca9842700833109be5399498cf4c1eaafe2368c15bd81",
  "web/src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v30-a45.v1.json":
    "0ff48d1ca8c47f08483ab846c3e4a58fcf37a86ecba2bb85d9aad6e4c9800f60",
  "web/src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v30-a60.v1.json":
    "db9b445b328e4d32329de7a659ad82d1085d5e4f77dc7d77e696774a9ef81ddb",
});
const EXPECTED_FIXTURES = Object.freeze({
  "semantic-storyboard-v20-a30-a45.v1.json": Object.freeze({
    fixtureId: "semantic-storyboard-v20-a30-a45",
    anglesDeg: Object.freeze([30, 45]),
    expectedRangeRelation: "unequal_range",
    isComplementary: false,
    programCount: 2,
    continuationCount: 0,
    recoveryCount: 0,
    negativeCount: 0,
    fakeProviderStreamCount: 2,
    hasSoleAbstain: false,
    hasAcceptedPrefix: false,
  }),
  "semantic-storyboard-v20-a30-a60.v1.json": Object.freeze({
    fixtureId: "semantic-storyboard-v20-a30-a60",
    anglesDeg: Object.freeze([30, 60]),
    expectedRangeRelation: "equal_range",
    isComplementary: true,
    programCount: 3,
    continuationCount: 6,
    recoveryCount: 1,
    negativeCount: 5,
    negativeLaneIds: Object.freeze([
      "unsupported_wind",
      "unsupported_unequal_launch_height",
      "unsupported_requested_angles",
      "unsupported_svg_injection",
      "ambiguous_make_it_better",
    ]),
    fakeProviderStreamCount: 16,
    hasSoleAbstain: true,
    hasAcceptedPrefix: true,
  }),
  "semantic-storyboard-v20-a45-a60.v1.json": Object.freeze({
    fixtureId: "semantic-storyboard-v20-a45-a60",
    anglesDeg: Object.freeze([45, 60]),
    expectedRangeRelation: "unequal_range",
    isComplementary: false,
    programCount: 1,
    continuationCount: 0,
    recoveryCount: 0,
    negativeCount: 0,
    fakeProviderStreamCount: 1,
    hasSoleAbstain: false,
    hasAcceptedPrefix: false,
  }),
});

function isInside(target, boundary) {
  return target === boundary || target.startsWith(`${boundary}${path.sep}`);
}

function artifactRootFromEnvironment() {
  const configured = process.env.SEMANTIC_STORYBOARD_E2E_OUTPUT_DIR;
  return configured
    ? path.resolve(WEB_ROOT, configured)
    : DEFAULT_ARTIFACT_ROOT;
}

export function resolveArtifactRoot(candidate = artifactRootFromEnvironment()) {
  const resolved = path.resolve(candidate);
  if (path.basename(resolved) !== ARTIFACT_ROOT_NAME) {
    fail("artifact root", `must end in ${JSON.stringify(ARTIFACT_ROOT_NAME)}`);
  }
  const boundaries = [REPOSITORY_ROOT, path.resolve(tmpdir())];
  if (!boundaries.some((boundary) => isInside(resolved, boundary))) {
    fail(
      "artifact root",
      "must remain inside the repository or OS temp directory",
    );
  }
  const forbidden = new Set([
    path.parse(resolved).root,
    REPOSITORY_ROOT,
    WEB_ROOT,
    path.resolve(process.cwd()),
    ...(process.env.HOME ? [path.resolve(process.env.HOME)] : []),
  ]);
  if (forbidden.has(resolved)) fail("artifact root", "is too broad to remove");
  return resolved;
}

async function rejectSymlink(candidate, location) {
  try {
    const stat = await lstat(candidate);
    if (stat.isSymbolicLink()) fail(location, "must not be a symbolic link");
    return stat;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function assertSafeRoot(candidate) {
  const artifactRoot = resolveArtifactRoot(candidate);
  const boundary = isInside(artifactRoot, REPOSITORY_ROOT)
    ? REPOSITORY_ROOT
    : path.resolve(tmpdir());
  let cursor = artifactRoot;
  while (cursor !== boundary && isInside(cursor, boundary)) {
    await rejectSymlink(cursor, `artifact path ${cursor}`);
    cursor = path.dirname(cursor);
  }
  const realBoundary = await realpath(boundary);
  let parent = path.dirname(artifactRoot);
  while (true) {
    try {
      parent = await realpath(parent);
      break;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const next = path.dirname(parent);
      if (next === parent) throw error;
      parent = next;
    }
  }
  if (!isInside(parent, realBoundary)) {
    fail("artifact root", "escapes its boundary after resolving ancestors");
  }
  return artifactRoot;
}

async function prepareRoot(candidate, allowTemporaryRoot) {
  const artifactRoot = await assertSafeRoot(candidate);
  const permitted = allowTemporaryRoot
    ? isInside(artifactRoot, path.resolve(tmpdir()))
    : artifactRoot === DEFAULT_ARTIFACT_ROOT;
  if (!permitted) {
    fail(
      "artifact root",
      allowTemporaryRoot
        ? "test preparation is allowed only inside the OS temp directory"
        : `prepare may remove only ${DEFAULT_ARTIFACT_ROOT}`,
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

export async function prepareArtifactRoot(candidate = resolveArtifactRoot()) {
  return prepareRoot(candidate, false);
}

export async function prepareArtifactRootForTests(candidate) {
  return prepareRoot(candidate, true);
}

async function existingRoot(candidate) {
  const artifactRoot = await assertSafeRoot(candidate);
  const stat = await rejectSymlink(artifactRoot, "artifact root");
  if (!stat?.isDirectory())
    fail("artifact root", "must be an existing directory");
  return artifactRoot;
}

async function readRegularFile(filePath, location, maximum = MAX_JSON_BYTES) {
  const stat = await rejectSymlink(filePath, location);
  if (!stat?.isFile()) fail(location, "must be a regular file");
  if (stat.size < 1 || stat.size > maximum) {
    fail(location, `must contain between 1 and ${maximum} bytes`);
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

function validateLane(laneValue, location, kind) {
  const keys = [
    "baseFrontier",
    "baseScene",
    "baseSemanticScene",
    "checkpointCount",
    "checkpointIds",
    "events",
    "expectedTerminal",
    "fakeProviderStreamCount",
    "generation",
    "prompt",
    "providerRecords",
    "routingMode",
    "scenarioId",
    "tailOutcome",
  ];
  if (kind === "program") keys.push("programId");
  if (kind === "continuation") keys.push("fromPrefixCount", "fromProgramId");
  if (kind === "recovery") keys.push("fromPrefixCount", "fromScenarioId");
  const lane = exactKeys(laneValue, keys, location);
  const checkpointIds = lane.checkpointIds;
  if (
    !Array.isArray(checkpointIds) ||
    checkpointIds.length !== lane.checkpointCount
  ) {
    fail(`${location}.checkpointIds`, "must match checkpointCount");
  }
  if (new Set(checkpointIds).size !== checkpointIds.length) {
    fail(`${location}.checkpointIds`, "must be unique");
  }
  checkpointIds.forEach((entry, index) =>
    nonEmptyString(entry, `${location}.checkpointIds[${index}]`),
  );
  finiteNumber(lane.checkpointCount, `${location}.checkpointCount`, {
    integer: true,
    minimum: kind === "anchor" ? 1 : 0,
    maximum: kind === "anchor" ? 1 : 5,
  });
  exact(
    lane.routingMode,
    kind === "anchor" ? "reflex" : "director",
    `${location}.routingMode`,
  );
  const providerRecords = Array.isArray(lane.providerRecords)
    ? lane.providerRecords
    : fail(`${location}.providerRecords`, "must be an array");
  if (kind === "anchor")
    exact(providerRecords.length, 0, `${location}.providerRecords`);
  else if (
    kind === "program" ||
    kind === "continuation" ||
    kind === "recovery"
  ) {
    exact(
      providerRecords.length,
      lane.checkpointCount,
      `${location}.providerRecords length`,
    );
  }
  const terminal = plainObject(
    lane.expectedTerminal,
    `${location}.expectedTerminal`,
  );
  const frontier = plainObject(
    terminal.frontier,
    `${location}.expectedTerminal.frontier`,
  );
  if (kind !== "anchor" && lane.checkpointCount > 0) {
    nonEmptyString(
      frontier.programSha256,
      `${location}.expectedTerminal.frontier.programSha256`,
    );
  }
  return lane;
}

function validateFixture(value, expected, fileName, bytes) {
  const location = `fixture ${fileName}`;
  const fixture = exactKeys(
    value,
    [
      "acceptedPrefixMalformedTail",
      "anchor",
      "compilerVersion",
      "continuations",
      "coverage",
      "externalProviderRequestCount",
      "fakeProviderStreamCount",
      "fixtureId",
      "negativeLanes",
      "problemSpec",
      "programs",
      "protocol",
      "scenario",
      "soleAbstain",
      "v",
    ],
    location,
  );
  exact(fixture.v, 1, `${location}.v`);
  exact(fixture.protocol, PROTOCOL, `${location}.protocol`);
  exact(
    fixture.scenario,
    "qualified_semantic_storyboard",
    `${location}.scenario`,
  );
  exact(
    fixture.compilerVersion,
    "murmur.semantic_storyboard_choreography.v1",
    `${location}.compilerVersion`,
  );
  exact(fixture.fixtureId, expected.fixtureId, `${location}.fixtureId`);
  exact(
    fixture.externalProviderRequestCount,
    0,
    `${location}.externalProviderRequestCount`,
  );
  exact(
    fixture.fakeProviderStreamCount,
    expected.fakeProviderStreamCount,
    `${location}.fakeProviderStreamCount`,
  );
  const problem = exactKeys(
    fixture.problemSpec,
    ["anglesDeg", "speedMps", "v"],
    `${location}.problemSpec`,
  );
  exact(
    problem,
    { v: 1, speedMps: 20, anglesDeg: expected.anglesDeg },
    `${location}.problemSpec`,
  );
  const coverage = exactKeys(
    fixture.coverage,
    ["anglePair", "expectedRangeRelation", "isComplementary"],
    `${location}.coverage`,
  );
  exact(
    coverage.anglePair,
    expected.anglesDeg,
    `${location}.coverage.anglePair`,
  );
  exact(
    coverage.expectedRangeRelation,
    expected.expectedRangeRelation,
    `${location}.coverage.expectedRangeRelation`,
  );
  exact(
    coverage.isComplementary,
    expected.isComplementary,
    `${location}.coverage.isComplementary`,
  );
  const anchor = validateLane(fixture.anchor, `${location}.anchor`, "anchor");
  const programs = Array.isArray(fixture.programs)
    ? fixture.programs.map((lane, index) =>
        validateLane(lane, `${location}.programs[${index}]`, "program"),
      )
    : fail(`${location}.programs`, "must be an array");
  exact(programs.length, expected.programCount, `${location}.programs length`);
  const continuations = Array.isArray(fixture.continuations)
    ? fixture.continuations.map((lane, index) =>
        validateLane(
          lane,
          `${location}.continuations[${index}]`,
          lane.fromProgramId ? "continuation" : "recovery",
        ),
      )
    : fail(`${location}.continuations`, "must be an array");
  exact(
    continuations.length,
    expected.continuationCount,
    `${location}.continuations length`,
  );
  const recoveryLanes = continuations.filter((lane) => lane.fromScenarioId);
  exact(
    recoveryLanes.length,
    expected.recoveryCount,
    `${location}.recovery continuation count`,
  );
  if (recoveryLanes[0]) {
    exact(
      recoveryLanes[0].fromScenarioId,
      "accepted_prefix_malformed_tail",
      `${location}.recovery continuation source`,
    );
    exact(
      recoveryLanes[0].fromPrefixCount,
      1,
      `${location}.recovery continuation prefix`,
    );
  }
  const negativeLanes = Array.isArray(fixture.negativeLanes)
    ? fixture.negativeLanes.map((lane, index) =>
        validateLane(lane, `${location}.negativeLanes[${index}]`, "negative"),
      )
    : fail(`${location}.negativeLanes`, "must be an array");
  exact(
    negativeLanes.length,
    expected.negativeCount,
    `${location}.negativeLanes length`,
  );
  exact(
    negativeLanes.map((lane) => lane.scenarioId),
    expected.negativeLaneIds ?? [],
    `${location}.negativeLanes scenario order`,
  );
  for (const [index, lane] of negativeLanes.entries()) {
    exact(
      lane.checkpointCount,
      0,
      `${location}.negativeLanes[${index}].checkpointCount`,
    );
    exact(
      lane.events.map((event) => event.type),
      [
        "semantic_storyboard_scene_stream_started",
        "semantic_storyboard_scene_stream_declined",
      ],
      `${location}.negativeLanes[${index}].events`,
    );
  }
  exact(
    Boolean(fixture.soleAbstain),
    expected.hasSoleAbstain,
    `${location}.soleAbstain presence`,
  );
  exact(
    Boolean(fixture.acceptedPrefixMalformedTail),
    expected.hasAcceptedPrefix,
    `${location}.acceptedPrefixMalformedTail presence`,
  );
  if (fixture.soleAbstain)
    validateLane(fixture.soleAbstain, `${location}.soleAbstain`, "special");
  if (fixture.acceptedPrefixMalformedTail) {
    validateLane(
      fixture.acceptedPrefixMalformedTail,
      `${location}.acceptedPrefixMalformedTail`,
      "special",
    );
  }
  const programIds = programs.map((lane) => lane.programId);
  if (new Set(programIds).size !== programIds.length) {
    fail(`${location}.programs`, "must contain unique program IDs");
  }
  return {
    raw: fixture,
    summary: {
      path: path
        .relative(REPOSITORY_ROOT, path.join(FIXTURE_DIRECTORY, fileName))
        .split(path.sep)
        .join("/"),
      bytes: bytes.length,
      sha256: sha256(bytes),
      fixtureId: fixture.fixtureId,
      problemSpec: fixture.problemSpec,
      coverage: fixture.coverage,
      anchorCheckpointId: anchor.checkpointIds[0],
      programs: programs.map((lane) => ({
        programId: lane.programId,
        scenarioId: lane.scenarioId,
        checkpointIds: lane.checkpointIds,
        programSha256: lane.expectedTerminal.frontier.programSha256,
      })),
      continuationCount: continuations.length,
      recoveryContinuationCount: recoveryLanes.length,
      negativeLaneIds: negativeLanes.map((lane) => lane.scenarioId),
      fakeProviderStreamCount: fixture.fakeProviderStreamCount,
      hasSoleAbstain: Boolean(fixture.soleAbstain),
      hasAcceptedPrefixMalformedTail: Boolean(
        fixture.acceptedPrefixMalformedTail,
      ),
    },
  };
}

async function loadFixtureCatalog() {
  const names = (await readdir(FIXTURE_DIRECTORY, { withFileTypes: true }))
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .sort();
  exact(names, Object.keys(EXPECTED_FIXTURES).sort(), "fixture catalog files");
  const catalog = [];
  for (const fileName of names) {
    const absolute = path.join(FIXTURE_DIRECTORY, fileName);
    const bytes = await readRegularFile(absolute, `fixture ${fileName}`);
    catalog.push(
      validateFixture(
        parseJson(bytes, `fixture ${fileName}`),
        EXPECTED_FIXTURES[fileName],
        fileName,
        bytes,
      ),
    );
  }
  const programs = catalog.flatMap((entry) => entry.summary.programs);
  if (programs.length < 6)
    fail("fixture catalog", "must contain at least six programs");
  const signatures = programs.map((entry) =>
    canonicalJson(entry.checkpointIds),
  );
  if (
    new Set(signatures).size < 4 ||
    new Set(programs.map((entry) => entry.checkpointIds.length)).size < 2
  ) {
    fail(
      "fixture catalog",
      "must exercise different program orders, IDs, and lengths",
    );
  }
  return catalog;
}

async function sealedPriorFixtureEvidence() {
  const files = [];
  for (const [relative, expectedSha256] of Object.entries(
    SEALED_PRIOR_FIXTURES,
  ).sort(([left], [right]) => left.localeCompare(right))) {
    const bytes = await readRegularFile(
      path.join(REPOSITORY_ROOT, relative),
      `sealed prior fixture ${relative}`,
    );
    exact(
      sha256(bytes),
      expectedSha256,
      `sealed prior fixture ${relative} sha256`,
    );
    files.push({ path: relative, bytes: bytes.length, sha256: expectedSha256 });
  }
  return Object.freeze({
    baselineVersion: SEALED_PRIOR_FIXTURE_BASELINE,
    files: Object.freeze(files),
  });
}

function commandRaw(executable, args) {
  try {
    return execFileSync(executable, args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const stderr = error?.stderr?.toString().trim();
    fail(executable, `${args.join(" ")} failed${stderr ? ` (${stderr})` : ""}`);
  }
}

function command(executable, args) {
  return commandRaw(executable, args).trim();
}

function expectedNextDevContents(tracked) {
  let expected = tracked;
  for (const [source, generated] of [
    [
      'import "./.next/types/routes.d.ts";',
      'import "./.next/dev/types/routes.d.ts";',
    ],
    [
      'import "./.next/types/root-params.d.ts";',
      'import "./.next/dev/types/root-params.d.ts";',
    ],
  ]) {
    if (expected.split(source).length !== 2) return null;
    expected = expected.replace(source, generated);
  }
  return expected;
}

function gitProvenance() {
  exact(
    path.resolve(
      command("git", ["-C", REPOSITORY_ROOT, "rev-parse", "--show-toplevel"]),
    ),
    REPOSITORY_ROOT,
    "git repository root",
  );
  const status = commandRaw("git", [
    "-C",
    REPOSITORY_ROOT,
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
    "--",
    "backend/murmur/live_scene",
    "backend/murmur/api/routers/live_scenes.py",
    GENERATOR_RELATIVE_PATH,
    "web",
    ".github/workflows/ci.yml",
  ]).replace(/\n$/, "");
  let dirty = status ? status.split("\n") : [];
  const nextEnvStatus = ` M ${NEXT_ENV_RELATIVE_PATH}`;
  if (dirty.includes(nextEnvStatus)) {
    const tracked = commandRaw("git", [
      "-C",
      REPOSITORY_ROOT,
      "show",
      `HEAD:${NEXT_ENV_RELATIVE_PATH}`,
    ]);
    const working = readFileSync(
      path.join(REPOSITORY_ROOT, NEXT_ENV_RELATIVE_PATH),
      "utf8",
    );
    if (working === expectedNextDevContents(tracked)) {
      dirty = dirty.filter((entry) => entry !== nextEnvStatus);
    }
  }
  if (dirty.length > 0) {
    fail(
      "git",
      `semantic storyboard sources must be committed:\n${dirty.join("\n")}`,
    );
  }
  const source = validateSource(
    {
      gitCommit: command("git", [
        "-C",
        REPOSITORY_ROOT,
        "rev-parse",
        "HEAD^{commit}",
      ]),
      gitTree: command("git", [
        "-C",
        REPOSITORY_ROOT,
        "rev-parse",
        "HEAD^{tree}",
      ]),
    },
    "git",
  );
  const expected =
    process.env.SEMANTIC_STORYBOARD_EXPECTED_SHA ?? process.env.GITHUB_SHA;
  if (expected !== undefined) {
    exact(
      validateSource(
        { gitCommit: expected, gitTree: source.gitTree },
        "expected source",
      ).gitCommit,
      source.gitCommit,
      "expected semantic storyboard commit",
    );
  }
  return source;
}

async function walkFiles(root, predicate = () => true) {
  const files = [];
  const visit = async (directory) => {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) =>
      left.name.localeCompare(right.name),
    )) {
      const absolute = path.join(directory, entry.name);
      if (entry.isSymbolicLink())
        fail("source inventory", `${absolute} is a symbolic link`);
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile() && predicate(absolute)) files.push(absolute);
    }
  };
  await visit(root);
  return files;
}

async function runtimeSourceEvidence() {
  const explicit = [
    path.join(REPOSITORY_ROOT, GENERATOR_RELATIVE_PATH),
    path.join(REPOSITORY_ROOT, "backend/murmur/api/routers/live_scenes.py"),
    path.join(
      REPOSITORY_ROOT,
      "backend/murmur/live_scene/parametric_choreography_requests.py",
    ),
    path.join(REPOSITORY_ROOT, "backend/murmur/live_scene/service.py"),
    path.join(WEB_ROOT, "e2e/live-choreography-provenance.ts"),
    path.join(WEB_ROOT, "package.json"),
    path.join(REPOSITORY_ROOT, PACKAGE_LOCK_RELATIVE_PATH),
    path.join(REPOSITORY_ROOT, ".github/workflows/ci.yml"),
  ];
  const roots = [
    [
      path.join(REPOSITORY_ROOT, "backend/murmur/live_scene"),
      (file) =>
        path.basename(file).includes("semantic_storyboard") &&
        file.endsWith(".py"),
    ],
    [
      path.join(WEB_ROOT, "src/lib/live-scene"),
      (file) =>
        path.basename(file).includes("semantic-storyboard") &&
        /\.tsx?$/.test(file),
    ],
    [
      path.join(WEB_ROOT, "src/features/live-scene"),
      (file) =>
        /semantic-storyboard|certified-choreography|checkpoint-choreography/.test(
          path.basename(file),
        ) && /\.tsx?$/.test(file),
    ],
    [
      path.join(WEB_ROOT, "src/app/e2e/semantic-storyboard"),
      (file) => /\.tsx?$/.test(file),
    ],
    [
      path.join(WEB_ROOT, "src/app/labs/storyboard"),
      (file) => /\.tsx?$/.test(file),
    ],
    [
      path.join(WEB_ROOT, "src/app/(app)/canvas/storyboard"),
      (file) => /\.tsx?$/.test(file),
    ],
    [
      path.join(WEB_ROOT, "e2e"),
      (file) =>
        path.basename(file).includes("semantic-storyboard") &&
        file.endsWith(".ts"),
    ],
    [
      SCRIPT_ROOT,
      (file) =>
        path.basename(file).startsWith("semantic-storyboard-") &&
        file.endsWith(".mjs"),
    ],
    [FIXTURE_DIRECTORY, (file) => file.endsWith(".json")],
  ];
  const files = [...explicit];
  for (const [root, predicate] of roots)
    files.push(...(await walkFiles(root, predicate)));
  files.push(path.join(WEB_ROOT, "playwright.semantic-storyboard.config.ts"));
  const entries = [];
  for (const absolute of [...new Set(files)].sort()) {
    const bytes = await readRegularFile(absolute, `runtime source ${absolute}`);
    entries.push({
      path: path.relative(REPOSITORY_ROOT, absolute).split(path.sep).join("/"),
      bytes: bytes.length,
      sha256: sha256(bytes),
    });
  }
  return Object.freeze({
    files: entries,
    sha256: sha256(Buffer.from(canonicalJson(entries))),
  });
}

const CRC32_TABLE = Uint32Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) {
    crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return crc >>> 0;
});

function crc32(parts) {
  let crc = 0xffffffff;
  for (const bytes of parts) {
    for (const byte of bytes) {
      crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xff];
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngDimensions(bytes, location) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (
    bytes.length < 45 ||
    bytes.length > MAX_PNG_BYTES ||
    !bytes.subarray(0, 8).equals(signature)
  ) {
    fail(location, "must be a PNG image");
  }
  let offset = 8;
  let dimensions;
  let bitDepth;
  let colorType;
  let sawPalette = false;
  let sawImageData = false;
  let endedImageData = false;
  let sawEnd = false;
  const imageData = [];
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) {
      fail(location, "contains a truncated PNG chunk");
    }
    const length = bytes.readUInt32BE(offset);
    const chunkEnd = offset + 12 + length;
    if (!Number.isSafeInteger(chunkEnd) || chunkEnd > bytes.length) {
      fail(location, "contains a truncated PNG chunk");
    }
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
    if (bytes.readUInt32BE(offset + 8 + length) !== crc32([typeBytes, data])) {
      fail(location, `${type} has an invalid CRC`);
    }
    if (!dimensions) {
      if (type !== "IHDR" || length !== 13) {
        fail(location, "must begin with one 13-byte IHDR chunk");
      }
      const width = data.readUInt32BE(0);
      const height = data.readUInt32BE(4);
      if (
        width < 1 ||
        height < 1 ||
        !Number.isSafeInteger(width * height) ||
        width * height > 64_000_000
      ) {
        fail(location, "has unsafe image dimensions");
      }
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
      if (endedImageData) {
        fail(location, "contains non-consecutive IDAT chunks");
      }
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
    const decodedBytes = dimensions.height * (rowBytes + 1);
    if (
      !Number.isSafeInteger(decodedBytes) ||
      decodedBytes > MAX_DECODED_PNG_BYTES
    ) {
      fail(location, "has unsafe decoded image dimensions");
    }
    const decoded = inflateSync(Buffer.concat(imageData), {
      maxOutputLength: decodedBytes + 1,
    });
    if (decoded.length !== decodedBytes) {
      fail(location, "contains incomplete or overlong decoded image data");
    }
    for (let row = 0; row < dimensions.height; row += 1) {
      if (decoded[row * (rowBytes + 1)] > 4) {
        fail(location, `contains invalid filter type in decoded row ${row}`);
      }
    }
  } catch (error) {
    if (error?.name === "SemanticStoryboardEvidenceError") throw error;
    fail(location, `contains invalid compressed image data (${error.message})`);
  }
  return dimensions;
}

async function locateFfmpeg() {
  const executableName =
    process.platform === "darwin"
      ? "ffmpeg-mac"
      : process.platform === "win32"
        ? "ffmpeg-win64.exe"
        : "ffmpeg-linux";
  const roots = [
    path.join(WEB_ROOT, "node_modules/playwright-core/.local-browsers"),
    process.platform === "darwin"
      ? path.join(homedir(), "Library/Caches/ms-playwright")
      : process.platform === "win32"
        ? path.join(process.env.LOCALAPPDATA ?? homedir(), "ms-playwright")
        : path.join(homedir(), ".cache/ms-playwright"),
  ];
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    roots.unshift(
      process.env.PLAYWRIGHT_BROWSERS_PATH === "0"
        ? path.join(WEB_ROOT, "node_modules/playwright-core/.local-browsers")
        : path.resolve(process.env.PLAYWRIGHT_BROWSERS_PATH),
    );
  }
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
      .sort((a, b) => b.name.localeCompare(a.name))) {
      const executable = path.join(root, entry.name, executableName);
      try {
        await access(executable, constants.X_OK);
        return executable;
      } catch {
        // Search the next Playwright installation.
      }
    }
  }
  fail("WebM decoder", "Playwright ffmpeg is unavailable");
}

async function decodeWebm(filePath, bytes, location, skipDecode) {
  if (
    bytes.length < 256 ||
    !bytes.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])) ||
    !bytes
      .subarray(0, Math.min(bytes.length, 4_096))
      .includes(Buffer.from("webm"))
  ) {
    fail(location, "must be a complete-looking WebM recording");
  }
  if (skipDecode) return null;
  const ffmpeg = await locateFfmpeg();
  const result = spawnSync(
    ffmpeg,
    [
      "-v",
      "error",
      "-nostats",
      "-i",
      filePath,
      "-map",
      "0:v:0",
      "-vsync",
      "0",
      "-progress",
      "pipe:2",
      "-f",
      "image2",
      "-vcodec",
      "png",
      "-update",
      "1",
      "pipe:1",
    ],
    {
      stdio: ["ignore", "ignore", "pipe"],
      timeout: 120_000,
      maxBuffer: 8 * 1024 * 1024,
    },
  );
  if (result.error || result.status !== 0) {
    fail(
      location,
      `must decode completely${result.stderr?.length ? ` (${result.stderr.toString().trim()})` : ""}`,
    );
  }
  const progress = result.stderr.toString("utf8").trim().split(/\r?\n/);
  exact(
    [...progress].reverse().find((line) => line.startsWith("progress=")),
    "progress=end",
    `${location} decode progress`,
  );
  const values = progress
    .filter((line) => line.startsWith("out_time_us="))
    .map((line) => line.slice("out_time_us=".length));
  const terminal = values.at(-1);
  if (!terminal || !/^\d+$/.test(terminal)) {
    fail(location, "decoder did not report a terminal video timestamp");
  }
  const microseconds = Number(terminal);
  if (!Number.isSafeInteger(microseconds) || microseconds <= 0) {
    fail(location, "decoder reported an invalid video duration");
  }
  return Math.round(microseconds / 1_000);
}

async function artifactInventory(artifactRoot) {
  const ignored = new Set([
    MANIFEST_NAME,
    MANIFEST_DIGEST_NAME,
    ".manifest-finalize.lock",
  ]);
  const files = [];
  let totalBytes = 0;
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
          `artifact ${relative}`,
          MAX_ARTIFACT_BYTES,
        );
        totalBytes += bytes.length;
        if (totalBytes > MAX_TOTAL_ARTIFACT_BYTES)
          fail("artifact inventory", "exceeds the total byte limit");
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

async function uniqueNamedFile(root, fileName, location) {
  const matches = (await walkFiles(root)).filter(
    (candidate) => path.basename(candidate) === fileName,
  );
  if (matches.length !== 1)
    fail(
      location,
      `must contain exactly one ${fileName}; found ${matches.length}`,
    );
  return matches[0];
}

async function verifyDescriptor(root, descriptor, image, options) {
  const absolute = await uniqueNamedFile(
    root,
    descriptor.fileName,
    `capture artifact ${descriptor.fileName}`,
  );
  const bytes = await readRegularFile(
    absolute,
    `capture artifact ${descriptor.fileName}`,
    MAX_ARTIFACT_BYTES,
  );
  exact(bytes.length, descriptor.bytes, `${descriptor.fileName} bytes`);
  exact(sha256(bytes), descriptor.sha256, `${descriptor.fileName} sha256`);
  let dimensions;
  let durationMs;
  if (image) {
    dimensions = pngDimensions(bytes, descriptor.fileName);
    exact(dimensions.width, descriptor.width, `${descriptor.fileName} width`);
    exact(
      dimensions.height,
      descriptor.height,
      `${descriptor.fileName} height`,
    );
  } else {
    durationMs = await decodeWebm(
      absolute,
      bytes,
      descriptor.fileName,
      options.skipVideoDecode,
    );
  }
  return {
    path: path
      .relative(options.artifactRoot, absolute)
      .split(path.sep)
      .join("/"),
    bytes: bytes.length,
    sha256: sha256(bytes),
    ...(dimensions ?? {}),
    ...(typeof durationMs === "number" ? { durationMs } : {}),
  };
}

function videoCropDurationEvidence(durationMs, requestedDurationMs) {
  finiteNumber(durationMs, "capture video decoded duration", { minimum: 1 });
  finiteNumber(requestedDurationMs, "capture video requested duration", {
    minimum: 1,
  });
  const deltaMs = durationMs - requestedDurationMs;
  if (
    deltaMs < -MAX_VIDEO_CROP_SHORTFALL_MS ||
    deltaMs > MAX_VIDEO_CROP_OVERHANG_MS
  ) {
    fail(
      "capture video duration",
      `must be no more than ${MAX_VIDEO_CROP_SHORTFALL_MS}ms shorter or ${MAX_VIDEO_CROP_OVERHANG_MS}ms longer than the requested crop; received ${deltaMs}ms delta`,
    );
  }
  return {
    durationMs,
    requestedDurationMs,
    deltaMs,
    maximumShortfallMs: MAX_VIDEO_CROP_SHORTFALL_MS,
    maximumOverhangMs: MAX_VIDEO_CROP_OVERHANG_MS,
  };
}

export async function validateCaptureBundle(
  candidate = resolveArtifactRoot(),
  options = {},
) {
  const artifactRoot = await existingRoot(candidate);
  const fixtureCatalog = await loadFixtureCatalog();
  const captureRoot = path.join(artifactRoot, "capture");
  const observationPath = await uniqueNamedFile(
    captureRoot,
    CAPTURE_FILES.observation,
    "capture observation",
  );
  const observationBytes = await readRegularFile(
    observationPath,
    "capture observation",
  );
  const observation = validateCaptureObservation(
    parseJson(observationBytes, "capture observation"),
    fixtureCatalog.map((entry) => entry.raw),
  );
  const descriptorEntries = [
    ["video", observation.artifacts.video, false],
    ["fullPage", observation.artifacts.fullPage, true],
    ["boardOnly", observation.artifacts.boardOnly, true],
    ["contactSheet", observation.artifacts.contactSheet, true],
    ...observation.artifacts.checkpoints.map((descriptor, index) => [
      `checkpoint-${index}`,
      descriptor,
      true,
    ]),
  ];
  const artifacts = {};
  for (const [key, descriptor, image] of descriptorEntries) {
    artifacts[key] = await verifyDescriptor(captureRoot, descriptor, image, {
      artifactRoot,
      skipVideoDecode: options.skipVideoDecode === true,
    });
  }
  if (artifacts.video.durationMs !== undefined) {
    artifacts.video = {
      ...artifacts.video,
      ...videoCropDurationEvidence(
        artifacts.video.durationMs,
        observation.timing.videoCrop.durationMs,
      ),
    };
  }
  return Object.freeze({
    artifactRoot,
    observation,
    observationDescriptor: {
      path: path
        .relative(artifactRoot, observationPath)
        .split(path.sep)
        .join("/"),
      bytes: observationBytes.length,
      sha256: sha256(observationBytes),
    },
    artifacts,
  });
}

async function buildManifestInternal(artifactRoot, provenance, options = {}) {
  const fixtureCatalog = await loadFixtureCatalog();
  const reports = {};
  const interruptionBySuite = {};
  let latency;
  let environment;
  for (const suite of SUITES) {
    const reportPath = path.join(artifactRoot, suite, "report.json");
    const bytes = await readRegularFile(reportPath, `${suite} report`);
    const validated = validateReport(
      parseJson(bytes, `${suite} report`),
      suite,
    );
    exact(validated.source, provenance, `${suite} report source`);
    if (environment)
      exact(validated.environment, environment, `${suite} report environment`);
    else environment = validated.environment;
    reports[suite] = {
      path: `${suite}/report.json`,
      bytes: bytes.length,
      sha256: sha256(bytes),
      testCount: validated.testCount,
    };
    if (validated.interruption) {
      interruptionBySuite[suite] = validated.interruption;
    }
    if (validated.latency) latency = validated.latency;
  }
  const interruption = combineInterruptionEvidence(
    interruptionBySuite.accelerated,
    interruptionBySuite["product-smoke"],
  );
  if (!latency) fail("reports", "must include provider-free latency evidence");
  const latencyMatches = fixtureCatalog.flatMap((entry) =>
    entry.summary.programs
      .filter(
        (program) =>
          latency.checkpointIds.length === program.checkpointIds.length + 1 &&
          latency.checkpointIds.every(
            (checkpointId, index) =>
              checkpointId ===
              [entry.summary.anchorCheckpointId, ...program.checkpointIds][
                index
              ],
          ),
      )
      .map((program) => ({
        fixtureId: entry.summary.fixtureId,
        programId: program.programId,
      })),
  );
  exact(latencyMatches.length, 1, "provider-free latency fixture match count");
  const latencyEvidence = {
    ...latency,
    ...latencyMatches[0],
  };
  exact(environment.nodeVersion, process.version, "finalizer Node.js version");
  exact(environment.platform, process.platform, "finalizer platform");
  exact(environment.arch, process.arch, "finalizer architecture");
  const capture = await validateCaptureBundle(artifactRoot, options);
  exact(
    latencyEvidence.fixtureId,
    capture.observation.selectedFixtureId,
    "provider-free latency fixture",
  );
  const captureFixture = fixtureCatalog.find(
    (entry) =>
      entry.summary.fixtureId === capture.observation.selectedFixtureId,
  );
  const captureContinuation = captureFixture?.raw.continuations.find(
    (lane) => lane.scenarioId === capture.observation.selectedProgramId,
  );
  if (!captureContinuation?.fromProgramId) {
    fail("capture", "must continue from a fixture program");
  }
  exact(
    latencyEvidence.programId,
    captureContinuation.fromProgramId,
    "provider-free latency base program",
  );
  const inventory = await artifactInventory(artifactRoot);
  const runtimeSources = await runtimeSourceEvidence();
  const sealedPriorFixtures = await sealedPriorFixtureEvidence();
  const packageLock = runtimeSources.files.find(
    (entry) => entry.path === PACKAGE_LOCK_RELATIVE_PATH,
  );
  const generator = runtimeSources.files.find(
    (entry) => entry.path === GENERATOR_RELATIVE_PATH,
  );
  if (!packageLock || !generator)
    fail("runtime sources", "must bind lockfile and fixture generator");
  const fixtureSummaries = fixtureCatalog.map((entry) => entry.summary);
  return {
    schemaVersion: 1,
    gate: GATE,
    protocol: PROTOCOL,
    source: {
      ...provenance,
      packageLock,
      fixtureGenerator: generator,
      runtimeSources,
      sealedPriorFixtures,
    },
    fixtures: {
      catalogSha256: sha256(Buffer.from(canonicalJson(fixtureSummaries))),
      catalog: fixtureSummaries,
      selectedFixtureId: capture.observation.selectedFixtureId,
      selectedProgramId: capture.observation.selectedProgramId,
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
    },
    evidence: {
      reports,
      interruption,
      latency: latencyEvidence,
      capture: {
        observation: capture.observationDescriptor,
        checkpointIds: capture.observation.expectedCheckpointIds,
        timing: capture.observation.timing,
        artifacts: capture.artifacts,
      },
    },
    artifacts: inventory,
  };
}

export async function buildManifest(candidate = resolveArtifactRoot()) {
  const artifactRoot = await existingRoot(candidate);
  return buildManifestInternal(artifactRoot, gitProvenance());
}

export async function buildManifestForTests(
  candidate,
  provenance,
  options = {},
) {
  const artifactRoot = await existingRoot(candidate);
  return buildManifestInternal(
    artifactRoot,
    validateSource(provenance, "test provenance"),
    {
      ...options,
      skipVideoDecode: true,
    },
  );
}

async function atomicWrite(destination, bytes) {
  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${process.pid}.${Date.now()}.tmp`,
  );
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rejectSymlink(destination, "manifest destination");
    await rename(temporary, destination);
  } finally {
    if (handle) await handle.close().catch(() => undefined);
    await rm(temporary, { force: true });
  }
}

async function writeManifestInternal(candidate, provenance, options = {}) {
  const artifactRoot = await existingRoot(candidate);
  const lockPath = path.join(artifactRoot, ".manifest-finalize.lock");
  let lock;
  try {
    try {
      lock = await open(lockPath, "wx", 0o600);
    } catch (error) {
      if (error?.code === "EEXIST")
        fail("manifest", "another finalizer holds the lock");
      throw error;
    }
    const manifest = await buildManifestInternal(
      artifactRoot,
      provenance,
      options,
    );
    const bytes = Buffer.from(canonicalJson(manifest));
    const digest = sha256(bytes);
    await atomicWrite(path.join(artifactRoot, MANIFEST_NAME), bytes);
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

export async function writeManifest(candidate = resolveArtifactRoot()) {
  return writeManifestInternal(candidate, gitProvenance());
}

export async function writeManifestForTests(
  candidate,
  provenance,
  options = {},
) {
  return writeManifestInternal(
    candidate,
    validateSource(provenance, "test provenance"),
    {
      ...options,
      skipVideoDecode: true,
    },
  );
}

async function validateManifestInternal(candidate, provenance, options = {}) {
  const artifactRoot = await existingRoot(candidate);
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
  const expected = await buildManifestInternal(
    artifactRoot,
    provenance,
    options,
  );
  exact(parseJson(manifestBytes, "manifest"), expected, "manifest content");
  return { artifactRoot, manifest: expected, digest };
}

export async function validateManifest(candidate = resolveArtifactRoot()) {
  return validateManifestInternal(candidate, gitProvenance());
}

export async function validateManifestForTests(
  candidate,
  provenance,
  options = {},
) {
  return validateManifestInternal(
    candidate,
    validateSource(provenance, "test provenance"),
    {
      ...options,
      skipVideoDecode: true,
    },
  );
}

export async function fixtureCatalogForTests() {
  return (await loadFixtureCatalog()).map((entry) => entry.summary);
}

export async function sealedPriorFixtureEvidenceForTests() {
  return sealedPriorFixtureEvidence();
}

export async function artifactInventoryForTests(candidate) {
  return artifactInventory(await existingRoot(candidate));
}

export function validateVideoCropDurationForTests(
  durationMs,
  requestedDurationMs,
) {
  return videoCropDurationEvidence(durationMs, requestedDurationMs);
}

export function validatePngForTests(bytes) {
  return pngDimensions(bytes, "test PNG");
}

export async function validateWebmForTests(candidate) {
  const bytes = await readRegularFile(
    candidate,
    "test WebM",
    MAX_ARTIFACT_BYTES,
  );
  return decodeWebm(candidate, bytes, "test WebM", false);
}

export async function pathExistsForTests(candidate) {
  try {
    await access(candidate, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}
