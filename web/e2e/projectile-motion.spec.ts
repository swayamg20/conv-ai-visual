import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Browser, type Page } from "@playwright/test";

import fixtureV20A30Value from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a30.v1.json";
import primaryFixtureValue from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json";
import fixtureV20A60Value from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a60.v1.json";
import fixtureV30A45Value from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v30-a45.v1.json";
import fixtureV30A60Value from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v30-a60.v1.json";
import {
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
} from "../src/lib/live-scene/projectile-choreography-stream";
import {
  applyScenePatch,
  decodeScenePatchEvent,
} from "../src/lib/live-scene/patch";
import { createSceneState, type SceneState } from "../src/lib/live-scene";
import {
  expectProjectileSvgMatchesScene,
  inspectProjectileSvgAgainstScene,
  type ProjectileSvgSemanticSignature,
} from "./projectile-motion-dom-oracle";
import {
  PROJECTILE_CLARIFICATION_CASES,
  PROJECTILE_MAIN_CHECKPOINTS,
  PROJECTILE_MOTION_E2E_BRIDGE_KEY,
  attachBoardOnlyScreenshot,
  beginProjectileTraceObservation,
  endProjectileTraceObservation,
  expectNoHorizontalOverflow,
  expectProjectileTerminal,
  expectProviderFree,
  expectStableElement,
  expectedProjectileBoard,
  firstMeaningfulProjectileVisualAt,
  fixtureCheckpoints,
  observeProjectileStage,
  observeProjectilePhysicsSamples,
  observeProviderFreeRequests,
  projectileBoard,
  projectileBridgeState,
  projectileFixture,
  projectileRuntimeObservation,
  projectileStage,
  rememberStableElement,
  selectProjectileProblem,
  selectProjectileTraceTipSamples,
  stopAtVisibleCheckpoint,
  stopDuringActiveTrace,
  waitForActiveTrace,
  waitForProjectileBridge,
  type ProjectileRuntimeObservation,
} from "./projectile-motion-helpers";
import { observeChoreographyExecution } from "./live-choreography-provenance";

const primaryFixture = projectileFixture(primaryFixtureValue);
const qualifiedProjectileFixtures = Object.freeze([
  projectileFixture(fixtureV20A30Value),
  primaryFixture,
  projectileFixture(fixtureV20A60Value),
  projectileFixture(fixtureV30A45Value),
  projectileFixture(fixtureV30A60Value),
]);
const primaryProblem = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 45,
} as const);
const retargetProblem = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 60,
} as const);
const FIRST_MEANINGFUL_SAMPLE_COUNT = 20;
const FIRST_MEANINGFUL_THRESHOLD_MS = 300;
const INTERRUPTION_CATEGORIES = [
  "path_trace",
  "marker_motion",
  "vector_morph",
  "focus",
  "equation_morph",
  "hold",
] as const;
const INTERRUPTION_REPETITIONS_PER_CATEGORY = 4;
const INTERRUPTION_STALE_WINDOW_MS = 2_000;
const INTERRUPTION_THRESHOLD_MS = 150;

type InterruptionCategory = (typeof INTERRUPTION_CATEGORIES)[number];
type RuntimeSnapshot = ProjectileRuntimeObservation["snapshot"];
type AcceptedCheckpoint = RuntimeSnapshot["accepted"][number];
type Settlement = AcceptedCheckpoint["presentation"]["settlement"];

type InterruptionActiveSurface =
  | {
      readonly kind: "path_trace";
      readonly checkpointId: "trace_ascent";
      readonly targetId: "projectile__trajectory_ascent";
      readonly dashArray: number;
      readonly dashOffset: number;
    }
  | {
      readonly kind: "marker_motion";
      readonly checkpointId: "trace_ascent";
      readonly targetId: "projectile__projectile_marker";
      readonly firstTransform: string;
      readonly secondTransform: string;
      readonly firstPoint: { readonly x: number; readonly y: number };
      readonly secondPoint: { readonly x: number; readonly y: number };
      readonly displacementSvgUnits: number;
    }
  | {
      readonly kind: "vector_morph";
      readonly checkpointId: "parameters_retargeted";
      readonly targetId: "projectile__velocity_resultant";
      readonly beforePathD: string;
      readonly firstActivePathD: string;
      readonly secondActivePathD: string;
      readonly targetPathD: string;
    }
  | {
      readonly kind: "focus";
      readonly checkpointId: "trace_ascent";
      readonly targetId: "live-choreography-board";
      readonly beforeViewBox: string;
      readonly activeViewBox: string;
      readonly targetViewBox: string;
    }
  | {
      readonly kind: "equation_morph";
      readonly checkpointId: "trace_descent";
      readonly targetId: "projectile__vertical_state";
      readonly opacity: number;
    }
  | {
      readonly kind: "hold";
      readonly checkpointId: "setup";
      readonly targetId: "projectile__velocity_resultant";
      readonly dashArray: number;
      readonly dashOffset: number;
      readonly settledMainCount: 0;
    };

interface ExpectedAcceptedFrontier {
  readonly records: readonly AcceptedCheckpoint[];
  readonly scene: SceneState;
  readonly semanticScene: RuntimeSnapshot["committedSemanticScene"];
  readonly checkpointId: AcceptedCheckpoint["event"]["semantic"]["checkpointId"];
  readonly caption: string;
  readonly maximumReceivedSequence: number;
}

interface InterruptionPayload {
  readonly semanticDomProjection: ProjectileSvgSemanticSignature;
  readonly caption: string;
  readonly phase: RuntimeSnapshot["phase"];
  readonly generation: number;
  readonly visibleCheckpointId: string;
  readonly committedScene: RuntimeSnapshot["committedScene"];
  readonly committedSemanticScene: RuntimeSnapshot["committedSemanticScene"];
  readonly accepted: RuntimeSnapshot["accepted"];
}

interface InterruptionFrontier {
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly phase: RuntimeSnapshot["phase"];
  readonly checkpointId: string;
  readonly revision: number;
  readonly certificateHeadSha256: string;
  readonly payload: InterruptionPayload;
}

interface InterruptionTrial {
  readonly ordinal: number;
  readonly category: InterruptionCategory;
  readonly requestedAtMs: number;
  readonly settledAtMs: number;
  readonly staleObservedAtMs: number;
  readonly staleDomMutationCount: 0;
  readonly staleRuntimePublicationCount: 0;
  readonly activeSurface: InterruptionActiveSurface;
  readonly immediate: InterruptionFrontier;
  readonly afterStaleWindow: InterruptionFrontier;
  readonly terminal: InterruptionFrontier;
}

interface TriggeredInterruption {
  readonly requestedAtMs: number;
  readonly activeSurface: InterruptionActiveSurface;
}

interface ActiveSurfaceEndpoints {
  readonly beforePathD?: string;
  readonly targetPathD?: string;
  readonly beforeViewBox?: string;
  readonly targetViewBox?: string;
}

interface PreparedInterruptionTrial {
  readonly trigger: TriggeredInterruption;
  readonly interrupted: ExpectedAcceptedFrontier;
  readonly terminal: ExpectedAcceptedFrontier;
  readonly interruptedGeneration: number;
  readonly terminalGeneration: number;
}

async function drawLaunch(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Draw this launch" }).click();
}

async function waitForPresentedElement(
  page: Page,
  checkpointId: string,
  elementId: string,
): Promise<void> {
  await page.waitForFunction(
    ({ checkpoint, id }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const element = stage?.querySelector<SVGGraphicsElement>(
        `[data-element-id="${id}"]`,
      );
      if (stage?.dataset.visibleCheckpointId !== checkpoint || !element) {
        return false;
      }
      const style = getComputedStyle(element);
      return (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity) > 0.02
      );
    },
    { checkpoint: checkpointId, id: elementId },
    { polling: "raf" },
  );
}

async function stopDuringEmphasis(page: Page): Promise<string> {
  const result = await page.waitForFunction(
    () => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const target = stage?.querySelector<SVGGraphicsElement>(
        '[data-element-id="projectile__velocity_resultant"]',
      );
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const match = target?.style.filter.match(/brightness\(([^)]+)\)/);
      const brightness = match ? Number(match[1]) : 1;
      if (
        stage?.dataset.visibleCheckpointId !== "setup" ||
        !Number.isFinite(brightness) ||
        brightness <= 1.005 ||
        !stop
      ) {
        return false;
      }
      const filter = target?.style.filter ?? "";
      stop.click();
      return filter;
    },
    undefined,
    { polling: "raf" },
  );
  const filter = await result.jsonValue();
  if (filter === false) throw new Error("The setup focus cue never animated");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return filter;
}

async function stopDuringHold(page: Page): Promise<{
  readonly dashArray: number;
  readonly dashOffset: number;
}> {
  const result = await page.waitForFunction(
    () => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const target = stage?.querySelector<SVGGraphicsElement>(
        '[data-element-id="projectile__velocity_resultant"]',
      );
      const path = target?.querySelector("path");
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const dashArray = Number(path?.getAttribute("stroke-dasharray"));
      const dashOffset = Number(path?.getAttribute("stroke-dashoffset"));
      if (
        stage?.dataset.visibleCheckpointId !== "setup" ||
        stage.dataset.settledMainCount !== "0" ||
        !Number.isFinite(dashArray) ||
        !Number.isFinite(dashOffset) ||
        dashArray <= 0 ||
        Math.abs(dashOffset) > dashArray * 0.005 ||
        !stop
      ) {
        return false;
      }
      stop.click();
      return { dashArray, dashOffset };
    },
    undefined,
    { polling: "raf", timeout: 15_000 },
  );
  const value = await result.jsonValue();
  if (value === false) throw new Error("The setup hold was not observable");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return value;
}

async function stopDuringTransformAndTrace(page: Page): Promise<{
  readonly beforeOpacity: number;
  readonly interruptedOpacity: number;
  readonly traceDashArray: number;
  readonly traceDashOffset: number;
}> {
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  const beforeOpacity = await projectileBoard(page)
    .locator('[data-element-id="projectile__vertical_state"]')
    .evaluate((element) => Number(getComputedStyle(element).opacity));
  expect(beforeOpacity).toBeGreaterThan(0);

  const result = await page.waitForFunction(
    ({ initialOpacity }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const transformed = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__vertical_state"]',
      );
      const trace = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__trajectory_descent"]',
      );
      const tracePath = trace?.querySelector("path");
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const currentOpacity = Number(
        transformed ? getComputedStyle(transformed).opacity : Number.NaN,
      );
      const traceDashArray = Number(
        tracePath?.getAttribute("stroke-dasharray"),
      );
      const traceDashOffset = Number(
        tracePath?.getAttribute("stroke-dashoffset"),
      );
      if (
        stage?.dataset.visibleCheckpointId !== "trace_descent" ||
        !Number.isFinite(currentOpacity) ||
        currentOpacity <= 0 ||
        currentOpacity >= initialOpacity - 0.005 ||
        !Number.isFinite(traceDashArray) ||
        !Number.isFinite(traceDashOffset) ||
        traceDashArray <= 0 ||
        traceDashOffset <= 0 ||
        traceDashOffset >= traceDashArray ||
        !stop
      ) {
        return false;
      }
      stop.click();
      return {
        beforeOpacity: initialOpacity,
        interruptedOpacity: currentOpacity,
        traceDashArray,
        traceDashOffset,
      };
    },
    { initialOpacity: beforeOpacity },
    { polling: "raf", timeout: 20_000 },
  );
  const value = await result.jsonValue();
  if (value === false) {
    throw new Error("The descent transform and trace never overlapped");
  }
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return value;
}

function rounded(value: number): number {
  return Number(value.toFixed(3));
}

function nearestRankP95(samples: readonly number[]): number {
  if (samples.length === 0) throw new Error("p95 requires at least one sample");
  return [...samples].sort((left, right) => left - right)[
    Math.ceil(samples.length * 0.95) - 1
  ]!;
}

function fixtureMismatch(
  actual: unknown,
  expected: unknown,
  location: string,
  pathCoordinates = false,
): string | null {
  if (typeof actual === "number" && typeof expected === "number") {
    const equal = pathCoordinates
      ? Math.abs(actual - expected) <=
        Number.EPSILON * Math.max(1, Math.abs(actual), Math.abs(expected))
      : Object.is(actual, expected);
    return equal ? null : `${location}: ${actual} !== ${expected}`;
  }
  if (
    actual === null ||
    expected === null ||
    typeof actual !== "object" ||
    typeof expected !== "object"
  ) {
    return Object.is(actual, expected)
      ? null
      : `${location}: ${String(actual)} !== ${String(expected)}`;
  }
  const actualArray = Array.isArray(actual);
  const expectedArray = Array.isArray(expected);
  if (actualArray || expectedArray) {
    if (!actualArray || !expectedArray || actual.length !== expected.length) {
      return `${location}: array shape differs`;
    }
    for (let index = 0; index < actual.length; index += 1) {
      const mismatch = fixtureMismatch(
        actual[index],
        expected[index],
        `${location}[${index}]`,
        pathCoordinates,
      );
      if (mismatch) return mismatch;
    }
    return null;
  }
  const actualRecord = actual as Record<string, unknown>;
  const expectedRecord = expected as Record<string, unknown>;
  const actualKeys = Object.keys(actualRecord);
  const expectedKeys = Object.keys(expectedRecord);
  // The generator writes sorted JSON keys while browser decoders reconstruct
  // contract order. Object order is not data; array order (including SVG paint
  // order) remains exact in the branch above.
  const missingKeys = expectedKeys.filter(
    (key) => !Object.prototype.hasOwnProperty.call(actualRecord, key),
  );
  const unexpectedKeys = actualKeys.filter(
    (key) => !Object.prototype.hasOwnProperty.call(expectedRecord, key),
  );
  if (missingKeys.length > 0 || unexpectedKeys.length > 0) {
    return `${location}: object keys differ (missing: ${missingKeys.join(", ") || "none"}; unexpected: ${unexpectedKeys.join(", ") || "none"})`;
  }
  for (const key of expectedKeys) {
    const mismatch = fixtureMismatch(
      actualRecord[key],
      expectedRecord[key],
      `${location}.${key}`,
      expectedRecord.kind === "path" && key === "points",
    );
    if (mismatch) return mismatch;
  }
  return null;
}

function expectFixtureExact(
  actual: unknown,
  expected: unknown,
  location: string,
): void {
  expect(fixtureMismatch(actual, expected, location)).toBeNull();
}

function pathDataTokens(value: string): readonly (string | number)[] {
  const tokenPattern = /[MLZ]|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi;
  const tokens = value.match(tokenPattern) ?? [];
  const residue = value.replace(tokenPattern, "").replace(/[\s,]/g, "");
  if (residue || tokens.length === 0) {
    throw new Error(`Unsupported projectile path data: ${value}`);
  }
  return tokens.map((token) =>
    /^[MLZ]$/i.test(token) ? token : Number(token),
  );
}

function expectPathDataEquivalent(actual: string, expected: string): void {
  expect(
    fixtureMismatch(
      pathDataTokens(actual),
      pathDataTokens(expected),
      "pathData",
      true,
    ),
  ).toBeNull();
}

function checkpointPatchEvent(
  checkpoint: ProjectileChoreographySceneCheckpointEventV1,
) {
  return decodeScenePatchEvent({
    type: "scene_patch",
    generation: checkpoint.generation,
    attempt: checkpoint.attempt,
    sequence: checkpoint.sequence,
    baseRevision: checkpoint.baseRevision,
    resultRevision: checkpoint.resultRevision,
    patch: checkpoint.patch,
  });
}

function rewriteCheckpointGeneration(
  checkpoint: ProjectileChoreographySceneCheckpointEventV1,
  generation: number,
  sequence: number,
): ProjectileChoreographySceneCheckpointEventV1 {
  const event = decodeProjectileChoreographySceneStreamEventV1({
    ...checkpoint,
    generation,
    sequence,
  });
  if (event.type !== "projectile_choreography_scene_checkpoint") {
    throw new Error("Rewritten projectile fixture event is not a checkpoint");
  }
  return event;
}

function expectedAcceptedFrontier(
  events: readonly ProjectileChoreographySceneCheckpointEventV1[],
  settlements: readonly Settlement[],
  maximumReceivedSequence?: number,
): ExpectedAcceptedFrontier {
  if (events.length === 0 || events.length !== settlements.length) {
    throw new Error("Expected projectile events and settlements must align");
  }
  let scene = createSceneState({ revision: 0, nodes: [] });
  const records = events.map((event, index) => {
    scene = applyScenePatch(scene, checkpointPatchEvent(event));
    const semanticScene = {
      revision: event.semantic.semanticResultRevision,
      components: [event.semantic.resultComponent],
      certificateHeadSha256: event.semantic.semanticResultCertificateSha256,
    } satisfies RuntimeSnapshot["committedSemanticScene"];
    const resultViewport =
      event.semantic.presentation.resultViewports.cinematic;
    return {
      event,
      scene,
      semanticScene,
      viewport: resultViewport,
      layout: "cinematic",
      presentation: {
        type: "projectile_choreography_checkpoint_presented",
        checkpointId: event.semantic.checkpointId,
        certificateSha256: event.semantic.semanticResultCertificateSha256,
        sceneRevision: event.resultRevision,
        semanticRevision: event.semantic.semanticResultRevision,
        layout: "cinematic",
        resultViewport,
        settlement: settlements[index],
      },
    } satisfies AcceptedCheckpoint;
  });
  const terminal = records.at(-1);
  if (!terminal) throw new Error("Expected projectile frontier is empty");
  const maximumSequence = maximumReceivedSequence ?? terminal.event.sequence;
  if (maximumSequence < terminal.event.sequence) {
    throw new Error("Expected projectile receive frontier precedes acceptance");
  }
  return {
    records,
    scene: terminal.scene,
    semanticScene: terminal.semanticScene,
    checkpointId: terminal.event.semantic.checkpointId,
    caption: terminal.event.patch.narration,
    maximumReceivedSequence: maximumSequence,
  };
}

function expectedInterruptedMainFrontier(
  prefixCount: number,
): ExpectedAcceptedFrontier {
  const main = fixtureCheckpoints(primaryFixture, "main");
  const events = main.slice(0, prefixCount);
  const terminalStreamEvent = main.at(-1);
  if (!terminalStreamEvent) {
    throw new Error("The primary fixture has no main checkpoint stream");
  }
  return expectedAcceptedFrontier(
    events,
    events.map((_, index) =>
      index === events.length - 1 ? "cancelled_to_checkpoint" : "completed",
    ),
    terminalStreamEvent.sequence,
  );
}

function expectedContinuedMainFrontier(
  prefixCount: number,
): ExpectedAcceptedFrontier {
  const main = fixtureCheckpoints(primaryFixture, "main");
  const prefix = main.slice(0, prefixCount);
  const suffix = main
    .slice(prefixCount)
    .map((checkpoint, index) =>
      rewriteCheckpointGeneration(checkpoint, 2, index + 1),
    );
  return expectedAcceptedFrontier(
    [...prefix, ...suffix],
    [...prefix, ...suffix].map((_, index) =>
      index === prefix.length - 1 ? "cancelled_to_checkpoint" : "completed",
    ),
  );
}

function expectedVectorRetargetFrontier(): ExpectedAcceptedFrontier {
  const main = fixtureCheckpoints(primaryFixture, "main").slice(0, 4);
  const clarification = fixtureCheckpoints(primaryFixture, "clarifyApex");
  const continuation = fixtureCheckpoints(
    primaryFixture,
    "continueAfterClarification",
  );
  const retarget = fixtureCheckpoints(primaryFixture, "retargetAfterSummary");
  const events = [...main, ...clarification, ...continuation, ...retarget];
  return expectedAcceptedFrontier(
    events,
    events.map((_, index) =>
      index === events.length - 1 ? "cancelled_to_checkpoint" : "completed",
    ),
  );
}

function viewportString(viewport: {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}): string {
  return `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;
}

function scenePathData(scene: SceneState, id: string): string {
  const node = scene.nodes.find((candidate) => candidate.id === id);
  if (!node || node.kind !== "path") {
    throw new Error(`Expected projectile path ${id} is unavailable`);
  }
  return `${node.points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`)
    .join(" ")}${node.closed ? " Z" : ""}`;
}

function acceleratedObservationsPath(testInfo: {
  readonly project: { readonly outputDir: string };
}): string {
  return path.join(
    path.dirname(testInfo.project.outputDir),
    "observations.json",
  );
}

async function expectExactRuntimeFrontier(
  observation: ProjectileRuntimeObservation,
  expected: ExpectedAcceptedFrontier,
  phase: "completed" | "interrupted",
  generation: number,
): Promise<void> {
  const { snapshot } = observation;
  expect(snapshot.phase).toBe(phase);
  expect(snapshot.generation).toBe(generation);
  expect(snapshot.visibleCheckpointId).toBe(expected.checkpointId);
  expect(snapshot.narration).toBe(expected.caption);
  expectFixtureExact(snapshot.committedScene, expected.scene, "committedScene");
  expectFixtureExact(
    snapshot.provisionalScene,
    expected.scene,
    "provisionalScene",
  );
  expectFixtureExact(
    snapshot.committedSemanticScene,
    expected.semanticScene,
    "committedSemanticScene",
  );
  expectFixtureExact(
    snapshot.provisionalSemanticScene,
    expected.semanticScene,
    "provisionalSemanticScene",
  );
  expectFixtureExact(snapshot.accepted, expected.records, "accepted");
  const terminalRecord = expected.records.at(-1);
  if (!terminalRecord) throw new Error("The expected frontier is empty");
  expect(snapshot.attempt).toBe(terminalRecord.event.attempt);
  expect(snapshot.sequence).toBeGreaterThanOrEqual(
    terminalRecord.event.sequence,
  );
  expect(snapshot.sequence).toBeLessThanOrEqual(
    expected.maximumReceivedSequence,
  );
  expectFixtureExact(
    snapshot.committedViewport,
    terminalRecord.viewport,
    "committedViewport",
  );
  expectFixtureExact(
    snapshot.provisionalViewport,
    terminalRecord.viewport,
    "provisionalViewport",
  );
  expect(snapshot.queuedCheckpointCount).toBe(0);
  expect(snapshot.activeRevision).toBeUndefined();
  expect(snapshot.error).toBeUndefined();
  expect(snapshot.decline).toBeUndefined();
  if (phase === "interrupted") {
    expect(snapshot.completion).toBeUndefined();
  } else {
    expect(snapshot.completion).toBeDefined();
    expect(Number.isFinite(snapshot.completion?.firstPatchMs)).toBe(true);
    expect(snapshot.completion?.firstPatchMs).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(snapshot.completion?.totalMs)).toBe(true);
    expect(snapshot.completion?.totalMs).toBeGreaterThanOrEqual(0);
    expect(typeof snapshot.completion?.repaired).toBe("boolean");
  }
  expect(snapshot.rendererTrusted).toBe(true);
}

async function interruptionFrontier(
  page: Page,
  expected: ExpectedAcceptedFrontier,
  phase: "completed" | "interrupted",
  generation: number,
): Promise<InterruptionFrontier> {
  const observation = await projectileRuntimeObservation(page);
  await expectExactRuntimeFrontier(observation, expected, phase, generation);
  const semanticDomProjection = await expectProjectileSvgMatchesScene(
    page,
    expected.scene,
  );
  const stage = await observeProjectileStage(page);
  expect(stage.phase).toBe(phase);
  expect(stage.visibleCheckpointId).toBe(expected.checkpointId);
  expect(stage.caption).toBe(expected.caption);
  const certificateHeadSha256 =
    observation.snapshot.committedSemanticScene.certificateHeadSha256;
  if (!certificateHeadSha256) {
    throw new Error("The accepted projectile frontier has no certificate");
  }
  const payload: InterruptionPayload = {
    semanticDomProjection,
    caption: stage.caption,
    phase: observation.snapshot.phase,
    generation: observation.snapshot.generation,
    visibleCheckpointId: observation.snapshot.visibleCheckpointId ?? "",
    committedScene: observation.snapshot.committedScene,
    committedSemanticScene: observation.snapshot.committedSemanticScene,
    accepted: observation.snapshot.accepted,
  };
  return {
    generation: observation.snapshot.generation,
    attempt: observation.snapshot.attempt,
    sequence: observation.snapshot.sequence,
    phase: observation.snapshot.phase,
    checkpointId: observation.snapshot.visibleCheckpointId ?? "",
    revision: observation.snapshot.committedScene.revision,
    certificateHeadSha256,
    payload,
  };
}

async function rawProjectileSurface(page: Page): Promise<{
  readonly svg: string;
  readonly caption: string;
}> {
  return projectileStage(page).evaluate((stage) => {
    const svg = stage.querySelector("svg");
    const caption = stage.querySelector("figcaption p");
    if (!svg || !caption) {
      throw new Error("The projectile stability surface is incomplete");
    }
    return { svg: svg.outerHTML, caption: caption.outerHTML };
  });
}

async function waitForInterruptedSurface(
  page: Page,
  checkpointId: string,
): Promise<{
  readonly settledAtMs: number;
}> {
  const handle = await page.waitForFunction(
    ({ bridgeKey, checkpoint }) => {
      const bridge = (window as typeof window & Record<string, unknown>)[
        bridgeKey
      ] as
        | {
            getRuntimeObservation(): ProjectileRuntimeObservation | null;
            getRuntimeObservationHistory(): readonly ProjectileRuntimeObservation[];
          }
        | undefined;
      const observation = bridge?.getRuntimeObservation();
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const owner = window as typeof window & Record<string, unknown>;
      const guard = owner.__projectile_stale_guard__ as
        | {
            observer: MutationObserver;
            mutations: { observedAtMs: number; summary: string }[];
            settledAtMs?: number;
            runtimeHistoryLength?: number;
          }
        | undefined;
      if (
        !bridge ||
        !observation ||
        observation.snapshot.phase !== "interrupted" ||
        observation.snapshot.visibleCheckpointId !== checkpoint ||
        stage?.dataset.phase !== "interrupted" ||
        stage.dataset.visibleCheckpointId !== checkpoint ||
        !guard
      ) {
        return false;
      }
      guard.observer.takeRecords();
      guard.mutations.length = 0;
      guard.settledAtMs = performance.now();
      guard.runtimeHistoryLength = bridge.getRuntimeObservationHistory().length;
      return guard.settledAtMs;
    },
    { bridgeKey: PROJECTILE_MOTION_E2E_BRIDGE_KEY, checkpoint: checkpointId },
    { polling: "raf" },
  );
  const settledAtMs = await handle.jsonValue();
  if (settledAtMs === false) {
    throw new Error("The interrupted runtime and DOM never jointly settled");
  }
  return {
    settledAtMs,
  };
}

async function finishStaleWindow(
  page: Page,
  settledAtMs: number,
): Promise<{
  readonly staleObservedAtMs: number;
  readonly staleDomMutationCount: number;
  readonly staleRuntimePublicationCount: number;
  readonly diagnostics: readonly string[];
}> {
  await page.waitForFunction(
    ({ settled, staleWindow }) =>
      performance.now() >= settled + staleWindow + 5,
    { settled: settledAtMs, staleWindow: INTERRUPTION_STALE_WINDOW_MS },
    { polling: "raf" },
  );
  return page.evaluate((bridgeKey) => {
    const owner = window as typeof window & Record<string, unknown>;
    const bridge = owner[bridgeKey] as
      | {
          getRuntimeObservationHistory(): readonly ProjectileRuntimeObservation[];
        }
      | undefined;
    const guard = owner.__projectile_stale_guard__ as
      | {
          observer: MutationObserver;
          mutations: { observedAtMs: number; summary: string }[];
          settledAtMs: number;
          runtimeHistoryLength: number;
        }
      | undefined;
    if (!bridge || !guard) {
      throw new Error("The projectile stale guard is unavailable");
    }
    const observedAtMs = performance.now();
    guard.observer.takeRecords().forEach((record) =>
      guard.mutations.push({
        observedAtMs,
        summary: `${record.type}:${(record.target as Element).nodeName}`,
      }),
    );
    guard.observer.disconnect();
    const diagnostics = guard.mutations.map((record) => record.summary);
    const staleDomMutationCount = diagnostics.length;
    const staleRuntimePublicationCount =
      bridge.getRuntimeObservationHistory().length - guard.runtimeHistoryLength;
    if (staleRuntimePublicationCount > 0) {
      diagnostics.push(`runtime-publications:${staleRuntimePublicationCount}`);
    }
    delete owner.__projectile_stale_guard__;
    return {
      staleObservedAtMs: observedAtMs,
      staleDomMutationCount,
      staleRuntimePublicationCount,
      diagnostics,
    };
  }, PROJECTILE_MOTION_E2E_BRIDGE_KEY);
}

async function createInterruptionTrialPage(browser: Browser, baseURL: string) {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 1_280, height: 720 },
    screen: { width: 1_280, height: 720 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "no-preference",
    serviceWorkers: "block",
  });
  return { context, page: await context.newPage() };
}

async function stopOnActiveSurface(
  page: Page,
  category: InterruptionCategory,
  endpoints: ActiveSurfaceEndpoints = {},
): Promise<TriggeredInterruption> {
  const handle = await page.waitForFunction(
    ({ requestedCategory, expectedEndpoints }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      if (!stage || !stop) return false;
      const commit = (activeSurface: InterruptionActiveSurface) => {
        const owner = window as typeof window & Record<string, unknown>;
        const mutations: { observedAtMs: number; summary: string }[] = [];
        const observer = new MutationObserver((records) => {
          const observedAtMs = performance.now();
          records.forEach((record) =>
            mutations.push({
              observedAtMs,
              summary: `${record.type}:${(record.target as Element).nodeName}`,
            }),
          );
        });
        observer.observe(stage, {
          attributes: true,
          childList: true,
          characterData: true,
          subtree: true,
        });
        owner.__projectile_stale_guard__ = {
          observer,
          mutations,
        };
        const requestedAtMs = performance.now();
        stop.click();
        return { requestedAtMs, activeSurface };
      };
      const dash = (selector: string) => {
        const path = stage.querySelector<SVGPathElement>(selector);
        return {
          dashArray: Number(path?.getAttribute("stroke-dasharray")),
          dashOffset: Number(path?.getAttribute("stroke-dashoffset")),
        };
      };

      if (
        requestedCategory === "path_trace" ||
        requestedCategory === "marker_motion"
      ) {
        if (stage.dataset.visibleCheckpointId !== "trace_ascent") return false;
        const { dashArray, dashOffset } = dash(
          '[data-element-id="projectile__trajectory_ascent"] path',
        );
        if (
          !Number.isFinite(dashArray) ||
          !Number.isFinite(dashOffset) ||
          dashArray <= 0 ||
          dashOffset <= 0 ||
          dashOffset >= dashArray
        ) {
          return false;
        }
        if (requestedCategory === "path_trace") {
          return commit({
            kind: "path_trace",
            checkpointId: "trace_ascent",
            targetId: "projectile__trajectory_ascent",
            dashArray,
            dashOffset,
          });
        }
        const transform =
          stage
            .querySelector<SVGGElement>(
              '[data-element-id="projectile__projectile_marker"]',
            )
            ?.getAttribute("transform") ?? "";
        if (!transform.startsWith("translate(")) return false;
        const match = transform.match(
          /^translate\(\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*[ ,]\s*([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*\)$/i,
        );
        if (!match) return false;
        const point = { x: Number(match[1]), y: Number(match[2]) };
        const owner = window as typeof window & Record<string, unknown>;
        const key = "__projectile_marker_motion_probe__";
        const prior = owner[key] as
          | { readonly transform: string; readonly point: typeof point }
          | undefined;
        owner[key] = { transform, point };
        return !prior ||
          Math.hypot(point.x - prior.point.x, point.y - prior.point.y) <= 0.01
          ? false
          : commit({
              kind: "marker_motion",
              checkpointId: "trace_ascent",
              targetId: "projectile__projectile_marker",
              firstTransform: prior.transform,
              secondTransform: transform,
              firstPoint: prior.point,
              secondPoint: point,
              displacementSvgUnits: Math.hypot(
                point.x - prior.point.x,
                point.y - prior.point.y,
              ),
            });
      }

      if (requestedCategory === "focus") {
        if (stage.dataset.visibleCheckpointId !== "trace_ascent") return false;
        const activeViewBox =
          stage.querySelector("svg")?.getAttribute("viewBox") ?? "";
        const before = expectedEndpoints.beforeViewBox ?? "";
        const target = expectedEndpoints.targetViewBox ?? "";
        const parse = (value: string) =>
          value.trim().split(/[ ,]+/).map(Number);
        const [activeValues, beforeValues, targetValues] = [
          activeViewBox,
          before,
          target,
        ].map(parse);
        const intermediate =
          activeValues.length === 4 &&
          beforeValues.length === 4 &&
          targetValues.length === 4 &&
          activeValues.every((value, index) => {
            const low = Math.min(beforeValues[index], targetValues[index]);
            const high = Math.max(beforeValues[index], targetValues[index]);
            return Number.isFinite(value) && value > low && value < high;
          });
        return intermediate
          ? commit({
              kind: "focus",
              checkpointId: "trace_ascent",
              targetId: "live-choreography-board",
              beforeViewBox: before,
              activeViewBox,
              targetViewBox: target,
            })
          : false;
      }

      if (requestedCategory === "equation_morph") {
        if (stage.dataset.visibleCheckpointId !== "trace_descent") return false;
        const target = stage.querySelector<SVGGElement>(
          '[data-element-id="projectile__vertical_state"]',
        );
        const opacity = Number(
          target ? getComputedStyle(target).opacity : Number.NaN,
        );
        return Number.isFinite(opacity) && opacity > 0.02 && opacity < 0.8
          ? commit({
              kind: "equation_morph",
              checkpointId: "trace_descent",
              targetId: "projectile__vertical_state",
              opacity,
            })
          : false;
      }

      if (requestedCategory === "hold") {
        if (
          stage.dataset.visibleCheckpointId !== "setup" ||
          stage.dataset.settledMainCount !== "0"
        ) {
          return false;
        }
        const { dashArray, dashOffset } = dash(
          '[data-element-id="projectile__velocity_resultant"] path',
        );
        const settled =
          Number.isFinite(dashArray) &&
          Number.isFinite(dashOffset) &&
          dashArray > 0 &&
          Math.abs(dashOffset) <= 0.003;
        if (!settled) return false;
        const owner = window as typeof window & Record<string, unknown>;
        const key = "__projectile_hold_probe__";
        const priorDashOffset = owner[key] as number | undefined;
        if (priorDashOffset === undefined || priorDashOffset !== dashOffset) {
          owner[key] = dashOffset;
          return false;
        }
        return commit({
          kind: "hold",
          checkpointId: "setup",
          targetId: "projectile__velocity_resultant",
          dashArray,
          dashOffset,
          settledMainCount: 0,
        });
      }

      const activePathD =
        stage
          .querySelector<SVGPathElement>(
            '[data-element-id="projectile__velocity_resultant"] path',
          )
          ?.getAttribute("d") ?? "";
      const beforePathD = expectedEndpoints.beforePathD ?? "";
      const targetPathD = expectedEndpoints.targetPathD ?? "";
      const pathTokens = (value: string) => {
        const tokenPattern = /[MLZ]|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi;
        const tokens = value.match(tokenPattern) ?? [];
        const residue = value.replace(tokenPattern, "").replace(/[\s,]/g, "");
        return residue || tokens.length === 0
          ? null
          : tokens.map((token) =>
              /^[MLZ]$/i.test(token) ? token : Number(token),
            );
      };
      const pathsMatch = (left: string, right: string) => {
        const leftTokens = pathTokens(left);
        const rightTokens = pathTokens(right);
        return (
          leftTokens !== null &&
          rightTokens !== null &&
          leftTokens.length === rightTokens.length &&
          leftTokens.every((token, index) => {
            const other = rightTokens[index];
            return typeof token === "number" && typeof other === "number"
              ? Math.abs(token - other) <=
                  Number.EPSILON * Math.max(1, Math.abs(token), Math.abs(other))
              : token === other;
          })
        );
      };
      if (
        stage.dataset.visibleCheckpointId !== "parameters_retargeted" ||
        !activePathD ||
        !beforePathD ||
        !targetPathD ||
        !pathTokens(activePathD) ||
        !pathTokens(beforePathD) ||
        !pathTokens(targetPathD) ||
        pathsMatch(activePathD, beforePathD) ||
        pathsMatch(activePathD, targetPathD)
      ) {
        return false;
      }
      const owner = window as typeof window & Record<string, unknown>;
      const key = "__projectile_vector_morph_probe__";
      const firstActivePathD = owner[key] as string | undefined;
      if (!firstActivePathD) {
        owner[key] = activePathD;
        return false;
      }
      return activePathD !== firstActivePathD
        ? commit({
            kind: "vector_morph",
            checkpointId: "parameters_retargeted",
            targetId: "projectile__velocity_resultant",
            beforePathD,
            firstActivePathD,
            secondActivePathD: activePathD,
            targetPathD,
          })
        : false;
    },
    { requestedCategory: category, expectedEndpoints: endpoints },
    { polling: "raf", timeout: 30_000 },
  );
  const value = (await handle.jsonValue()) as
    | false
    | {
        requestedAtMs: number;
        activeSurface: InterruptionActiveSurface;
      };
  if (value === false) throw new Error(`${category} never became active`);
  const activeSurface = { ...value.activeSurface };
  if (activeSurface.kind === "path_trace" || activeSurface.kind === "hold") {
    activeSurface.dashArray = rounded(activeSurface.dashArray);
    activeSurface.dashOffset = rounded(activeSurface.dashOffset);
  } else if (activeSurface.kind === "marker_motion") {
    activeSurface.firstPoint = {
      x: rounded(activeSurface.firstPoint.x),
      y: rounded(activeSurface.firstPoint.y),
    };
    activeSurface.secondPoint = {
      x: rounded(activeSurface.secondPoint.x),
      y: rounded(activeSurface.secondPoint.y),
    };
    activeSurface.displacementSvgUnits = rounded(
      activeSurface.displacementSvgUnits,
    );
  } else if (activeSurface.kind === "equation_morph") {
    activeSurface.opacity = rounded(activeSurface.opacity);
  }
  return {
    requestedAtMs: value.requestedAtMs,
    activeSurface,
  };
}

function interruptionMainPrefixCount(
  category: Exclude<InterruptionCategory, "vector_morph">,
): number {
  switch (category) {
    case "hold":
      return 1;
    case "focus":
    case "path_trace":
    case "marker_motion":
      return 3;
    case "equation_morph":
      return 5;
  }
}

async function prepareVectorMorphInterruption(
  page: Page,
): Promise<PreparedInterruptionTrial> {
  await page.goto(
    "/e2e/projectile-motion?layout=cinematic&motion=real&flow=adaptive&speed=accelerated&proof=keyframes",
  );
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
    { timeout: 30_000 },
  );
  await stopAtVisibleCheckpoint(page, "apex_state");
  await page
    .getByRole("button", {
      name: "At the apex, why is acceleration still down?",
    })
    .click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "clarifyApex", "cinematic"),
    { settledMainCount: 4, timeout: 30_000 },
  );
  await page.getByRole("button", { name: "Continue the flight" }).click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "continueAfterClarification",
      "cinematic",
    ),
    { timeout: 30_000 },
  );
  const beforePathD = await projectileBoard(page)
    .locator('[data-element-id="projectile__velocity_resultant"] path')
    .getAttribute("d");
  if (!beforePathD) {
    throw new Error("The pre-retarget velocity vector path is unavailable");
  }
  const canonicalBeforePathD = scenePathData(
    primaryFixture.lanes.continueAfterClarification.expectedTerminal.scene,
    "projectile__velocity_resultant",
  );
  expectPathDataEquivalent(beforePathD, canonicalBeforePathD);
  const targetPathD = scenePathData(
    primaryFixture.lanes.retargetAfterSummary.expectedTerminal.scene,
    "projectile__velocity_resultant",
  );
  await page
    .getByRole("button", { name: "Set launch angle to 60 degrees" })
    .click();
  await page.getByRole("button", { name: "Morph to 20 m/s · 60°" }).click();
  const trigger = await stopOnActiveSurface(page, "vector_morph", {
    beforePathD: canonicalBeforePathD,
    targetPathD,
  });
  const interrupted = expectedVectorRetargetFrontier();
  return {
    trigger,
    interrupted,
    terminal: interrupted,
    interruptedGeneration: 4,
    terminalGeneration: 4,
  };
}

async function prepareMainInterruption(
  page: Page,
  category: Exclude<InterruptionCategory, "vector_morph">,
): Promise<PreparedInterruptionTrial> {
  const speed = category === "hold" ? "normal" : "accelerated";
  await page.goto(
    `/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=${speed}&proof=keyframes`,
  );
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  let endpoints: ActiveSurfaceEndpoints | undefined;
  if (category === "focus") {
    const traceAscent = fixtureCheckpoints(primaryFixture, "main")[2];
    if (!traceAscent) throw new Error("The trace-ascent fixture is missing");
    endpoints = {
      beforeViewBox: viewportString(
        traceAscent.semantic.presentation.baseViewports.cinematic,
      ),
      targetViewBox: viewportString(
        traceAscent.semantic.presentation.resultViewports.cinematic,
      ),
    };
  }
  const trigger = await stopOnActiveSurface(page, category, endpoints);
  const prefixCount = interruptionMainPrefixCount(category);
  return {
    trigger,
    interrupted: expectedInterruptedMainFrontier(prefixCount),
    terminal: expectedContinuedMainFrontier(prefixCount),
    interruptedGeneration: 1,
    terminalGeneration: 2,
  };
}

async function runInterruptionTrial(
  browser: Browser,
  baseURL: string,
  category: InterruptionCategory,
  ordinal: number,
): Promise<InterruptionTrial> {
  const { context, page } = await createInterruptionTrialPage(browser, baseURL);
  const requests = observeProviderFreeRequests(page);
  try {
    const prepared =
      category === "vector_morph"
        ? await prepareVectorMorphInterruption(page)
        : await prepareMainInterruption(page, category);
    expect(prepared.trigger.activeSurface.kind).toBe(category);
    const settled = await waitForInterruptedSurface(
      page,
      prepared.interrupted.checkpointId,
    );
    const requestedAtMs = rounded(prepared.trigger.requestedAtMs);
    const settledAtMs = rounded(settled.settledAtMs);
    expect(settledAtMs - requestedAtMs).toBeGreaterThanOrEqual(0);
    const immediate = await interruptionFrontier(
      page,
      prepared.interrupted,
      "interrupted",
      prepared.interruptedGeneration,
    );
    const immediateRaw = await rawProjectileSurface(page);
    const stale = await finishStaleWindow(page, settled.settledAtMs);
    const staleObservedAtMs = rounded(stale.staleObservedAtMs);
    expect(staleObservedAtMs - settledAtMs).toBeGreaterThanOrEqual(
      INTERRUPTION_STALE_WINDOW_MS,
    );
    expect(stale.staleDomMutationCount, stale.diagnostics.join(", ")).toBe(0);
    expect(
      stale.staleRuntimePublicationCount,
      stale.diagnostics.join(", "),
    ).toBe(0);
    const afterStaleWindow = await interruptionFrontier(
      page,
      prepared.interrupted,
      "interrupted",
      prepared.interruptedGeneration,
    );
    expect(afterStaleWindow).toEqual(immediate);
    expect(await rawProjectileSurface(page)).toEqual(immediateRaw);

    let terminal: InterruptionFrontier;
    if (category === "vector_morph") {
      terminal = afterStaleWindow;
    } else {
      await page.getByRole("button", { name: "Continue the flight" }).click();
      await expectProjectileTerminal(
        page,
        expectedProjectileBoard(primaryFixture, "main", "cinematic"),
        { timeout: 90_000 },
      );
      terminal = await interruptionFrontier(
        page,
        prepared.terminal,
        "completed",
        prepared.terminalGeneration,
      );
    }
    expectProviderFree(requests);
    return {
      ordinal,
      category,
      requestedAtMs,
      settledAtMs,
      staleObservedAtMs,
      staleDomMutationCount: 0,
      staleRuntimePublicationCount: 0,
      activeSurface: prepared.trigger.activeSurface,
      immediate,
      afterStaleWindow,
      terminal,
    };
  } finally {
    await context.close();
  }
}

test("records twenty fresh click-to-first-ink samples below the local 300 ms p95 boundary", async ({
  browser,
  baseURL,
}, testInfo) => {
  if (!baseURL)
    throw new Error("The projectile browser base URL is unavailable");
  const execution = observeChoreographyExecution(testInfo.config, browser);
  const samplesMs: number[] = [];

  for (let index = 0; index < FIRST_MEANINGFUL_SAMPLE_COUNT; index += 1) {
    const context = await browser.newContext({
      baseURL,
      viewport: { width: 1_280, height: 720 },
      screen: { width: 1_280, height: 720 },
      deviceScaleFactor: 1,
      colorScheme: "dark",
      locale: "en-US",
      timezoneId: "UTC",
      reducedMotion: "no-preference",
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    try {
      await page.goto(
        "/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=accelerated&proof=none",
        { waitUntil: "domcontentloaded" },
      );
      await waitForProjectileBridge(page);
      const startedAtMs = await page
        .getByRole("button", { name: "Draw this launch" })
        .evaluate((button) => {
          const startedAt = performance.now();
          (button as HTMLButtonElement).click();
          return startedAt;
        });
      const firstVisibleAtMs = await firstMeaningfulProjectileVisualAt(page);
      const sample = rounded(firstVisibleAtMs - startedAtMs);
      expect(sample).toBeGreaterThanOrEqual(0);
      samplesMs.push(sample);
    } finally {
      await context.close();
    }
  }

  const p95Ms = rounded(nearestRankP95(samplesMs));
  expect(samplesMs).toHaveLength(FIRST_MEANINGFUL_SAMPLE_COUNT);
  expect(p95Ms).toBeLessThan(FIRST_MEANINGFUL_THRESHOLD_MS);
  const observationsPath = acceleratedObservationsPath(testInfo);
  await mkdir(path.dirname(observationsPath), { recursive: true });
  await writeFile(
    observationsPath,
    `${JSON.stringify(
      {
        v: 1,
        gate: "1.7",
        execution,
        firstMeaningful: {
          samplesMs,
          p95Ms,
          thresholdExclusiveMs: FIRST_MEANINGFUL_THRESHOLD_MS,
        },
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  await testInfo.attach("projectile-motion-first-meaningful-observations", {
    path: observationsPath,
    contentType: "application/json",
  });
});

test.describe("rendered formula containment across qualified launches", () => {
  for (const fixture of qualifiedProjectileFixtures) {
    const { speedMps, angleDeg } = fixture.problemSpec;

    test(`${speedMps} m/s at ${angleDeg} degrees contains every main checkpoint`, async ({
      page,
    }) => {
      const requests = observeProviderFreeRequests(page);
      await page.goto(
        "/e2e/projectile-motion?layout=cinematic&motion=reduced&flow=main&speed=accelerated&proof=keyframes",
      );
      await waitForProjectileBridge(page);
      await selectProjectileProblem(page, fixture.problemSpec);

      const checkpoints = fixtureCheckpoints(fixture, "main");
      expect(checkpoints).toHaveLength(PROJECTILE_MAIN_CHECKPOINTS.length);
      let expectedScene = createSceneState({ revision: 0, nodes: [] });
      await drawLaunch(page);

      for (const [index, checkpoint] of checkpoints.entries()) {
        expectedScene = applyScenePatch(
          expectedScene,
          checkpointPatchEvent(checkpoint),
        );
        await expect(projectileStage(page)).toHaveAttribute(
          "data-settled-main-count",
          String(index + 1),
        );
        await expect(projectileStage(page)).toHaveAttribute(
          "data-visible-checkpoint-id",
          checkpoint.semantic.checkpointId,
        );
        await expectProjectileSvgMatchesScene(page, expectedScene);
      }

      await expectProjectileTerminal(
        page,
        expectedProjectileBoard(fixture, "main", "cinematic"),
      );
      expectProviderFree(requests);
    });
  }
});

test("the rendered formula oracle rejects internal glyph overflow without an authored geometry change", async ({
  page,
}) => {
  await page.goto(
    "/e2e/projectile-motion?layout=cinematic&motion=reduced&flow=main&speed=accelerated&proof=none",
  );
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "main", "cinematic"),
  );
  await projectileBoard(page)
    .locator('[data-element-id="projectile__summary"] .katex-html')
    .evaluate((element) => {
      (element as HTMLElement).style.fontSize = "400%";
    });

  const inspection = await inspectProjectileSvgAgainstScene(
    page,
    primaryFixture.lanes.main.expectedTerminal.scene,
  );
  expect(inspection.mismatches).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        code: "latex_content_bounds",
        nodeId: "projectile__summary",
      }),
    ]),
  );
});

test("the certified main flow draws a curved trace with one stable projectile marker and no provider", async ({
  page,
  browser,
  baseURL,
}, testInfo) => {
  if (!baseURL)
    throw new Error("The projectile browser base URL is unavailable");
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?proof=keyframes");
  await waitForProjectileBridge(page);

  await expect(
    page.getByRole("heading", { name: "Projectile motion studio" }),
  ).toBeVisible();
  for (const speed of [20, 25, 30]) {
    await expect(
      page.getByRole("button", {
        name: `Set launch speed to ${speed} metres per second`,
      }),
    ).toHaveAttribute("aria-pressed", String(speed === 20));
  }
  for (const angle of [30, 45, 60]) {
    await expect(
      page.getByRole("button", {
        name: `Set launch angle to ${angle} degrees`,
      }),
    ).toHaveAttribute("aria-pressed", String(angle === 45));
  }

  await beginProjectileTraceObservation(page);
  await drawLaunch(page);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "1",
  );
  await rememberStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );

  const trace = await waitForActiveTrace(page, "trace_ascent");
  expect(trace.dashOffset).toBeGreaterThan(0);
  expect(trace.dashOffset).toBeLessThan(trace.dashArray);
  expect(trace.markerTransform).toMatch(/^translate\(/);
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );
  const curvePath = projectileBoard(page).locator(
    '[data-element-id="projectile__trajectory_ascent"] path',
  );
  await expect(curvePath).toHaveCount(1);
  const curveGeometry = await curvePath.evaluate((element) => {
    const path = element as SVGPathElement;
    const totalLength = path.getTotalLength();
    const start = path.getPointAtLength(0);
    const midpoint = path.getPointAtLength(totalLength / 2);
    const end = path.getPointAtLength(totalLength);
    return {
      totalLength,
      twiceTriangleArea: Math.abs(
        (end.x - start.x) * (midpoint.y - start.y) -
          (end.y - start.y) * (midpoint.x - start.x),
      ),
    };
  });
  expect(curveGeometry.totalLength).toBeGreaterThan(0);
  expect(curveGeometry.twiceTriangleArea).toBeGreaterThan(10);

  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  await expectProjectileTerminal(page, expected);
  const semanticDom = await expectProjectileSvgMatchesScene(
    page,
    primaryFixture.lanes.main.expectedTerminal.scene,
  );
  expect(semanticDom.sourceRevision).toBe(expected.revision);
  expect(semanticDom.paintOrder).toEqual(expected.nodeIds);
  expect(semanticDom.residueFree).toBe(true);
  const traceTipSamples = selectProjectileTraceTipSamples(
    await endProjectileTraceObservation(page),
  );
  expect(traceTipSamples).toHaveLength(6);
  for (const sample of traceTipSamples) {
    expect(
      Math.abs(sample.revealedProgress - sample.localProgress),
    ).toBeLessThanOrEqual(0.035);
    expect(sample.errorCssPx).toBeLessThanOrEqual(1);
  }
  const physicsSamples = await observeProjectilePhysicsSamples(page);
  expect(physicsSamples).toHaveLength(3);
  for (const sample of physicsSamples) {
    expect(sample.errorCssPx).toBeLessThanOrEqual(1);
  }

  const terminalCheckpoint = fixtureCheckpoints(primaryFixture, "main").at(-1);
  if (!terminalCheckpoint) {
    throw new Error("The primary fixture has no terminal checkpoint");
  }
  const runtimeBeforeReplay = await projectileRuntimeObservation(page);
  expect(runtimeBeforeReplay.snapshot.phase).toBe("completed");
  expectFixtureExact(
    runtimeBeforeReplay.snapshot.committedScene,
    primaryFixture.lanes.main.expectedTerminal.scene,
    "terminal committedScene",
  );
  expectFixtureExact(
    runtimeBeforeReplay.snapshot.committedSemanticScene,
    {
      revision: terminalCheckpoint.semantic.semanticResultRevision,
      components: [terminalCheckpoint.semantic.resultComponent],
      certificateHeadSha256:
        terminalCheckpoint.semantic.semanticResultCertificateSha256,
    },
    "terminal committedSemanticScene",
  );
  expect(runtimeBeforeReplay.snapshot.accepted).toHaveLength(6);
  expect(
    runtimeBeforeReplay.snapshot.accepted.map(
      ({ event }) => event.semantic.checkpointId,
    ),
  ).toEqual(PROJECTILE_MAIN_CHECKPOINTS);
  expectFixtureExact(
    runtimeBeforeReplay.snapshot.accepted.map(({ event }) => event),
    fixtureCheckpoints(primaryFixture, "main"),
    "terminal accepted events",
  );

  const bridgeBeforeReplay = await projectileBridgeState(page);
  const replayProviderRequestsBefore = requests.liveSceneRequests.length;
  await page.getByRole("button", { name: "Replay" }).click();
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "replaying",
  );
  await expectProjectileTerminal(page, expected);
  const replaySemanticDom = await expectProjectileSvgMatchesScene(
    page,
    primaryFixture.lanes.main.expectedTerminal.scene,
  );
  const runtimeAfterReplay = await projectileRuntimeObservation(page);
  expect(runtimeAfterReplay.snapshot.committedScene).toEqual(
    runtimeBeforeReplay.snapshot.committedScene,
  );
  expect(runtimeAfterReplay.snapshot.committedSemanticScene).toEqual(
    runtimeBeforeReplay.snapshot.committedSemanticScene,
  );
  expect(runtimeAfterReplay.snapshot.accepted).toEqual(
    runtimeBeforeReplay.snapshot.accepted,
  );
  expect(await projectileBridgeState(page)).toEqual(bridgeBeforeReplay);
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );
  const replayProviderRequestCount =
    requests.liveSceneRequests.length - replayProviderRequestsBefore;
  expect(replayProviderRequestCount).toBe(0);

  const reducedContext = await browser.newContext({
    baseURL,
    viewport: { width: 1_280, height: 720 },
    screen: { width: 1_280, height: 720 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const reducedPage = await reducedContext.newPage();
  const reducedRequests = observeProviderFreeRequests(reducedPage);
  let reducedSemanticDom: Awaited<
    ReturnType<typeof expectProjectileSvgMatchesScene>
  >;
  try {
    await reducedPage.goto(
      "/e2e/projectile-motion?layout=cinematic&motion=reduced&flow=main&speed=accelerated&proof=none",
    );
    await waitForProjectileBridge(reducedPage);
    await drawLaunch(reducedPage);
    await expectProjectileTerminal(reducedPage, expected);
    reducedSemanticDom = await expectProjectileSvgMatchesScene(
      reducedPage,
      primaryFixture.lanes.main.expectedTerminal.scene,
    );
    const reducedRuntime = await projectileRuntimeObservation(reducedPage);
    expect(reducedRuntime.snapshot.committedScene).toEqual(
      runtimeBeforeReplay.snapshot.committedScene,
    );
    expect(reducedRuntime.snapshot.committedSemanticScene).toEqual(
      runtimeBeforeReplay.snapshot.committedSemanticScene,
    );
    expect(reducedRuntime.snapshot.accepted).toEqual(
      runtimeBeforeReplay.snapshot.accepted,
    );
    expectProviderFree(reducedRequests);
  } finally {
    await reducedContext.close();
  }

  expect(reducedSemanticDom).toEqual(semanticDom);
  expect(replaySemanticDom).toEqual(semanticDom);
  const observationsPath = acceleratedObservationsPath(testInfo);
  const observations = JSON.parse(
    await readFile(observationsPath, "utf8"),
  ) as Record<string, unknown>;
  observations.motionBoundary = {
    traceTipSamples,
    canonicalTerminal: {
      animatedSemanticDom: semanticDom,
      reducedMotionSemanticDom: reducedSemanticDom,
      replaySemanticDom,
      replayProviderRequestCount,
    },
  };
  await writeFile(
    observationsPath,
    `${JSON.stringify(observations, null, 2)}\n`,
    "utf8",
  );
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );
  await attachBoardOnlyScreenshot(
    page,
    testInfo,
    "main-terminal-board-mute-first",
  );

  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 1,
    calls: [
      {
        ordinal: 1,
        generation: 1,
        routingMode: "reflex",
        problemSpec: primaryProblem,
        baseRevision: 0,
        semanticRevision: 0,
        certificateHeadSha256: null,
        checkpointId: null,
        clarifiedTopics: [],
        activeClarification: null,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  });
  expectProviderFree(requests);
});

test.describe("real certified clarification lanes", () => {
  for (const clarification of PROJECTILE_CLARIFICATION_CASES) {
    test(`${clarification.topic} stops at its prerequisite and adds only its one-shot detail`, async ({
      page,
    }) => {
      const requests = observeProviderFreeRequests(page);
      await page.goto("/e2e/projectile-motion?flow=main&proof=keyframes");
      await waitForProjectileBridge(page);
      await drawLaunch(page);

      if (clarification.prerequisite === "trace_descent") {
        await stopDuringActiveTrace(page, "trace_descent");
      } else {
        const evidenceElement =
          clarification.prerequisite === "decompose_velocity"
            ? "projectile__equation_x"
            : "projectile__apex_marker";
        await waitForPresentedElement(
          page,
          clarification.prerequisite,
          evidenceElement,
        );
        await stopAtVisibleCheckpoint(page, clarification.prerequisite);
      }

      await expect(projectileStage(page)).toHaveAttribute(
        "data-settled-main-count",
        String(clarification.settledMainCount),
      );
      await page.getByRole("button", { name: clarification.button }).click();

      const expected = expectedProjectileBoard(
        primaryFixture,
        clarification.lane,
        "cinematic",
      );
      await expectProjectileTerminal(page, expected, {
        settledMainCount: clarification.settledMainCount,
      });
      await expectProjectileSvgMatchesScene(
        page,
        primaryFixture.lanes[clarification.lane].expectedTerminal.scene,
      );
      await expect(page.getByText(clarification.settledLabel)).toBeVisible();
      await expect(
        page.getByRole("button", { name: clarification.button }),
      ).toHaveCount(0);

      const frontier = fixtureCheckpoints(primaryFixture, "main")[
        clarification.settledMainCount - 1
      ];
      if (!frontier) throw new Error("Missing clarification prerequisite");
      expect(await projectileBridgeState(page)).toEqual({
        runnerCallCount: 2,
        calls: [
          expect.objectContaining({
            ordinal: 1,
            generation: 1,
            baseRevision: 0,
            requestedRoute: { intent: "advance", targetStage: "solve" },
          }),
          {
            ordinal: 2,
            generation: 2,
            routingMode: "reflex",
            problemSpec: primaryProblem,
            baseRevision: clarification.settledMainCount,
            semanticRevision: clarification.settledMainCount,
            certificateHeadSha256:
              frontier.semantic.semanticResultCertificateSha256,
            checkpointId: clarification.prerequisite,
            clarifiedTopics: [],
            activeClarification: null,
            requestedRoute: {
              intent: "clarify",
              topic: clarification.topic,
            },
          },
        ],
      });
      expectProviderFree(requests);
    });
  }
});

test("Stop settles one exact trace frontier and Continue consumes only the real remaining suffix", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?proof=keyframes");
  await waitForProjectileBridge(page);
  await drawLaunch(page);

  await stopDuringActiveTrace(page, "trace_ascent");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "3",
  );
  const traceFrontier = fixtureCheckpoints(primaryFixture, "main")[2];
  if (!traceFrontier) throw new Error("Missing trace-ascent fixture frontier");
  await page.getByRole("button", { name: "Continue the flight" }).click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "main", "cinematic"),
  );

  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 2,
    calls: [
      expect.objectContaining({
        ordinal: 1,
        generation: 1,
        baseRevision: 0,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      {
        ordinal: 2,
        generation: 2,
        routingMode: "reflex",
        problemSpec: primaryProblem,
        baseRevision: 3,
        semanticRevision: 3,
        certificateHeadSha256:
          traceFrontier.semantic.semanticResultCertificateSha256,
        checkpointId: "trace_ascent",
        clarifiedTopics: [],
        activeClarification: null,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  });
  expectProviderFree(requests);
});

test("apex clarification continues to summary, retargets in place, and Replay makes zero requests", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?flow=adaptive&proof=keyframes");
  await waitForProjectileBridge(page);
  await drawLaunch(page);

  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    "apex_state",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  await stopAtVisibleCheckpoint(page, "apex_state");
  await page
    .getByRole("button", {
      name: "At the apex, why is acceleration still down?",
    })
    .click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "clarifyApex", "cinematic"),
    { settledMainCount: 4 },
  );

  await page.getByRole("button", { name: "Continue the flight" }).click();
  const summary = await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "continueAfterClarification",
      "cinematic",
    ),
  );
  await rememberStableElement(
    page,
    "projectile__ground",
    "__projectile_ground_before_retarget__",
  );
  await rememberStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_before_retarget__",
  );

  const callsBeforeSelection = await projectileBridgeState(page);
  await page
    .getByRole("button", { name: "Set launch angle to 60 degrees" })
    .click();
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "45",
  );
  expect(await observeProjectileStage(page)).toEqual(summary);
  expect(await projectileBridgeState(page)).toEqual(callsBeforeSelection);
  await expect(
    page.getByRole("button", { name: "Morph to 20 m/s · 60°" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Morph to 20 m/s · 60°" }).click();
  const retargeted = await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "retargetAfterSummary",
      "cinematic",
    ),
  );
  await expectStableElement(
    page,
    "projectile__ground",
    "__projectile_ground_before_retarget__",
  );
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_before_retarget__",
  );
  await expect(page.getByText("apex acceleration explained")).toBeVisible();

  const beforeReplay = await projectileBridgeState(page);
  await page.getByRole("button", { name: "Replay" }).click();
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "replaying",
  );
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "retargetAfterSummary",
      "cinematic",
    ),
  );
  expect(await observeProjectileStage(page)).toEqual(retargeted);
  expect(await projectileBridgeState(page)).toEqual(beforeReplay);

  expect(beforeReplay).toEqual({
    runnerCallCount: 4,
    calls: [
      expect.objectContaining({
        ordinal: 1,
        generation: 1,
        baseRevision: 0,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      expect.objectContaining({
        ordinal: 2,
        generation: 2,
        baseRevision: 4,
        checkpointId: "apex_state",
        requestedRoute: { intent: "clarify", topic: "apex_acceleration" },
      }),
      expect.objectContaining({
        ordinal: 3,
        generation: 3,
        baseRevision: 5,
        checkpointId: "apex_state",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: "apex_acceleration",
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      expect.objectContaining({
        ordinal: 4,
        generation: 4,
        problemSpec: primaryProblem,
        baseRevision: 7,
        checkpointId: "summary",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: null,
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: retargetProblem,
        },
      }),
    ],
  });
  expectProviderFree(requests);
});

test("the apex frontier can retarget directly instead of continuing", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?flow=adaptive");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  await stopAtVisibleCheckpoint(page, "apex_state");
  await page
    .getByRole("button", {
      name: "At the apex, why is acceleration still down?",
    })
    .click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "clarifyApex", "cinematic"),
    { settledMainCount: 4 },
  );
  await selectProjectileProblem(page, retargetProblem);
  await page.getByRole("button", { name: "Morph to 20 m/s · 60°" }).click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "retargetAtApex", "cinematic"),
    { settledMainCount: 4 },
  );
  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 3,
    calls: [
      expect.objectContaining({ generation: 1, baseRevision: 0 }),
      expect.objectContaining({
        generation: 2,
        baseRevision: 4,
        requestedRoute: { intent: "clarify", topic: "apex_acceleration" },
      }),
      expect.objectContaining({
        generation: 3,
        problemSpec: primaryProblem,
        baseRevision: 5,
        checkpointId: "apex_state",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: "apex_acceleration",
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: retargetProblem,
        },
      }),
    ],
  });
  expectProviderFree(requests);
});

test.describe("interruption phase boundaries", () => {
  test("six active surfaces settle within 150 ms and stay exact for two seconds", async ({
    browser,
    baseURL,
  }, testInfo) => {
    testInfo.setTimeout(900_000);
    if (!baseURL) {
      throw new Error("The projectile browser base URL is unavailable");
    }
    const trials: InterruptionTrial[] = [];
    for (const category of INTERRUPTION_CATEGORIES) {
      for (
        let repetition = 0;
        repetition < INTERRUPTION_REPETITIONS_PER_CATEGORY;
        repetition += 1
      ) {
        trials.push(
          await runInterruptionTrial(
            browser,
            baseURL,
            category,
            trials.length + 1,
          ),
        );
      }
    }
    const p95Ms = rounded(
      nearestRankP95(
        trials.map(({ requestedAtMs, settledAtMs }) =>
          rounded(settledAtMs - requestedAtMs),
        ),
      ),
    );
    expect(trials).toHaveLength(
      INTERRUPTION_CATEGORIES.length * INTERRUPTION_REPETITIONS_PER_CATEGORY,
    );
    expect(p95Ms).toBeLessThan(INTERRUPTION_THRESHOLD_MS);

    const observationsPath = acceleratedObservationsPath(testInfo);
    const observations = JSON.parse(
      await readFile(observationsPath, "utf8"),
    ) as Record<string, unknown>;
    const motionBoundary = observations.motionBoundary;
    if (
      !motionBoundary ||
      typeof motionBoundary !== "object" ||
      Array.isArray(motionBoundary)
    ) {
      throw new Error("The accelerated motion boundary evidence is missing");
    }
    observations.motionBoundary = {
      ...(motionBoundary as Record<string, unknown>),
      interruption: {
        categories: INTERRUPTION_CATEGORIES,
        repetitionsPerCategory: INTERRUPTION_REPETITIONS_PER_CATEGORY,
        staleWindowMs: INTERRUPTION_STALE_WINDOW_MS,
        thresholdExclusiveMs: INTERRUPTION_THRESHOLD_MS,
        p95Ms,
        trials,
      },
    };
    await writeFile(
      observationsPath,
      `${JSON.stringify(observations, null, 2)}\n`,
      "utf8",
    );
  });

  test("transform and trace interruption preserve the descent target identities", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "2",
    );
    await rememberStableElement(
      page,
      "projectile__vertical_state",
      "__vertical_state_before_transform__",
    );
    const interrupted = await stopDuringTransformAndTrace(page);
    expect(interrupted.interruptedOpacity).toBeLessThan(
      interrupted.beforeOpacity,
    );
    expect(interrupted.traceDashOffset).toBeGreaterThan(0);
    expect(interrupted.traceDashOffset).toBeLessThan(
      interrupted.traceDashArray,
    );
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "5",
    );
    await expectStableElement(
      page,
      "projectile__vertical_state",
      "__vertical_state_before_transform__",
    );
    expectProviderFree(requests);
  });

  test("emphasis interruption settles setup after the first painted cue", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    expect(await stopDuringEmphasis(page)).toMatch(/^brightness\(/);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "1",
    );
    await expect(page.getByText("scene 1", { exact: false })).toBeVisible();
    expectProviderFree(requests);
  });

  test("hold interruption settles setup after motion has visually finished", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes&speed=normal");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    const hold = await stopDuringHold(page);
    expect(Math.abs(hold.dashOffset)).toBeLessThanOrEqual(
      hold.dashArray * 0.005,
    );
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "1",
    );
    await expect(page.getByText("scene 1", { exact: false })).toBeVisible();
    expectProviderFree(requests);
  });
});

test("closed query controls reject invalid values and UI selection cannot mutate the accepted board", async ({
  page,
}) => {
  const invalidPage = await page.context().newPage();
  const invalid = await invalidPage.goto(
    "/e2e/projectile-motion?layout=wide&speed=turbo",
  );
  expect(invalid?.status()).toBe(404);
  const repeated = await invalidPage.goto(
    "/e2e/projectile-motion?layout=compact&layout=cinematic",
  );
  expect(repeated?.status()).toBe(404);
  await invalidPage.close();

  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?motion=reduced");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "main", "cinematic"),
  );
  const before = await observeProjectileStage(page);
  const beforeCalls = await projectileBridgeState(page);
  await page
    .getByRole("button", { name: "Set launch angle to 60 degrees" })
    .click();
  expect(await observeProjectileStage(page)).toEqual(before);
  expect(await projectileBridgeState(page)).toEqual(beforeCalls);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "45",
  );

  await page.getByRole("button", { name: "Reset board" }).click();
  await expect(projectileStage(page)).toHaveAttribute("data-phase", "idle");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    "none",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "none",
  );
  await expect(projectileBoard(page).locator("[data-element-id]")).toHaveCount(
    0,
  );
  expect(await projectileBridgeState(page)).toEqual(beforeCalls);
  expectProviderFree(requests);
});

test.describe("responsive compact proof", () => {
  for (const viewport of [
    { width: 375, height: 812 },
    { width: 320, height: 568 },
  ] as const) {
    test(`${viewport.width}x${viewport.height} keeps controls, board, and caption contained`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize(viewport);
      const requests = observeProviderFreeRequests(page);
      await page.goto("/e2e/projectile-motion?layout=compact&motion=reduced");
      await waitForProjectileBridge(page);
      await drawLaunch(page);
      await expectProjectileTerminal(
        page,
        expectedProjectileBoard(primaryFixture, "main", "compact"),
      );
      await expect(projectileStage(page)).toHaveAttribute(
        "data-layout",
        "compact",
      );
      await expectNoHorizontalOverflow(page);
      const productBounds = await page
        .getByTestId("projectile-choreography-product")
        .boundingBox();
      const stageBounds = await projectileStage(page).boundingBox();
      expect(productBounds).not.toBeNull();
      expect(stageBounds).not.toBeNull();
      expect(productBounds!.x).toBeGreaterThanOrEqual(0);
      expect(productBounds!.x + productBounds!.width).toBeLessThanOrEqual(
        viewport.width,
      );
      expect(stageBounds!.x).toBeGreaterThanOrEqual(0);
      expect(stageBounds!.x + stageBounds!.width).toBeLessThanOrEqual(
        viewport.width,
      );
      if (viewport.width === 320) {
        await attachBoardOnlyScreenshot(
          page,
          testInfo,
          "compact-320x568-terminal-mute-first",
        );
      }
      expectProviderFree(requests);
    });
  }
});

test("reduced motion materializes the same six-checkpoint cinematic terminal", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?motion=reduced");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  const actual = await expectProjectileTerminal(page, expected);
  expect(actual.nodeIds).toEqual(expected.nodeIds);
  expect(actual.caption).toBe(expected.caption);
  expect(actual.viewBox).toBe(expected.viewBox);
  expect(PROJECTILE_MAIN_CHECKPOINTS).toHaveLength(6);
  expect((await projectileBridgeState(page)).runnerCallCount).toBe(1);
  expectProviderFree(requests);
});
