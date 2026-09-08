import assert from "node:assert/strict";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  EvidenceError,
  REPOSITORY_ROOT,
  WEB_ROOT,
  atomicWriteForTests,
  assertSingleWebmArtifact,
  assertArtifactRootBoundaryForTests,
  buildManifest,
  deriveFixtureEvidence,
  deriveRuntimeEvidence,
  inspectRelevantGitStatusForTests,
  nearestRankP95,
  prepareArtifactRoot,
  prepareArtifactRootForTests,
  resolveArtifactRoot,
  sha256,
  validateGithubShaForTests,
  validateExpectedShaForTests,
  validateManifest,
  validateProviderFreeRequestsForTests,
  validateRealTimePacingForTests,
  validateRuntimeEvidenceForTests,
  verifyEvidenceProvenance,
  writeManifest,
} from "./live-choreography-artifacts-lib.mjs";

const GIT_COMMIT = "1".repeat(40);
const GIT_TREE = "2".repeat(40);
const SOURCE = Object.freeze({ gitCommit: GIT_COMMIT, gitTree: GIT_TREE });
const ENVIRONMENT = Object.freeze({
  nodeVersion: process.version,
  platform: process.platform,
  arch: process.arch,
  playwrightVersion: "1.58.2",
  browserName: "chromium",
  browserVersion: "141.0.7390.37",
});
const TRACKED_NEXT_ENV = `/// <reference types="next" />
/// <reference types="next/image-types/global" />
import "./.next/types/routes.d.ts";
import "./.next/types/root-params.d.ts";

// NOTE: This file should not be edited
// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.
`;
const DEV_NEXT_ENV = TRACKED_NEXT_ENV.replaceAll(
  "./.next/types/",
  "./.next/dev/types/",
);

async function checkedInLesson() {
  const fixture = JSON.parse(
    await readFile(
      path.join(
        WEB_ROOT,
        "src/features/live-scene/fixtures/completing-the-square.v1.json",
      ),
      "utf8",
    ),
  );
  return deriveFixtureEvidence(fixture);
}

test("derives the exact checked-in lesson and adaptive checkpoint", async () => {
  const lesson = await checkedInLesson();

  assert.equal(lesson.authoredDurationMs, 63_800);
  assert.deepEqual(
    lesson.checkpoints.map((checkpoint) => checkpoint.checkpointId),
    [
      "problem",
      "area_model",
      "split_linear_term",
      "rearrange_halves",
      "missing_corner",
      "balance_and_complete",
      "factor_square",
      "solve_roots",
    ],
  );
  assert.deepEqual(lesson.adaptive.cornerDetail.nodeIds, [
    "square-lesson__corner_calc",
    "square-lesson__corner_dim_h",
    "square-lesson__corner_dim_v",
  ]);
});

test("rejects either kind of provider-free request evidence", () => {
  assert.deepEqual(
    validateProviderFreeRequestsForTests({
      liveSceneRequests: [],
      unexpectedRequests: [],
    }),
    { liveSceneRequests: [], unexpectedRequests: [] },
  );
  assert.throws(
    () =>
      validateProviderFreeRequestsForTests({
        liveSceneRequests: [],
        unexpectedRequests: ["https://telemetry.invalid/collect"],
      }),
    /provider-free requests\.unexpectedRequests: must be empty/,
  );
  assert.throws(
    () =>
      validateProviderFreeRequestsForTests({
        liveSceneRequests: ["\/api\/live-scenes"],
        unexpectedRequests: [],
      }),
    /provider-free requests\.liveSceneRequests: must be empty/,
  );
});

test("runtime evidence must equal the exact checkpoint-derived event sequence", async () => {
  const lesson = await checkedInLesson();
  const expected = deriveRuntimeEvidence(lesson);

  assert.equal(expected.length, 40);
  assert.deepEqual(validateRuntimeEvidenceForTests(expected, lesson), expected);

  const swappedCues = expected.map((event) => ({ ...event }));
  [swappedCues[0].cue, swappedCues[1].cue] = [
    swappedCues[1].cue,
    swappedCues[0].cue,
  ];
  assert.throws(
    () => validateRuntimeEvidenceForTests(swappedCues, lesson),
    /runtime evidence\[0\]\.cue/,
  );

  const wrongCertificate = expected.map((event) => ({ ...event }));
  wrongCertificate[8].certificateSha256 = "f".repeat(64);
  assert.throws(
    () => validateRuntimeEvidenceForTests(wrongCertificate, lesson),
    /runtime evidence\[8\]\.certificateSha256/,
  );

  assert.throws(
    () => validateRuntimeEvidenceForTests(expected.slice(0, -1), lesson),
    /must contain exactly 40 entries/,
  );
});

test("computes SHA-256 and the strict twenty-sample nearest-rank p95", () => {
  assert.equal(
    sha256(Buffer.from("murmur")),
    "6200f53485b683973d0c8cb0da433414326ca268363546ece184689555b06568",
  );
  assert.equal(
    nearestRankP95(Array.from({ length: 20 }, (_, index) => index)),
    18,
  );
  assert.throws(
    () => nearestRankP95([1, 2, 3]),
    (error) =>
      error instanceof EvidenceError &&
      /exactly 20 entries/.test(error.message),
  );
});

test("real-time pacing accepts only the closed phase path and authored timing", async () => {
  const lesson = await checkedInLesson();
  const firstVisibleAtMs = 500;
  let settledAtMs = firstVisibleAtMs;
  const settlements = lesson.checkpoints.map((checkpoint, index) => {
    settledAtMs += checkpoint.phase.durationMs + checkpoint.phase.holdAfterMs;
    return {
      ordinal: index + 1,
      checkpointId: checkpoint.checkpointId,
      phase:
        index === lesson.checkpoints.length - 1 ? "completed" : "completing",
      atMs: settledAtMs,
    };
  });
  const phaseTransitions = [
    { phase: "idle", checkpointId: "none", settledMainCount: 0, atMs: 10 },
    {
      phase: "connecting",
      checkpointId: "none",
      settledMainCount: 0,
      atMs: 100,
    },
    {
      phase: "streaming",
      checkpointId: "none",
      settledMainCount: 0,
      atMs: 200,
    },
    {
      phase: "completing",
      checkpointId: "none",
      settledMainCount: 0,
      atMs: 300,
    },
    ...settlements.map((settlement) => ({
      phase: settlement.phase,
      checkpointId: settlement.checkpointId,
      settledMainCount: settlement.ordinal,
      atMs: settlement.atMs,
    })),
  ];
  const evidence = {
    firstVisibleAtMs,
    completedAtMs: settlements.at(-1).atMs,
    settlements,
    phaseTransitions,
  };

  const timing = validateRealTimePacingForTests(evidence, lesson);
  assert.deepEqual(
    timing.map(({ checkpointId, unexplainedMs }) => ({
      checkpointId,
      unexplainedMs,
    })),
    lesson.checkpoints.map(({ checkpointId }) => ({
      checkpointId,
      unexplainedMs: 0,
    })),
  );

  assert.throws(
    () =>
      validateRealTimePacingForTests(
        {
          ...evidence,
          phaseTransitions: phaseTransitions.with(2, {
            ...phaseTransitions[2],
            phase: "thinking",
          }),
        },
        lesson,
      ),
    /phaseTransitions\[2\]\.phase/,
  );

  assert.throws(
    () =>
      validateRealTimePacingForTests(
        {
          ...evidence,
          phaseTransitions: phaseTransitions.with(2, {
            ...phaseTransitions[2],
            atMs: phaseTransitions[1].atMs,
          }),
        },
        lesson,
      ),
    /phaseTransitions\[2\]\.atMs: must increase strictly/,
  );

  const delayedSettlements = settlements.map((settlement, index) => ({
    ...settlement,
    atMs: settlement.atMs + (index >= 2 ? 1_201 : 0),
  }));
  assert.throws(
    () =>
      validateRealTimePacingForTests(
        {
          ...evidence,
          completedAtMs: delayedSettlements.at(-1).atMs,
          settlements: delayedSettlements,
          phaseTransitions: phaseTransitions.map((transition, index) =>
            index >= 6
              ? { ...transition, atMs: transition.atMs + 1_201 }
              : transition,
          ),
        },
        lesson,
      ),
    /unexplained 1201\.0 ms gap/,
  );

  const earlySettlements = settlements.map((settlement, index) => ({
    ...settlement,
    atMs: settlement.atMs - (index >= 4 ? 251 : 0),
  }));
  assert.throws(
    () =>
      validateRealTimePacingForTests(
        {
          ...evidence,
          completedAtMs: earlySettlements.at(-1).atMs,
          settlements: earlySettlements,
          phaseTransitions: phaseTransitions.map((transition, index) =>
            index >= 8
              ? { ...transition, atMs: transition.atMs - 251 }
              : transition,
          ),
        },
        lesson,
      ),
    /settles 251\.0 ms before its authored motion and hold/,
  );
});

test("prepare production entry point refuses a non-canonical artifact root", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-artifacts-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const artifactRoot = path.join(temporaryRoot, "live-choreography");
  const sibling = path.join(temporaryRoot, "preserve.txt");
  await writeFile(sibling, "safe");

  await assert.rejects(
    prepareArtifactRoot(artifactRoot),
    /prepare may remove only/,
  );

  assert.equal(await readFile(sibling, "utf8"), "safe");
  assert.equal(resolveArtifactRoot(artifactRoot), artifactRoot);
  assert.throws(
    () => resolveArtifactRoot(path.join(temporaryRoot, "wrong-name")),
    /exact directory name/,
  );
});

test("test-only prepare removes only its exact temporary artifact root", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-artifacts-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const artifactRoot = path.join(temporaryRoot, "live-choreography");
  const sibling = path.join(temporaryRoot, "preserve.txt");
  await prepareArtifactRootForTests(artifactRoot);
  await writeFile(path.join(artifactRoot, "old.txt"), "old");
  await writeFile(sibling, "safe");

  await prepareArtifactRootForTests(artifactRoot);

  assert.equal(await readFile(sibling, "utf8"), "safe");
  await assert.rejects(readFile(path.join(artifactRoot, "old.txt")), {
    code: "ENOENT",
  });
});

test("test-only prepare refuses a symlinked artifact root", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-artifacts-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const target = path.join(temporaryRoot, "target");
  const artifactRoot = path.join(temporaryRoot, "live-choreography");
  await prepareArtifactRootForTests(path.join(target, "live-choreography"));
  await writeFile(path.join(target, "live-choreography", "sentinel"), "safe");
  await symlink(path.join(target, "live-choreography"), artifactRoot);

  await assert.rejects(
    prepareArtifactRootForTests(artifactRoot),
    /must not be a symbolic link/,
  );
  assert.equal(
    await readFile(path.join(target, "live-choreography", "sentinel"), "utf8"),
    "safe",
  );
});

test("build, write, and validate reject a symlinked artifact root", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-artifacts-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const target = path.join(temporaryRoot, "target", "live-choreography");
  const linkedRoot = path.join(temporaryRoot, "linked", "live-choreography");
  await mkdir(target, { recursive: true });
  await mkdir(path.dirname(linkedRoot), { recursive: true });
  await symlink(target, linkedRoot);

  for (const operation of [buildManifest, writeManifest, validateManifest]) {
    await assert.rejects(operation(linkedRoot), /must not be a symbolic link/);
  }
});

test("artifact roots may not escape their lexical boundary after realpath", async () => {
  await assert.rejects(
    assertArtifactRootBoundaryForTests(
      path.join(tmpdir(), "murmur-artifacts", "live-choreography"),
      path.join(REPOSITORY_ROOT, "var", "live-choreography"),
    ),
    /escapes its temporary boundary after realpath/,
  );
});

test("atomic writes skip precreated symlinks and create a fresh exclusive temp", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-atomic-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const destination = path.join(temporaryRoot, "manifest.json");
  const sentinel = path.join(temporaryRoot, "sentinel.txt");
  const collision = path.join(
    temporaryRoot,
    ".manifest.json.collision_token.tmp",
  );
  await writeFile(sentinel, "safe");
  await symlink(sentinel, collision);

  await atomicWriteForTests(destination, Buffer.from("fresh"), [
    "collision_token",
    "exclusive_token",
  ]);

  assert.equal(await readFile(destination, "utf8"), "fresh");
  assert.equal(await readFile(sentinel, "utf8"), "safe");
  assert.deepEqual(
    (await readdir(temporaryRoot)).filter((name) => name.endsWith(".tmp")),
    [".manifest.json.collision_token.tmp"],
  );
});

test("atomic writes clean their exclusively-created temp after rename failure", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-atomic-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const destination = path.join(temporaryRoot, "manifest.json");
  await mkdir(destination);

  await assert.rejects(
    atomicWriteForTests(destination, Buffer.from("fresh"), ["cleanup_token"]),
  );
  assert.deepEqual(await readdir(temporaryRoot), ["manifest.json"]);
});

test("atomic writes reject an existing destination symlink", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-atomic-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const destination = path.join(temporaryRoot, "manifest.json");
  const sentinel = path.join(temporaryRoot, "sentinel.txt");
  await writeFile(sentinel, "safe");
  await symlink(sentinel, destination);

  await assert.rejects(
    atomicWriteForTests(destination, Buffer.from("fresh"), ["exclusive_token"]),
    /atomic destination.*must not be a symbolic link/,
  );
  assert.equal(await readFile(sentinel, "utf8"), "safe");
  assert.deepEqual((await readdir(temporaryRoot)).sort(), [
    "manifest.json",
    "sentinel.txt",
  ]);
});

test("evidence contains exactly one canonical WebM", async (t) => {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "murmur-artifacts-"));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const artifactRoot = path.join(temporaryRoot, "live-choreography");
  await prepareArtifactRootForTests(artifactRoot);
  const canonicalVideo = path.join(
    artifactRoot,
    "capture",
    "live-choreography.webm",
  );
  await writeFile(canonicalVideo, "canonical");

  await assert.doesNotReject(assertSingleWebmArtifact(artifactRoot));

  const rawVideoRoot = path.join(artifactRoot, "capture", "test-results");
  await mkdir(rawVideoRoot, { recursive: true });
  await writeFile(path.join(rawVideoRoot, "raw-video.webm"), "duplicate");
  await assert.rejects(
    assertSingleWebmArtifact(artifactRoot),
    /WebM artifacts/,
  );
});

test("Git guard ignores only the exact Next dev-generated import drift", () => {
  assert.equal(
    inspectRelevantGitStatusForTests(
      " M web/next-env.d.ts",
      TRACKED_NEXT_ENV,
      DEV_NEXT_ENV,
    ),
    "",
  );
  assert.equal(
    inspectRelevantGitStatusForTests(
      " M web/next-env.d.ts\n M web/src/components/svg-canvas.tsx",
      TRACKED_NEXT_ENV,
      DEV_NEXT_ENV,
    ),
    " M web/src/components/svg-canvas.tsx",
  );
  assert.equal(
    inspectRelevantGitStatusForTests(
      " M web/next-env.d.ts",
      TRACKED_NEXT_ENV,
      `${DEV_NEXT_ENV}// unrelated edit\n`,
    ),
    " M web/next-env.d.ts",
  );
  assert.equal(
    inspectRelevantGitStatusForTests(
      "M  web/next-env.d.ts",
      TRACKED_NEXT_ENV,
      DEV_NEXT_ENV,
    ),
    "M  web/next-env.d.ts",
  );
});

test("evidence provenance must match every observation, report, and finalize runtime", () => {
  const evidenceSources = [
    ["accelerated observations.source", SOURCE],
    ["capture observations.source", SOURCE],
    ["accelerated report source", SOURCE],
    ["capture report source", SOURCE],
  ];
  const evidenceEnvironments = [
    ["accelerated observations.environment", ENVIRONMENT],
    ["capture observations.environment", ENVIRONMENT],
    ["accelerated report environment", ENVIRONMENT],
    ["capture report environment", ENVIRONMENT],
  ];

  assert.deepEqual(
    verifyEvidenceProvenance(evidenceSources, SOURCE, evidenceEnvironments),
    { source: SOURCE, environment: ENVIRONMENT },
  );
  assert.throws(
    () =>
      verifyEvidenceProvenance(
        evidenceSources.with(1, [
          "capture observations.source",
          { ...SOURCE, gitCommit: "3".repeat(40) },
        ]),
        SOURCE,
        evidenceEnvironments,
      ),
    /capture observations\.source/,
  );
  assert.throws(
    () =>
      verifyEvidenceProvenance(
        evidenceSources,
        { ...SOURCE, gitTree: "not-a-git-object" },
        evidenceEnvironments,
      ),
    /lowercase 40-character Git object ID/,
  );
  assert.throws(
    () =>
      verifyEvidenceProvenance(
        evidenceSources,
        SOURCE,
        evidenceEnvironments.with(3, [
          "capture report environment",
          { ...ENVIRONMENT, browserVersion: "140.0.0.0" },
        ]),
      ),
    /capture report environment/,
  );
  assert.throws(
    () =>
      verifyEvidenceProvenance(evidenceSources, SOURCE, evidenceEnvironments, {
        nodeVersion: "v0.0.0",
        platform: process.platform,
        arch: process.arch,
      }),
    /environment\.nodeVersion at finalize/,
  );
});

test("optional GITHUB_SHA must be a full object ID equal to HEAD", () => {
  assert.doesNotThrow(() => validateGithubShaForTests(GIT_COMMIT, undefined));
  assert.doesNotThrow(() => validateGithubShaForTests(GIT_COMMIT, GIT_COMMIT));
  assert.throws(
    () => validateGithubShaForTests(GIT_COMMIT, "1".repeat(39)),
    /lowercase 40-character Git object ID/,
  );
  assert.throws(
    () => validateGithubShaForTests(GIT_COMMIT, "3".repeat(40)),
    /GITHUB_SHA/,
  );
});

test("an explicit choreography SHA uses its own fail-closed provenance label", () => {
  assert.doesNotThrow(() =>
    validateExpectedShaForTests(
      GIT_COMMIT,
      GIT_COMMIT,
      "CHOREOGRAPHY_EXPECTED_SHA",
    ),
  );
  assert.throws(
    () =>
      validateExpectedShaForTests(
        GIT_COMMIT,
        "3".repeat(40),
        "CHOREOGRAPHY_EXPECTED_SHA",
      ),
    /CHOREOGRAPHY_EXPECTED_SHA/,
  );
});
