import { expect, test, type Page, type TestInfo } from "@playwright/test";

import type { DecodedSemanticStoryboardFixtureLane } from "../src/features/live-scene/semantic-storyboard-fixture-schema";
import { expectSemanticStoryboardSvgMatchesScene } from "./semantic-storyboard-dom-oracle";
import {
  STORYBOARD_PROMPTS,
  STORYBOARD_SCENARIOS,
  acceptedModelCheckpointIds,
  continueSemanticStoryboard,
  expectNoSemanticStoryboardOverflow,
  expectSemanticStoryboardCaptionContained,
  expectSemanticStoryboardElementStable,
  expectSemanticStoryboardFrontierQuiet,
  expectSemanticStoryboardProviderFree,
  expectSemanticStoryboardTerminal,
  installSemanticStoryboardVisibleCheckpointProbe,
  interruptSemanticStoryboardAtSurface,
  observeSemanticStoryboardNetwork,
  observeSemanticStoryboardStage,
  rememberSemanticStoryboardElement,
  semanticStoryboardBridgeState,
  semanticStoryboardEventHistory,
  semanticStoryboardFixture,
  semanticStoryboardLane,
  semanticStoryboardRoot,
  semanticStoryboardSessionObservation,
  semanticStoryboardSessionHistory,
  semanticStoryboardStage,
  semanticStoryboardVisibleCheckpointHistory,
  setSemanticStoryboardPrompt,
  startSemanticStoryboard,
  type SemanticStoryboardInterruptionObservation,
  type SemanticStoryboardInterruptionSurface,
  waitForSemanticStoryboardBridge,
  waitForSemanticStoryboardStatus,
} from "./semantic-storyboard-helpers";

const ACCELERATED_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=reduced&speed=accelerated&proof=none";
const NORMAL_MOTION_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=real&speed=normal&proof=none";
const PROVIDER_WAIT_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=real&speed=accelerated&proof=keyframes";
const REPLAY_INTERRUPTION_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=real&speed=accelerated&proof=none";
const INTERRUPTION_TRIALS = 4;
const INTERRUPTION_P95_LIMIT_MS = 150;
const LATENCY_CONTEXTS = 20;
const LATENCY_THRESHOLDS_MS = Object.freeze({
  submitToAnchorVisibleP95: 300,
  directorDispatchToFirstModelVisibleP95: 2_000,
  firstModelCheckpointEventToVisibleP95: 250,
  postPaintAcceptanceToNextBeatVisibleP95: 1_000,
});

async function resetSemanticStoryboard(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Reset" }).click();
  await waitForSemanticStoryboardStatus(page, "ready");
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-scene-revision",
    "0",
  );
}

async function runFreshScenario(
  page: Page,
  prompt: string,
  lane: DecodedSemanticStoryboardFixtureLane,
  layout: "cinematic" | "compact" = "cinematic",
) {
  await setSemanticStoryboardPrompt(page, prompt);
  await startSemanticStoryboard(page);
  await expectSemanticStoryboardTerminal(page, lane, layout);
  return (await semanticStoryboardSessionObservation(page)).snapshot;
}

async function expectCertifiedProgram(
  page: Page,
  lane: DecodedSemanticStoryboardFixtureLane,
): Promise<void> {
  const terminal = lane.checkpoints.at(-1);
  if (!terminal) throw new Error(`${lane.scenarioId} has no certified program`);
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-program-sha256",
    terminal.transition.checkpoint.certificate.body.resultProgramSha256,
  );
}

async function expectMinimumHitTargets(page: Page): Promise<void> {
  const undersized = await semanticStoryboardRoot(page).evaluate((root) =>
    Array.from(
      root.querySelectorAll<HTMLElement>("button,a,textarea"),
      (element) => {
        const bounds = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          label:
            element.getAttribute("aria-label") ??
            element.textContent?.trim() ??
            element.tagName,
          width: bounds.width,
          height: bounds.height,
          visible:
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            bounds.width > 0 &&
            bounds.height > 0,
        };
      },
    ).filter(
      ({ visible, width, height }) => visible && (width < 44 || height < 44),
    ),
  );
  expect(undersized).toEqual([]);
}

function viewportString(viewport: {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}): string {
  return `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;
}

function nearestRankP95(values: readonly number[]): number {
  if (values.length === 0) throw new Error("No interruption latency samples");
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.ceil(sorted.length * 0.95) - 1];
}

function acceptedPostPaintTimeline(
  history: Awaited<ReturnType<typeof semanticStoryboardSessionHistory>>,
): ReadonlyMap<string, number> {
  const timeline = new Map<string, number>();
  for (const observation of history) {
    for (const accepted of observation.snapshot.runtime.accepted) {
      const checkpointId = accepted.event.transition.checkpoint.checkpointId;
      if (!timeline.has(checkpointId)) {
        timeline.set(checkpointId, observation.observedAtMs);
      }
    }
  }
  return timeline;
}

function latencyStatistics(values: readonly number[], thresholdMs: number) {
  expect(values.length).toBeGreaterThan(0);
  expect(values.every((value) => Number.isFinite(value) && value >= 0)).toBe(
    true,
  );
  const p95Ms = nearestRankP95(values);
  const maxMs = Math.max(...values);
  expect(p95Ms).toBeLessThan(thresholdMs);
  return {
    sampleCount: values.length,
    p95Ms: Number(p95Ms.toFixed(3)),
    maxMs: Number(maxMs.toFixed(3)),
  };
}

function roundedMs(value: number): number {
  return Number(value.toFixed(3));
}

async function expectExactInterruptedPrefix(
  page: Page,
  lane: DecodedSemanticStoryboardFixtureLane,
  modelCheckpointCount: number,
): Promise<void> {
  const anchor = semanticStoryboardFixture(lane.request.problemSpec).anchor;
  const checkpoint =
    modelCheckpointCount === 0
      ? anchor.checkpoints[0]
      : lane.checkpoints[modelCheckpointCount - 1];
  if (!checkpoint)
    throw new Error("The expected interruption prefix is absent");
  const observation = await semanticStoryboardSessionObservation(page);
  expect(acceptedModelCheckpointIds(observation.snapshot)).toEqual(
    lane.checkpointIds.slice(0, modelCheckpointCount),
  );
  const accepted = observation.snapshot.runtime.accepted.at(-1);
  if (!accepted) throw new Error("The interrupted frontier has no certificate");
  expect(observation.snapshot.runtime.committedScene.revision).toBe(
    checkpoint.transition.resultScene.revision,
  );
  expect(observation.snapshot.runtime.committedSemanticScene).toEqual(
    checkpoint.transition.resultSemanticScene,
  );
  expect(
    accepted.event.transition.checkpoint.certificate.body
      .resultLowLevelSceneSha256,
  ).toBe(
    checkpoint.transition.checkpoint.certificate.body.resultLowLevelSceneSha256,
  );
  expect(
    accepted.event.transition.checkpoint.certificate.body
      .resultSemanticSceneSha256,
  ).toBe(
    checkpoint.transition.checkpoint.certificate.body.resultSemanticSceneSha256,
  );
  expect(observation.snapshot.runtime.rendererTrusted).toBe(true);
  const stage = await observeSemanticStoryboardStage(page);
  expect(stage.viewBox).toBe(
    viewportString(
      checkpoint.transition.checkpoint.presentation.resultViewports.cinematic,
    ),
  );
  expect(stage.programSha256).toBe(
    checkpoint.transition.checkpoint.certificate.body.resultProgramSha256,
  );
  expect(stage.certificateHead).toBe(
    checkpoint.transition.resultSemanticScene.certificateHeadSha256,
  );
  await expectSemanticStoryboardSvgMatchesScene(
    page,
    checkpoint.transition.resultScene,
  );
}

interface InterruptionCase {
  readonly surface: SemanticStoryboardInterruptionSurface;
  readonly route: string;
  readonly prompt: string;
  readonly lane: DecodedSemanticStoryboardFixtureLane;
  readonly expectedModelCheckpointCount: number;
  readonly replay?: true;
}

async function runInterruptionTrial(
  page: Page,
  item: InterruptionCase,
): Promise<SemanticStoryboardInterruptionObservation> {
  await page.goto(item.route);
  await waitForSemanticStoryboardBridge(page);
  await setSemanticStoryboardPrompt(page, item.prompt);
  if (item.replay) {
    await startSemanticStoryboard(page);
    await expectSemanticStoryboardTerminal(page, item.lane, "cinematic");
    await page.getByRole("button", { name: "Replay" }).click();
    await expect(semanticStoryboardRoot(page)).toHaveAttribute(
      "data-session-status",
      "replaying",
    );
  } else {
    await startSemanticStoryboard(page);
  }
  const anchorViewport = semanticStoryboardFixture(
    item.lane.request.problemSpec,
  ).anchor.resultFrontiers.cinematic.viewport;
  const targetViewport =
    item.lane.checkpoints[0]?.transition.checkpoint.presentation.resultViewports
      .cinematic;
  const interruption = await interruptSemanticStoryboardAtSurface(page, {
    surface: item.surface,
    ...(anchorViewport ? { baseViewBox: viewportString(anchorViewport) } : {}),
    ...(targetViewport
      ? { targetViewBox: viewportString(targetViewport) }
      : {}),
  });
  await expectExactInterruptedPrefix(
    page,
    item.lane,
    item.expectedModelCheckpointCount,
  );
  return interruption;
}

async function attachInterruptionEvidence(
  testInfo: TestInfo,
  observations: readonly SemanticStoryboardInterruptionObservation[],
): Promise<void> {
  const bySurface = Object.groupBy(observations, ({ surface }) => surface);
  const results = Object.entries(bySurface).map(([surface, samples]) => {
    const values = (samples ?? []).map(({ latencyMs }) => latencyMs);
    const p95Ms = nearestRankP95(values);
    expect(values).toHaveLength(INTERRUPTION_TRIALS);
    expect(p95Ms).toBeLessThan(INTERRUPTION_P95_LIMIT_MS);
    return {
      surface,
      trials: values.length,
      latenciesMs: values.map((value) => Number(value.toFixed(3))),
      p95Ms: Number(p95Ms.toFixed(3)),
      maxMs: Number(Math.max(...values).toFixed(3)),
    };
  });
  await testInfo.attach("semantic-storyboard-interruption-observation", {
    body: JSON.stringify(
      {
        schemaVersion: 1,
        clock: "browser_performance_now",
        thresholdMs: INTERRUPTION_P95_LIMIT_MS,
        results,
        unavailableSurfaces: [],
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
}

test("the Director owns semantic teaching order instead of a hardcoded stage order", async ({
  page,
  baseURL,
}) => {
  if (!baseURL) throw new Error("The storyboard E2E base URL is unavailable");
  const readNetwork = observeSemanticStoryboardNetwork(
    page,
    baseURL,
    ACCELERATED_ROUTE,
  );
  await page.goto(ACCELERATED_ROUTE);
  await waitForSemanticStoryboardBridge(page);

  const pathsLane = semanticStoryboardLane(STORYBOARD_SCENARIOS.pathsFirst);
  const paths = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.pathsFirst,
    pathsLane,
  );
  expect(acceptedModelCheckpointIds(paths)).toEqual(pathsLane.checkpointIds);
  await expectCertifiedProgram(page, pathsLane);
  expect(acceptedModelCheckpointIds(paths).slice(0, 2)).toEqual([
    "storyboard-checkpoint-trace-lower-angle",
    "storyboard-checkpoint-trace-higher-angle",
  ]);
  await expect(page.getByTestId("storyboard-speed-fieldset")).toHaveAttribute(
    "disabled",
    "",
  );
  await expect(page.getByTestId("storyboard-angle-fieldset")).toHaveAttribute(
    "disabled",
    "",
  );
  await expect(
    page.getByLabel("What should the board explain next?"),
  ).toBeEnabled();
  await expectSemanticStoryboardSvgMatchesScene(page, pathsLane.resultScene);

  await resetSemanticStoryboard(page);
  const higherLane = semanticStoryboardLane(STORYBOARD_SCENARIOS.higherFirst);
  const higher = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.higherFirst,
    higherLane,
  );
  expect(acceptedModelCheckpointIds(higher)).toEqual(higherLane.checkpointIds);
  await expectCertifiedProgram(page, higherLane);
  expect(acceptedModelCheckpointIds(higher).slice(0, 2)).toEqual([
    "storyboard-checkpoint-trace-higher-angle",
    "storyboard-checkpoint-trace-lower-angle",
  ]);

  await resetSemanticStoryboard(page);
  const formulaLane = semanticStoryboardLane(STORYBOARD_SCENARIOS.formulaFirst);
  const formula = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.formulaFirst,
    formulaLane,
  );
  expect(acceptedModelCheckpointIds(formula)).toEqual(
    formulaLane.checkpointIds,
  );
  await expectCertifiedProgram(page, formulaLane);
  expect(acceptedModelCheckpointIds(formula)[0]).toBe(
    "storyboard-checkpoint-reveal-range-formula",
  );

  await resetSemanticStoryboard(page);
  await page.getByTestId("storyboard-angles-30-45").click();
  const formulaInequalityLane = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.formulaInequality,
  );
  const formulaInequality = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.formulaInequality,
    formulaInequalityLane,
  );
  expect(acceptedModelCheckpointIds(formulaInequality)).toEqual(
    formulaInequalityLane.checkpointIds,
  );
  await expectCertifiedProgram(page, formulaInequalityLane);

  await resetSemanticStoryboard(page);
  const motionInequalityLane = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.motionInequality,
  );
  const motionInequality = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.motionInequality,
    motionInequalityLane,
  );
  expect(acceptedModelCheckpointIds(motionInequality)).toEqual(
    motionInequalityLane.checkpointIds,
  );
  await expectCertifiedProgram(page, motionInequalityLane);

  await resetSemanticStoryboard(page);
  await page.getByTestId("storyboard-angles-45-60").click();
  const heightThenFlightLane = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.heightThenFlight,
  );
  const heightThenFlight = await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.heightThenFlight,
    heightThenFlightLane,
  );
  expect(acceptedModelCheckpointIds(heightThenFlight)).toEqual(
    heightThenFlightLane.checkpointIds,
  );
  await expectCertifiedProgram(page, heightThenFlightLane);
  await expectSemanticStoryboardSvgMatchesScene(
    page,
    heightThenFlightLane.resultScene,
  );
  expectSemanticStoryboardProviderFree(readNetwork());
});

test("provider-free first-visible latency stays inside its percentile budgets across fresh contexts", async ({
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(300_000);
  if (!baseURL) throw new Error("The storyboard E2E base URL is unavailable");
  const higher = semanticStoryboardLane(STORYBOARD_SCENARIOS.higherFirst);
  const expectedCheckpointIds = ["storyboard-anchor", ...higher.checkpointIds];
  const trials: Array<{
    ordinal: number;
    submitAtMs: number;
    anchorVisibleAtMs: number;
    submitToAnchorVisibleMs: number;
    directorDispatchAtMs: number;
    firstModelVisibleAtMs: number;
    directorDispatchToFirstModelVisibleMs: number;
    firstModelCheckpointEventAtMs: number;
    firstModelCheckpointEventToVisibleMs: number;
    postPaintAcceptanceToNextBeatVisible: Array<{
      fromCheckpointId: string;
      toCheckpointId: string;
      postPaintAcceptedAtMs: number;
      nextVisibleAtMs: number;
      durationMs: number;
    }>;
  }> = [];

  for (let ordinal = 1; ordinal <= LATENCY_CONTEXTS; ordinal += 1) {
    const context = await browser.newContext({
      viewport: { width: 1_280, height: 720 },
      colorScheme: "dark",
      reducedMotion: "reduce",
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    try {
      const readNetwork = observeSemanticStoryboardNetwork(
        page,
        baseURL,
        ACCELERATED_ROUTE,
      );
      await page.goto(new URL(ACCELERATED_ROUTE, baseURL).href);
      await waitForSemanticStoryboardBridge(page);
      await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.higherFirst);
      await installSemanticStoryboardVisibleCheckpointProbe(page);
      const submitAtMs = await page
        .getByRole("button", { name: "Make it visible" })
        .evaluate((button) => {
          const observedAtMs = performance.now();
          (button as HTMLButtonElement).click();
          return observedAtMs;
        });
      await expectSemanticStoryboardTerminal(page, higher, "cinematic");

      const [visibleHistory, bridge, eventHistory, sessionHistory] =
        await Promise.all([
          semanticStoryboardVisibleCheckpointHistory(page),
          semanticStoryboardBridgeState(page),
          semanticStoryboardEventHistory(page),
          semanticStoryboardSessionHistory(page),
        ]);
      expect(visibleHistory.map(({ checkpointId }) => checkpointId)).toEqual(
        expectedCheckpointIds,
      );
      const checkpointEvents = eventHistory.filter(
        (event) => event.checkpointId !== null,
      );
      expect(checkpointEvents.map(({ checkpointId }) => checkpointId)).toEqual(
        expectedCheckpointIds,
      );
      const director = bridge.calls.find(
        (call) => call.routingMode === "director",
      );
      if (!director) throw new Error("The Director dispatch was not observed");
      const visibleByCheckpoint = new Map(
        visibleHistory.map((item) => [item.checkpointId, item.observedAtMs]),
      );
      const eventByCheckpoint = new Map(
        checkpointEvents.map((item) => [
          item.checkpointId as string,
          item.observedAtMs,
        ]),
      );
      const postPaintByCheckpoint = acceptedPostPaintTimeline(sessionHistory);
      const anchorVisibleAtMs = visibleByCheckpoint.get("storyboard-anchor");
      const firstModelId = higher.checkpointIds[0];
      const firstModelVisibleAtMs = firstModelId
        ? visibleByCheckpoint.get(firstModelId)
        : undefined;
      if (
        anchorVisibleAtMs === undefined ||
        firstModelId === undefined ||
        firstModelVisibleAtMs === undefined
      ) {
        throw new Error("The anchor or first model-visible boundary is absent");
      }
      const firstModelCheckpointEventAtMs = eventByCheckpoint.get(firstModelId);
      if (firstModelCheckpointEventAtMs === undefined) {
        throw new Error("The first model checkpoint event is absent");
      }
      const postPaintAcceptanceToNextBeatVisible = expectedCheckpointIds
        .slice(1)
        .map((toCheckpointId, index) => {
          const fromCheckpointId = expectedCheckpointIds[index];
          const postPaintAcceptedAtMs =
            postPaintByCheckpoint.get(fromCheckpointId);
          const nextVisibleAtMs = visibleByCheckpoint.get(toCheckpointId);
          if (
            postPaintAcceptedAtMs === undefined ||
            nextVisibleAtMs === undefined
          ) {
            throw new Error(
              `Missing post-paint/visible join for ${fromCheckpointId} -> ${toCheckpointId}`,
            );
          }
          return {
            fromCheckpointId,
            toCheckpointId,
            postPaintAcceptedAtMs,
            nextVisibleAtMs,
            durationMs: nextVisibleAtMs - postPaintAcceptedAtMs,
          };
        });
      trials.push({
        ordinal,
        submitAtMs,
        anchorVisibleAtMs,
        submitToAnchorVisibleMs: anchorVisibleAtMs - submitAtMs,
        directorDispatchAtMs: director.observedAtMs,
        firstModelVisibleAtMs,
        directorDispatchToFirstModelVisibleMs:
          firstModelVisibleAtMs - director.observedAtMs,
        firstModelCheckpointEventAtMs,
        firstModelCheckpointEventToVisibleMs:
          firstModelVisibleAtMs - firstModelCheckpointEventAtMs,
        postPaintAcceptanceToNextBeatVisible,
      });
      expectSemanticStoryboardProviderFree(readNetwork());
    } finally {
      await context.close();
    }
  }

  const submitToAnchorVisible = trials.map(
    ({ submitToAnchorVisibleMs }) => submitToAnchorVisibleMs,
  );
  const directorDispatchToFirstModelVisible = trials.map(
    ({ directorDispatchToFirstModelVisibleMs }) =>
      directorDispatchToFirstModelVisibleMs,
  );
  const firstModelCheckpointEventToVisible = trials.map(
    ({ firstModelCheckpointEventToVisibleMs }) =>
      firstModelCheckpointEventToVisibleMs,
  );
  const postPaintAcceptanceToNextBeatVisible = trials.flatMap((trial) =>
    trial.postPaintAcceptanceToNextBeatVisible.map(
      ({ durationMs }) => durationMs,
    ),
  );
  const statistics = {
    submitToAnchorVisible: latencyStatistics(
      submitToAnchorVisible,
      LATENCY_THRESHOLDS_MS.submitToAnchorVisibleP95,
    ),
    directorDispatchToFirstModelVisible: latencyStatistics(
      directorDispatchToFirstModelVisible,
      LATENCY_THRESHOLDS_MS.directorDispatchToFirstModelVisibleP95,
    ),
    firstModelCheckpointEventToVisible: latencyStatistics(
      firstModelCheckpointEventToVisible,
      LATENCY_THRESHOLDS_MS.firstModelCheckpointEventToVisibleP95,
    ),
    postPaintAcceptanceToNextBeatVisible: latencyStatistics(
      postPaintAcceptanceToNextBeatVisible,
      LATENCY_THRESHOLDS_MS.postPaintAcceptanceToNextBeatVisibleP95,
    ),
  };
  await testInfo.attach(
    "semantic-storyboard-provider-free-latency-observation",
    {
      body: JSON.stringify(
        {
          schemaVersion: 1,
          clock: "browser_performance_now",
          freshContextCount: trials.length,
          route: ACCELERATED_ROUTE,
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
          thresholdsMs: LATENCY_THRESHOLDS_MS,
          statistics,
          trials: trials.map((trial) => ({
            ...trial,
            submitAtMs: roundedMs(trial.submitAtMs),
            anchorVisibleAtMs: roundedMs(trial.anchorVisibleAtMs),
            submitToAnchorVisibleMs: roundedMs(trial.submitToAnchorVisibleMs),
            directorDispatchAtMs: roundedMs(trial.directorDispatchAtMs),
            firstModelVisibleAtMs: roundedMs(trial.firstModelVisibleAtMs),
            directorDispatchToFirstModelVisibleMs: roundedMs(
              trial.directorDispatchToFirstModelVisibleMs,
            ),
            firstModelCheckpointEventAtMs: roundedMs(
              trial.firstModelCheckpointEventAtMs,
            ),
            firstModelCheckpointEventToVisibleMs: roundedMs(
              trial.firstModelCheckpointEventToVisibleMs,
            ),
            postPaintAcceptanceToNextBeatVisible:
              trial.postPaintAcceptanceToNextBeatVisible.map((item) => ({
                ...item,
                postPaintAcceptedAtMs: roundedMs(item.postPaintAcceptedAtMs),
                nextVisibleAtMs: roundedMs(item.nextVisibleAtMs),
                durationMs: roundedMs(item.durationMs),
              })),
          })),
        },
        null,
        2,
      ),
      contentType: "application/json",
    },
  );
});

test("continuation extends the exact accepted frontier and Replay performs no stream call or clear", async ({
  page,
  baseURL,
}) => {
  if (!baseURL) throw new Error("The storyboard E2E base URL is unavailable");
  const readNetwork = observeSemanticStoryboardNetwork(
    page,
    baseURL,
    ACCELERATED_ROUTE,
  );
  await page.goto(ACCELERATED_ROUTE);
  await waitForSemanticStoryboardBridge(page);
  const higherLane = semanticStoryboardLane(STORYBOARD_SCENARIOS.higherFirst);
  await runFreshScenario(page, STORYBOARD_PROMPTS.higherFirst, higherLane);
  const before = (await semanticStoryboardSessionObservation(page)).snapshot;
  const beforeCalls = await semanticStoryboardBridgeState(page);
  expect(beforeCalls.runnerCallCount).toBe(2);
  await rememberSemanticStoryboardElement(
    page,
    "projectile-comparison__ground_axis",
    "__storyboard_ground__",
  );
  await rememberSemanticStoryboardElement(
    page,
    "projectile-comparison__trajectory_higher",
    "__storyboard_higher_path__",
  );

  await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.continue);
  await continueSemanticStoryboard(page);
  const continuation = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.continuationFromHigherComplete,
  );
  await expectSemanticStoryboardTerminal(page, continuation, "cinematic");
  const after = (await semanticStoryboardSessionObservation(page)).snapshot;
  expect(acceptedModelCheckpointIds(after)).toEqual([
    ...higherLane.checkpointIds,
    ...continuation.checkpointIds,
  ]);
  expect(after.runtime.committedScene.revision).toBe(
    before.runtime.committedScene.revision + 1,
  );
  expect(after.runtime.committedSemanticScene.revision).toBe(
    before.runtime.committedSemanticScene.revision + 1,
  );
  const afterCalls = await semanticStoryboardBridgeState(page);
  expect(afterCalls.runnerCallCount).toBe(3);
  expect(afterCalls.calls[2]).toMatchObject({
    routingMode: "director",
    prompt: STORYBOARD_PROMPTS.continue,
    baseRevision: before.runtime.committedScene.revision,
    semanticRevision: before.runtime.committedSemanticScene.revision,
    certificateHeadSha256:
      before.runtime.committedSemanticScene.certificateHeadSha256,
    acceptedRecordCount: higherLane.checkpointIds.length,
  });
  await expectSemanticStoryboardElementStable(
    page,
    "projectile-comparison__ground_axis",
    "__storyboard_ground__",
  );
  await expectSemanticStoryboardElementStable(
    page,
    "projectile-comparison__trajectory_higher",
    "__storyboard_higher_path__",
  );
  const terminalSignature = await expectSemanticStoryboardSvgMatchesScene(
    page,
    continuation.resultScene,
  );

  await page.getByRole("button", { name: "Replay" }).click();
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-session-status",
    "replaying",
  );
  await waitForSemanticStoryboardStatus(page, "paused");
  await expect(semanticStoryboardStage(page)).toHaveAttribute(
    "data-phase",
    "completed",
  );
  expect(await semanticStoryboardBridgeState(page)).toEqual(afterCalls);
  await expectSemanticStoryboardElementStable(
    page,
    "projectile-comparison__ground_axis",
    "__storyboard_ground__",
  );
  expect(
    await expectSemanticStoryboardSvgMatchesScene(
      page,
      continuation.resultScene,
    ),
  ).toEqual(terminalSignature);
  expectSemanticStoryboardProviderFree(readNetwork());
});

test("decline and malformed-tail acceptance preserve only certified visible work", async ({
  page,
}) => {
  await page.goto(ACCELERATED_ROUTE);
  await waitForSemanticStoryboardBridge(page);
  const abstain = semanticStoryboardLane(STORYBOARD_SCENARIOS.abstain);
  await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.abstain);
  await startSemanticStoryboard(page);
  await expectSemanticStoryboardTerminal(page, abstain, "cinematic", {
    sessionStatus: "declined",
    phase: "declined",
  });
  const declined = (await semanticStoryboardSessionObservation(page)).snapshot;
  expect(acceptedModelCheckpointIds(declined)).toEqual([]);
  expect(declined.runtime.accepted).toHaveLength(1);
  await expect(page.getByTestId("semantic-storyboard-decline")).toContainText(
    "outside the supported storyboard vocabulary",
  );
  await expectSemanticStoryboardSvgMatchesScene(page, abstain.resultScene);
  await expectSemanticStoryboardFrontierQuiet(page);

  const fixture = semanticStoryboardFixture();
  for (const negative of fixture.negativeLanes) {
    await page.goto(ACCELERATED_ROUTE);
    await waitForSemanticStoryboardBridge(page);
    if (negative.request.routingMode !== "director") {
      throw new Error(`${negative.scenarioId} is not a Director fixture lane`);
    }
    await setSemanticStoryboardPrompt(page, negative.request.prompt);
    await startSemanticStoryboard(page);
    await expectSemanticStoryboardTerminal(page, negative, "cinematic", {
      sessionStatus: "declined",
      phase: "declined",
    });
    const safe = (await semanticStoryboardSessionObservation(page)).snapshot;
    expect(acceptedModelCheckpointIds(safe)).toEqual([]);
    expect(safe.runtime.accepted).toHaveLength(1);
    expect(safe.runtime.committedSemanticScene).toEqual(
      fixture.anchor.resultSemanticScene,
    );
    expect(
      (await semanticStoryboardEventHistory(page))
        .filter((event) => event.checkpointId !== null)
        .map((event) => event.checkpointId),
    ).toEqual(["storyboard-anchor"]);
    await expectSemanticStoryboardSvgMatchesScene(
      page,
      fixture.anchor.resultScene,
    );
    await expectSemanticStoryboardFrontierQuiet(page);
  }

  await page.goto(ACCELERATED_ROUTE);
  await waitForSemanticStoryboardBridge(page);
  const acceptedPrefix = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.acceptedPrefix,
  );
  await runFreshScenario(
    page,
    STORYBOARD_PROMPTS.acceptedPrefix,
    acceptedPrefix,
  );
  const accepted = (await semanticStoryboardSessionObservation(page)).snapshot;
  expect(acceptedModelCheckpointIds(accepted)).toEqual(
    acceptedPrefix.checkpointIds,
  );
  await expect(
    page.getByTestId("semantic-storyboard-accepted-prefix"),
  ).toBeVisible();
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-completion-reason",
    "accepted_prefix",
  );
  await expectSemanticStoryboardSvgMatchesScene(
    page,
    acceptedPrefix.resultScene,
  );
  await expectSemanticStoryboardFrontierQuiet(page);

  const acceptedPrefixCalls = await semanticStoryboardBridgeState(page);
  const acceptedPrefixSignature = await expectSemanticStoryboardSvgMatchesScene(
    page,
    acceptedPrefix.resultScene,
  );
  await page.getByRole("button", { name: "Replay" }).click();
  await waitForSemanticStoryboardStatus(page, "paused");
  expect(await semanticStoryboardBridgeState(page)).toEqual(
    acceptedPrefixCalls,
  );
  expect(
    await expectSemanticStoryboardSvgMatchesScene(
      page,
      acceptedPrefix.resultScene,
    ),
  ).toEqual(acceptedPrefixSignature);

  const recovery = fixture.continuations.find(
    (lane) => lane.fromScenarioId === acceptedPrefix.scenarioId,
  );
  if (!recovery) {
    throw new Error("The accepted-prefix fixture has no recovery continuation");
  }
  await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.continue);
  await continueSemanticStoryboard(page);
  await expectSemanticStoryboardTerminal(page, recovery, "cinematic");
  const recovered = (await semanticStoryboardSessionObservation(page)).snapshot;
  expect(acceptedModelCheckpointIds(recovered)).toEqual([
    ...acceptedPrefix.checkpointIds,
    ...recovery.checkpointIds,
  ]);
  const recoveryCalls = await semanticStoryboardBridgeState(page);
  expect(recoveryCalls.runnerCallCount).toBe(3);
  expect(recoveryCalls.calls[2]).toMatchObject({
    routingMode: "director",
    prompt: STORYBOARD_PROMPTS.continue,
    baseRevision: accepted.runtime.committedScene.revision,
    semanticRevision: accepted.runtime.committedSemanticScene.revision,
    certificateHeadSha256:
      accepted.runtime.committedSemanticScene.certificateHeadSha256,
    acceptedRecordCount: acceptedPrefix.checkpointIds.length,
  });
  await expectSemanticStoryboardSvgMatchesScene(page, recovery.resultScene);
});

test("Stop qualifies every externally observable active surface in four trials and continues from the exact trace frontier", async ({
  page,
}, testInfo) => {
  test.setTimeout(300_000);
  const higher = semanticStoryboardLane(STORYBOARD_SCENARIOS.higherFirst);
  const formula = semanticStoryboardLane(STORYBOARD_SCENARIOS.formulaFirst);
  const cases: readonly InterruptionCase[] = [
    {
      surface: "provider_wait",
      route: PROVIDER_WAIT_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 0,
    },
    {
      surface: "draw",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 0,
    },
    {
      surface: "trace_path",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 1,
    },
    {
      surface: "marker_movement",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 1,
    },
    {
      surface: "relationship_morph",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.formulaFirst,
      lane: formula,
      expectedModelCheckpointCount: 3,
    },
    {
      surface: "camera_focus",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 1,
    },
    {
      surface: "hold",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.formulaFirst,
      lane: formula,
      expectedModelCheckpointCount: 1,
    },
    {
      surface: "post_paint_barrier",
      route: NORMAL_MOTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 1,
    },
    {
      surface: "replay",
      route: REPLAY_INTERRUPTION_ROUTE,
      prompt: STORYBOARD_PROMPTS.higherFirst,
      lane: higher,
      expectedModelCheckpointCount: 1,
      replay: true,
    },
  ];
  const observations: SemanticStoryboardInterruptionObservation[] = [];
  for (const item of cases) {
    for (let trial = 0; trial < INTERRUPTION_TRIALS; trial += 1) {
      const observation = await runInterruptionTrial(page, item);
      observations.push(observation);
      const continueFromTrace = item.surface === "trace_path" && trial === 3;
      if (continueFromTrace) {
        await rememberSemanticStoryboardElement(
          page,
          "projectile-comparison__ground_axis",
          "__storyboard_interrupted_ground__",
        );
        await rememberSemanticStoryboardElement(
          page,
          "projectile-comparison__trajectory_higher",
          "__storyboard_interrupted_higher__",
        );
      }
      await expectSemanticStoryboardFrontierQuiet(page);
      if (!continueFromTrace) continue;

      const before = await semanticStoryboardSessionObservation(page);
      await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.continue);
      await continueSemanticStoryboard(page);
      const continuation = semanticStoryboardLane(
        STORYBOARD_SCENARIOS.continuationFromHigherFirstBeat,
      );
      await expectSemanticStoryboardTerminal(page, continuation, "cinematic", {
        timeout: 45_000,
      });
      const calls = await semanticStoryboardBridgeState(page);
      expect(calls.runnerCallCount).toBe(3);
      expect(calls.calls[2]).toMatchObject({
        routingMode: "director",
        prompt: STORYBOARD_PROMPTS.continue,
        baseRevision: before.snapshot.runtime.committedScene.revision,
        semanticRevision:
          before.snapshot.runtime.committedSemanticScene.revision,
        certificateHeadSha256:
          before.snapshot.runtime.committedSemanticScene.certificateHeadSha256,
        acceptedRecordCount: 1,
      });
      await expectSemanticStoryboardElementStable(
        page,
        "projectile-comparison__ground_axis",
        "__storyboard_interrupted_ground__",
      );
      await expectSemanticStoryboardElementStable(
        page,
        "projectile-comparison__trajectory_higher",
        "__storyboard_interrupted_higher__",
      );
      await expectSemanticStoryboardSvgMatchesScene(
        page,
        continuation.resultScene,
      );
    }
  }
  await attachInterruptionEvidence(testInfo, observations);
});

test("compact layouts remain exact at 375 and 320 pixels and honor the actual OS reduced-motion preference", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const explicitRoute =
    "/e2e/semantic-storyboard?layout=compact&motion=real&speed=accelerated&proof=none";
  await page.goto(explicitRoute);
  await waitForSemanticStoryboardBridge(page);
  const paths = semanticStoryboardLane(STORYBOARD_SCENARIOS.pathsFirst);
  await runFreshScenario(page, STORYBOARD_PROMPTS.pathsFirst, paths, "compact");
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-layout",
    "compact",
  );
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-reduced-motion",
    "false",
  );
  const fullMotionSignature = await expectSemanticStoryboardSvgMatchesScene(
    page,
    paths.resultScene,
  );
  await expectNoSemanticStoryboardOverflow(page);
  await expectSemanticStoryboardCaptionContained(page);
  await expectMinimumHitTargets(page);

  await page.setViewportSize({ width: 320, height: 568 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/labs/storyboard");
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-layout",
    "compact",
  );
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-reduced-motion",
    "true",
  );
  expect(
    await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);
  await startSemanticStoryboard(page);
  await expectSemanticStoryboardTerminal(page, paths, "compact");
  const reducedSignature = await expectSemanticStoryboardSvgMatchesScene(
    page,
    paths.resultScene,
  );
  expect(reducedSignature).toEqual(fullMotionSignature);
  await expectNoSemanticStoryboardOverflow(page);
  await expectSemanticStoryboardCaptionContained(page);
  await expectMinimumHitTargets(page);
});
