import fs from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import {
  CINEMATIC_CHECKPOINTS,
  COMPACT_CHECKPOINTS,
  MAIN_CHOREOGRAPHY_EVIDENCE,
  acknowledgeCheckpoint,
  assertRetainedDomIdentity,
  choreographyStage,
  expectCinematicCaptionContained,
  interruptCaptureCheckpoint,
  observeSettledCheckpoint,
  readCaptureBridgeState,
  replayAcceptedChoreography,
  waitForCaptureEvidence,
  type ExpectedChoreographyCheckpoint,
  type SettledCheckpointObservation,
} from "./live-choreography-helpers";
import type {
  LiveChoreographyCaptureInterruptResult,
  LiveChoreographyReplayCheckpointObservation,
} from "../src/features/live-scene/live-choreography-demo";
import type { ChoreographyEvidenceTraceEvent } from "../src/features/live-scene/choreography-playback";
import {
  observeChoreographyExecution,
  type ChoreographyEnvironmentObservation,
  type ChoreographySourceObservation,
} from "./live-choreography-provenance";

const CAPTURE_BRIDGE_KEY = "__MURMUR_CHOREOGRAPHY_CAPTURE__";
const CORNER_DETAIL_CAPTION =
  "Both exposed edges measure three, so the missing corner is three by three and its area is nine.";
const CORNER_DETAIL_NODE_IDS = [
  "square-lesson__corner_calc",
  "square-lesson__corner_dim_h",
  "square-lesson__corner_dim_v",
] as const;

interface StageSnapshot {
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
  readonly domIdentity: Readonly<Record<string, number>>;
  readonly canonicalSvg: string;
}

type InterruptionCategory =
  "move" | "token_morph" | "camera_focus" | "emphasis" | "authored_hold";

interface InterruptionTrial {
  readonly label: string;
  readonly category: InterruptionCategory;
  readonly checkpointOrdinal: number;
  readonly cue: "transform" | "focus" | "emphasize" | "hold";
  readonly timing: "motion" | "hold";
}

interface InterruptionCaseObservation {
  readonly label: string;
  readonly category: InterruptionCategory;
  readonly checkpointId: ExpectedChoreographyCheckpoint["checkpointId"];
  readonly sequence: number;
  readonly cue: InterruptionTrial["cue"];
  readonly cueTargetIds: readonly string[];
  readonly timing: InterruptionTrial["timing"];
  readonly authoredDurationMs: number;
  readonly authoredHoldAfterMs: number;
  readonly trigger: LiveChoreographyCaptureInterruptResult["trigger"];
  readonly delayAfterPresentedMs: number;
  readonly activeRevision: number;
  readonly requestedAtMs: number;
  readonly settledAtMs: number;
  readonly settleMs: number;
  readonly target: SettledCheckpointObservation;
  readonly evidenceBefore: readonly ChoreographyEvidenceTraceEvent[];
  readonly evidenceAfter: readonly ChoreographyEvidenceTraceEvent[];
  readonly staleWindowMs: 2_000;
  readonly stabilityBefore: InterruptedStabilitySnapshot;
  readonly stabilityAfter: InterruptedStabilitySnapshot;
  readonly staleStable: true;
}

interface InterruptedStabilitySnapshot {
  readonly stage: StageSnapshot;
  readonly frontier: {
    readonly phase: "interrupted";
    readonly checkpointId: ExpectedChoreographyCheckpoint["checkpointId"];
    readonly settledMainCount: number;
    readonly rendererTrusted: true;
    readonly waitingFor: null;
  };
  readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
}

interface ReplayObservation {
  readonly liveCheckpoints: readonly SettledCheckpointObservation[];
  readonly replayedCheckpoints: readonly LiveChoreographyReplayCheckpointObservation[];
  readonly checkpointIds: readonly string[];
  readonly certificateSha256s: readonly string[];
  readonly liveEvidence: readonly ChoreographyEvidenceTraceEvent[];
  readonly replayEvidence: readonly ChoreographyEvidenceTraceEvent[];
  readonly finalCanonicalSvgMatches: true;
  readonly equivalent: true;
}

interface ProviderFreeRequestObservation {
  readonly liveSceneRequests: readonly string[];
  readonly unexpectedRequests: readonly string[];
}

interface ResponsiveObservation extends ProviderFreeRequestObservation {
  readonly viewport: { readonly width: number; readonly height: number };
  readonly documentWidth: number;
  readonly bodyWidth: number;
  readonly stage: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly finalViewBox: string;
}

interface AcceleratedObservations {
  readonly v: 1;
  source: ChoreographySourceObservation | null;
  environment: ChoreographyEnvironmentObservation | null;
  latency?: ProviderFreeRequestObservation & {
    readonly firstMeaningfulVisualMs: readonly number[];
    readonly p95Ms: number;
  };
  cinematic?: ProviderFreeRequestObservation & {
    readonly checkpoints: readonly SettledCheckpointObservation[];
  };
  reducedMotion?: ProviderFreeRequestObservation & {
    readonly final: SettledCheckpointObservation;
    readonly equivalentToCinematic: boolean;
  };
  responsive: ResponsiveObservation[];
  adaptive?: ProviderFreeRequestObservation & {
    readonly interruptionSettleMsSamples: readonly number[];
    readonly interruptionSettleP95Ms: number;
    readonly interruptionCases: readonly InterruptionCaseObservation[];
    readonly cornerDetailNodeIds: readonly string[];
    readonly replay: ReplayObservation;
    readonly replayEquivalent: boolean;
  };
}

const observations: AcceleratedObservations = {
  v: 1,
  source: null,
  environment: null,
  responsive: [],
};

interface FirstMeaningfulVisualProbe {
  firstVisibleAtMs: number | null;
}

function captureUrl(
  layout: "cinematic" | "compact",
  motion: "real" | "reduced",
): string {
  return `/e2e/choreography?pace=step&timing=accelerated&layout=${layout}&motion=${motion}`;
}

const INTERRUPTION_TRIALS: readonly InterruptionTrial[] = Object.freeze(
  (
    [
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
    ] as const
  ).flatMap((definition) =>
    Array.from({ length: 4 }, (_, index) =>
      Object.freeze({
        ...definition,
        label: `${definition.category}-${index + 1}`,
      }),
    ),
  ),
);

function observeProviderFreeRequests(
  page: Page,
  documentTargets: readonly string[],
): { liveSceneRequests: string[]; unexpectedRequests: string[] } {
  const liveSceneRequests: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    const isLocal =
      url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const target = isLocal
      ? `${url.pathname}${url.search}`
      : `${url.origin}${url.pathname}`;
    if (url.pathname.startsWith("/api/live-scenes")) {
      liveSceneRequests.push(target);
    }
    const allowed =
      isLocal &&
      request.method() === "GET" &&
      (documentTargets.includes(target) ||
        url.pathname.startsWith("/_next/") ||
        url.pathname.startsWith("/__nextjs_font/") ||
        url.pathname === "/favicon.ico");
    if (!allowed) unexpectedRequests.push(target);
  });
  return { liveSceneRequests, unexpectedRequests };
}

function expectProviderFree(requests: ProviderFreeRequestObservation): void {
  expect(requests.liveSceneRequests).toEqual([]);
  expect(requests.unexpectedRequests).toEqual([]);
}

function percentile(
  samples: readonly number[],
  percentileValue: number,
): number {
  const sorted = [...samples].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * percentileValue) - 1)];
}

function rounded(value: number): number {
  return Number(value.toFixed(3));
}

async function installFirstMeaningfulVisualProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const probe: FirstMeaningfulVisualProbe = { firstVisibleAtMs: null };
    Object.defineProperty(window, "__MURMUR_FIRST_MEANINGFUL_VISUAL__", {
      configurable: false,
      enumerable: false,
      writable: false,
      value: probe,
    });

    const sample = (): void => {
      const nodes = Array.from(
        document.querySelectorAll<SVGGraphicsElement>(
          '[data-testid="live-choreography-stage"] svg > [data-element-id]',
        ),
      );
      const visible = nodes.some((node) => {
        const bounds = node.getBoundingClientRect();
        const opacity = Number.parseFloat(
          getComputedStyle(node).opacity || "0",
        );
        return bounds.width > 0 && bounds.height > 0 && opacity > 0;
      });
      if (visible) {
        probe.firstVisibleAtMs = performance.now();
        return;
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
}

async function firstMeaningfulVisualAtMs(page: Page): Promise<number> {
  await page.waitForFunction(() => {
    const probe = (
      window as typeof window & {
        __MURMUR_FIRST_MEANINGFUL_VISUAL__?: FirstMeaningfulVisualProbe;
      }
    ).__MURMUR_FIRST_MEANINGFUL_VISUAL__;
    return (
      probe?.firstVisibleAtMs !== null && probe?.firstVisibleAtMs !== undefined
    );
  });
  return page.evaluate(() => {
    const probe = (
      window as typeof window & {
        __MURMUR_FIRST_MEANINGFUL_VISUAL__: FirstMeaningfulVisualProbe;
      }
    ).__MURMUR_FIRST_MEANINGFUL_VISUAL__;
    if (probe.firstVisibleAtMs === null) {
      throw new Error("The first meaningful visual probe did not settle");
    }
    return probe.firstVisibleAtMs;
  });
}

async function waitForCheckpointGate(
  page: Page,
  expected: ExpectedChoreographyCheckpoint,
): Promise<void> {
  await page.waitForFunction(
    ({ key, ordinal, checkpointId }) => {
      const bridge = (window as typeof window & Record<string, unknown>)[
        key
      ] as
        | {
            readonly version: number;
            readonly pace: string;
            getState(): {
              readonly waitingFor: {
                readonly generation: number;
                readonly sequence: number;
                readonly checkpointId: string;
              } | null;
              readonly acknowledgedThrough: number;
            };
          }
        | undefined;
      const state = bridge?.getState();
      return (
        bridge?.version === 1 &&
        bridge.pace === "step" &&
        state?.acknowledgedThrough === ordinal - 1 &&
        state.waitingFor?.generation === 1 &&
        state.waitingFor.sequence === ordinal &&
        state.waitingFor.checkpointId === checkpointId
      );
    },
    {
      key: CAPTURE_BRIDGE_KEY,
      ordinal: expected.ordinal,
      checkpointId: expected.checkpointId,
    },
  );

  const state = await readCaptureBridgeState(page);
  expect(state.acknowledgedThrough).toBe(expected.ordinal - 1);
  expect(state.waitingFor).toMatchObject({
    generation: 1,
    sequence: expected.ordinal,
    checkpointId: expected.checkpointId,
  });
  expect(state.waitingFor?.openedAtMs).toBeGreaterThanOrEqual(0);
}

async function runStepLesson(
  page: Page,
  checkpoints: readonly ExpectedChoreographyCheckpoint[],
): Promise<readonly SettledCheckpointObservation[]> {
  const results: SettledCheckpointObservation[] = [];
  let previous: SettledCheckpointObservation | undefined;

  for (const checkpoint of checkpoints) {
    await waitForCheckpointGate(page, checkpoint);
    const current = await observeSettledCheckpoint(page, checkpoint);
    await expect(choreographyStage(page)).toHaveAttribute(
      "data-phase",
      "streaming",
    );
    if (previous) assertRetainedDomIdentity(previous, current);
    results.push(current);
    previous = current;
    await acknowledgeCheckpoint(page, checkpoint);
  }

  await expect(choreographyStage(page)).toHaveAttribute(
    "data-phase",
    "completed",
  );
  return results;
}

async function waitForStableStage(page: Page): Promise<void> {
  await choreographyStage(page).evaluate(async (element) => {
    await document.fonts.ready;
    await Promise.all(
      element
        .getAnimations({ subtree: true })
        .map((animation) => animation.finished.catch(() => undefined)),
    );
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}

async function stageSnapshot(page: Page): Promise<StageSnapshot> {
  await waitForStableStage(page);
  return choreographyStage(page).evaluate((element) => {
    interface IdentityRegistry {
      next: number;
      readonly tokens: WeakMap<Element, number>;
    }
    const owner = window as typeof window & {
      __MURMUR_CHOREOGRAPHY_DOM_IDENTITY__?: IdentityRegistry;
    };
    const registry = owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ ?? {
      next: 1,
      tokens: new WeakMap<Element, number>(),
    };
    owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ = registry;

    const svg = element.querySelector("svg");
    if (!svg) throw new Error("The choreography stage has no SVG canvas");
    const nodes = Array.from(
      svg.querySelectorAll<SVGElement>(":scope > [data-element-id]"),
    );
    const domIdentity = Object.fromEntries(
      nodes.map((node) => {
        let token = registry.tokens.get(node);
        if (token === undefined) {
          token = registry.next;
          registry.next += 1;
          registry.tokens.set(node, token);
        }
        return [node.dataset.elementId ?? "", token];
      }),
    );
    const serialize = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return JSON.stringify(node.nodeValue ?? "");
      }
      if (!(node instanceof Element)) return "";
      const attributes = Array.from(node.attributes)
        .map(({ name, value }) => [name, value] as const)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([name, value]) => `${name}=${JSON.stringify(value)}`)
        .join(" ");
      const opening = attributes
        ? `<${node.namespaceURI}:${node.localName} ${attributes}>`
        : `<${node.namespaceURI}:${node.localName}>`;
      return `${opening}${Array.from(node.childNodes).map(serialize).join("")}</${node.namespaceURI}:${node.localName}>`;
    };

    return {
      caption: element.querySelector("figcaption")?.textContent?.trim() ?? "",
      viewBox: svg.getAttribute("viewBox") ?? "",
      nodeIds: nodes.map((node) => node.dataset.elementId ?? ""),
      domIdentity,
      canonicalSvg: serialize(svg),
    };
  });
}

function expectSharedDomIdentity(
  before: StageSnapshot,
  after: StageSnapshot,
): void {
  for (const id of before.nodeIds) {
    if (after.domIdentity[id] !== undefined) {
      expect(after.domIdentity[id], `DOM identity changed for ${id}`).toBe(
        before.domIdentity[id],
      );
    }
  }
}

function interruptedEvidenceThrough(
  target: ExpectedChoreographyCheckpoint,
): readonly ChoreographyEvidenceTraceEvent[] {
  return Object.freeze(
    MAIN_CHOREOGRAPHY_EVIDENCE.filter(
      (event) => event.sequence <= target.ordinal,
    ).map((event) =>
      event.sequence === target.ordinal && event.type === "checkpointSettled"
        ? Object.freeze({
            ...event,
            settlement: "cancelled_to_checkpoint" as const,
          })
        : event,
    ),
  );
}

async function interruptedStabilitySnapshot(
  page: Page,
  target: ExpectedChoreographyCheckpoint,
  expectedEvidence: readonly ChoreographyEvidenceTraceEvent[],
): Promise<InterruptedStabilitySnapshot> {
  await expect
    .poll(async () => (await readCaptureBridgeState(page)).waitingFor)
    .toBeNull();
  const [rendered, state, attributes] = await Promise.all([
    stageSnapshot(page),
    readCaptureBridgeState(page),
    choreographyStage(page).evaluate((element) => ({
      phase: element.getAttribute("data-phase"),
      checkpointId: element.getAttribute("data-checkpoint-id"),
      settledMainCount: Number(element.getAttribute("data-settled-main-count")),
      rendererTrusted: element.getAttribute("data-renderer-trusted"),
    })),
  ]);

  expect(attributes).toEqual({
    phase: "interrupted",
    checkpointId: target.checkpointId,
    settledMainCount: target.ordinal,
    rendererTrusted: "true",
  });
  expect(state.waitingFor).toBeNull();
  expect(state.evidence).toEqual(expectedEvidence);

  return {
    stage: rendered,
    frontier: {
      phase: "interrupted",
      checkpointId: target.checkpointId,
      settledMainCount: target.ordinal,
      rendererTrusted: true,
      waitingFor: null,
    },
    evidence: state.evidence,
  };
}

async function runInterruptionTrial(
  page: Page,
  route: string,
  trial: InterruptionTrial,
): Promise<InterruptionCaseObservation> {
  await page.goto(route);
  const target = CINEMATIC_CHECKPOINTS[trial.checkpointOrdinal - 1];
  const prefix = CINEMATIC_CHECKPOINTS.slice(0, target.ordinal - 1);
  const releaseCheckpoint = prefix.at(-1);
  if (!releaseCheckpoint) {
    throw new Error("Interruption trials require a retained predecessor");
  }

  let previous: SettledCheckpointObservation | undefined;
  for (const checkpoint of prefix) {
    await waitForCheckpointGate(page, checkpoint);
    const current = await observeSettledCheckpoint(page, checkpoint);
    if (previous) assertRetainedDomIdentity(previous, current);
    previous = current;
    if (checkpoint !== releaseCheckpoint) {
      await acknowledgeCheckpoint(page, checkpoint);
    }
  }

  const cueTargetIds =
    trial.cue === "hold" ? [] : [...(target.cueTargets[trial.cue] ?? [])];
  if (trial.cue === "hold") {
    expect(target.holdAfterMs).toBeGreaterThan(0);
  } else {
    expect(target.cues).toContain(trial.cue);
    expect(cueTargetIds.length).toBeGreaterThan(0);
  }
  if (trial.category === "token_morph") {
    expect(cueTargetIds).toEqual([
      "square-lesson__eq_16",
      "square-lesson__eq_equal_result",
    ]);
  }

  const delayAfterPresentedMs =
    trial.timing === "hold" ? Math.ceil(target.durationMs / 16) + 50 : 0;
  const result = await interruptCaptureCheckpoint(
    page,
    {
      generation: 1,
      sequence: target.ordinal,
      checkpointId: target.checkpointId,
      certificateSha256: target.certificateSha256,
      delayAfterPresentedMs,
    },
    releaseCheckpoint,
  );
  const expectedEvidence = interruptedEvidenceThrough(target);
  expect(result.target).toEqual({
    generation: 1,
    sequence: target.ordinal,
    checkpointId: target.checkpointId,
    certificateSha256: target.certificateSha256,
  });
  expect(result.trigger).toBe(
    trial.timing === "hold"
      ? "afterFirstCuePresentedDelay"
      : "firstCuePresented",
  );
  expect(result.delayAfterPresentedMs).toBe(delayAfterPresentedMs);
  expect(result.activeRevision).toBe(target.ordinal);
  expect(result.requestedAtMs).toBeGreaterThanOrEqual(0);
  expect(result.settledAtMs).toBeGreaterThanOrEqual(result.requestedAtMs);
  expect(result.settleMs).toBeGreaterThanOrEqual(0);
  expect(result.evidenceBefore).toEqual(expectedEvidence.slice(0, -1));
  expect(result.evidenceAfter).toEqual(expectedEvidence);
  const targetSettlements = result.evidenceAfter.filter(
    (event) =>
      event.sequence === target.ordinal && event.type === "checkpointSettled",
  );
  expect(targetSettlements).toEqual([
    expect.objectContaining({ settlement: "cancelled_to_checkpoint" }),
  ]);
  expect(
    result.evidenceAfter.some((event) => event.sequence > target.ordinal),
  ).toBe(false);
  await waitForCaptureEvidence(page, expectedEvidence);
  await expect(choreographyStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );

  const settled = await observeSettledCheckpoint(page, target);
  if (!previous) throw new Error("The interruption prefix was not observed");
  assertRetainedDomIdentity(previous, settled);
  expect(settled.transientResidueCount).toBe(0);

  const stableBefore = await interruptedStabilitySnapshot(
    page,
    target,
    expectedEvidence,
  );
  await page.waitForTimeout(2_000);
  const stableAfter = await interruptedStabilitySnapshot(
    page,
    target,
    expectedEvidence,
  );
  expect(stableAfter).toEqual(stableBefore);

  return {
    label: trial.label,
    category: trial.category,
    checkpointId: target.checkpointId,
    sequence: target.ordinal,
    cue: trial.cue,
    cueTargetIds,
    timing: trial.timing,
    authoredDurationMs: target.durationMs,
    authoredHoldAfterMs: target.holdAfterMs,
    trigger: result.trigger,
    delayAfterPresentedMs,
    activeRevision: result.activeRevision,
    requestedAtMs: rounded(result.requestedAtMs),
    settledAtMs: rounded(result.settledAtMs),
    settleMs: rounded(result.settleMs),
    target: settled,
    evidenceBefore: result.evidenceBefore,
    evidenceAfter: result.evidenceAfter,
    staleWindowMs: 2_000,
    stabilityBefore: stableBefore,
    stabilityAfter: stableAfter,
    staleStable: true,
  };
}

function assertReplayDomIdentity(
  previous: LiveChoreographyReplayCheckpointObservation,
  current: LiveChoreographyReplayCheckpointObservation,
): void {
  for (const id of previous.nodeIds) {
    if (current.domIdentity[id] !== undefined) {
      expect(
        current.domIdentity[id],
        `Replay DOM identity changed for ${id}`,
      ).toBe(previous.domIdentity[id]);
    }
  }
}

async function runReplayProof(
  page: Page,
  route: string,
  requests: ProviderFreeRequestObservation,
): Promise<ReplayObservation> {
  await page.goto(route);
  const liveCheckpoints = await runStepLesson(page, CINEMATIC_CHECKPOINTS);
  const liveEvidence = await waitForCaptureEvidence(
    page,
    MAIN_CHOREOGRAPHY_EVIDENCE,
  );
  const liveFinal = await stageSnapshot(page);
  const requestsBeforeReplay = requests.liveSceneRequests.length;
  const replay = await replayAcceptedChoreography(page);

  expect(replay.checkpoints).toHaveLength(CINEMATIC_CHECKPOINTS.length);
  let previous: LiveChoreographyReplayCheckpointObservation | undefined;
  for (const [index, replayed] of replay.checkpoints.entries()) {
    const expected = CINEMATIC_CHECKPOINTS[index];
    const live = liveCheckpoints[index];
    const cueTrace = MAIN_CHOREOGRAPHY_EVIDENCE.filter(
      (event) => event.sequence === expected.ordinal,
    );
    expect({
      ordinal: replayed.ordinal,
      checkpointId: replayed.checkpointId,
      certificateSha256: replayed.certificateSha256,
      caption: replayed.caption,
      viewport: replayed.viewport,
      nodeIds: replayed.nodeIds,
      rendererTrusted: replayed.rendererTrusted,
      cueTrace: replayed.cueTrace,
    }).toEqual({
      ordinal: expected.ordinal,
      checkpointId: expected.checkpointId,
      certificateSha256: expected.certificateSha256,
      caption: live.caption,
      viewport: expected.resultViewport,
      nodeIds: live.nodeIds,
      rendererTrusted: true,
      cueTrace,
    });
    expect(Object.keys(replayed.domIdentity)).toEqual(replayed.nodeIds);
    expect(new Set(Object.values(replayed.domIdentity)).size).toBe(
      replayed.nodeIds.length,
    );
    if (previous) assertReplayDomIdentity(previous, replayed);
    previous = replayed;
  }
  expect(replay.evidence).toEqual(liveEvidence);

  const replayedFinal = await stageSnapshot(page);
  const finalCheckpoint = replay.checkpoints.at(-1);
  if (!finalCheckpoint) throw new Error("Replay exposed no final checkpoint");
  expect(replayedFinal.caption).toBe(liveFinal.caption);
  expect(replayedFinal.viewBox).toBe(liveFinal.viewBox);
  expect(replayedFinal.nodeIds).toEqual(liveFinal.nodeIds);
  expect(replayedFinal.domIdentity).toEqual(finalCheckpoint.domIdentity);
  expect(replayedFinal.canonicalSvg).toBe(liveFinal.canonicalSvg);
  expect(requests.liveSceneRequests).toHaveLength(requestsBeforeReplay);
  expectProviderFree(requests);

  return {
    liveCheckpoints,
    replayedCheckpoints: replay.checkpoints,
    checkpointIds: replay.checkpoints.map(
      (checkpoint) => checkpoint.checkpointId,
    ),
    certificateSha256s: replay.checkpoints.map(
      (checkpoint) => checkpoint.certificateSha256,
    ),
    liveEvidence,
    replayEvidence: replay.evidence,
    finalCanonicalSvgMatches: true,
    equivalent: true,
  };
}

async function responsiveObservation(
  page: Page,
  viewport: { readonly width: number; readonly height: number },
): Promise<Omit<ResponsiveObservation, keyof ProviderFreeRequestObservation>> {
  const measured = await page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>(
      '[data-testid="live-choreography-stage"]',
    );
    if (!stage) throw new Error("The choreography stage is unavailable");
    const bounds = stage.getBoundingClientRect();
    return {
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      stage: {
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
      },
      finalViewBox: stage.querySelector("svg")?.getAttribute("viewBox") ?? "",
    };
  });

  expect(measured.documentWidth).toBeLessThanOrEqual(viewport.width);
  expect(measured.bodyWidth).toBeLessThanOrEqual(viewport.width);
  expect(measured.stage.x).toBeGreaterThanOrEqual(-0.5);
  expect(measured.stage.y).toBeGreaterThanOrEqual(-0.5);
  expect(measured.stage.x + measured.stage.width).toBeLessThanOrEqual(
    viewport.width + 0.5,
  );
  expect(measured.stage.y + measured.stage.height).toBeLessThanOrEqual(
    viewport.height + 0.5,
  );
  return { viewport, ...measured };
}

test.describe("Gate 1.5 live visual choreography", () => {
  test.beforeAll(async ({ browser }, testInfo) => {
    const execution = observeChoreographyExecution(testInfo.config, browser);
    observations.source = execution.source;
    observations.environment = execution.environment;
  });

  test("starts meaningful choreography under 100ms p95 over twenty fresh runs", async ({
    page,
  }, testInfo) => {
    testInfo.setTimeout(120_000);
    const route = captureUrl("cinematic", "real");
    const requests = observeProviderFreeRequests(page, [route]);
    const samples: number[] = [];
    await installFirstMeaningfulVisualProbe(page);

    for (let run = 0; run < 20; run += 1) {
      await page.goto(route);
      const firstCheckpoint = CINEMATIC_CHECKPOINTS[0];
      await waitForCheckpointGate(page, firstCheckpoint);
      const state = await readCaptureBridgeState(page);
      const openedAtMs = state.waitingFor?.openedAtMs;
      if (openedAtMs === undefined) {
        throw new Error("The first checkpoint gate did not expose its origin");
      }
      const sample = (await firstMeaningfulVisualAtMs(page)) - openedAtMs;
      expect(sample).toBeGreaterThanOrEqual(0);
      samples.push(rounded(sample));
      await page.goto("about:blank");
    }

    const p95Ms = rounded(percentile(samples, 0.95));
    expect(samples).toHaveLength(20);
    expect(p95Ms).toBeLessThan(100);
    expectProviderFree(requests);
    observations.latency = {
      firstMeaningfulVisualMs: samples,
      p95Ms,
      liveSceneRequests: [...requests.liveSceneRequests],
      unexpectedRequests: [...requests.unexpectedRequests],
    };
  });

  test("settles the exact eight-checkpoint lesson without replacing retained ink or calling a model", async ({
    page,
  }) => {
    const route = captureUrl("cinematic", "real");
    const requests = observeProviderFreeRequests(page, [route]);
    await page.goto(route);
    const checkpoints = await runStepLesson(page, CINEMATIC_CHECKPOINTS);

    expect(checkpoints).toHaveLength(8);
    expectProviderFree(requests);
    observations.cinematic = {
      checkpoints,
      liveSceneRequests: [...requests.liveSceneRequests],
      unexpectedRequests: [...requests.unexpectedRequests],
    };
  });

  test("reduced motion reaches the same certified final state", async ({
    page,
  }) => {
    const route = captureUrl("cinematic", "reduced");
    const requests = observeProviderFreeRequests(page, [route]);
    await page.goto(route);
    const checkpoints = await runStepLesson(page, CINEMATIC_CHECKPOINTS);
    const final = checkpoints.at(-1)!;
    const expectedFinal = CINEMATIC_CHECKPOINTS.at(-1)!;

    expect({
      checkpointId: final.checkpointId,
      caption: final.caption,
      viewBox: final.viewBox,
      nodeIds: final.nodeIds,
    }).toEqual({
      checkpointId: expectedFinal.checkpointId,
      caption: expectedFinal.caption,
      viewBox: `${expectedFinal.resultViewport.x} ${expectedFinal.resultViewport.y} ${expectedFinal.resultViewport.width} ${expectedFinal.resultViewport.height}`,
      nodeIds: expectedFinal.nodeIds,
    });
    expectProviderFree(requests);
    await page.setViewportSize({ width: 700, height: 394 });
    await expectCinematicCaptionContained(page);
    observations.reducedMotion = {
      final,
      equivalentToCinematic: true,
      liveSceneRequests: [...requests.liveSceneRequests],
      unexpectedRequests: [...requests.unexpectedRequests],
    };
  });

  for (const viewport of [
    { width: 375, height: 812 },
    { width: 320, height: 568 },
  ] as const) {
    test(`keeps the compact ${viewport.width}x${viewport.height} stage inside the viewport`, async ({
      page,
    }) => {
      const route = captureUrl("compact", "reduced");
      const requests = observeProviderFreeRequests(page, [route]);
      await page.setViewportSize(viewport);
      await page.goto(route);
      await runStepLesson(page, COMPACT_CHECKPOINTS);
      const measured = await responsiveObservation(page, viewport);

      expectProviderFree(requests);
      observations.responsive.push({
        ...measured,
        liveSceneRequests: [...requests.liveSceneRequests],
        unexpectedRequests: [...requests.unexpectedRequests],
      });
    });
  }

  test("interrupts twenty active checkpoints, proves stale-window stability, and replays every frontier", async ({
    page,
  }, testInfo) => {
    testInfo.setTimeout(240_000);
    const route = captureUrl("cinematic", "real");
    const requests = observeProviderFreeRequests(page, [
      route,
      "/labs/live-scene",
    ]);
    expect(INTERRUPTION_TRIALS).toHaveLength(20);
    expect(new Set(INTERRUPTION_TRIALS.map((trial) => trial.label)).size).toBe(
      20,
    );

    const interruptionCases: InterruptionCaseObservation[] = [];
    for (const trial of INTERRUPTION_TRIALS) {
      interruptionCases.push(
        await test.step(trial.label, () =>
          runInterruptionTrial(page, route, trial),
        ),
      );
    }
    for (const category of [
      "move",
      "token_morph",
      "camera_focus",
      "emphasis",
      "authored_hold",
    ] as const) {
      expect(
        interruptionCases.filter((trial) => trial.category === category),
      ).toHaveLength(4);
    }
    const interruptionSettleMsSamples = interruptionCases.map(
      (trial) => trial.settleMs,
    );
    const interruptionSettleP95Ms = rounded(
      percentile(interruptionSettleMsSamples, 0.95),
    );
    expect(interruptionSettleMsSamples).toHaveLength(20);
    expect(interruptionSettleP95Ms).toBeLessThan(150);

    const replay = await runReplayProof(page, route, requests);

    await page.goto("/labs/live-scene");
    await page
      .getByTestId("authoring-mode-picker")
      .getByText("Live choreography", { exact: true })
      .click();
    await expect(
      page.getByRole("radio", { name: "Live choreography" }),
    ).toBeChecked();
    await expect(
      page.getByRole("radio", { name: "Ask at the corner" }),
    ).toBeChecked();

    const stage = choreographyStage(page);
    await page.getByRole("button", { name: "Begin the lesson" }).click();
    await expect(stage).toHaveAttribute("data-checkpoint-id", "missing_corner");
    await expect(stage).toHaveAttribute("data-settled-main-count", "5");
    await expect(stage).toHaveAttribute("data-phase", "streaming");
    const beforeInterrupt = await stageSnapshot(page);
    await page.getByRole("button", { name: "Stop here and ask" }).click();
    await expect(stage).toHaveAttribute("data-phase", "interrupted");
    const interrupted = await stageSnapshot(page);
    expect(interrupted).toEqual(beforeInterrupt);

    await page.getByRole("button", { name: "Why is the corner 9?" }).click();
    await expect(stage).toHaveAttribute("data-checkpoint-id", "corner_detail");
    await expect(stage).toHaveAttribute("data-settled-main-count", "5");
    await expect(stage).toHaveAttribute("data-corner-clarified", "true");
    await expect(stage).toHaveAttribute("data-phase", "completed");
    const cornerDetail = await stageSnapshot(page);
    expect(cornerDetail.caption).toBe(CORNER_DETAIL_CAPTION);
    expect(cornerDetail.viewBox).toBe("220 220 420 330");
    expect(cornerDetail.nodeIds).toEqual([
      ...CINEMATIC_CHECKPOINTS[4].nodeIds,
      ...CORNER_DETAIL_NODE_IDS,
    ]);
    expectSharedDomIdentity(interrupted, cornerDetail);

    await page.getByRole("button", { name: "Continue the solution" }).click();
    await expect(stage).toHaveAttribute("data-checkpoint-id", "solve_roots");
    await expect(stage).toHaveAttribute("data-settled-main-count", "8");
    await expect(stage).toHaveAttribute("data-phase", "completed");
    const completed = await stageSnapshot(page);
    const expectedFinal = CINEMATIC_CHECKPOINTS.at(-1)!;
    expect(completed.caption).toBe(expectedFinal.caption);
    expect(completed.viewBox).toBe(
      `${expectedFinal.resultViewport.x} ${expectedFinal.resultViewport.y} ${expectedFinal.resultViewport.width} ${expectedFinal.resultViewport.height}`,
    );
    expect(completed.nodeIds).toEqual(expectedFinal.nodeIds);

    const requestsBeforeReplay = requests.liveSceneRequests.length;
    await page.getByRole("button", { name: "Replay" }).click();
    await expect(stage).toHaveAttribute("data-phase", "replaying");
    await expect(stage).toHaveAttribute("data-phase", "completed");
    const replayed = await stageSnapshot(page);
    expect(replayed.caption).toBe(completed.caption);
    expect(replayed.viewBox).toBe(completed.viewBox);
    expect(replayed.nodeIds).toEqual(completed.nodeIds);
    expect(replayed.canonicalSvg).toBe(completed.canonicalSvg);
    expect(requests.liveSceneRequests).toHaveLength(requestsBeforeReplay);
    expectProviderFree(requests);

    observations.adaptive = {
      interruptionSettleMsSamples,
      interruptionSettleP95Ms,
      interruptionCases,
      cornerDetailNodeIds: [...CORNER_DETAIL_NODE_IDS],
      replay,
      replayEquivalent: true,
      liveSceneRequests: [...requests.liveSceneRequests],
      unexpectedRequests: [...requests.unexpectedRequests],
    };
  });

  test.afterAll(async () => {
    const artifactRoot = path.resolve(
      process.env.CHOREOGRAPHY_E2E_ARTIFACT_DIR ?? "../var/live-choreography",
    );
    const outputPath = path.join(
      artifactRoot,
      "accelerated",
      "observations.json",
    );
    const temporaryPath = `${outputPath}.${process.pid}.tmp`;
    await fs.mkdir(path.dirname(outputPath), { recursive: true });
    await fs.writeFile(
      temporaryPath,
      `${JSON.stringify(observations, null, 2)}\n`,
      "utf8",
    );
    await fs.rename(temporaryPath, outputPath);
  });
});
