import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import { chromium } from "@playwright/test";

import {
  DEFAULT_ARTIFACT_ROOT,
  ProjectileMotionEvidenceError,
  fixtureCatalogForTests,
  locateFfmpegForTests,
  pathExistsForTests,
  probeWebmDurationForTests,
  prepareArtifactRoot,
  prepareArtifactRootForTests,
  relevantDirtyStatusForTests,
  resolveArtifactRoot,
  sha256,
  validateCaptureObservationForTests,
  validateManifestForTests,
  validateReportForTests,
  validateWebmForTests,
  writeManifestForTests,
} from "./projectile-motion-artifacts-lib.mjs";

const SOURCE = Object.freeze({
  gitCommit: "1".repeat(40),
  gitTree: "2".repeat(40),
});
const ENVIRONMENT = Object.freeze({
  nodeVersion: process.version,
  platform: process.platform,
  arch: process.arch,
  playwrightVersion: "1.58.2",
  browserName: "chromium",
  browserVersion: "141.0.7390.37",
});
const CHECKPOINT_IDS = Object.freeze([
  "setup",
  "decompose_velocity",
  "trace_ascent",
  "apex_state",
  "trace_descent",
  "summary",
]);
const TRACKED_NEXT_ENV = `/// <reference types="next" />
/// <reference types="next/image-types/global" />
import "./.next/types/routes.d.ts";
import "./.next/types/root-params.d.ts";
`;
const GENERATED_NEXT_ENV = TRACKED_NEXT_ENV.replaceAll(
  "./.next/types/",
  "./.next/dev/types/",
);

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

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  typeBytes.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return chunk;
}

function png(width, height) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const scanlines = Buffer.alloc(height * (1 + width * 4));
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(scanlines)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function webm() {
  const bytes = Buffer.alloc(4_096);
  Buffer.from([0x1a, 0x45, 0xdf, 0xa3]).copy(bytes, 0);
  Buffer.from("webm").copy(bytes, 32);
  Buffer.from([0x18, 0x53, 0x80, 0x67]).copy(bytes, 64);
  Buffer.from([0x16, 0x54, 0xae, 0x6b]).copy(bytes, 96);
  Buffer.from([0x1f, 0x43, 0xb6, 0x75]).copy(bytes, 128);
  return bytes;
}

function playwrightReport(suite) {
  const expected = {
    accelerated: { file: "projectile-motion.spec.ts", testCount: 16 },
    capture: { file: "projectile-motion-capture.spec.ts", testCount: 1 },
    "product-smoke": {
      file: "projectile-motion-product-auth.spec.ts",
      testCount: 2,
    },
  }[suite];
  return {
    config: {
      version: ENVIRONMENT.playwrightVersion,
      metadata: {
        gate: "1.7",
        suite,
        source: SOURCE,
        environment: ENVIRONMENT,
        actualWorkers: 1,
      },
    },
    suites: [
      {
        title: "projectile motion",
        file: `e2e/${expected.file}`,
        specs: Array.from({ length: expected.testCount }, (_, index) => ({
          title: `${suite} proof`,
          tests: [
            {
              expectedStatus: "passed",
              status: "expected",
              results: [{ status: "passed" }],
            },
          ],
          ordinal: index + 1,
        })),
        suites: [],
      },
    ],
    errors: [],
  };
}

function descriptor(relativePath, bytes, dimensions) {
  return {
    path: relativePath,
    bytes: bytes.length,
    sha256: sha256(bytes),
    ...dimensions,
  };
}

function captureObservation(primaryFixture, artifacts) {
  const startedAtMs = 100;
  const firstMeaningfulVisualAtMs = 240;
  const completedAtMs =
    firstMeaningfulVisualAtMs + primaryFixture.authoredDurationMs;
  let settlementClockMs = startedAtMs;
  const checkpointSettlements = primaryFixture.checkpointTimings.map(
    ({ checkpointId, authoredWindowMs }) => {
      settlementClockMs += authoredWindowMs;
      return { checkpointId, settledAtMs: settlementClockMs };
    },
  );
  const runtimeEvidence = {
    scenarioId: "main_solve",
    settledCheckpointIds: [...CHECKPOINT_IDS],
    finalCheckpointId: "summary",
    finalRevision: primaryFixture.finalRevision,
    certificateHeadSha256: primaryFixture.certificateHeadSha256,
    caption: "The launch angle and speed determine the complete arc.",
    visibleNodeIds: [
      "projectile__ground",
      "projectile__trajectory_ascent",
      "projectile__trajectory_descent",
      "projectile__projectile_marker",
      "projectile__summary",
    ],
    stableNodeIds: ["projectile__ground", "projectile__projectile_marker"],
    traceCueObserved: true,
    markerId: "projectile__projectile_marker",
    pathIds: [
      "projectile__trajectory_ascent",
      "projectile__trajectory_descent",
    ],
    motionSamples: [
      {
        checkpointId: "trace_ascent",
        pathId: "projectile__trajectory_ascent",
        markerId: "projectile__projectile_marker",
        dashArray: 240,
        dashOffset: 120,
        markerTransform: "translate(300 220)",
      },
      {
        checkpointId: "trace_descent",
        pathId: "projectile__trajectory_descent",
        markerId: "projectile__projectile_marker",
        dashArray: 260,
        dashOffset: 130,
        markerTransform: "translate(500 260)",
      },
    ],
    pathGeometrySamples: [
      {
        pathId: "projectile__trajectory_ascent",
        pathD: "M 0 100 Q 50 0 100 50",
        totalLength: 150,
        samples: [
          { fraction: 0, x: 0, y: 100, markerTransform: "translate(0 100)" },
          { fraction: 0.25, x: 25, y: 60, markerTransform: "translate(25 60)" },
          { fraction: 0.5, x: 50, y: 35, markerTransform: "translate(50 35)" },
          { fraction: 0.75, x: 75, y: 32, markerTransform: "translate(75 32)" },
          { fraction: 1, x: 100, y: 50, markerTransform: "translate(100 50)" },
        ],
      },
      {
        pathId: "projectile__trajectory_descent",
        pathD: "M 100 50 Q 150 100 200 100",
        totalLength: 140,
        samples: [
          { fraction: 0, x: 100, y: 50, markerTransform: "translate(100 50)" },
          {
            fraction: 0.25,
            x: 125,
            y: 70,
            markerTransform: "translate(125 70)",
          },
          {
            fraction: 0.5,
            x: 150,
            y: 85,
            markerTransform: "translate(150 85)",
          },
          {
            fraction: 0.75,
            x: 175,
            y: 96,
            markerTransform: "translate(175 96)",
          },
          {
            fraction: 1,
            x: 200,
            y: 100,
            markerTransform: "translate(200 100)",
          },
        ],
      },
    ],
    bridgeCalls: [
      {
        ordinal: 1,
        generation: 1,
        routingMode: "reflex",
        problemSpec: { v: 1, speedMps: 20, angleDeg: 45 },
        baseRevision: 0,
        semanticRevision: 0,
        certificateHeadSha256: null,
        checkpointId: null,
        clarifiedTopics: [],
        activeClarification: null,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  };
  return {
    v: 1,
    gate: "1.7",
    execution: { source: SOURCE, environment: ENVIRONMENT },
    fixtureSource: {
      fixtureId: primaryFixture.fixtureId,
      compilerVersion: primaryFixture.compilerVersion,
      fixtureSha256: primaryFixture.sha256,
    },
    browser: {
      route:
        "/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=normal&proof=none",
      viewport: { width: 1280, height: 720 },
      layout: "cinematic",
      reducedMotion: false,
      colorScheme: "dark",
    },
    runtime: {
      evidence: runtimeEvidence,
      evidenceSha256: sha256(Buffer.from(JSON.stringify(runtimeEvidence))),
    },
    network: {
      providerRequestCount: 0,
      liveSceneRequests: [],
      unexpectedRequests: [],
      failedRequests: [],
    },
    timing: {
      startedAtMs,
      firstMeaningfulVisualAtMs,
      completedAtMs,
      visualDurationMs: primaryFixture.authoredDurationMs,
      checkpointSettlements,
    },
    artifacts,
  };
}

async function createBundle() {
  const temporary = await mkdtemp(
    path.join(tmpdir(), "murmur-projectile-artifacts-"),
  );
  const root = path.join(temporary, "projectile-motion-e2e");
  await prepareArtifactRootForTests(root);
  const fixtures = await fixtureCatalogForTests();
  const primaryFixture = fixtures.find(
    ({ fixtureId }) => fixtureId === "projectile-motion-v20-a45",
  );
  assert.ok(primaryFixture);

  for (const suite of ["accelerated", "capture", "product-smoke"]) {
    await writeFile(
      path.join(root, suite, "report.json"),
      `${JSON.stringify(playwrightReport(suite), null, 2)}\n`,
    );
  }
  const latencySamples = Array.from({ length: 20 }, (_, index) => 80 + index);
  await writeFile(
    path.join(root, "accelerated/observations.json"),
    `${JSON.stringify(
      {
        v: 1,
        gate: "1.7",
        execution: { source: SOURCE, environment: ENVIRONMENT },
        firstMeaningful: {
          samplesMs: latencySamples,
          p95Ms: 98,
          thresholdExclusiveMs: 300,
        },
      },
      null,
      2,
    )}\n`,
  );

  const video = webm();
  const pageScreenshot = png(1280, 720);
  const boardScreenshot = png(960, 640);
  const contactSheet = png(1920, 1920);
  const checkpointScreenshots = CHECKPOINT_IDS.map((checkpointId, index) => ({
    checkpointId,
    bytes: png(960, 640),
    path: `capture/checkpoints/${String(index + 1).padStart(2, "0")}-${checkpointId}.png`,
  }));
  await mkdir(path.join(root, "capture/checkpoints"), { recursive: true });
  await Promise.all([
    writeFile(path.join(root, "capture/projectile-motion.webm"), video),
    writeFile(
      path.join(root, "capture/projectile-motion-page.png"),
      pageScreenshot,
    ),
    writeFile(
      path.join(root, "capture/projectile-motion-board.png"),
      boardScreenshot,
    ),
    writeFile(
      path.join(root, "capture/projectile-motion-contact-sheet.png"),
      contactSheet,
    ),
    ...checkpointScreenshots.map((entry) =>
      writeFile(path.join(root, entry.path), entry.bytes),
    ),
  ]);
  const observation = captureObservation(primaryFixture, {
    video: descriptor("capture/projectile-motion.webm", video),
    pageScreenshot: descriptor(
      "capture/projectile-motion-page.png",
      pageScreenshot,
      { width: 1280, height: 720 },
    ),
    boardScreenshot: descriptor(
      "capture/projectile-motion-board.png",
      boardScreenshot,
      { width: 960, height: 640 },
    ),
    contactSheet: descriptor(
      "capture/projectile-motion-contact-sheet.png",
      contactSheet,
      { width: 1920, height: 1920 },
    ),
    checkpointScreenshots: checkpointScreenshots.map((entry) => ({
      checkpointId: entry.checkpointId,
      ...descriptor(entry.path, entry.bytes, { width: 960, height: 640 }),
    })),
  });
  await writeFile(
    path.join(root, "capture/observations.json"),
    `${JSON.stringify(observation, null, 2)}\n`,
  );
  return { temporary, root, primaryFixture, observation };
}

test("resolves only a narrowly named projectile artifact root", () => {
  assert.equal(
    resolveArtifactRoot(DEFAULT_ARTIFACT_ROOT),
    DEFAULT_ARTIFACT_ROOT,
  );
  assert.throws(
    () => resolveArtifactRoot(path.dirname(DEFAULT_ARTIFACT_ROOT)),
    ProjectileMotionEvidenceError,
  );
  assert.throws(
    () => resolveArtifactRoot(path.join(tmpdir(), "live-choreography")),
    /projectile-motion-e2e/,
  );
});

test("production prepare refuses a non-default root", async () => {
  const temporary = await mkdtemp(
    path.join(tmpdir(), "murmur-projectile-boundary-"),
  );
  const root = path.join(temporary, "projectile-motion-e2e");
  try {
    await assert.rejects(
      () => prepareArtifactRoot(root),
      /prepare may remove only/,
    );
    assert.equal(await pathExistsForTests(root), false);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("prepare honors one explicitly configured narrow output root", async () => {
  const temporary = await mkdtemp(
    path.join(tmpdir(), "murmur-projectile-custom-"),
  );
  const root = path.join(temporary, "projectile-motion-e2e");
  const previous = process.env.PROJECTILE_MOTION_E2E_OUTPUT_DIR;
  try {
    process.env.PROJECTILE_MOTION_E2E_OUTPUT_DIR = root;
    await prepareArtifactRoot(root);
    assert.equal(
      await pathExistsForTests(path.join(root, "accelerated")),
      true,
    );
    assert.equal(await pathExistsForTests(path.join(root, "capture")), true);
    assert.equal(
      await pathExistsForTests(path.join(root, "product-smoke")),
      true,
    );
  } finally {
    if (previous === undefined) {
      delete process.env.PROJECTILE_MOTION_E2E_OUTPUT_DIR;
    } else {
      process.env.PROJECTILE_MOTION_E2E_OUTPUT_DIR = previous;
    }
    await rm(temporary, { recursive: true, force: true });
  }
});

test("test prepare cleans only the exact projectile root and creates suite lanes", async () => {
  const temporary = await mkdtemp(
    path.join(tmpdir(), "murmur-projectile-prepare-"),
  );
  const root = path.join(temporary, "projectile-motion-e2e");
  const sibling = path.join(temporary, "keep-me.txt");
  try {
    await mkdir(root);
    await writeFile(path.join(root, "stale.txt"), "stale");
    await writeFile(sibling, "keep");
    await prepareArtifactRootForTests(root);
    assert.equal(await pathExistsForTests(path.join(root, "stale.txt")), false);
    assert.equal(await readFile(sibling, "utf8"), "keep");
    for (const suite of ["accelerated", "capture", "product-smoke"]) {
      assert.equal(await pathExistsForTests(path.join(root, suite)), true);
    }
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("ignores only Next's exact generated type-path rewrite", () => {
  assert.equal(
    relevantDirtyStatusForTests(
      " M web/next-env.d.ts",
      TRACKED_NEXT_ENV,
      GENERATED_NEXT_ENV,
    ),
    "",
  );
  assert.equal(
    relevantDirtyStatusForTests(
      " M web/next-env.d.ts\n M web/src/app/page.tsx",
      TRACKED_NEXT_ENV,
      GENERATED_NEXT_ENV,
    ),
    " M web/src/app/page.tsx",
  );
  assert.equal(
    relevantDirtyStatusForTests(
      " M web/next-env.d.ts",
      TRACKED_NEXT_ENV,
      `${GENERATED_NEXT_ENV}// manual change\n`,
    ),
    " M web/next-env.d.ts",
  );
});

test("fixture catalog binds five deterministic projectile lessons", async () => {
  const fixtures = await fixtureCatalogForTests();
  assert.equal(fixtures.length, 5);
  assert.deepEqual(
    fixtures.map(({ fixtureId }) => fixtureId),
    [
      "projectile-motion-v20-a30",
      "projectile-motion-v20-a45",
      "projectile-motion-v20-a60",
      "projectile-motion-v30-a45",
      "projectile-motion-v30-a60",
    ],
  );
  for (const fixture of fixtures) {
    assert.deepEqual(fixture.checkpointIds, CHECKPOINT_IDS);
    assert.match(fixture.sha256, /^[a-f0-9]{64}$/);
    assert.ok(fixture.authoredDurationMs > 0);
  }
});

test("capture validation rejects provider calls and broken certificate binding", async () => {
  const { temporary, primaryFixture, observation } = await createBundle();
  try {
    const validated = validateCaptureObservationForTests(
      observation,
      primaryFixture,
    );
    assert.equal(validated.network.providerRequestCount, 0);

    assert.throws(
      () =>
        validateCaptureObservationForTests(
          {
            ...observation,
            network: { ...observation.network, providerRequestCount: 1 },
          },
          primaryFixture,
        ),
      /providerRequestCount/,
    );
    assert.throws(
      () =>
        validateCaptureObservationForTests(
          {
            ...observation,
            runtime: (() => {
              const evidence = {
                ...observation.runtime.evidence,
                certificateHeadSha256: "f".repeat(64),
              };
              return {
                evidence,
                evidenceSha256: sha256(Buffer.from(JSON.stringify(evidence))),
              };
            })(),
          },
          primaryFixture,
        ),
      /certificateHeadSha256/,
    );
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("report validation rejects empty and failed Playwright proofs", () => {
  assert.equal(
    validateReportForTests(playwrightReport("accelerated"), "accelerated")
      .testCount,
    16,
  );
  const failed = structuredClone(playwrightReport("capture"));
  failed.suites[0].specs[0].tests[0].results[0].status = "failed";
  assert.throws(
    () => validateReportForTests(failed, "capture"),
    /must equal "passed"/,
  );
  const empty = structuredClone(playwrightReport("product-smoke"));
  empty.suites = [];
  assert.throws(
    () => validateReportForTests(empty, "product-smoke"),
    /at least one test/,
  );
});

test("media validation binds a generated WebM duration and rejects short or tampered media", async () => {
  const temporary = await mkdtemp(
    path.join(tmpdir(), "murmur-projectile-webm-"),
  );
  const shortVideoPath = path.join(temporary, "short.webm");
  const videoPath = path.join(temporary, "accepted.webm");
  const tamperedVideoPath = path.join(temporary, "tampered.webm");
  let browser;
  try {
    const ffmpeg = await locateFfmpegForTests();
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 320, height: 180 },
      recordVideo: {
        dir: path.join(temporary, "raw-video"),
        size: { width: 320, height: 180 },
      },
    });
    const page = await context.newPage();
    const video = page.video();
    assert.ok(video);
    await page.setContent(`
      <style>
        html, body { margin: 0; width: 100%; height: 100%; background: #050507; }
        .projectile {
          width: 32px; height: 32px; border-radius: 50%; background: #f4b63f;
          animation: flight 1.2s linear infinite alternate;
        }
        @keyframes flight {
          from { transform: translate(12px, 132px); }
          to { transform: translate(276px, 16px); }
        }
      </style>
      <div class="projectile"></div>
    `);
    await page.waitForTimeout(1_500);
    await context.close();
    await video.saveAs(shortVideoPath);
    await video.delete();

    const shortDurationMs = await probeWebmDurationForTests(shortVideoPath);
    assert.ok(shortDurationMs < 35_000);
    await assert.rejects(
      () => validateWebmForTests(shortVideoPath),
      /must be 35000-45000ms/,
    );

    execFileSync(
      ffmpeg,
      [
        "-v",
        "error",
        "-itsscale",
        String(40_000 / shortDurationMs),
        "-i",
        shortVideoPath,
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-f",
        "webm",
        videoPath,
      ],
      { stdio: ["ignore", "ignore", "pipe"], timeout: 120_000 },
    );
    const verified = await validateWebmForTests(videoPath);
    assert.ok(verified.bytes > 4_096);
    assert.match(verified.sha256, /^[a-f0-9]{64}$/);
    assert.ok(verified.durationMs >= 35_000 && verified.durationMs <= 45_000);

    const acceptedBytes = await readFile(videoPath);
    await writeFile(
      tamperedVideoPath,
      acceptedBytes.subarray(0, Math.floor(acceptedBytes.length / 2)),
    );
    await assert.rejects(
      () => validateWebmForTests(tamperedVideoPath),
      /complete-looking WebM|complete WebM|duration|decoded near-final frame/,
    );
  } finally {
    await browser?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

test("finalize and validate bind source, fixtures, runtime, browser, reports, and media", async () => {
  const { temporary, root } = await createBundle();
  try {
    const finalized = await writeManifestForTests(root, SOURCE);
    assert.match(finalized.digest, /^[a-f0-9]{64}$/);
    assert.equal(finalized.manifest.source.gitCommit, SOURCE.gitCommit);
    assert.equal(finalized.manifest.fixtures.catalog.length, 5);
    assert.equal(finalized.manifest.evidence.network.providerRequestCount, 0);
    assert.equal(
      finalized.manifest.evidence.runtime.evidence.finalCheckpointId,
      "summary",
    );
    assert.equal(finalized.manifest.evidence.reports.accelerated.testCount, 16);
    assert.ok(
      finalized.manifest.artifacts.some(
        ({ path: relativePath }) =>
          relativePath === "capture/projectile-motion.webm",
      ),
    );

    const validated = await validateManifestForTests(root, SOURCE);
    assert.equal(validated.digest, finalized.digest);

    await writeFile(
      path.join(root, "capture/projectile-motion-board.png"),
      png(961, 640),
    );
    await assert.rejects(
      () => validateManifestForTests(root, SOURCE),
      /byte count|digest|width/,
    );
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
