import { spawnSync } from "node:child_process";
import { constants } from "node:fs";
import { lstat, open } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));

export const REPOSITORY_ROOT = path.resolve(SCRIPT_ROOT, "..", "..");
export const REVIEW_RECORD_RELATIVE_PATH =
  "docs/evaluations/live-choreography-gate-1.5.json";
export const REVIEW_CATEGORIES = Object.freeze([
  "conceptualClarity",
  "visualContinuity",
  "compositionFocus",
  "motionCraft",
  "pacingRhythm",
  "delightOriginality",
]);
export const RELEVANT_IMPLEMENTATION_PATHS = Object.freeze([
  "backend/murmur",
  "scripts/generate_live_choreography_fixture.py",
  "web/src",
  "web/public",
  "web/e2e",
  "web/scripts",
  "web/package.json",
  "web/package-lock.json",
  "web/next-env.d.ts",
  "web/next.config.mjs",
  "web/postcss.config.mjs",
  "web/tailwind.config.ts",
  "web/tsconfig.json",
  "web/playwright.choreography.config.ts",
  ".github/workflows/ci.yml",
]);

const MAX_RECORD_BYTES = 256 * 1024;
const GIT_SHA_PATTERN = /^[a-f0-9]{40}$/;
const MANIFEST_DIGEST_PATTERN = /^[a-f0-9]{64}$/;
const RUN_URL_PATTERN =
  /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/actions\/runs\/([1-9][0-9]*)(?:\/attempts\/([1-9][0-9]*))?\/?$/;

export class ReviewValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ReviewValidationError";
  }
}

function fail(location, message) {
  throw new ReviewValidationError(`${location}: ${message}`);
}

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function exactObject(value, expectedKeys, location) {
  if (!isPlainObject(value)) fail(location, "must be a plain object");
  const actual = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    fail(
      location,
      `must contain exactly keys ${expected.join(", ")}; received ${actual.join(", ")}`,
    );
  }
  return value;
}

function array(value, location) {
  if (!Array.isArray(value)) fail(location, "must be an array");
  return value;
}

function literal(value, expected, location) {
  if (value !== expected) {
    fail(location, `must equal ${JSON.stringify(expected)}`);
  }
  return value;
}

function trimmedString(value, location, maximumLength = 1_000) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value !== value.trim() ||
    value.length > maximumLength
  ) {
    fail(
      location,
      `must be a non-empty trimmed string of at most ${maximumLength} characters`,
    );
  }
  return value;
}

function boundedInteger(value, minimum, maximum, location) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    fail(location, `must be an integer from ${minimum} through ${maximum}`);
  }
  return value;
}

function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  const midpoint = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1
    ? ordered[midpoint]
    : (ordered[midpoint - 1] + ordered[midpoint]) / 2;
}

function validateCi(value) {
  const ci = exactObject(value, ["runUrl", "artifactName"], "review.ci");
  const runUrl = trimmedString(ci.runUrl, "review.ci.runUrl", 512);
  const runMatch = runUrl.match(RUN_URL_PATTERN);
  if (!runMatch) {
    fail(
      "review.ci.runUrl",
      "must be a durable https://github.com/<owner>/<repo>/actions/runs/<id> URL without a query or fragment",
    );
  }
  const artifactName = trimmedString(
    ci.artifactName,
    "review.ci.artifactName",
    255,
  );
  if (/[\x00-\x1f\\/]/.test(artifactName)) {
    fail(
      "review.ci.artifactName",
      "must not contain controls or path separators",
    );
  }
  const [, runId, requestedAttempt] = runMatch;
  const artifactMatch = artifactName.match(
    new RegExp(`^live-choreography-${runId}-([1-9][0-9]*)$`),
  );
  if (!artifactMatch) {
    fail(
      "review.ci.artifactName",
      `must identify an artifact from run ${runId} as live-choreography-${runId}-<attempt>`,
    );
  }
  if (requestedAttempt && artifactMatch[1] !== requestedAttempt) {
    fail(
      "review.ci.artifactName",
      `attempt ${artifactMatch[1]} does not match CI run URL attempt ${requestedAttempt}`,
    );
  }
  return { runUrl, artifactName };
}

function validateReviewer(value, index) {
  const location = `review.reviewers[${index}]`;
  const reviewer = exactObject(
    value,
    ["name", "role", "scores", "cornerNineComprehension"],
    location,
  );
  const name = trimmedString(reviewer.name, `${location}.name`, 120);
  if (reviewer.role !== "product_owner" && reviewer.role !== "viewer") {
    fail(`${location}.role`, 'must equal "product_owner" or "viewer"');
  }
  const scores = exactObject(
    reviewer.scores,
    REVIEW_CATEGORIES,
    `${location}.scores`,
  );
  const normalizedScores = Object.fromEntries(
    REVIEW_CATEGORIES.map((category) => [
      category,
      boundedInteger(scores[category], 1, 10, `${location}.scores.${category}`),
    ]),
  );
  const comprehension = exactObject(
    reviewer.cornerNineComprehension,
    ["correct", "replayCount", "explanation"],
    `${location}.cornerNineComprehension`,
  );
  literal(
    comprehension.correct,
    true,
    `${location}.cornerNineComprehension.correct`,
  );
  const replayCount = boundedInteger(
    comprehension.replayCount,
    0,
    1,
    `${location}.cornerNineComprehension.replayCount`,
  );
  const explanation = trimmedString(
    comprehension.explanation,
    `${location}.cornerNineComprehension.explanation`,
  );
  if (
    !/\b3\s*(?:×|x|\*)\s*3\s*=\s*9\b/i.test(explanation) &&
    !/\b3\s+times\s+3\s+(?:equals|is)\s+9\b/i.test(explanation)
  ) {
    fail(
      `${location}.cornerNineComprehension.explanation`,
      "must explain the missing area with 3 × 3 = 9",
    );
  }
  return {
    name,
    role: reviewer.role,
    scores: normalizedScores,
    cornerNineComprehension: { correct: true, replayCount, explanation },
  };
}

export function validateReviewRecord(value) {
  const review = exactObject(
    value,
    [
      "version",
      "gate",
      "reviewedImplementationSha",
      "manifestDigest",
      "ci",
      "reviewers",
      "blockingFindings",
      "decision",
    ],
    "review",
  );
  literal(review.version, 1, "review.version");
  literal(review.gate, "1.5", "review.gate");
  const reviewedImplementationSha = trimmedString(
    review.reviewedImplementationSha,
    "review.reviewedImplementationSha",
    40,
  );
  if (!GIT_SHA_PATTERN.test(reviewedImplementationSha)) {
    fail(
      "review.reviewedImplementationSha",
      "must be a full lowercase 40-character Git SHA",
    );
  }
  const manifestDigest = trimmedString(
    review.manifestDigest,
    "review.manifestDigest",
    64,
  );
  if (!MANIFEST_DIGEST_PATTERN.test(manifestDigest)) {
    fail(
      "review.manifestDigest",
      "must be a full lowercase 64-character SHA-256 digest",
    );
  }
  const ci = validateCi(review.ci);
  const reviewerValues = array(review.reviewers, "review.reviewers");
  if (reviewerValues.length < 3) {
    fail("review.reviewers", "must contain at least three reviewers");
  }
  const reviewers = reviewerValues.map(validateReviewer);
  const normalizedNames = reviewers.map((reviewer) =>
    reviewer.name.toLocaleLowerCase("en-US"),
  );
  if (new Set(normalizedNames).size !== normalizedNames.length) {
    fail("review.reviewers", "must contain distinct reviewer names");
  }
  const productOwnerCount = reviewers.filter(
    (reviewer) => reviewer.role === "product_owner",
  ).length;
  const viewerCount = reviewers.filter(
    (reviewer) => reviewer.role === "viewer",
  ).length;
  literal(productOwnerCount, 1, "review.reviewers product_owner count");
  if (viewerCount < 2) {
    fail("review.reviewers viewer count", "must be at least two");
  }

  const allScores = reviewers.flatMap((reviewer) =>
    REVIEW_CATEGORIES.map((category) => reviewer.scores[category]),
  );
  const aggregateMean =
    allScores.reduce((total, score) => total + score, 0) / allScores.length;
  if (aggregateMean < 8) {
    fail(
      "review aggregate mean",
      `must be at least 8.0; received ${aggregateMean.toFixed(3)}`,
    );
  }
  const categoryMedians = Object.fromEntries(
    REVIEW_CATEGORIES.map((category) => [
      category,
      median(reviewers.map((reviewer) => reviewer.scores[category])),
    ]),
  );
  for (const [category, categoryMedian] of Object.entries(categoryMedians)) {
    if (categoryMedian < 7) {
      fail(
        `review category median ${category}`,
        `must be at least 7.0; received ${categoryMedian.toFixed(3)}`,
      );
    }
  }

  const blockingFindings = array(
    review.blockingFindings,
    "review.blockingFindings",
  );
  if (blockingFindings.length !== 0) {
    fail("review.blockingFindings", "must be empty");
  }
  literal(review.decision, "pass", "review.decision");

  return {
    record: {
      version: 1,
      gate: "1.5",
      reviewedImplementationSha,
      manifestDigest,
      ci,
      reviewers,
      blockingFindings: [],
      decision: "pass",
    },
    metrics: { aggregateMean, categoryMedians },
  };
}

function runGit(repositoryRoot, arguments_, allowedStatuses = [0]) {
  const result = spawnSync("git", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error) {
    fail("git", `could not run git: ${result.error.message}`);
  }
  if (!allowedStatuses.includes(result.status)) {
    const detail = (result.stderr || result.stdout || "").trim();
    fail(
      "git",
      `${arguments_.join(" ")} failed with status ${result.status}${detail ? `: ${detail}` : ""}`,
    );
  }
  return result;
}

export function verifyReviewedImplementation(
  reviewedImplementationSha,
  repositoryRoot = REPOSITORY_ROOT,
) {
  const ancestor = runGit(
    repositoryRoot,
    ["merge-base", "--is-ancestor", reviewedImplementationSha, "HEAD"],
    [0, 1],
  );
  if (ancestor.status !== 0) {
    fail(
      "review.reviewedImplementationSha",
      `${reviewedImplementationSha} is not an ancestor of HEAD`,
    );
  }

  const range = `${reviewedImplementationSha}..HEAD`;
  const drift = runGit(
    repositoryRoot,
    ["diff", "--quiet", range, "--", ...RELEVANT_IMPLEMENTATION_PATHS],
    [0, 1],
  );
  if (drift.status !== 0) {
    const names = runGit(repositoryRoot, [
      "diff",
      "--name-only",
      range,
      "--",
      ...RELEVANT_IMPLEMENTATION_PATHS,
    ])
      .stdout.trim()
      .split("\n")
      .filter(Boolean);
    fail(
      "review implementation drift",
      `reviewed code changed after ${reviewedImplementationSha}: ${names.join(", ")}`,
    );
  }
}

async function readOptionalReviewRecord(recordPath) {
  let before;
  try {
    before = await lstat(recordPath);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  if (before.isSymbolicLink() || !before.isFile()) {
    fail("review record", `${recordPath} must be a regular non-symlink file`);
  }
  if (before.size === 0 || before.size > MAX_RECORD_BYTES) {
    fail(
      "review record",
      `size must be between 1 and ${MAX_RECORD_BYTES} bytes`,
    );
  }

  let handle;
  try {
    handle = await open(
      recordPath,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const information = await handle.stat();
    if (
      !information.isFile() ||
      information.dev !== before.dev ||
      information.ino !== before.ino
    ) {
      fail("review record", `${recordPath} changed while it was opened`);
    }
    const bytes = await handle.readFile();
    if (bytes.length !== information.size) {
      fail("review record", `${recordPath} changed while it was read`);
    }
    return bytes;
  } finally {
    if (handle) await handle.close();
  }
}

function parseReviewRecord(bytes) {
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail("review record", `contains invalid JSON: ${error.message}`);
  }
}

export async function validateLiveChoreographyReview({
  repositoryRoot = REPOSITORY_ROOT,
} = {}) {
  const recordPath = path.join(repositoryRoot, REVIEW_RECORD_RELATIVE_PATH);
  const bytes = await readOptionalReviewRecord(recordPath);
  if (bytes === null) {
    return { status: "not_recorded", recordPath };
  }
  const validated = validateReviewRecord(parseReviewRecord(bytes));
  verifyReviewedImplementation(
    validated.record.reviewedImplementationSha,
    repositoryRoot,
  );
  return { status: "valid", recordPath, ...validated };
}
