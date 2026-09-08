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
  validateAdaptiveEvidenceForTests,
  validateGithubShaForTests,
  validateExpectedShaForTests,
  validateManifest,
  validateProviderFreeRequestsForTests,
  validatePlaywrightCiForTests,
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

const INTERRUPTION_CASES = Object.freeze([
  {
    category: "move",
    checkpointOrdinal: 4,
    cue: "transform",
    timing: "motion",
  },
  {
    category: "token_morph",
    checkpointOrdinal: 7,
    cue: "transform",
    timing: "motion",
  },
  {
    category: "camera_focus",
    checkpointOrdinal: 4,
    cue: "focus",
    timing: "motion",
  },
  {
    category: "emphasis",
    checkpointOrdinal: 6,
    cue: "emphasize",
    timing: "motion",
  },
  {
    category: "authored_hold",
    checkpointOrdinal: 4,
    cue: "hold",
    timing: "hold",
  },
]);

function domIdentity(lesson, offset) {
  return new Map(
    [
      ...new Set(
        lesson.checkpoints.flatMap((checkpoint) => checkpoint.nodeIds),
      ),
    ].map((id, index) => [id, offset + index + 1]),
  );
}

function settledCheckpoint(checkpoint, identities) {
  return {
    ordinal: checkpoint.ordinal,
    checkpointId: checkpoint.checkpointId,
    caption: checkpoint.caption,
    viewBox: checkpoint.viewports.cinematic.result,
    nodeIds: [...checkpoint.nodeIds],
    domIdentity: Object.fromEntries(
      checkpoint.nodeIds.map((id) => [id, identities.get(id)]),
    ),
    rendererTrusted: true,
    transientResidueCount: 0,
  };
}

function viewport(viewBox) {
  const [x, y, width, height] = viewBox.split(" ").map(Number);
  return { v: 1, x, y, width, height };
}

function interruptionEvidence(runtimeEvidence, checkpoint) {
  return runtimeEvidence
    .filter((event) => event.sequence <= checkpoint.sequence)
    .map((event) =>
      event.sequence === checkpoint.sequence &&
      event.type === "checkpointSettled"
        ? { ...event, settlement: "cancelled_to_checkpoint" }
        : { ...event },
    );
}

function adaptiveEvidence(lesson) {
  const runtimeEvidence = deriveRuntimeEvidence(lesson);
  const liveIdentities = domIdentity(lesson, 0);
  const replayIdentities = domIdentity(lesson, 10_000);
  const liveCheckpoints = lesson.checkpoints.map((checkpoint) =>
    settledCheckpoint(checkpoint, liveIdentities),
  );
  const interruptionCases = INTERRUPTION_CASES.flatMap((definition) =>
    Array.from({ length: 4 }, (_, repeat) => {
      const checkpoint = lesson.checkpoints[definition.checkpointOrdinal - 1];
      const target = settledCheckpoint(checkpoint, liveIdentities);
      const evidenceAfter = interruptionEvidence(runtimeEvidence, checkpoint);
      const stage = {
        caption: target.caption,
        viewBox: target.viewBox,
        nodeIds: [...target.nodeIds],
        domIdentity: { ...target.domIdentity },
        canonicalSvg: `<svg data-checkpoint-id="${checkpoint.checkpointId}" />`,
      };
      const stability = {
        stage,
        frontier: {
          phase: "interrupted",
          checkpointId: checkpoint.checkpointId,
          settledMainCount: checkpoint.ordinal,
          rendererTrusted: true,
          waitingFor: null,
        },
        evidence: structuredClone(evidenceAfter),
      };
      const settleMs = repeat + 1 + INTERRUPTION_CASES.indexOf(definition) * 4;
      const requestedAtMs = 1_000 + repeat;
      return {
        label: `${definition.category}-${repeat + 1}`,
        category: definition.category,
        checkpointId: checkpoint.checkpointId,
        sequence: checkpoint.sequence,
        cue: definition.cue,
        cueTargetIds:
          definition.cue === "hold"
            ? []
            : [
                ...checkpoint.phase.cues.find(
                  ({ cue }) => cue === definition.cue,
                ).targetIds,
              ],
        timing: definition.timing,
        authoredDurationMs: checkpoint.phase.durationMs,
        authoredHoldAfterMs: checkpoint.phase.holdAfterMs,
        trigger:
          definition.timing === "hold"
            ? "afterFirstCuePresentedDelay"
            : "firstCuePresented",
        delayAfterPresentedMs:
          definition.timing === "hold"
            ? Math.ceil(checkpoint.phase.durationMs / 16) + 50
            : 0,
        activeRevision: checkpoint.resultRevision,
        requestedAtMs,
        settledAtMs: requestedAtMs + settleMs,
        settleMs,
        target,
        evidenceBefore: structuredClone(evidenceAfter.slice(0, -1)),
        evidenceAfter: structuredClone(evidenceAfter),
        staleWindowMs: 2_000,
        stabilityBefore: structuredClone(stability),
        stabilityAfter: structuredClone(stability),
        staleStable: true,
      };
    }),
  );
  const replayedCheckpoints = lesson.checkpoints.map((checkpoint, index) => ({
    ordinal: checkpoint.ordinal,
    checkpointId: checkpoint.checkpointId,
    certificateSha256: checkpoint.certificateSha256,
    caption: checkpoint.caption,
    viewport: viewport(checkpoint.viewports.cinematic.result),
    nodeIds: [...checkpoint.nodeIds],
    domIdentity: Object.fromEntries(
      checkpoint.nodeIds.map((id) => [id, replayIdentities.get(id)]),
    ),
    rendererTrusted: true,
    cueTrace: structuredClone(
      runtimeEvidence.filter((event) => event.sequence === index + 1),
    ),
  }));
  const samples = interruptionCases.map(({ settleMs }) => settleMs);
  return {
    interruptionSettleMsSamples: samples,
    interruptionSettleP95Ms: nearestRankP95(samples),
    interruptionCases,
    cornerDetailNodeIds: [...lesson.adaptive.cornerDetail.nodeIds],
    replay: {
      liveCheckpoints,
      replayedCheckpoints,
      checkpointIds: lesson.checkpoints.map(({ checkpointId }) => checkpointId),
      certificateSha256s: lesson.checkpoints.map(
        ({ certificateSha256 }) => certificateSha256,
      ),
      liveEvidence: structuredClone(runtimeEvidence),
      replayEvidence: structuredClone(runtimeEvidence),
      finalCanonicalSvgMatches: true,
      equivalent: true,
    },
    replayEquivalent: true,
    liveSceneRequests: [],
    unexpectedRequests: [],
  };
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

  assert.equal(expected.length, 41);
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
    /must contain exactly 41 entries/,
  );
});

test("accepts the complete twenty-case interruption and eight-checkpoint replay proof", async () => {
  const lesson = await checkedInLesson();
  const evidence = adaptiveEvidence(lesson);

  const validated = validateAdaptiveEvidenceForTests(evidence, lesson);

  assert.equal(validated.interruptionCases.length, 20);
  assert.equal(validated.interruptionP95Ms, 19);
  assert.deepEqual(
    Object.fromEntries(
      INTERRUPTION_CASES.map(({ category }) => [
        category,
        validated.interruptionCases.filter(
          (entry) => entry.category === category,
        ).length,
      ]),
    ),
    {
      move: 4,
      token_morph: 4,
      camera_focus: 4,
      emphasis: 4,
      authored_hold: 4,
    },
  );
  assert.equal(validated.replay.liveCheckpoints.length, 8);
  assert.equal(validated.replay.replayedCheckpoints.length, 8);
  assert.equal(validated.replay.liveEvidence.length, 41);
  assert.equal(validated.replay.replayEvidence.length, 41);
});

test("rejects mutated interruption timing, settlement, stability, and target evidence", async () => {
  const lesson = await checkedInLesson();
  const mutations = [
    {
      name: "duplicate labels",
      pattern: /interruptionCases: labels must be unique/,
      mutate(value) {
        value.interruptionCases[1].label = value.interruptionCases[0].label;
      },
    },
    {
      name: "wrong cue targets",
      pattern: /interruptionCases\[0\]\.cueTargetIds/,
      mutate(value) {
        value.interruptionCases[0].cueTargetIds[0] = "wrong-node";
      },
    },
    {
      name: "non-finite request timestamp",
      pattern: /interruptionCases\[0\]\.requestedAtMs/,
      mutate(value) {
        value.interruptionCases[0].requestedAtMs = Number.NaN;
      },
    },
    {
      name: "settlement duration drift",
      pattern: /interruptionCases\[0\]\.settleMs: must equal/,
      mutate(value) {
        value.interruptionCases[0].settledAtMs += 2;
      },
    },
    {
      name: "completed instead of cancelled settlement",
      pattern: /interruptionCases\[0\]\.evidenceAfter.*settlement/,
      mutate(value) {
        value.interruptionCases[0].evidenceAfter.at(-1).settlement =
          "completed";
      },
    },
    {
      name: "stale-window stage drift",
      pattern: /interruptionCases\[0\]\.stabilityAfter\.stage\.caption/,
      mutate(value) {
        value.interruptionCases[0].stabilityAfter.stage.caption = "drifted";
      },
    },
    {
      name: "transient residue",
      pattern: /interruptionCases\[0\]\.target\.transientResidueCount/,
      mutate(value) {
        value.interruptionCases[0].target.transientResidueCount = 1;
      },
    },
    {
      name: "aggregate samples detached from cases",
      pattern: /interruptionSettleMsSamples: must equal/,
      mutate(value) {
        value.interruptionSettleMsSamples[0] += 1;
      },
    },
  ];

  for (const mutation of mutations) {
    const evidence = adaptiveEvidence(lesson);
    mutation.mutate(evidence);
    assert.throws(
      () => validateAdaptiveEvidenceForTests(evidence, lesson),
      mutation.pattern,
      mutation.name,
    );
  }
});

test("rejects replay order, certificates, cue traces, retained identity, and requests", async () => {
  const lesson = await checkedInLesson();
  const mutations = [
    {
      name: "checkpoint order drift",
      pattern: /replayedCheckpoints\[0\]\.checkpointId/,
      mutate(value) {
        value.replay.replayedCheckpoints[0].checkpointId = "area_model";
      },
    },
    {
      name: "certificate drift",
      pattern: /replayedCheckpoints\[0\]\.certificateSha256/,
      mutate(value) {
        value.replay.replayedCheckpoints[0].certificateSha256 = "f".repeat(64);
      },
    },
    {
      name: "cue trace truncation",
      pattern: /replayedCheckpoints\[0\]\.cueTrace: must contain exactly/,
      mutate(value) {
        value.replay.replayedCheckpoints[0].cueTrace.pop();
      },
    },
    {
      name: "retained DOM identity replacement",
      pattern: /replayedCheckpoints\[1\]\.domIdentity\..*: changed/,
      mutate(value) {
        const [retainedId] = value.replay.replayedCheckpoints[0].nodeIds.filter(
          (id) => value.replay.replayedCheckpoints[1].nodeIds.includes(id),
        );
        value.replay.replayedCheckpoints[1].domIdentity[retainedId] = 99_999;
      },
    },
    {
      name: "DOM identity token reassigned after node removal",
      pattern: /replayedCheckpoints\[2\]\.domIdentity\..*: reuses token/,
      mutate(value) {
        const previous = value.replay.replayedCheckpoints[1];
        const current = value.replay.replayedCheckpoints[2];
        const removedId = previous.nodeIds.find(
          (id) => !current.nodeIds.includes(id),
        );
        const addedId = current.nodeIds.find(
          (id) => !previous.nodeIds.includes(id),
        );
        current.domIdentity[addedId] = previous.domIdentity[removedId];
      },
    },
    {
      name: "replay DOM identity token reused from the live run",
      pattern: /replayedCheckpoints\[0\]\.domIdentity\..*: reuses a DOM token/,
      mutate(value) {
        const live = value.replay.liveCheckpoints[0];
        const replayed = value.replay.replayedCheckpoints[0];
        replayed.domIdentity[replayed.nodeIds[0]] =
          live.domIdentity[live.nodeIds[0]];
      },
    },
    {
      name: "replay trace truncation",
      pattern: /replayEvidence: must contain exactly 41 entries/,
      mutate(value) {
        value.replay.replayEvidence.pop();
      },
    },
    {
      name: "unexpected request",
      pattern: /unexpectedRequests: must be empty/,
      mutate(value) {
        value.unexpectedRequests.push("https://telemetry.invalid/collect");
      },
    },
  ];

  for (const mutation of mutations) {
    const evidence = adaptiveEvidence(lesson);
    mutation.mutate(evidence);
    assert.throws(
      () => validateAdaptiveEvidenceForTests(evidence, lesson),
      mutation.pattern,
      mutation.name,
    );
  }
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

test("Playwright CI metadata binds the report to one commit and repository run", () => {
  const ci = {
    commitHref: `https://github.com/swayamg20/conv-ai-visual/commit/${GIT_COMMIT}`,
    commitHash: GIT_COMMIT,
    buildHref:
      "https://github.com/swayamg20/conv-ai-visual/actions/runs/34256064840",
  };
  assert.deepEqual(validatePlaywrightCiForTests(ci, SOURCE), ci);
  assert.throws(
    () =>
      validatePlaywrightCiForTests(
        { ...ci, commitHash: "3".repeat(40) },
        SOURCE,
      ),
    /commitHash/,
  );
  assert.throws(
    () =>
      validatePlaywrightCiForTests(
        {
          ...ci,
          buildHref: "https://github.com/other/repository/actions/runs/1",
        },
        SOURCE,
      ),
    /same GitHub repository/,
  );
});
