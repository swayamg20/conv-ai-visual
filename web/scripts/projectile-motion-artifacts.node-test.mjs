import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
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
  validateAcceleratedObservationForTests,
  validateCaptureObservationForTests,
  validateManifestForTests,
  validateRecordingTimingForTests,
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
const RAW_PRIMARY_FIXTURE = JSON.parse(
  readFileSync(
    new URL(
      "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json",
      import.meta.url,
    ),
    "utf8",
  ),
);
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

function checkpointEvents(lane) {
  return lane.events.filter(
    ({ type }) => type === "projectile_choreography_scene_checkpoint",
  );
}

function materialize(events) {
  let scene = { revision: 0, nodes: [] };
  return events.map((event) => {
    const nodes = [...scene.nodes];
    const positions = new Map(nodes.map((node, index) => [node.id, index]));
    const removed = new Set();
    for (const operation of event.patch.operations) {
      if (operation.op === "remove") removed.add(operation.id);
      else if (positions.has(operation.node.id)) {
        nodes[positions.get(operation.node.id)] = operation.node;
      } else {
        positions.set(operation.node.id, nodes.length);
        nodes.push(operation.node);
      }
    }
    scene = {
      revision: event.resultRevision,
      nodes: nodes.filter(({ id }) => !removed.has(id)),
    };
    return {
      event,
      scene,
      semanticScene: {
        certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
        components: [event.semantic.resultComponent],
        revision: event.semantic.semanticResultRevision,
      },
    };
  });
}

function fixtureRecords() {
  const mainEvents = checkpointEvents(RAW_PRIMARY_FIXTURE.lanes.main);
  const vectorEvents = [
    ...mainEvents.slice(0, 4),
    ...checkpointEvents(RAW_PRIMARY_FIXTURE.lanes.clarifyApex),
    ...checkpointEvents(RAW_PRIMARY_FIXTURE.lanes.continueAfterClarification),
    ...checkpointEvents(RAW_PRIMARY_FIXTURE.lanes.retargetAfterSummary),
  ];
  return { main: materialize(mainEvents), vector: materialize(vectorEvents) };
}

function semanticDom(record) {
  const nodeSignature = (node) => {
    if (node.kind === "line") {
      return {
        kind: "line",
        id: node.id,
        points: node.points.map(([x, y]) => [
          Number(x.toFixed(6)),
          Number(y.toFixed(6)),
        ]),
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
  const viewport = record.event.semantic.presentation.resultViewports.cinematic;
  const labels = new Set(["text", "latex", "latex_token"]);
  return {
    sourceRevision: record.scene.revision,
    viewBox: `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`,
    paintOrder: [
      ...record.scene.nodes.filter(({ kind }) => !labels.has(kind)),
      ...record.scene.nodes.filter(({ kind }) => labels.has(kind)),
    ].map(({ id }) => id),
    nodes: record.scene.nodes.map(nodeSignature),
    residueFree: true,
  };
}

function acceptedPrefix(records, cancelledIndex, continuationStart) {
  return records.map((record, index) => {
    const viewport =
      record.event.semantic.presentation.resultViewports.cinematic;
    const event =
      continuationStart !== undefined && index >= continuationStart
        ? {
            ...structuredClone(record.event),
            generation: 2,
            sequence: index - continuationStart + 1,
          }
        : structuredClone(record.event);
    return {
      event,
      scene: structuredClone(record.scene),
      semanticScene: structuredClone(record.semanticScene),
      viewport: structuredClone(viewport),
      layout: "cinematic",
      presentation: {
        type: "projectile_choreography_checkpoint_presented",
        checkpointId: record.event.semantic.checkpointId,
        certificateSha256:
          record.event.semantic.semanticResultCertificateSha256,
        sceneRevision: record.scene.revision,
        semanticRevision: record.semanticScene.revision,
        layout: "cinematic",
        resultViewport: structuredClone(viewport),
        settlement:
          index === cancelledIndex ? "cancelled_to_checkpoint" : "completed",
      },
    };
  });
}

function pathGeometry(record, pathId) {
  const pathNode = record.scene.nodes.find(({ id }) => id === pathId);
  assert.equal(pathNode?.kind, "path");
  const points = pathNode.points;
  const segments = points.slice(1).map((point, index) => ({
    start: points[index],
    end: point,
    length: Math.hypot(
      point[0] - points[index][0],
      point[1] - points[index][1],
    ),
  }));
  const totalLength = segments.reduce((total, { length }) => total + length, 0);
  const pointAt = (fraction) => {
    let remaining = totalLength * fraction;
    for (const segment of segments) {
      if (remaining <= segment.length) {
        const progress = remaining / segment.length;
        return [
          segment.start[0] + (segment.end[0] - segment.start[0]) * progress,
          segment.start[1] + (segment.end[1] - segment.start[1]) * progress,
        ];
      }
      remaining -= segment.length;
    }
    return points.at(-1);
  };
  const rounded = (value) => Number(value.toFixed(3));
  return {
    pathId,
    pathD: points
      .map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x} ${y}`)
      .join(" "),
    totalLength: rounded(totalLength),
    samples: [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
      const [xValue, yValue] = pointAt(fraction);
      const x = rounded(xValue);
      const y = rounded(yValue);
      return { fraction, x, y, markerTransform: `translate(${x} ${y})` };
    }),
  };
}

function shiftFixturePathCoordinates(value, scale = 1, seen = new WeakSet()) {
  if (value === null || typeof value !== "object") return;
  if (seen.has(value)) return;
  seen.add(value);
  if (
    value.kind === "path" &&
    Array.isArray(value.points) &&
    !seen.has(value.points)
  ) {
    seen.add(value.points);
    for (const point of value.points) {
      point.forEach((coordinate, index) => {
        const bytes = new ArrayBuffer(8);
        const view = new DataView(bytes);
        view.setFloat64(0, coordinate);
        let bits = view.getBigUint64(0);
        for (let step = 0; step < scale; step += 1) bits += 1n;
        view.setBigUint64(0, bits);
        point[index] = view.getFloat64(0);
      });
    }
  }
  Object.values(value).forEach((entry) =>
    shiftFixturePathCoordinates(entry, scale, seen),
  );
}

function captureObservation(primaryFixture, artifacts) {
  const terminal = fixtureRecords().main.at(-1);
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
      pathGeometry(terminal, "projectile__trajectory_ascent"),
      pathGeometry(terminal, "projectile__trajectory_descent"),
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

function acceleratedObservation(primaryFixture) {
  const records = fixtureRecords();
  const frontier = (
    record,
    prefix,
    generation,
    phase,
    cancelledIndex,
    continuationStart,
  ) => {
    const checkpointId = record.event.semantic.checkpointId;
    const certificateHeadSha256 =
      record.event.semantic.semanticResultCertificateSha256;
    const payload = {
      semanticDomProjection: semanticDom(record),
      caption: record.event.patch.narration,
      phase,
      generation,
      visibleCheckpointId: checkpointId,
      committedScene: structuredClone(record.scene),
      committedSemanticScene: structuredClone(record.semanticScene),
      accepted: acceptedPrefix(prefix, cancelledIndex, continuationStart),
    };
    return {
      generation,
      phase,
      checkpointId,
      revision: record.scene.revision,
      certificateHeadSha256,
      payload,
    };
  };
  const traceSampleSpecs = [
    ["trace_ascent", "projectile__trajectory_ascent", 0.25, 200, 300],
    ["trace_ascent", "projectile__trajectory_ascent", 0.5, 300, 200],
    ["trace_ascent", "projectile__trajectory_ascent", 0.75, 400, 150],
    ["trace_descent", "projectile__trajectory_descent", 0.25, 500, 150],
    ["trace_descent", "projectile__trajectory_descent", 0.5, 600, 200],
    ["trace_descent", "projectile__trajectory_descent", 0.75, 700, 300],
  ];
  const traceTipSamples = traceSampleSpecs.map(
    ([checkpointId, pathId, localProgress, x, y]) => ({
      checkpointId,
      pathId,
      localProgress,
      dashArray: 100,
      dashOffset: 100 * (1 - localProgress),
      revealedProgress: localProgress,
      markerCss: { x, y },
      traceTipCss: { x, y },
      errorCssPx: 0,
    }),
  );
  const categories = [
    "path_trace",
    "marker_motion",
    "vector_morph",
    "focus",
    "equation_morph",
    "hold",
  ];
  const mainIndexByCategory = {
    path_trace: 2,
    marker_motion: 2,
    focus: 2,
    equation_morph: 4,
    hold: 0,
  };
  const pathNode = (record, id) => {
    const node = record.scene.nodes.find((candidate) => candidate.id === id);
    assert.equal(node?.kind, "path");
    return node;
  };
  const pathData = ({ points, closed }) =>
    `${points
      .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`)
      .join(" ")}${closed ? " Z" : ""}`;
  const interpolatedPathData = (before, target, progress) =>
    pathData({
      closed: before.closed,
      points: before.points.map(([x, y], index) => [
        x + (target.points[index][0] - x) * progress,
        y + (target.points[index][1] - y) * progress,
      ]),
    });
  const markerOriginNode = pathNode(
    records.main[1],
    "projectile__projectile_marker",
  );
  const markerXs = markerOriginNode.points.map(([x]) => x);
  const markerYs = markerOriginNode.points.map(([, y]) => y);
  const markerOrigin = [
    (Math.min(...markerXs) + Math.max(...markerXs)) / 2,
    (Math.min(...markerYs) + Math.max(...markerYs)) / 2,
  ];
  const trajectory = pathNode(records.main[2], "projectile__trajectory_ascent");
  const markerPoints = [trajectory.points[8], trajectory.points[16]].map(
    ([x, y]) => ({
      x: Number((x - markerOrigin[0]).toFixed(3)),
      y: Number((y - markerOrigin[1]).toFixed(3)),
    }),
  );
  const vectorBefore = pathNode(
    records.vector.at(-2),
    "projectile__velocity_resultant",
  );
  const vectorTarget = pathNode(
    records.vector.at(-1),
    "projectile__velocity_resultant",
  );
  const focusPresentation = records.main[2].event.semantic.presentation;
  const viewBox = ({ x, y, width, height }) => `${x} ${y} ${width} ${height}`;
  const viewBoxValues = ({ x, y, width, height }) => [x, y, width, height];
  const focusBefore = viewBoxValues(focusPresentation.baseViewports.cinematic);
  const focusTarget = viewBoxValues(
    focusPresentation.resultViewports.cinematic,
  );
  const activeSurface = {
    path_trace: {
      kind: "path_trace",
      checkpointId: "trace_ascent",
      targetId: "projectile__trajectory_ascent",
      dashArray: 100,
      dashOffset: 50,
    },
    marker_motion: {
      kind: "marker_motion",
      checkpointId: "trace_ascent",
      targetId: "projectile__projectile_marker",
      firstTransform: `translate(${markerPoints[0].x} ${markerPoints[0].y})`,
      secondTransform: `translate(${markerPoints[1].x} ${markerPoints[1].y})`,
      firstPoint: markerPoints[0],
      secondPoint: markerPoints[1],
      displacementSvgUnits: Number(
        Math.hypot(
          markerPoints[1].x - markerPoints[0].x,
          markerPoints[1].y - markerPoints[0].y,
        ).toFixed(3),
      ),
    },
    vector_morph: {
      kind: "vector_morph",
      checkpointId: "parameters_retargeted",
      targetId: "projectile__velocity_resultant",
      beforePathD: pathData(vectorBefore),
      firstActivePathD: interpolatedPathData(vectorBefore, vectorTarget, 0.25),
      secondActivePathD: interpolatedPathData(vectorBefore, vectorTarget, 0.5),
      targetPathD: pathData(vectorTarget),
    },
    focus: {
      kind: "focus",
      checkpointId: "trace_ascent",
      targetId: "live-choreography-board",
      beforeViewBox: viewBox(focusPresentation.baseViewports.cinematic),
      activeViewBox: focusBefore
        .map((value, index) => (value + focusTarget[index]) / 2)
        .join(" "),
      targetViewBox: viewBox(focusPresentation.resultViewports.cinematic),
    },
    equation_morph: {
      kind: "equation_morph",
      checkpointId: "trace_descent",
      targetId: "projectile__vertical_state",
      opacity: 0.5,
    },
    hold: {
      kind: "hold",
      checkpointId: "setup",
      targetId: "projectile__velocity_resultant",
      dashArray: 100,
      dashOffset: 0,
      settledMainCount: 0,
    },
  };
  const trials = categories.flatMap((category, categoryIndex) =>
    Array.from({ length: 4 }, (_, repetition) => {
      const ordinal = categoryIndex * 4 + repetition + 1;
      const isVector = category === "vector_morph";
      const prefix = isVector
        ? records.vector
        : records.main.slice(0, mainIndexByCategory[category] + 1);
      const immediate = frontier(
        prefix.at(-1),
        prefix,
        isVector ? 4 : 1,
        "interrupted",
        prefix.length - 1,
      );
      const terminal = isVector
        ? structuredClone(immediate)
        : frontier(
            records.main.at(-1),
            records.main,
            2,
            "completed",
            mainIndexByCategory[category],
            mainIndexByCategory[category] + 1,
          );
      const requestedAtMs = ordinal * 10_000;
      const settledAtMs = requestedAtMs + 40 + repetition;
      return {
        ordinal,
        category,
        requestedAtMs,
        settledAtMs,
        staleObservedAtMs: settledAtMs + 2_000,
        staleDomMutationCount: 0,
        staleRuntimePublicationCount: 0,
        activeSurface: structuredClone(activeSurface[category]),
        immediate,
        afterStaleWindow: structuredClone(immediate),
        terminal,
      };
    }),
  );
  const terminalSemanticDom = semanticDom(records.main.at(-1));
  return {
    v: 1,
    gate: "1.7",
    execution: { source: SOURCE, environment: ENVIRONMENT },
    firstMeaningful: {
      samplesMs: Array.from({ length: 20 }, (_, index) => 80 + index),
      p95Ms: 98,
      thresholdExclusiveMs: 300,
    },
    motionBoundary: {
      traceTipSamples,
      canonicalTerminal: {
        animatedSemanticDom: structuredClone(terminalSemanticDom),
        reducedMotionSemanticDom: structuredClone(terminalSemanticDom),
        replaySemanticDom: structuredClone(terminalSemanticDom),
        replayProviderRequestCount: 0,
      },
      interruption: {
        categories,
        repetitionsPerCategory: 4,
        staleWindowMs: 2_000,
        thresholdExclusiveMs: 150,
        p95Ms: 43,
        trials,
      },
    },
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
  await writeFile(
    path.join(root, "accelerated/observations.json"),
    `${JSON.stringify(acceleratedObservation(primaryFixture), null, 2)}\n`,
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

    const wrongPath = structuredClone(observation);
    wrongPath.runtime.evidence.pathGeometrySamples[0].pathD =
      wrongPath.runtime.evidence.pathGeometrySamples[0].pathD.replace(
        /L ([-\d.]+) ([-\d.]+)/,
        (_, x, y) => `L ${Number(x) + 1} ${y}`,
      );
    wrongPath.runtime.evidenceSha256 = sha256(
      Buffer.from(JSON.stringify(wrongPath.runtime.evidence)),
    );
    assert.throws(
      () => validateCaptureObservationForTests(wrongPath, primaryFixture),
      /pathD vertices/,
    );

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

test("accelerated evidence binds motion geometry, terminal parity, and 24 interruption trials", async () => {
  const fixtures = await fixtureCatalogForTests();
  const primaryFixture = fixtures.find(
    ({ fixtureId }) => fixtureId === "projectile-motion-v20-a45",
  );
  assert.ok(primaryFixture);
  const observation = acceleratedObservation(primaryFixture);
  const validated = validateAcceleratedObservationForTests(
    observation,
    primaryFixture,
  );
  assert.equal(validated.motionBoundary.traceTipSamples.length, 6);
  assert.equal(validated.motionBoundary.interruption.trials.length, 24);
  assert.equal(validated.motionBoundary.interruption.p95Ms, 43);
  const retargetGuide =
    observation.motionBoundary.interruption.trials[8].immediate.payload.semanticDomProjection.nodes.find(
      ({ id }) => id === "projectile__apex_guide",
    );
  const rawRetargetGuide = fixtureRecords()
    .vector.at(-1)
    .scene.nodes.find(({ id }) => id === "projectile__apex_guide");
  assert.deepEqual(
    retargetGuide.points,
    rawRetargetGuide.points.map(([x, y]) => [
      Number(x.toFixed(6)),
      Number(y.toFixed(6)),
    ]),
  );

  const oneUlpBrowserEvidence = structuredClone(observation);
  shiftFixturePathCoordinates(oneUlpBrowserEvidence);
  assert.doesNotThrow(() =>
    validateAcceleratedObservationForTests(
      oneUlpBrowserEvidence,
      primaryFixture,
    ),
  );

  const tamperCases = [
    [
      (value) => {
        const after =
          value.motionBoundary.interruption.trials[0].afterStaleWindow;
        shiftFixturePathCoordinates(after);
      },
      /stale-window frontier/,
    ],
    [
      (value) =>
        (value.motionBoundary.interruption.categories[0] = "trace_marker"),
      /categories/,
    ],
    [
      (value) => {
        value.motionBoundary.traceTipSamples[0].markerCss.x = 202;
        value.motionBoundary.traceTipSamples[0].errorCssPx = 2;
      },
      /at most one CSS pixel/,
    ],
    [
      (value) =>
        (value.motionBoundary.canonicalTerminal.replaySemanticDom.viewBox =
          "0 0 1 1"),
      /replaySemanticDom/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials.slice(-2).forEach((trial) => {
          trial.settledAtMs = trial.requestedAtMs + 150;
          trial.staleObservedAtMs = trial.settledAtMs + 2_000;
        });
        value.motionBoundary.interruption.p95Ms = 150;
      },
      /below 150ms/,
    ],
    [
      (value) => {
        const trial = value.motionBoundary.interruption.trials[0];
        trial.settledAtMs = trial.requestedAtMs - 1;
      },
      /settledAtMs/,
    ],
    [
      (value) => {
        const trial = value.motionBoundary.interruption.trials[0];
        trial.staleObservedAtMs = trial.settledAtMs + 1_999;
      },
      /at least 2000ms/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].staleDomMutationCount = 1;
      },
      /staleDomMutationCount/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].staleRuntimePublicationCount = 1;
      },
      /staleRuntimePublicationCount/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].immediate.revision = 99;
      },
      /revision/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].immediate.certificateHeadSha256 =
          "f".repeat(64);
      },
      /certificateHeadSha256/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].immediate.payload.accepted.at(
          -1,
        ).event.sequence = 99;
      },
      /sequence/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].terminal.payload.accepted[3].event.generation = 1;
      },
      /generation/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].immediate.payload.accepted.at(
          -1,
        ).presentation.settlement = "completed";
      },
      /settlement/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[0].immediate.payload.accepted.at(
          -1,
        ).presentation.type = "fabricated";
      },
      /presentation\.type/,
    ],
    [
      (value) => {
        value.motionBoundary.interruption.trials[4].activeSurface.secondPoint.x += 1;
      },
      /secondPoint\.x/,
    ],
    [
      (value) => {
        const surface =
          value.motionBoundary.interruption.trials[8].activeSurface;
        [surface.firstActivePathD, surface.secondActivePathD] = [
          surface.secondActivePathD,
          surface.firstActivePathD,
        ];
      },
      /distinct and ordered/,
    ],
    [
      (value) => {
        const surface =
          value.motionBoundary.interruption.trials[12].activeSurface;
        const parts = surface.activeViewBox.split(" ");
        parts[0] = String(Number(parts[0]) + 1);
        surface.activeViewBox = parts.join(" ");
      },
      /activeViewBox/,
    ],
    [
      (value) => shiftFixturePathCoordinates(value, 4),
      /scaled Number\.EPSILON/,
    ],
  ];
  for (const [tamper, pattern] of tamperCases) {
    const candidate = structuredClone(observation);
    tamper(candidate);
    assert.throws(
      () => validateAcceleratedObservationForTests(candidate, primaryFixture),
      pattern,
    );
  }
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
    assert.deepEqual(validateRecordingTimingForTests(40_000, 37_000), {
      durationMs: 40_000,
      captureTimingDeltaMs: 3_000,
      maximumShortfallMs: 50,
      maximumOverhangMs: 5_000,
    });
    assert.deepEqual(validateRecordingTimingForTests(36_950, 37_000), {
      durationMs: 36_950,
      captureTimingDeltaMs: -50,
      maximumShortfallMs: 50,
      maximumOverhangMs: 5_000,
    });
    assert.throws(
      () => validateRecordingTimingForTests(36_949, 37_000),
      /no more than 50ms shorter/,
    );
    assert.throws(
      () => validateRecordingTimingForTests(42_001, 37_000),
      /5000ms longer/,
    );

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
    assert.equal(
      finalized.manifest.evidence.motionBoundary.interruption.trials.length,
      24,
    );
    assert.equal(
      finalized.manifest.evidence.motionBoundary.canonicalTerminal
        .animatedSemanticDom.nodes.length,
      24,
    );
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
