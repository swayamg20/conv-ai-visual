import fs from "node:fs/promises";
import path from "node:path";

import {
  expect,
  type Locator,
  type Page,
  type Request,
  type TestInfo,
} from "@playwright/test";

import complementaryFixtureValue from "../src/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a60.v1.json";
import unequalFixtureValue from "../src/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a45.v1.json";
import comparisonFixtureValue from "../src/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a45-a60.v1.json";
import {
  decodeSemanticStoryboardFixtureEnvelope,
  type DecodedSemanticStoryboardFixtureEnvelope,
  type DecodedSemanticStoryboardFixtureLane,
} from "../src/features/live-scene/semantic-storyboard-fixture-schema";
import type { SemanticStoryboardSessionSnapshot } from "../src/features/live-scene/semantic-storyboard-session-controller";
import type { PairedProjectileComparisonSpecV1 } from "../src/lib/live-scene/semantic-storyboard";

export const SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY =
  "__MURMUR_SEMANTIC_STORYBOARD_E2E__" as const;
export const SEMANTIC_STORYBOARD_PARTIAL_STREAM_PROBE_KEY =
  "__MURMUR_SEMANTIC_STORYBOARD_PARTIAL_STREAM__" as const;
const SEMANTIC_STORYBOARD_VISIBLE_CHECKPOINT_PROBE_KEY =
  "__MURMUR_SEMANTIC_STORYBOARD_VISIBLE_CHECKPOINTS__" as const;

export const STORYBOARD_PROMPTS = Object.freeze({
  pathsFirst: "Trace both flights before comparing their landing ranges.",
  higherFirst: "Begin with the higher arc, then compare height and time.",
  formulaFirst: "Show the mathematical reason for the equal ranges first.",
  continue: "Continue with exactly one new useful visual beat.",
  abstain: "Do nothing if this request has no supported forward step.",
  acceptedPrefix:
    "Show one formula, then stop safely if later output is malformed.",
  formulaInequality:
    "Use the range formula to show that these distances differ.",
  motionInequality: "Trace the steeper flight first, then compare the ranges.",
  heightThenFlight:
    "Trace the lower arc first, then compare height and flight time.",
});

export const STORYBOARD_SCENARIOS = Object.freeze({
  anchor: "anchor",
  pathsFirst: "motion_then_equal",
  higherFirst: "higher_arc_first",
  formulaFirst: "math_then_equal",
  continuationFromHigherFirstBeat: "continue_higher_arc_first_prefix_1",
  continuationFromHigherComplete: "continue_higher_arc_first_prefix_4",
  abstain: "sole_abstain",
  acceptedPrefix: "accepted_prefix_malformed_tail",
  formulaInequality: "formula_inequality",
  motionInequality: "motion_inequality",
  heightThenFlight: "height_then_flight",
});

const FIXTURES = Object.freeze([
  decodeSemanticStoryboardFixtureEnvelope(unequalFixtureValue),
  decodeSemanticStoryboardFixtureEnvelope(complementaryFixtureValue),
  decodeSemanticStoryboardFixtureEnvelope(comparisonFixtureValue),
]);

export interface SemanticStoryboardRunnerCallObservation {
  readonly ordinal: number;
  readonly observedAtMs: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly prompt: string | null;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly acceptedRecordCount: number;
}

export interface SemanticStoryboardFixtureEventObservation {
  readonly ordinal: number;
  readonly observedAtMs: number;
  readonly type:
    | "semantic_storyboard_scene_stream_started"
    | "semantic_storyboard_scene_checkpoint"
    | "semantic_storyboard_scene_stream_completed"
    | "semantic_storyboard_scene_stream_declined"
    | "semantic_storyboard_scene_stream_failed";
  readonly generation: number;
  readonly sequence: number | null;
  readonly resultRevision: number | null;
  readonly checkpointId: string | null;
}

export interface SemanticStoryboardE2EBridgeState {
  readonly runnerCallCount: number;
  readonly calls: readonly SemanticStoryboardRunnerCallObservation[];
}

export interface SemanticStoryboardSessionObservation {
  readonly observedAtMs: number;
  readonly snapshot: SemanticStoryboardSessionSnapshot;
}

export interface SemanticStoryboardE2EBridgeV1 {
  readonly version: 1;
  getState(): SemanticStoryboardE2EBridgeState;
  getEventHistory(): readonly SemanticStoryboardFixtureEventObservation[];
  getSessionObservation(): SemanticStoryboardSessionObservation | null;
  getSessionObservationHistory(): readonly SemanticStoryboardSessionObservation[];
}

export interface SemanticStoryboardStageObservation {
  readonly phase: string;
  readonly sessionStatus: string;
  readonly settledBeatCount: number;
  readonly visibleCheckpointId: string;
  readonly acceptedSpeed: string;
  readonly acceptedAngles: string;
  readonly rendererTrusted: string;
  readonly layout: string;
  readonly sceneRevision: number;
  readonly semanticRevision: number;
  readonly generation: number;
  readonly lastRoute: string;
  readonly programSha256: string;
  readonly certificateHead: string;
  readonly completionReason: string;
  readonly completionDetail: string;
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
}

export interface SemanticStoryboardNetworkObservation {
  readonly liveSceneRequests: readonly string[];
  readonly unexpectedRequests: readonly string[];
  readonly failedRequests: readonly string[];
}

export type SemanticStoryboardInterruptionSurface =
  | "provider_wait"
  | "partial_record"
  | "draw"
  | "trace_path"
  | "marker_movement"
  | "relationship_morph"
  | "camera_focus"
  | "hold"
  | "post_paint_barrier"
  | "replay";

export interface SemanticStoryboardInterruptionObservation {
  readonly surface: SemanticStoryboardInterruptionSurface;
  readonly requestedAtMs: number;
  readonly settledAtMs: number;
  readonly latencyMs: number;
  readonly visibleCheckpointId: string;
  readonly detector: Readonly<Record<string, string | number>>;
}

export interface SemanticStoryboardInterruptionTarget {
  readonly surface: SemanticStoryboardInterruptionSurface;
  readonly baseViewBox?: string;
  readonly targetViewBox?: string;
}

export interface SemanticStoryboardVisibleCheckpointObservation {
  readonly ordinal: number;
  readonly checkpointId: string;
  readonly observedAtMs: number;
  readonly committedSceneRevision: number;
}

export function semanticStoryboardFixture(
  problem: PairedProjectileComparisonSpecV1 = {
    v: 1,
    speedMps: 20,
    anglesDeg: [30, 60],
  },
): DecodedSemanticStoryboardFixtureEnvelope {
  const fixture = FIXTURES.find(
    (candidate) =>
      candidate.problemSpec.speedMps === problem.speedMps &&
      candidate.problemSpec.anglesDeg[0] === problem.anglesDeg[0] &&
      candidate.problemSpec.anglesDeg[1] === problem.anglesDeg[1],
  );
  if (!fixture) throw new Error("No generated storyboard fixture matches");
  return fixture;
}

export function semanticStoryboardLane(
  scenarioId: string,
): DecodedSemanticStoryboardFixtureLane {
  for (const fixture of FIXTURES) {
    const lane = [
      fixture.anchor,
      ...fixture.programs,
      ...fixture.continuations,
      ...(fixture.soleAbstain ? [fixture.soleAbstain] : []),
      ...(fixture.acceptedPrefix ? [fixture.acceptedPrefix] : []),
    ].find((candidate) => candidate.scenarioId === scenarioId);
    if (lane) return lane;
  }
  throw new Error(`Unknown semantic storyboard scenario: ${scenarioId}`);
}

export function semanticStoryboardStage(page: Page): Locator {
  return page.getByTestId("semantic-storyboard-stage");
}

export function semanticStoryboardRoot(page: Page): Locator {
  return page.getByTestId("semantic-storyboard-product");
}

export function semanticStoryboardBoard(page: Page): Locator {
  return semanticStoryboardStage(page).getByTestId("live-choreography-board");
}

export async function waitForSemanticStoryboardBridge(
  page: Page,
): Promise<void> {
  await page.waitForFunction(
    (key) => Boolean((window as typeof window & Record<string, unknown>)[key]),
    SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY,
  );
}

export async function semanticStoryboardBridgeState(
  page: Page,
): Promise<SemanticStoryboardE2EBridgeState> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      SemanticStoryboardE2EBridgeV1 | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The semantic-storyboard E2E bridge is unavailable");
    }
    return bridge.getState();
  }, SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY);
}

export async function semanticStoryboardSessionObservation(
  page: Page,
): Promise<SemanticStoryboardSessionObservation> {
  const observation = await page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      SemanticStoryboardE2EBridgeV1 | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The semantic-storyboard E2E bridge is unavailable");
    }
    return bridge.getSessionObservation();
  }, SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY);
  if (!observation) throw new Error("No storyboard snapshot was observed");
  return observation;
}

export async function semanticStoryboardSessionHistory(
  page: Page,
): Promise<readonly SemanticStoryboardSessionObservation[]> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      SemanticStoryboardE2EBridgeV1 | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The semantic-storyboard E2E bridge is unavailable");
    }
    return bridge.getSessionObservationHistory();
  }, SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY);
}

export async function semanticStoryboardEventHistory(
  page: Page,
): Promise<readonly SemanticStoryboardFixtureEventObservation[]> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      SemanticStoryboardE2EBridgeV1 | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The semantic-storyboard E2E bridge is unavailable");
    }
    return bridge.getEventHistory();
  }, SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY);
}

/**
 * Observe the browser-visible checkpoint boundary in the same monotonic clock
 * as the E2E bridge. The runtime publishes this attribute only after the
 * executor emits `firstCuePresented`, which itself follows a rendered,
 * non-hidden SVG sample; it is intentionally distinct from later post-paint
 * checkpoint acceptance.
 */
export async function installSemanticStoryboardVisibleCheckpointProbe(
  page: Page,
): Promise<void> {
  await page.evaluate((key) => {
    const root = document.querySelector<HTMLElement>(
      '[data-testid="semantic-storyboard-product"]',
    );
    const stage = document.querySelector<HTMLElement>(
      '[data-testid="semantic-storyboard-stage"]',
    );
    if (!root || !stage) throw new Error("The storyboard stage is unavailable");
    const history: SemanticStoryboardVisibleCheckpointObservation[] = [];
    const seen = new Set<string>();
    const record = () => {
      const checkpointId = stage.dataset.visibleCheckpointId;
      if (!checkpointId || checkpointId === "none" || seen.has(checkpointId)) {
        return;
      }
      seen.add(checkpointId);
      history.push({
        ordinal: history.length + 1,
        checkpointId,
        observedAtMs: performance.now(),
        committedSceneRevision: Number(root.dataset.sceneRevision ?? -1),
      });
    };
    const observer = new MutationObserver(record);
    observer.observe(stage, {
      attributes: true,
      attributeFilter: ["data-visible-checkpoint-id"],
    });
    record();
    (window as typeof window & Record<string, unknown>)[key] = Object.freeze({
      version: 1 as const,
      getHistory: () => history.map((item) => Object.freeze({ ...item })),
    });
  }, SEMANTIC_STORYBOARD_VISIBLE_CHECKPOINT_PROBE_KEY);
}

export async function semanticStoryboardVisibleCheckpointHistory(
  page: Page,
): Promise<readonly SemanticStoryboardVisibleCheckpointObservation[]> {
  return page.evaluate((key) => {
    const probe = (window as typeof window & Record<string, unknown>)[key] as
      | {
          readonly version: 1;
          getHistory(): readonly SemanticStoryboardVisibleCheckpointObservation[];
        }
      | undefined;
    if (!probe || probe.version !== 1) {
      throw new Error("The visible-checkpoint probe is unavailable");
    }
    return probe.getHistory();
  }, SEMANTIC_STORYBOARD_VISIBLE_CHECKPOINT_PROBE_KEY);
}

export function acceptedModelCheckpointIds(
  snapshot: SemanticStoryboardSessionSnapshot,
): readonly string[] {
  return snapshot.runtime.accepted.flatMap((accepted) =>
    accepted.event.transition.checkpoint.checkpointOrigin === "model_record"
      ? [accepted.event.transition.checkpoint.checkpointId]
      : [],
  );
}

export async function setSemanticStoryboardPrompt(
  page: Page,
  prompt: string,
): Promise<void> {
  const input = page.getByLabel("What should the board explain next?");
  await expect(input).toBeEnabled();
  await input.fill(prompt);
}

export async function startSemanticStoryboard(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Make it visible" }).click();
}

export async function continueSemanticStoryboard(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Continue from here" }).click();
}

export async function waitForSemanticStoryboardStatus(
  page: Page,
  status: string,
  timeout = 30_000,
): Promise<void> {
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-session-status",
    status,
    { timeout },
  );
}

export async function observeSemanticStoryboardStage(
  page: Page,
): Promise<SemanticStoryboardStageObservation> {
  return semanticStoryboardStage(page).evaluate((element) => {
    const svg = element.querySelector("svg");
    const caption = element.querySelector("figcaption p");
    if (!svg || !caption) throw new Error("The storyboard stage is incomplete");
    const integer = (value: string | undefined) => Number(value ?? "NaN");
    return {
      phase: element.dataset.phase ?? "",
      sessionStatus: element.dataset.sessionStatus ?? "",
      settledBeatCount: integer(element.dataset.settledBeatCount),
      visibleCheckpointId: element.dataset.visibleCheckpointId ?? "",
      acceptedSpeed: element.dataset.acceptedSpeed ?? "",
      acceptedAngles: element.dataset.acceptedAngles ?? "",
      rendererTrusted: element.dataset.rendererTrusted ?? "",
      layout: element.dataset.layout ?? "",
      sceneRevision: integer(element.dataset.sceneRevision),
      semanticRevision: integer(element.dataset.semanticRevision),
      generation: integer(element.dataset.generation),
      lastRoute: element.dataset.lastRoute ?? "",
      programSha256: element.dataset.programSha256 ?? "",
      certificateHead: element.dataset.certificateHead ?? "",
      completionReason: element.dataset.completionReason ?? "",
      completionDetail: element.dataset.completionDetail ?? "",
      caption: caption.textContent?.trim() ?? "",
      viewBox: svg.getAttribute("viewBox") ?? "",
      nodeIds: Array.from(
        svg.querySelectorAll<SVGGraphicsElement>("[data-element-id]"),
        (node) => node.dataset.elementId ?? "",
      ),
    };
  });
}

export async function expectSemanticStoryboardTerminal(
  page: Page,
  lane: DecodedSemanticStoryboardFixtureLane,
  layout: "cinematic" | "compact",
  options: {
    readonly sessionStatus?: "paused" | "declined";
    readonly phase?: "completed" | "declined";
    readonly timeout?: number;
  } = {},
): Promise<SemanticStoryboardStageObservation> {
  const status = options.sessionStatus ?? "paused";
  const phase = options.phase ?? "completed";
  await waitForSemanticStoryboardStatus(page, status, options.timeout);
  const stage = semanticStoryboardStage(page);
  await expect(stage).toHaveAttribute("data-phase", phase);
  await expect(stage).toHaveAttribute("data-renderer-trusted", "true");
  await expect(stage).toHaveAttribute(
    "data-settled-beat-count",
    String(lane.resultSemanticScene.components[0]?.acceptedRecords.length ?? 0),
  );
  await expect(stage).toHaveAttribute(
    "data-scene-revision",
    String(lane.resultScene.revision),
  );
  await expect(stage).toHaveAttribute(
    "data-semantic-revision",
    String(lane.resultSemanticScene.revision),
  );
  const viewport =
    lane.resultFrontiers[layout].viewport ??
    semanticStoryboardFixture(lane.request.problemSpec).anchor.resultFrontiers[
      layout
    ].viewport;
  if (!viewport)
    throw new Error(`${lane.scenarioId} has no ${layout} viewport`);
  const observation = await observeSemanticStoryboardStage(page);
  expect(observation.viewBox).toBe(
    `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`,
  );
  expect(observation.nodeIds).toHaveLength(lane.resultScene.nodes.length);
  expect(new Set(observation.nodeIds).size).toBe(observation.nodeIds.length);
  expect(
    observation.nodeIds.every((id) => id.startsWith("projectile-comparison__")),
  ).toBe(true);
  const component = lane.resultSemanticScene.components[0];
  expect(observation.acceptedSpeed).toBe(
    component ? String(component.problemSpec.speedMps) : "none",
  );
  expect(observation.acceptedAngles).toBe(
    component
      ? `${component.problemSpec.anglesDeg[0]}:${component.problemSpec.anglesDeg[1]}`
      : "none",
  );
  expect(observation.certificateHead).toBe(
    lane.resultSemanticScene.certificateHeadSha256,
  );
  return observation;
}

function requestTarget(request: Request): string {
  const url = new URL(request.url());
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  return local
    ? `${request.method()} ${url.pathname}${url.search}`
    : `${request.method()} ${url.origin}${url.pathname}`;
}

export function observeSemanticStoryboardNetwork(
  page: Page,
  baseURL: string,
  routePath: string,
): () => SemanticStoryboardNetworkObservation {
  const origin = new URL(baseURL).origin;
  const expectedDocument = new URL(routePath, baseURL);
  const liveSceneRequests = new Set<string>();
  const unexpectedRequests = new Set<string>();
  const failedRequests = new Set<string>();
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    const target = requestTarget(request);
    if (url.pathname.startsWith("/api/live-scenes")) {
      liveSceneRequests.add(target);
    }
    const allowedDocument =
      request.method() === "GET" &&
      url.origin === origin &&
      url.pathname === expectedDocument.pathname &&
      url.search === expectedDocument.search;
    const allowedStatic =
      request.method() === "GET" &&
      url.origin === origin &&
      (url.pathname.startsWith("/_next/") ||
        url.pathname.startsWith("/__nextjs_font/") ||
        url.pathname === "/favicon.ico");
    if (!allowedDocument && !allowedStatic) unexpectedRequests.add(target);
  });
  page.on("requestfailed", (request) => {
    failedRequests.add(
      `${requestTarget(request)}: ${request.failure()?.errorText ?? "unknown failure"}`,
    );
  });
  return () => ({
    liveSceneRequests: [...liveSceneRequests],
    unexpectedRequests: [...unexpectedRequests],
    failedRequests: [...failedRequests],
  });
}

export function expectSemanticStoryboardProviderFree(
  observation: SemanticStoryboardNetworkObservation,
): void {
  expect(observation.liveSceneRequests).toEqual([]);
  expect(observation.unexpectedRequests).toEqual([]);
  expect(observation.failedRequests).toEqual([]);
}

export async function expectNoSemanticStoryboardOverflow(
  page: Page,
): Promise<void> {
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

export async function expectSemanticStoryboardCaptionContained(
  page: Page,
): Promise<void> {
  const bounds = await semanticStoryboardStage(page).evaluate((element) => {
    const caption = element.querySelector("figcaption p");
    if (!caption) throw new Error("The storyboard caption is unavailable");
    const stageBounds = element.getBoundingClientRect();
    const captionBounds = caption.getBoundingClientRect();
    return {
      stage: {
        top: stageBounds.top,
        right: stageBounds.right,
        bottom: stageBounds.bottom,
        left: stageBounds.left,
      },
      caption: {
        top: captionBounds.top,
        right: captionBounds.right,
        bottom: captionBounds.bottom,
        left: captionBounds.left,
        height: captionBounds.height,
      },
    };
  });
  expect(bounds.caption.height).toBeGreaterThan(0);
  expect(bounds.caption.top).toBeGreaterThanOrEqual(bounds.stage.top - 0.5);
  expect(bounds.caption.right).toBeLessThanOrEqual(bounds.stage.right + 0.5);
  expect(bounds.caption.bottom).toBeLessThanOrEqual(bounds.stage.bottom + 0.5);
  expect(bounds.caption.left).toBeGreaterThanOrEqual(bounds.stage.left - 0.5);
}

export async function rememberSemanticStoryboardElement(
  page: Page,
  elementId: string,
  memoryKey: string,
): Promise<void> {
  await page.evaluate(
    ({ id, key }) => {
      const element = document.querySelector(`[data-element-id="${id}"]`);
      if (!element) throw new Error(`Missing stable storyboard element ${id}`);
      (window as typeof window & Record<string, unknown>)[key] = element;
    },
    { id: elementId, key: memoryKey },
  );
}

export async function expectSemanticStoryboardElementStable(
  page: Page,
  elementId: string,
  memoryKey: string,
): Promise<void> {
  expect(
    await page.evaluate(
      ({ id, key }) =>
        (window as typeof window & Record<string, unknown>)[key] ===
        document.querySelector(`[data-element-id="${id}"]`),
      { id: elementId, key: memoryKey },
    ),
  ).toBe(true);
}

/**
 * Detect one real rendered surface in the page and click Stop from the same
 * animation frame. The returned interval is entirely browser-monotonic; it
 * excludes Playwright transport latency.
 */
export async function interruptSemanticStoryboardAtSurface(
  page: Page,
  target: SemanticStoryboardInterruptionTarget,
): Promise<SemanticStoryboardInterruptionObservation> {
  return page.evaluate(
    async ({ input, bridgeKey }) => {
      const root = document.querySelector<HTMLElement>(
        '[data-testid="semantic-storyboard-product"]',
      );
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="semantic-storyboard-stage"]',
      );
      if (!root || !stage) throw new Error("The storyboard surface is absent");

      const checkpointFor = {
        trace_path: "storyboard-checkpoint-trace-higher-angle",
        marker_movement: "storyboard-checkpoint-trace-higher-angle",
        camera_focus: "storyboard-checkpoint-trace-higher-angle",
        post_paint_barrier: "storyboard-checkpoint-trace-higher-angle",
        replay: "storyboard-checkpoint-trace-higher-angle",
        relationship_morph: "storyboard-checkpoint-relate-equal-range",
        hold: "storyboard-checkpoint-reveal-range-formula",
      } as const;
      const detector: Record<string, string | number> = {};
      let priorMarkerTransform = "";
      const detected = () => {
        const visibleCheckpointId = stage.dataset.visibleCheckpointId ?? "";
        const svg = stage.querySelector<SVGSVGElement>("svg");
        if (!svg) return false;
        if (input.surface === "provider_wait") {
          const bridge = (window as typeof window & Record<string, unknown>)[
            bridgeKey
          ] as
            | {
                getState(): SemanticStoryboardE2EBridgeState;
                getEventHistory(): readonly SemanticStoryboardFixtureEventObservation[];
              }
            | undefined;
          const state = bridge?.getState();
          const director = state?.calls.find(
            (call) => call.routingMode === "director",
          );
          const events = bridge?.getEventHistory() ?? [];
          const started = director
            ? events.some(
                (event) =>
                  event.generation === director.generation &&
                  event.type === "semantic_storyboard_scene_stream_started",
              )
            : false;
          const checkpoint = director
            ? events.some(
                (event) =>
                  event.generation === director.generation &&
                  event.type === "semantic_storyboard_scene_checkpoint",
              )
            : false;
          if (
            root.dataset.sessionStatus === "directing" &&
            started &&
            !checkpoint
          ) {
            detector.directorGeneration = director?.generation ?? -1;
            return true;
          }
          return false;
        }
        if (input.surface === "partial_record") {
          const probe = (window as typeof window & Record<string, unknown>)
            .__MURMUR_SEMANTIC_STORYBOARD_PARTIAL_STREAM__ as
            | {
                activePartialOpen?: boolean;
                partialChunkCount?: number;
              }
            | undefined;
          if (
            root.dataset.sessionStatus === "directing" &&
            probe?.activePartialOpen === true &&
            typeof probe.partialChunkCount === "number" &&
            probe.partialChunkCount > 0
          ) {
            detector.partialChunkCount = probe.partialChunkCount;
            return true;
          }
          return false;
        }
        if (input.surface === "draw") {
          const path = stage.querySelector<SVGPathElement>(
            '[data-element-id="projectile-comparison__ground_axis"] path',
          );
          const dashArray = Number(path?.getAttribute("stroke-dasharray"));
          const dashOffset = Number(path?.getAttribute("stroke-dashoffset"));
          if (
            visibleCheckpointId === "storyboard-anchor" &&
            Number.isFinite(dashArray) &&
            Number.isFinite(dashOffset) &&
            dashArray > 0 &&
            dashOffset > 0 &&
            dashOffset < dashArray
          ) {
            detector.dashArray = dashArray;
            detector.dashOffset = dashOffset;
            return true;
          }
          return false;
        }

        const expectedCheckpoint =
          checkpointFor[input.surface as keyof typeof checkpointFor];
        if (visibleCheckpointId !== expectedCheckpoint) return false;
        if (input.surface === "trace_path" || input.surface === "replay") {
          if (
            input.surface === "replay" &&
            root.dataset.sessionStatus !== "replaying"
          ) {
            return false;
          }
          const path = stage.querySelector<SVGPathElement>(
            '[data-element-id="projectile-comparison__trajectory_higher"] path',
          );
          const dashArray = Number(path?.getAttribute("stroke-dasharray"));
          const dashOffset = Number(path?.getAttribute("stroke-dashoffset"));
          if (
            Number.isFinite(dashArray) &&
            Number.isFinite(dashOffset) &&
            dashArray > 0 &&
            dashOffset > 0 &&
            dashOffset < dashArray
          ) {
            detector.dashArray = dashArray;
            detector.dashOffset = dashOffset;
            return true;
          }
          return false;
        }
        if (input.surface === "marker_movement") {
          const marker = stage.querySelector<SVGGElement>(
            '[data-element-id="projectile-comparison__projectile_marker_higher"]',
          );
          const transform = marker?.getAttribute("transform") ?? "";
          const moved =
            priorMarkerTransform !== "" &&
            transform !== "" &&
            transform !== priorMarkerTransform;
          if (moved) {
            detector.previousTransform = priorMarkerTransform;
            detector.currentTransform = transform;
          }
          priorMarkerTransform = transform;
          return moved;
        }
        if (input.surface === "relationship_morph") {
          const relation = stage.querySelector<SVGGElement>(
            '[data-element-id="projectile-comparison__range_relation"]',
          );
          const opacity = Number.parseFloat(
            relation ? getComputedStyle(relation).opacity : "",
          );
          if (Number.isFinite(opacity) && opacity > 0.02 && opacity < 0.72) {
            detector.opacity = opacity;
            return true;
          }
          return false;
        }
        if (input.surface === "camera_focus") {
          const viewBox = svg.getAttribute("viewBox") ?? "";
          if (
            input.baseViewBox &&
            input.targetViewBox &&
            viewBox !== input.baseViewBox &&
            viewBox !== input.targetViewBox
          ) {
            detector.viewBox = viewBox;
            return true;
          }
          return false;
        }
        if (input.surface === "hold") {
          const formula = stage.querySelector<SVGGElement>(
            '[data-element-id="projectile-comparison__range_formula"]',
          );
          const opacity = Number.parseFloat(
            formula ? getComputedStyle(formula).opacity : "",
          );
          const brightness = Number.parseFloat(
            formula?.style.filter.match(/brightness\(([^)]+)\)/)?.[1] ?? "",
          );
          if (
            root.dataset.sessionStatus === "directing" &&
            Number.isFinite(opacity) &&
            Math.abs(opacity - 1) < 0.001 &&
            Number.isFinite(brightness) &&
            Math.abs(brightness - 1) < 0.001
          ) {
            detector.opacity = opacity;
            detector.brightness = brightness;
            return true;
          }
          return false;
        }
        if (input.surface === "post_paint_barrier") {
          const bridge = (window as typeof window & Record<string, unknown>)[
            bridgeKey
          ] as
            | {
                getSessionObservation(): SemanticStoryboardSessionObservation | null;
              }
            | undefined;
          const path = stage.querySelector<SVGPathElement>(
            '[data-element-id="projectile-comparison__trajectory_higher"] path',
          );
          const marker = stage.querySelector<SVGGElement>(
            '[data-element-id="projectile-comparison__projectile_marker_higher"]',
          );
          const snapshot = bridge?.getSessionObservation()?.snapshot;
          const exactTargetInstalled =
            path !== null &&
            marker !== null &&
            !path.hasAttribute("stroke-dasharray") &&
            !path.hasAttribute("stroke-dashoffset") &&
            !marker.hasAttribute("transform");
          if (
            root.dataset.sessionStatus === "directing" &&
            snapshot?.runtime.accepted.length === 1 &&
            exactTargetInstalled
          ) {
            detector.acceptedBeforeClick = snapshot.runtime.accepted.length;
            detector.sceneRevisionBeforeClick =
              snapshot.runtime.committedScene.revision;
            return true;
          }
          return false;
        }
        return false;
      };

      const findStop = () =>
        Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
          (candidate) =>
            candidate.textContent?.trim() === "Stop at this beat" &&
            !candidate.disabled,
        );
      const deadline = performance.now() + 45_000;
      while (!detected() || !findStop()) {
        if (performance.now() >= deadline) {
          throw new Error(`Timed out detecting ${input.surface}`);
        }
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => resolve()),
        );
      }

      const stop = findStop();
      if (!stop) throw new Error("The Stop control disappeared before click");
      const requestedAtMs = performance.now();
      const settledAtMs = await new Promise<number>((resolve, reject) => {
        const complete = () => {
          if (
            root.dataset.sessionStatus !== "paused" ||
            stage.dataset.phase !== "interrupted"
          ) {
            return false;
          }
          observer.disconnect();
          globalThis.clearTimeout(timeout);
          resolve(performance.now());
          return true;
        };
        const observer = new MutationObserver(complete);
        const timeout = globalThis.setTimeout(() => {
          observer.disconnect();
          reject(new Error(`Interruption did not settle for ${input.surface}`));
        }, 2_000);
        observer.observe(root, { attributes: true, subtree: true });
        stop.click();
        complete();
      });
      return {
        surface: input.surface,
        requestedAtMs,
        settledAtMs,
        latencyMs: settledAtMs - requestedAtMs,
        visibleCheckpointId: stage.dataset.visibleCheckpointId ?? "none",
        detector,
      };
    },
    { input: target, bridgeKey: SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY },
  );
}

export async function stopDuringSemanticStoryboardTrace(
  page: Page,
  checkpointId:
    | "storyboard-checkpoint-trace-lower-angle"
    | "storyboard-checkpoint-trace-higher-angle",
): Promise<{ readonly dashArray: number; readonly dashOffset: number }> {
  const pathId = checkpointId.endsWith("lower-angle")
    ? "projectile-comparison__trajectory_lower"
    : "projectile-comparison__trajectory_higher";
  const result = await page.waitForFunction(
    ({ checkpoint, path }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="semantic-storyboard-stage"]',
      );
      const geometry = stage?.querySelector<SVGPathElement>(
        `[data-element-id="${path}"] path`,
      );
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this beat" &&
          !candidate.disabled,
      );
      const dashArray = Number(geometry?.getAttribute("stroke-dasharray"));
      const dashOffset = Number(geometry?.getAttribute("stroke-dashoffset"));
      if (
        stage?.dataset.visibleCheckpointId !== checkpoint ||
        !Number.isFinite(dashArray) ||
        !Number.isFinite(dashOffset) ||
        dashArray <= 0 ||
        dashOffset <= 0 ||
        dashOffset >= dashArray ||
        !stop
      ) {
        return false;
      }
      (stop as HTMLButtonElement).click();
      return { dashArray, dashOffset };
    },
    { checkpoint: checkpointId, path: pathId },
    { polling: "raf", timeout: 30_000 },
  );
  const value = await result.jsonValue();
  if (value === false) throw new Error("The storyboard trace never animated");
  await expect(semanticStoryboardStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return value;
}

export async function expectSemanticStoryboardFrontierQuiet(
  page: Page,
  durationMs = 2_000,
): Promise<void> {
  const result = await page.evaluate(async (duration) => {
    const root = document.querySelector<HTMLElement>(
      '[data-testid="semantic-storyboard-product"]',
    );
    const stage = document.querySelector<HTMLElement>(
      '[data-testid="semantic-storyboard-stage"]',
    );
    if (!root || !stage) throw new Error("The storyboard frontier is missing");
    const owner = window as typeof window & Record<string, unknown>;
    const bridge = owner.__MURMUR_SEMANTIC_STORYBOARD_E2E__ as
      SemanticStoryboardE2EBridgeV1 | undefined;
    const state = () => ({
      status: root.dataset.sessionStatus,
      revision: root.dataset.sceneRevision,
      semanticRevision: root.dataset.semanticRevision,
      program: root.dataset.programSha256,
      head: root.dataset.certificateHead,
      html: stage.innerHTML,
      runner: bridge?.getState() ?? null,
      events: bridge?.getEventHistory() ?? null,
      sessions: bridge?.getSessionObservationHistory() ?? null,
      partialTransport: owner.__MURMUR_SEMANTIC_STORYBOARD_PARTIAL_STREAM__
        ? JSON.parse(
            JSON.stringify(owner.__MURMUR_SEMANTIC_STORYBOARD_PARTIAL_STREAM__),
          )
        : null,
    });
    const before = state();
    let mutations = 0;
    const mutationDetails: string[] = [];
    const observer = new MutationObserver((entries) => {
      mutations += entries.length;
      for (const entry of entries.slice(0, 20 - mutationDetails.length)) {
        const target = entry.target as Element;
        mutationDetails.push(
          `${entry.type}:${target.tagName.toLowerCase()}${
            entry.attributeName ? `[${entry.attributeName}]` : ""
          }`,
        );
      }
    });
    observer.observe(root, {
      attributes: true,
      childList: true,
      characterData: true,
      subtree: true,
    });
    await new Promise((resolve) => globalThis.setTimeout(resolve, duration));
    observer.disconnect();
    return {
      before,
      after: state(),
      mutations,
      mutationDetails,
    };
  }, durationMs);
  expect(result.mutations, result.mutationDetails.join(", ")).toBe(0);
  expect(result.after).toEqual(result.before);
}

export async function attachSemanticStoryboardBoardScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  explicitPath?: string,
): Promise<Buffer> {
  const stage = semanticStoryboardStage(page);
  const caption = stage.locator("figcaption");
  const targetPath = explicitPath ?? testInfo.outputPath(`${name}.png`);
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  const display = await caption.evaluate((element) => element.style.display);
  await caption.evaluate((element) => {
    element.style.display = "none";
  });
  let png: Buffer;
  try {
    png = await semanticStoryboardBoard(page).screenshot({
      path: targetPath,
      type: "png",
    });
  } finally {
    await caption.evaluate((element, prior) => {
      element.style.display = prior;
    }, display);
  }
  await testInfo.attach(name, { path: targetPath, contentType: "image/png" });
  return png;
}
