import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  CAPTURE_FILES,
  EXPECTED_REPORTS,
  GATE,
  PROTOCOL,
  SemanticStoryboardEvidenceError,
  sha256,
  validateCaptureObservation,
  validateReport,
} from "./semantic-storyboard-artifact-contract.mjs";
import {
  artifactInventoryForTests,
  fixtureCatalogForTests,
  prepareArtifactRootForTests,
  resolveArtifactRoot,
  sealedPriorFixtureEvidenceForTests,
  validateManifestForTests,
  validatePngForTests,
  validateVideoCropDurationForTests,
  writeManifestForTests,
} from "./semantic-storyboard-artifacts-lib.mjs";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(SCRIPT_ROOT, "..");
const FIXTURE_PATH = path.join(
  WEB_ROOT,
  "src/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a60.v1.json",
);
const SOURCE = Object.freeze({
  gitCommit: "a".repeat(40),
  gitTree: "b".repeat(40),
});
const ENVIRONMENT = Object.freeze({
  nodeVersion: process.version,
  platform: process.platform,
  arch: process.arch,
  playwrightVersion: "1.58.2",
  browserName: "chromium",
  browserVersion: "140.0.0.0",
});
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

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(data.length + 12);
  chunk.writeUInt32BE(data.length, 0);
  typeBytes.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32([typeBytes, data]), data.length + 8);
  return chunk;
}

function png(width, height) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const pixels = Buffer.alloc((width * 4 + 1) * height);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(pixels)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

const FULL_PAGE_PNG = png(1_280, 720);
const BOARD_PNG = png(64, 48);
const CONTACT_SHEET_PNG = png(128, 144);

function jsonAttachment(name, value) {
  return {
    name,
    contentType: "application/json",
    body: Buffer.from(JSON.stringify(value)).toString("base64"),
  };
}

function interruptionAttachment(suite) {
  const latenciesMs = [10, 20, 30, 40];
  if (suite === "accelerated") {
    return jsonAttachment("semantic-storyboard-interruption-observation", {
      schemaVersion: 1,
      clock: "browser_performance_now",
      thresholdMs: 150,
      results: [
        "provider_wait",
        "draw",
        "trace_path",
        "marker_movement",
        "relationship_morph",
        "camera_focus",
        "hold",
        "post_paint_barrier",
        "replay",
      ].map((surface) => ({
        surface,
        trials: 4,
        latenciesMs,
        p95Ms: 40,
        maxMs: 40,
      })),
      unavailableSurfaces: [],
    });
  }
  if (suite === "product-smoke") {
    return jsonAttachment(
      "semantic-storyboard-partial-record-interruption-observation",
      {
        schemaVersion: 1,
        clock: "browser_performance_now",
        thresholdMs: 150,
        results: [
          {
            surface: "partial_record",
            trials: 4,
            observations: latenciesMs.map((latencyMs, index) => ({
              trial: index + 1,
              requestedAtMs: index * 100,
              settledAtMs: index * 100 + latencyMs,
              latencyMs,
            })),
            latenciesMs,
            p95Ms: 40,
            maxMs: 40,
            abortCount: 4,
            requestCount: 8,
            partialChunkCount: 4,
          },
        ],
        unavailableSurfaces: [],
      },
    );
  }
  return null;
}

function latencyAttachment() {
  const checkpointIds = [
    "storyboard-anchor",
    "storyboard-checkpoint-trace-higher-angle",
    "storyboard-checkpoint-trace-lower-angle",
    "storyboard-checkpoint-relate-higher-apex",
    "storyboard-checkpoint-relate-longer-flight",
  ];
  const trials = Array.from({ length: 20 }, (_, index) => {
    const submitAtMs = index * 1_000;
    const anchorVisibleAtMs = submitAtMs + 100;
    const directorDispatchAtMs = submitAtMs + 120;
    const firstModelCheckpointEventAtMs = submitAtMs + 200;
    const firstModelVisibleAtMs = submitAtMs + 220;
    return {
      ordinal: index + 1,
      submitAtMs,
      anchorVisibleAtMs,
      submitToAnchorVisibleMs: 100,
      directorDispatchAtMs,
      firstModelVisibleAtMs,
      directorDispatchToFirstModelVisibleMs: 100,
      firstModelCheckpointEventAtMs,
      firstModelCheckpointEventToVisibleMs: 20,
      postPaintAcceptanceToNextBeatVisible: checkpointIds
        .slice(1)
        .map((toCheckpointId, checkpointIndex) => {
          const nextVisibleAtMs = firstModelVisibleAtMs + checkpointIndex * 100;
          const postPaintAcceptedAtMs =
            checkpointIndex === 0
              ? anchorVisibleAtMs + 5
              : nextVisibleAtMs - 90;
          return {
            fromCheckpointId: checkpointIds[checkpointIndex],
            toCheckpointId,
            postPaintAcceptedAtMs,
            nextVisibleAtMs,
            durationMs: nextVisibleAtMs - postPaintAcceptedAtMs,
          };
        }),
    };
  });
  return jsonAttachment(
    "semantic-storyboard-provider-free-latency-observation",
    {
      schemaVersion: 1,
      clock: "browser_performance_now",
      freshContextCount: 20,
      route:
        "/e2e/semantic-storyboard?layout=cinematic&motion=reduced&speed=accelerated&proof=none",
      boundaries: {
        submitToAnchorVisible:
          "button submit -> firstCuePresented-backed anchor stage publication",
        directorDispatchToFirstModelVisible:
          "fixture Director invocation -> firstCuePresented-backed model stage publication",
        firstModelCheckpointEventToVisible:
          "first immediately-presentable model checkpoint callback -> firstCuePresented-backed matching stage publication",
        postPaintAcceptanceToNextBeatVisible:
          "accepted checkpoint subscriber publication after executor settlement -> next firstCuePresented-backed stage publication",
      },
      thresholdsMs: {
        submitToAnchorVisibleP95: 300,
        directorDispatchToFirstModelVisibleP95: 2_000,
        firstModelCheckpointEventToVisibleP95: 250,
        postPaintAcceptanceToNextBeatVisibleP95: 1_000,
      },
      statistics: {
        submitToAnchorVisible: { sampleCount: 20, p95Ms: 100, maxMs: 100 },
        directorDispatchToFirstModelVisible: {
          sampleCount: 20,
          p95Ms: 100,
          maxMs: 100,
        },
        firstModelCheckpointEventToVisible: {
          sampleCount: 20,
          p95Ms: 20,
          maxMs: 20,
        },
        postPaintAcceptanceToNextBeatVisible: {
          sampleCount: 80,
          p95Ms: 115,
          maxMs: 115,
        },
      },
      trials,
    },
  );
}

function reportAttachments(suite) {
  return [
    interruptionAttachment(suite),
    suite === "accelerated" ? latencyAttachment() : null,
  ].filter(Boolean);
}

function report(suite) {
  const expected = EXPECTED_REPORTS[suite];
  const attachments = reportAttachments(suite);
  return {
    config: {
      metadata: {
        gate: GATE,
        protocol: PROTOCOL,
        suite,
        source: SOURCE,
        environment: ENVIRONMENT,
        actualWorkers: 1,
      },
      version: ENVIRONMENT.playwrightVersion,
    },
    errors: [],
    suites: [
      {
        specs: [
          {
            file: expected.file,
            tests: Array.from({ length: expected.testCount }, (_, index) => ({
              expectedStatus: "passed",
              status: "expected",
              results: [
                {
                  status: "passed",
                  attachments:
                    attachments.length > 0 && index === expected.testCount - 1
                      ? attachments
                      : [],
                },
              ],
            })),
          },
        ],
        suites: [],
      },
    ],
  };
}

function descriptor(fileName, bytes, image = false) {
  return {
    fileName,
    bytes: bytes.length,
    sha256: sha256(bytes),
    ...(image
      ? { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
      : {}),
  };
}

function domSignature(scene, viewBox) {
  const label = (node) =>
    node.kind === "text" ||
    node.kind === "latex" ||
    node.kind === "latex_token";
  const nodeSignature = (node) => {
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
  };
  return {
    sourceRevision: scene.revision,
    viewBox,
    paintOrder: [
      ...scene.nodes.filter((node) => !label(node)).map((node) => node.id),
      ...scene.nodes.filter(label).map((node) => node.id),
    ],
    nodes: scene.nodes.map(nodeSignature),
    residueFree: true,
  };
}

async function captureObservation() {
  const fixture = JSON.parse(await readFile(FIXTURE_PATH, "utf8"));
  const program = fixture.programs.find(
    (candidate) => candidate.programId === "higher_arc_first",
  );
  const continuation = fixture.continuations.find(
    (candidate) =>
      candidate.scenarioId === "continue_higher_arc_first_prefix_4",
  );
  assert.ok(program && continuation);
  const expectedCheckpointIds = [
    ...fixture.anchor.checkpointIds,
    ...program.checkpointIds,
    ...continuation.checkpointIds,
  ];
  const acceptedAt = [900, 3_659, 5_659, 7_209, 8_509, 9_459];
  const eventAt = [100, 1_000, 3_659, 5_659, 7_209, 8_509];
  const generations = [1, 2, 2, 2, 2, 3];
  const runnerCalls = [
    {
      ordinal: 1,
      observedAtMs: 100,
      generation: 1,
      routingMode: "reflex",
      prompt: null,
      problemSpec: fixture.problemSpec,
      baseRevision: 0,
      semanticRevision: 0,
      certificateHeadSha256: null,
      acceptedRecordCount: 0,
    },
    {
      ordinal: 2,
      observedAtMs: 1_000,
      generation: 2,
      routingMode: "director",
      prompt: program.prompt,
      problemSpec: fixture.problemSpec,
      baseRevision: 1,
      semanticRevision: 1,
      certificateHeadSha256:
        fixture.anchor.expectedTerminal.frontier.certificateHeadSha256,
      acceptedRecordCount: 0,
    },
    {
      ordinal: 3,
      observedAtMs: 8_509,
      generation: 3,
      routingMode: "director",
      prompt: continuation.prompt,
      problemSpec: fixture.problemSpec,
      baseRevision: continuation.baseScene.revision,
      semanticRevision: continuation.baseSemanticScene.revision,
      certificateHeadSha256:
        continuation.baseSemanticScene.certificateHeadSha256,
      acceptedRecordCount: continuation.fromPrefixCount,
    },
  ];
  const viewBox = "0 0 960 640";
  const stage = {
    phase: "completed",
    sessionStatus: "paused",
    settledBeatCount: 5,
    visibleCheckpointId: continuation.checkpointIds.at(-1),
    acceptedSpeed: String(fixture.problemSpec.speedMps),
    acceptedAngles: fixture.problemSpec.anglesDeg.join(":"),
    rendererTrusted: "true",
    layout: "cinematic",
    sceneRevision: continuation.expectedTerminal.frontier.revision,
    semanticRevision: continuation.expectedTerminal.frontier.revision,
    generation: 3,
    lastRoute: "director",
    programSha256: continuation.expectedTerminal.frontier.programSha256,
    certificateHead:
      continuation.expectedTerminal.frontier.certificateHeadSha256,
    completionReason: "model_stop",
    completionDetail: "none",
    caption: "Certified comparison",
    viewBox,
    nodeIds: continuation.expectedTerminal.scene.nodes.map((node) => node.id),
  };
  const checkpoints = expectedCheckpointIds.map((checkpointId, ordinal) => ({
    ordinal,
    checkpointId,
    ...descriptor(
      `semantic-storyboard-normal-speed-checkpoint-${String(ordinal).padStart(2, "0")}-${checkpointId}.png`,
      BOARD_PNG,
      true,
    ),
  }));
  return {
    fixture,
    observation: {
      schemaVersion: 1,
      route: {
        path: "/e2e/semantic-storyboard",
        query: "layout=cinematic&motion=real&proof=none&speed=normal",
      },
      viewport: { width: 1_280, height: 720 },
      runner: {
        runnerCallCount: 3,
        calls: runnerCalls,
      },
      timeline: {
        observations: expectedCheckpointIds.map((checkpointId, index) => ({
          observedAtMs: acceptedAt[index],
          status:
            index === expectedCheckpointIds.length - 1 ? "paused" : "directing",
          phase:
            index === expectedCheckpointIds.length - 1
              ? "completed"
              : "streaming",
          generation: generations[index],
          acceptedCheckpointIds: expectedCheckpointIds.slice(0, index + 1),
          sceneRevision: index + 1,
          semanticRevision: index + 1,
          rendererTrusted: true,
        })),
        acceptedCheckpointTimeline: expectedCheckpointIds.map(
          (checkpointId, index) => ({
            ordinal: index,
            checkpointId,
            generation: generations[index],
            observedAtMs: acceptedAt[index],
            sceneRevision: index + 1,
            semanticRevision: index + 1,
          }),
        ),
      },
      terminal: {
        stage,
        snapshot: {
          runtime: {
            phase: "completed",
            rendererTrusted: true,
            committedScene: continuation.expectedTerminal.scene,
            committedSemanticScene: continuation.expectedTerminal.semanticScene,
          },
          status: "paused",
          lastRoute: "director",
          pendingDirector: false,
          problemSpec: fixture.problemSpec,
          progress: {},
          controls: {},
          orchestrationError: null,
        },
      },
      timing: {
        startedAtMs: 100,
        terminalAtMs: 9_459,
        elapsedMs: 9_359,
        anchorPaintMs: 800,
        anchorPostPaintAcceptanceMs: 800,
        firstModelVisibleAtMs: 1_100,
        directorDispatchToFirstVisibleMs: 100,
        firstModelCheckpointEventToFirstVisibleMs: 100,
        anchorToFirstModelPostPaintMs: 2_759,
        directorDispatchToFirstModelPostPaintMs: 2_659,
        authoredVisualDurationMs: 8_459,
        observedFiveBeatSequenceMs: 8_459,
        videoCrop: {
          recordingStartedAtEpochMs: 1_700_000_000_000,
          pageTimeOriginMs: 1_700_000_000_000,
          directorDispatchAtEpochMs: 1_700_000_001_000,
          startMs: 900,
          durationMs: 8_859,
          leadInMs: 100,
          tailMs: 300,
        },
        callToStartedEventMs: runnerCalls.map((call) => ({
          ordinal: call.ordinal,
          generation: call.generation,
          routingMode: call.routingMode,
          durationMs: 0,
        })),
        checkpointEventToPostPaint: expectedCheckpointIds.map(
          (checkpointId, index) => ({
            checkpointId,
            eventAtMs: eventAt[index],
            acceptedAtMs: acceptedAt[index],
            durationMs: acceptedAt[index] - eventAt[index],
          }),
        ),
        postPaintToNextBeatVisibleGapsMs: expectedCheckpointIds
          .slice(1)
          .map((checkpointId, index) => {
            const nextVisibleAtMs = [1_100, 4_000, 6_000, 7_500, 9_000][index];
            return {
              fromCheckpointId: expectedCheckpointIds[index],
              toCheckpointId: checkpointId,
              postPaintAcceptedAtMs: acceptedAt[index],
              nextVisibleAtMs,
              durationMs: nextVisibleAtMs - acceptedAt[index],
            };
          }),
        transportCheckpointArrivalGapsMs: expectedCheckpointIds
          .slice(1)
          .map((checkpointId, index) => ({
            fromCheckpointId: expectedCheckpointIds[index],
            toCheckpointId: checkpointId,
            durationMs: eventAt[index + 1] - eventAt[index],
          })),
        unavailableMetrics: [
          "server_provider_first_byte",
          "record_complete_to_verification",
          "verification_duration",
          "post_paint_barrier_duration",
        ],
      },
      dom: {
        signature: domSignature(continuation.expectedTerminal.scene, viewBox),
        mismatches: [],
      },
      artifacts: {
        video: descriptor(CAPTURE_FILES.video, fakeWebm()),
        fullPage: descriptor(CAPTURE_FILES.fullPage, FULL_PAGE_PNG, true),
        boardOnly: descriptor(CAPTURE_FILES.boardOnly, BOARD_PNG, true),
        contactSheet: descriptor(
          CAPTURE_FILES.contactSheet,
          CONTACT_SHEET_PNG,
          true,
        ),
        checkpoints,
      },
    },
  };
}

function fakeWebm() {
  const bytes = Buffer.alloc(4_096);
  Buffer.from([0x1a, 0x45, 0xdf, 0xa3]).copy(bytes);
  Buffer.from("webm").copy(bytes, 32);
  return bytes;
}

async function temporaryArtifactRoot() {
  const parent = await mkdtemp(path.join(tmpdir(), "storyboard-artifacts-"));
  return path.join(parent, "semantic-storyboard-e2e");
}

async function writeEvidence(root) {
  for (const suite of Object.keys(EXPECTED_REPORTS)) {
    await writeFile(
      path.join(root, suite, "report.json"),
      JSON.stringify(report(suite)),
    );
  }
  const { observation } = await captureObservation();
  await writeFile(
    path.join(root, "capture", CAPTURE_FILES.observation),
    JSON.stringify(observation),
  );
  const descriptors = [
    observation.artifacts.fullPage,
    observation.artifacts.boardOnly,
    observation.artifacts.contactSheet,
    ...observation.artifacts.checkpoints,
  ];
  for (const artifact of descriptors) {
    const bytes =
      artifact.fileName === CAPTURE_FILES.fullPage
        ? FULL_PAGE_PNG
        : artifact.fileName === CAPTURE_FILES.contactSheet
          ? CONTACT_SHEET_PNG
          : BOARD_PNG;
    await writeFile(path.join(root, "capture", artifact.fileName), bytes);
  }
  await writeFile(path.join(root, "capture", CAPTURE_FILES.video), fakeWebm());
}

test("fixture catalog binds all generated programs without a static story order", async () => {
  const catalog = await fixtureCatalogForTests();
  assert.equal(catalog.length, 3);
  assert.equal(catalog.flatMap((fixture) => fixture.programs).length, 6);
  const primary = catalog.find((fixture) =>
    fixture.fixtureId.endsWith("a30-a60"),
  );
  assert.ok(primary);
  assert.equal(primary.continuationCount, 6);
  assert.equal(primary.recoveryContinuationCount, 1);
  assert.equal(primary.fakeProviderStreamCount, 16);
  assert.deepEqual(primary.negativeLaneIds, [
    "unsupported_wind",
    "unsupported_unequal_launch_height",
    "unsupported_requested_angles",
    "unsupported_svg_injection",
    "ambiguous_make_it_better",
  ]);
});

test("Gate 1.5 through Gate 1.7 fixture digests remain on a versioned baseline", async () => {
  const evidence = await sealedPriorFixtureEvidenceForTests();
  assert.equal(evidence.baselineVersion, "gate-1.8-prior-fixtures-v1");
  assert.equal(evidence.files.length, 9);
  assert.deepEqual(
    evidence.files.map(({ path: filePath }) => filePath),
    [...evidence.files.map(({ path: filePath }) => filePath)].sort(),
  );
});

test("report validator rejects wrong provenance, files, counts, and retries", () => {
  assert.equal(
    validateReport(report("accelerated"), "accelerated").testCount,
    6,
  );
  for (const mutate of [
    (value) => (value.config.metadata.gate = "1.7"),
    (value) => (value.suites[0].specs[0].file = "projectile-motion.spec.ts"),
    (value) => value.suites[0].specs[0].tests.pop(),
    (value) =>
      value.suites[0].specs[0].tests[0].results.push({ status: "passed" }),
  ]) {
    const candidate = structuredClone(report("accelerated"));
    mutate(candidate);
    assert.throws(
      () => validateReport(candidate, "accelerated"),
      SemanticStoryboardEvidenceError,
    );
  }
  const badInterruption = report("accelerated");
  const attachment =
    badInterruption.suites[0].specs[0].tests.at(-1).results[0].attachments[0];
  const evidence = JSON.parse(Buffer.from(attachment.body, "base64"));
  evidence.results[0].maxMs = 150;
  attachment.body = Buffer.from(JSON.stringify(evidence)).toString("base64");
  assert.throws(
    () => validateReport(badInterruption, "accelerated"),
    /maxMs|below 150ms/,
  );
  const badLatency = report("accelerated");
  const latency = badLatency.suites[0].specs[0].tests
    .at(-1)
    .results[0].attachments.find(
      ({ name }) =>
        name === "semantic-storyboard-provider-free-latency-observation",
    );
  const latencyEvidence = JSON.parse(Buffer.from(latency.body, "base64"));
  latencyEvidence.trials[0].anchorVisibleAtMs += 10;
  latency.body = Buffer.from(JSON.stringify(latencyEvidence)).toString(
    "base64",
  );
  assert.throws(
    () => validateReport(badLatency, "accelerated"),
    /submitToAnchorVisible.*durationMs/,
  );
  const badLatencySummary = report("accelerated");
  const summaryAttachment = badLatencySummary.suites[0].specs[0].tests
    .at(-1)
    .results[0].attachments.find(
      ({ name }) =>
        name === "semantic-storyboard-provider-free-latency-observation",
    );
  const summaryEvidence = JSON.parse(
    Buffer.from(summaryAttachment.body, "base64"),
  );
  summaryEvidence.statistics.submitToAnchorVisible.p95Ms = 299;
  summaryAttachment.body = Buffer.from(
    JSON.stringify(summaryEvidence),
  ).toString("base64");
  assert.throws(
    () => validateReport(badLatencySummary, "accelerated"),
    /submitToAnchorVisible\.p95Ms/,
  );
});

test("capture contract derives checkpoint order from the selected fixture program", async () => {
  const { fixture, observation } = await captureObservation();
  const validated = validateCaptureObservation(observation, [fixture]);
  assert.equal(
    validated.selectedProgramId,
    "continue_higher_arc_first_prefix_4",
  );
  const mutated = structuredClone(observation);
  mutated.artifacts.checkpoints.reverse();
  assert.throws(
    () => validateCaptureObservation(mutated, [fixture]),
    /ordinal|checkpointId/,
  );

  const crossRuntime = structuredClone(observation);
  const coordinate =
    crossRuntime.terminal.snapshot.runtime.committedScene.nodes[5].points[3];
  const expectedX = coordinate[0];
  coordinate[0] += (Number.EPSILON * Math.max(1, Math.abs(expectedX))) / 2;
  assert.notEqual(coordinate[0], expectedX);
  crossRuntime.dom.signature.nodes[5].points[3][0] = coordinate[0];
  validateCaptureObservation(crossRuntime, [fixture]);

  const svgLine = structuredClone(observation);
  const fixtureLineX =
    observation.terminal.snapshot.runtime.committedScene.nodes[3].points[1][0];
  svgLine.dom.signature.nodes[3].points[1][0] = Number(
    Math.fround(fixtureLineX).toFixed(6),
  );
  validateCaptureObservation(svgLine, [fixture]);

  const driftedSvgLine = structuredClone(svgLine);
  driftedSvgLine.dom.signature.nodes[3].points[1][0] += 0.00001;
  assert.throws(
    () => validateCaptureObservation(driftedSvgLine, [fixture]),
    /nearest Float32 representation/,
  );

  const drifted = structuredClone(observation);
  drifted.terminal.snapshot.runtime.committedScene.nodes[5].points[3][0] += 0.000001;
  drifted.dom.signature.nodes[5].points[3][0] += 0.000001;
  assert.throws(
    () => validateCaptureObservation(drifted, [fixture]),
    /scaled Number\.EPSILON/,
  );

  const styleDrift = structuredClone(observation);
  const styledNode =
    styleDrift.terminal.snapshot.runtime.committedScene.nodes.find(
      (node) => node.kind === "path",
    );
  assert.ok(styledNode);
  const expectedStrokeWidth = styledNode.style.strokeWidth;
  styledNode.style.strokeWidth +=
    Number.EPSILON * Math.max(1, Math.abs(expectedStrokeWidth)) * 2;
  assert.notEqual(styledNode.style.strokeWidth, expectedStrokeWidth);
  assert.throws(
    () => validateCaptureObservation(styleDrift, [fixture]),
    /must exactly match fixture-derived evidence/,
  );

  const copyDrift = structuredClone(observation);
  const copyNode =
    copyDrift.terminal.snapshot.runtime.committedScene.nodes.find(
      (node) => node.kind === "latex_token",
    );
  assert.ok(copyNode);
  copyNode.latex += " ";
  assert.throws(
    () => validateCaptureObservation(copyDrift, [fixture]),
    /must exactly match fixture-derived evidence/,
  );

  const reorderedNodes = structuredClone(observation);
  const [firstNode, secondNode] =
    reorderedNodes.terminal.snapshot.runtime.committedScene.nodes;
  reorderedNodes.terminal.snapshot.runtime.committedScene.nodes.splice(
    0,
    2,
    secondNode,
    firstNode,
  );
  assert.throws(
    () => validateCaptureObservation(reorderedNodes, [fixture]),
    /must exactly match fixture-derived evidence/,
  );
});

test("prepare is bounded, deterministic, and rejects unsafe roots", async () => {
  const root = await temporaryArtifactRoot();
  await prepareArtifactRootForTests(root);
  await writeFile(path.join(root, "stale.txt"), "stale");
  await prepareArtifactRootForTests(root);
  await assert.rejects(readFile(path.join(root, "stale.txt")), /ENOENT/);
  assert.throws(() => resolveArtifactRoot(path.dirname(root)), /must end in/);
});

test("artifact inventory rejects symlinks", async () => {
  const root = await temporaryArtifactRoot();
  await prepareArtifactRootForTests(root);
  await writeFile(path.join(root, "capture", "proof.json"), "{}\n");
  await symlink(
    path.join(root, "capture", "proof.json"),
    path.join(root, "capture", "linked.json"),
  );
  await assert.rejects(
    artifactInventoryForTests(root),
    SemanticStoryboardEvidenceError,
  );
});

test("media proof validates PNG contents and the decoded video crop", () => {
  assert.deepEqual(validatePngForTests(BOARD_PNG), { width: 64, height: 48 });
  const corrupted = Buffer.from(BOARD_PNG);
  corrupted[41] ^= 1;
  assert.throws(() => validatePngForTests(corrupted), /invalid CRC/);
  assert.equal(validateVideoCropDurationForTests(8_850, 8_859).deltaMs, -9);
  assert.throws(
    () => validateVideoCropDurationForTests(8_500, 8_859),
    /requested crop/,
  );
});

test("manifest round-trip revalidates every report, capture, and inventory byte", async () => {
  const root = await temporaryArtifactRoot();
  await prepareArtifactRootForTests(root);
  await writeEvidence(root);
  const written = await writeManifestForTests(root, SOURCE);
  const validated = await validateManifestForTests(root, SOURCE);
  assert.equal(validated.digest, written.digest);
  assert.equal(validated.manifest.evidence.reports.accelerated.testCount, 6);
  assert.equal(
    validated.manifest.evidence.latency.fixtureId,
    "semantic-storyboard-v20-a30-a60",
  );
  assert.equal(
    validated.manifest.evidence.latency.programId,
    "higher_arc_first",
  );
  await writeFile(
    path.join(root, "capture", CAPTURE_FILES.fullPage),
    Buffer.concat([FULL_PAGE_PNG, Buffer.from("tamper")]),
  );
  await assert.rejects(validateManifestForTests(root, SOURCE), /bytes|sha256/);
});
