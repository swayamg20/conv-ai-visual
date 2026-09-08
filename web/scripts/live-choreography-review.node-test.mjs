import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  RELEVANT_IMPLEMENTATION_PATHS,
  REVIEW_CATEGORIES,
  REVIEW_RECORD_RELATIVE_PATH,
  ReviewValidationError,
  validateLiveChoreographyReview,
  validateReviewRecord,
} from "./live-choreography-review-lib.mjs";

const MANIFEST_DIGEST = "a".repeat(64);
const PLACEHOLDER_SHA = "1".repeat(40);

function reviewer(name, role, score = 8, scoreOverrides = {}) {
  return {
    name,
    role,
    scores: Object.fromEntries(
      REVIEW_CATEGORIES.map((category) => [
        category,
        scoreOverrides[category] ?? score,
      ]),
    ),
    cornerNineComprehension: {
      correct: true,
      replayCount: 1,
      explanation:
        "The missing corner is a square with side length 3, so its area is 3 × 3 = 9.",
    },
  };
}

function validRecord(reviewedImplementationSha = PLACEHOLDER_SHA) {
  return {
    version: 1,
    gate: "1.5",
    reviewedImplementationSha,
    manifestDigest: MANIFEST_DIGEST,
    ci: {
      runUrl: "https://github.com/example/murmur/actions/runs/123456789",
      artifactName: "live-choreography-123456789-1",
    },
    reviewers: [
      reviewer("Product owner", "product_owner", 8),
      reviewer("Viewer one", "viewer", 9),
      reviewer("Viewer two", "viewer", 7),
    ],
    blockingFindings: [],
    decision: "pass",
  };
}

function git(repositoryRoot, arguments_) {
  return execFileSync("git", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

async function writeRelative(repositoryRoot, relativePath, contents) {
  const destination = path.join(repositoryRoot, relativePath);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, contents);
}

async function createRepository(t) {
  const repositoryRoot = await mkdtemp(
    path.join(tmpdir(), "murmur-review-repo-"),
  );
  t.after(() => rm(repositoryRoot, { recursive: true, force: true }));
  git(repositoryRoot, ["init", "-b", "main"]);
  git(repositoryRoot, ["config", "user.name", "Murmur Review Test"]);
  git(repositoryRoot, ["config", "user.email", "review@example.test"]);
  await writeRelative(
    repositoryRoot,
    "backend/murmur/runtime.py",
    "GATE = '1.5'\n",
  );
  await writeRelative(
    repositoryRoot,
    "scripts/generate_live_choreography_fixture.py",
    "print('fixture')\n",
  );
  await writeRelative(
    repositoryRoot,
    "web/src/runtime.ts",
    "export const gate = '1.5';\n",
  );
  await writeRelative(
    repositoryRoot,
    "web/scripts/artifact.mjs",
    "export const artifact = true;\n",
  );
  await writeRelative(repositoryRoot, ".github/workflows/ci.yml", "name: CI\n");
  git(repositoryRoot, ["add", "."]);
  git(repositoryRoot, ["commit", "-m", "feat: add reviewed implementation"]);
  return {
    repositoryRoot,
    reviewedSha: git(repositoryRoot, ["rev-parse", "HEAD"]),
  };
}

async function commitReviewRecord(repositoryRoot, record) {
  await writeRelative(
    repositoryRoot,
    REVIEW_RECORD_RELATIVE_PATH,
    `${JSON.stringify(record, null, 2)}\n`,
  );
  git(repositoryRoot, ["add", REVIEW_RECORD_RELATIVE_PATH]);
  git(repositoryRoot, ["commit", "-m", "docs: record choreography review"]);
}

test("accepts the exact review schema and computes its rubric", () => {
  const validated = validateReviewRecord(validRecord());
  assert.equal(validated.metrics.aggregateMean, 8);
  assert.deepEqual(
    validated.metrics.categoryMedians,
    Object.fromEntries(REVIEW_CATEGORIES.map((category) => [category, 8])),
  );
  assert.equal(validated.record.reviewers.length, 3);
});

test("rejects unknown fields, incomplete scores, and invalid identities", () => {
  assert.throws(
    () => validateReviewRecord({ ...validRecord(), generatedSummary: "pass" }),
    /must contain exactly keys/,
  );
  const missingScore = validRecord();
  delete missingScore.reviewers[0].scores.motionCraft;
  assert.throws(() => validateReviewRecord(missingScore), /motionCraft/);
  assert.throws(
    () =>
      validateReviewRecord({
        ...validRecord(),
        reviewedImplementationSha: "1".repeat(39),
      }),
    /40-character Git SHA/,
  );
  assert.throws(
    () =>
      validateReviewRecord({
        ...validRecord(),
        manifestDigest: "A".repeat(64),
      }),
    /64-character SHA-256/,
  );
});

test("rejects an aggregate mean below 8.0", () => {
  const record = validRecord();
  record.reviewers = [
    reviewer("Product owner", "product_owner", 7),
    reviewer("Viewer one", "viewer", 7),
    reviewer("Viewer two", "viewer", 7),
  ];
  assert.throws(() => validateReviewRecord(record), /aggregate mean/);
});

test("rejects any category median below 7.0 even when the mean passes", () => {
  const record = validRecord();
  record.reviewers = [
    reviewer("Product owner", "product_owner", 10, { conceptualClarity: 1 }),
    reviewer("Viewer one", "viewer", 10, { conceptualClarity: 6 }),
    reviewer("Viewer two", "viewer", 10, { conceptualClarity: 6 }),
  ];
  assert.throws(
    () => validateReviewRecord(record),
    /category median conceptualClarity/,
  );
});

test("requires one product owner, two viewers, and distinct people", () => {
  const noOwner = validRecord();
  noOwner.reviewers[0].role = "viewer";
  assert.throws(() => validateReviewRecord(noOwner), /product_owner count/);

  const duplicate = validRecord();
  duplicate.reviewers[2].name = "viewer ONE";
  assert.throws(
    () => validateReviewRecord(duplicate),
    /distinct reviewer names/,
  );
});

test("requires demonstrated corner comprehension within one replay", () => {
  const tooManyReplays = validRecord();
  tooManyReplays.reviewers[1].cornerNineComprehension.replayCount = 2;
  assert.throws(() => validateReviewRecord(tooManyReplays), /replayCount/);

  const unanswered = validRecord();
  unanswered.reviewers[1].cornerNineComprehension.explanation = "";
  assert.throws(() => validateReviewRecord(unanswered), /explanation/);

  const incorrectExplanation = validRecord();
  incorrectExplanation.reviewers[1].cornerNineComprehension.explanation =
    "The missing corner looks correct.";
  assert.throws(() => validateReviewRecord(incorrectExplanation), /3 × 3 = 9/);
});

test("requires a durable run URL, zero blockers, and a pass decision", () => {
  const branchUrl = validRecord();
  branchUrl.ci.runUrl = "https://github.com/example/murmur/actions?query=main";
  assert.throws(() => validateReviewRecord(branchUrl), /durable/);

  const foreignArtifact = validRecord();
  foreignArtifact.ci.artifactName = "live-choreography-987654321-1";
  assert.throws(
    () => validateReviewRecord(foreignArtifact),
    /from run 123456789/,
  );

  const attemptMismatch = validRecord();
  attemptMismatch.ci.runUrl =
    "https://github.com/example/murmur/actions/runs/123456789/attempts/2";
  assert.throws(() => validateReviewRecord(attemptMismatch), /does not match/);

  const blocked = validRecord();
  blocked.blockingFindings = ["clipped label"];
  assert.throws(() => validateReviewRecord(blocked), /must be empty/);

  const failed = validRecord();
  failed.decision = "fail";
  assert.throws(() => validateReviewRecord(failed), /must equal "pass"/);
});

test("an absent optional record reports not yet recorded without Git", async (t) => {
  const repositoryRoot = await mkdtemp(
    path.join(tmpdir(), "murmur-no-review-"),
  );
  t.after(() => rm(repositoryRoot, { recursive: true, force: true }));
  const result = await validateLiveChoreographyReview({ repositoryRoot });
  assert.equal(result.status, "not_recorded");
  assert.equal(
    result.recordPath,
    path.join(repositoryRoot, REVIEW_RECORD_RELATIVE_PATH),
  );
});

test("allows a docs-only review-note commit after the reviewed SHA", async (t) => {
  const { repositoryRoot, reviewedSha } = await createRepository(t);
  await commitReviewRecord(repositoryRoot, validRecord(reviewedSha));

  const result = await validateLiveChoreographyReview({ repositoryRoot });
  assert.equal(result.status, "valid");
  assert.equal(result.record.reviewedImplementationSha, reviewedSha);
});

test("rejects implementation drift after the reviewed SHA", async (t) => {
  const { repositoryRoot, reviewedSha } = await createRepository(t);
  await commitReviewRecord(repositoryRoot, validRecord(reviewedSha));
  await writeRelative(
    repositoryRoot,
    "web/src/runtime.ts",
    "export const gate = 'changed after review';\n",
  );
  git(repositoryRoot, ["add", "web/src/runtime.ts"]);
  git(repositoryRoot, ["commit", "-m", "feat: drift after review"]);

  await assert.rejects(
    validateLiveChoreographyReview({ repositoryRoot }),
    (error) =>
      error instanceof ReviewValidationError &&
      /review implementation drift/.test(error.message) &&
      /web\/src\/runtime\.ts/.test(error.message),
  );
});

test("rejects a reviewed SHA that is not an ancestor of HEAD", async (t) => {
  const { repositoryRoot } = await createRepository(t);
  git(repositoryRoot, ["checkout", "-b", "unreviewed-branch"]);
  await writeRelative(repositoryRoot, "branch-only.txt", "branch\n");
  git(repositoryRoot, ["add", "branch-only.txt"]);
  git(repositoryRoot, ["commit", "-m", "test: create sibling commit"]);
  const siblingSha = git(repositoryRoot, ["rev-parse", "HEAD"]);
  git(repositoryRoot, ["checkout", "main"]);
  await commitReviewRecord(repositoryRoot, validRecord(siblingSha));

  await assert.rejects(
    validateLiveChoreographyReview({ repositoryRoot }),
    /is not an ancestor of HEAD/,
  );
});

test("the drift scope contains every artifact-producing surface", async () => {
  for (const expected of [
    "backend/murmur",
    "scripts/generate_live_choreography_fixture.py",
    "web/src",
    "web/e2e",
    "web/scripts",
    "web/package-lock.json",
    "web/playwright.choreography.config.ts",
    ".github/workflows/ci.yml",
  ]) {
    assert.ok(RELEVANT_IMPLEMENTATION_PATHS.includes(expected), expected);
  }

  const source = await readFile(
    new URL("./live-choreography-review-lib.mjs", import.meta.url),
    "utf8",
  );
  assert.match(source, /\["diff", "--quiet", range, "--"/);
});
