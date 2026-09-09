import fs from "node:fs/promises";
import path from "node:path";

import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

import boundaryHighFixtureValue from "../src/features/live-scene/fixtures/completing-square-parametric-b16-c17.v3.json";
import boundaryLowFixtureValue from "../src/features/live-scene/fixtures/completing-square-parametric-b2-c80.v3.json";
import primaryFixtureValue from "../src/features/live-scene/fixtures/completing-square-parametric-b8-c20.v3.json";
import {
  decodeParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneCheckpointEventV3,
} from "../src/lib/live-scene/parametric-choreography-stream";
import { orderSceneNodesForSvgPaint } from "../src/features/live-scene/svg-node-reconciler";
import type { SceneNode } from "../src/lib/live-scene";

const E2E_BRIDGE_KEY = "__MURMUR_PARAMETRIC_CHOREOGRAPHY_E2E__";

interface RunnerCallObservation {
  readonly ordinal: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly problemText: string | null;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly checkpointId: string | null;
  readonly cornerClarified: boolean;
  readonly requestedRoute: unknown;
}

interface E2EBridgeState {
  readonly runnerCallCount: number;
  readonly calls: readonly RunnerCallObservation[];
}

interface ExpectedFinalBoard {
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
}

interface StageObservation extends ExpectedFinalBoard {
  readonly phase: string;
  readonly checkpointId: string;
  readonly visibleCheckpointId: string;
  readonly settledMainCount: string;
  readonly cornerClarified: string;
  readonly rendererTrusted: string;
}

interface ProviderFreeObservation {
  readonly liveSceneRequests: string[];
  readonly unexpectedRequests: string[];
}

interface MainFixtureValue {
  readonly lanes: {
    readonly main: {
      readonly events: readonly unknown[];
    };
  };
}

function stage(page: Page): Locator {
  return page.getByTestId("live-choreography-stage");
}

function fixtureCheckpoints(
  fixture: MainFixtureValue,
): readonly ParametricChoreographySceneCheckpointEventV3[] {
  return fixture.lanes.main.events
    .map(decodeParametricChoreographySceneStreamEventV3)
    .filter(
      (
        event,
      ): event is ParametricChoreographySceneCheckpointEventV3 =>
        event.type === "parametric_choreography_scene_checkpoint",
    );
}

function expectedFinalBoard(
  fixture: MainFixtureValue,
  layout: "cinematic" | "compact",
): ExpectedFinalBoard {
  const nodes = new Map<string, SceneNode>();
  const checkpoints = fixtureCheckpoints(fixture);
  for (const checkpoint of checkpoints) {
    for (const operation of checkpoint.patch.operations) {
      if (operation.op === "remove") nodes.delete(operation.id);
      else nodes.set(operation.node.id, operation.node);
    }
  }
  const final = checkpoints.at(-1)!;
  const viewport = final.semantic.presentation.resultViewports[layout];
  return Object.freeze({
    caption: final.patch.narration,
    viewBox: `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`,
    nodeIds: Object.freeze(
      orderSceneNodesForSvgPaint([...nodes.values()]).map((node) => node.id),
    ),
  });
}

async function waitForBridge(page: Page): Promise<void> {
  await page.waitForFunction(
    (key) =>
      Boolean(
        (window as typeof window & Record<string, unknown>)[key],
      ),
    E2E_BRIDGE_KEY,
  );
}

async function bridgeState(page: Page): Promise<E2EBridgeState> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      | { readonly version: number; getState(): E2EBridgeState }
      | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The parametric choreography e2e bridge is unavailable");
    }
    return bridge.getState();
  }, E2E_BRIDGE_KEY);
}

function observeProviderFreeRequests(page: Page): ProviderFreeObservation {
  const liveSceneRequests: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    const local =
      url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const target = local
      ? `${url.pathname}${url.search}`
      : `${url.origin}${url.pathname}`;
    if (url.pathname.startsWith("/api/live-scenes")) {
      liveSceneRequests.push(target);
    }
    const allowed =
      local &&
      request.method() === "GET" &&
      (url.pathname === "/e2e/parametric-choreography" ||
        url.pathname.startsWith("/_next/") ||
        url.pathname.startsWith("/__nextjs_font/") ||
        url.pathname === "/favicon.ico");
    if (!allowed) unexpectedRequests.push(target);
  });
  return { liveSceneRequests, unexpectedRequests };
}

function expectProviderFree(observation: ProviderFreeObservation): void {
  expect(observation.liveSceneRequests).toEqual([]);
  expect(observation.unexpectedRequests).toEqual([]);
}

async function observeStage(page: Page): Promise<StageObservation> {
  return stage(page).evaluate((element) => {
    const svg = element.querySelector("svg");
    const caption = element.querySelector("figcaption p");
    if (!svg || !caption) throw new Error("The lesson stage is incomplete");
    return {
      phase: element.dataset.phase ?? "",
      checkpointId: element.dataset.checkpointId ?? "",
      visibleCheckpointId: element.dataset.visibleCheckpointId ?? "",
      settledMainCount: element.dataset.settledMainCount ?? "",
      cornerClarified: element.dataset.cornerClarified ?? "",
      rendererTrusted: element.dataset.rendererTrusted ?? "",
      caption: caption.textContent?.trim() ?? "",
      viewBox: svg.getAttribute("viewBox") ?? "",
      nodeIds: Array.from(
        svg.querySelectorAll<SVGGraphicsElement>("[data-element-id]"),
        (node) => node.dataset.elementId ?? "",
      ),
    };
  });
}

async function expectCaptionVisibleAndContained(page: Page): Promise<void> {
  const lessonStage = stage(page);
  const caption = lessonStage.locator("figcaption p");
  await expect(caption).toBeVisible();
  const bounds = await lessonStage.evaluate((element) => {
    const captionElement = element.querySelector("figcaption p");
    const board = element.querySelector('[data-testid="live-choreography-board"]');
    if (!captionElement || !board) {
      throw new Error("The stage caption or board is unavailable");
    }
    const stageRect = element.getBoundingClientRect();
    const captionRect = captionElement.getBoundingClientRect();
    const boardRect = board.getBoundingClientRect();
    return {
      stageTop: stageRect.top,
      stageBottom: stageRect.bottom,
      stageLeft: stageRect.left,
      stageRight: stageRect.right,
      captionTop: captionRect.top,
      captionBottom: captionRect.bottom,
      captionLeft: captionRect.left,
      captionRight: captionRect.right,
      captionHeight: captionRect.height,
      boardBottom: boardRect.bottom,
      layout: element.dataset.layout,
    };
  });
  expect(bounds.captionHeight).toBeGreaterThan(0);
  expect(bounds.captionTop).toBeGreaterThanOrEqual(bounds.stageTop - 0.5);
  expect(bounds.captionBottom).toBeLessThanOrEqual(bounds.stageBottom + 0.5);
  expect(bounds.captionLeft).toBeGreaterThanOrEqual(bounds.stageLeft - 0.5);
  expect(bounds.captionRight).toBeLessThanOrEqual(bounds.stageRight + 0.5);
  if (bounds.layout === "cinematic") {
    expect(bounds.boardBottom).toBeLessThanOrEqual(bounds.captionTop + 0.5);
  }
}

async function attachProofScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  const screenshotDir = path.join(
    path.dirname(testInfo.project.outputDir),
    "screenshots",
  );
  const screenshotPath = path.join(screenshotDir, `${name}.png`);
  await fs.mkdir(screenshotDir, { recursive: true });
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

async function attachBoardOnlyProofScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  const lessonStage = stage(page);
  const caption = lessonStage.locator("figcaption");
  const board = lessonStage.getByTestId("live-choreography-board");
  const screenshotDir = path.join(
    path.dirname(testInfo.project.outputDir),
    "screenshots",
  );
  const screenshotPath = path.join(screenshotDir, `${name}.png`);
  await fs.mkdir(screenshotDir, { recursive: true });
  const previousDisplay = await caption.evaluate(
    (element) => element.style.display,
  );
  await caption.evaluate((element) => {
    element.style.display = "none";
  });
  try {
    await board.screenshot({ path: screenshotPath });
  } finally {
    await caption.evaluate((element, display) => {
      element.style.display = display;
    }, previousDisplay);
  }
  await expect(caption.locator("p")).toBeVisible();
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

async function expectLatexFacts(
  page: Page,
  facts: Readonly<Record<string, string>>,
): Promise<void> {
  const board = stage(page).getByTestId("live-choreography-board");
  for (const [nodeId, latex] of Object.entries(facts)) {
    const node = board.locator(`[data-element-id="${nodeId}"]`);
    await expect(node).toHaveCount(1);
    await expect(
      node.locator('annotation[encoding="application/x-tex"]'),
    ).toHaveText(latex);
  }
}

async function expectVisibleLatexContained(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const overflows = await stage(page)
    .getByTestId("live-choreography-board")
    .evaluate((board) => {
      const tolerance = 2;
      return Array.from(
        board.querySelectorAll<SVGGraphicsElement>(
          "svg [data-element-id]",
        ),
      ).flatMap((group) => {
        const id = group.dataset.elementId ?? "unknown";
        if (id.endsWith("--incoming") || id.endsWith("--outgoing")) return [];
        const style = getComputedStyle(group);
        const groupRect = group.getBoundingClientRect();
        if (
          style.display === "none" ||
          style.visibility === "hidden" ||
          Number(style.opacity) === 0 ||
          groupRect.width === 0 ||
          groupRect.height === 0
        ) {
          return [];
        }
        const foreignObject = group.querySelector("foreignObject");
        if (!foreignObject) return [];
        const content = foreignObject.firstElementChild;
        const katex = content?.querySelector(".katex");
        if (!(content instanceof HTMLElement) || !(katex instanceof HTMLElement)) {
          return [{ id, reason: "missing KaTeX measurement surface" }];
        }
        const foreignRect = foreignObject.getBoundingClientRect();
        const katexRect = katex.getBoundingClientRect();
        const reasons = [
          content.scrollWidth > content.clientWidth + tolerance
            ? `horizontal scroll ${content.scrollWidth}/${content.clientWidth}`
            : "",
          content.scrollHeight > content.clientHeight + tolerance
            ? `vertical scroll ${content.scrollHeight}/${content.clientHeight}`
            : "",
          katexRect.left < foreignRect.left - tolerance ||
          katexRect.right > foreignRect.right + tolerance
            ? `horizontal bbox ${katexRect.left.toFixed(1)}..${katexRect.right.toFixed(1)} outside ${foreignRect.left.toFixed(1)}..${foreignRect.right.toFixed(1)}`
            : "",
          katexRect.top < foreignRect.top - tolerance ||
          katexRect.bottom > foreignRect.bottom + tolerance
            ? `vertical bbox ${katexRect.top.toFixed(1)}..${katexRect.bottom.toFixed(1)} outside ${foreignRect.top.toFixed(1)}..${foreignRect.bottom.toFixed(1)}`
            : "",
        ].filter(Boolean);
        return reasons.length > 0 ? [{ id, reason: reasons.join("; ") }] : [];
      });
    });
  expect(overflows).toEqual([]);
}

async function captureMuteFirstKeyframe(
  page: Page,
  testInfo: TestInfo,
  checkpointId: string,
  artifactName: string,
  facts: Readonly<Record<string, string>>,
): Promise<void> {
  const lessonStage = stage(page);
  await expect(lessonStage).toHaveAttribute(
    "data-visible-checkpoint-id",
    checkpointId,
  );
  await expect(lessonStage).toHaveAttribute(
    "data-checkpoint-id",
    checkpointId,
  );
  await expect(lessonStage).toHaveAttribute("data-renderer-trusted", "true");
  await expectLatexFacts(page, facts);
  await expectVisibleLatexContained(page);
  await attachBoardOnlyProofScreenshot(page, testInfo, artifactName);
}

async function captureMuteFirstProof(
  page: Page,
  testInfo: TestInfo,
): Promise<void> {
  await captureMuteFirstKeyframe(
    page,
    testInfo,
    "split_linear_term",
    "01-half-calculation",
    { "square-lesson__half_calc": "8\\div 2=4" },
  );
  await captureMuteFirstKeyframe(
    page,
    testInfo,
    "missing_corner",
    "02-missing-corner",
    {
      "square-lesson__corner_dim_h": "4",
      "square-lesson__corner_dim_v": "4",
      "square-lesson__corner_area": "?",
    },
  );
  await captureMuteFirstKeyframe(
    page,
    testInfo,
    "balance_and_complete",
    "03-balanced-addition",
    {
      "square-lesson__corner_area": "16",
      "square-lesson__eq_plus_corner": "+",
      "square-lesson__eq_corner_value": "16",
      "square-lesson__eq_plus_rhs": "+",
      "square-lesson__eq_rhs_corner": "16",
    },
  );
  await captureMuteFirstKeyframe(
    page,
    testInfo,
    "factor_square",
    "04-completed-square",
    {
      "square-lesson__eq_factor": "(x+4)^2",
      "square-lesson__eq_equal_completed": "=",
      "square-lesson__eq_completed_rhs": "36=6^2",
    },
  );
  await captureMuteFirstKeyframe(
    page,
    testInfo,
    "solve_roots",
    "05-explicit-roots",
    {
      "square-lesson__root_pm": "\\pm 6",
      "square-lesson__root_positive": "2",
      "square-lesson__root_negative": "-10",
    },
  );
}

async function expectCompletedLesson(
  page: Page,
  expected: ExpectedFinalBoard,
  revision: number,
  timeout = 20_000,
): Promise<StageObservation> {
  const lessonStage = stage(page);
  await expect(lessonStage).toHaveAttribute("data-phase", "completed", {
    timeout,
  });
  await expect(lessonStage).toHaveAttribute("data-checkpoint-id", "solve_roots");
  await expect(lessonStage).toHaveAttribute("data-settled-main-count", "8");
  await expect(lessonStage).toHaveAttribute("data-renderer-trusted", "true");
  await expect(page.getByText(`scene ${revision}`, { exact: false })).toBeVisible();
  await expectCaptionVisibleAndContained(page);
  await expectVisibleLatexContained(page);
  const observation = await observeStage(page);
  expect(observation.caption).toBe(expected.caption);
  expect(observation.viewBox).toBe(expected.viewBox);
  expect(observation.nodeIds).toEqual(expected.nodeIds);
  return observation;
}

async function teach(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Teach this equation" }).click();
}

async function stopDuringMissingCornerCue(page: Page): Promise<{
  readonly visibleCheckpointId: string;
  readonly committedCheckpointId: string;
}> {
  const observation = await page.waitForFunction(
    () => {
      const lessonStage = document.querySelector<HTMLElement>(
        '[data-testid="live-choreography-stage"]',
      );
      const stop = Array.from(document.querySelectorAll("button")).find(
        (button) =>
          button.textContent?.trim() === "Stop at this checkpoint" &&
          !button.disabled,
      );
      if (
        !lessonStage ||
        !stop ||
        lessonStage.dataset.visibleCheckpointId !== "missing_corner" ||
        lessonStage.dataset.checkpointId !== "rearrange_halves"
      ) {
        return false;
      }
      const result = {
        visibleCheckpointId: lessonStage.dataset.visibleCheckpointId,
        committedCheckpointId: lessonStage.dataset.checkpointId,
      };
      stop.click();
      return result;
    },
    undefined,
    { polling: "raf" },
  );
  const value = await observation.jsonValue();
  if (value === false) {
    throw new Error("missing-corner interruption observation was not captured");
  }
  return value;
}

test("x² + 8x = 20 reaches the exact eight-chapter certified board without a provider", async ({
  page,
}, testInfo) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/parametric-choreography?proof=keyframes");
  await waitForBridge(page);
  await expect(page.getByLabel("Equation to teach")).toHaveValue(
    "x² + 8x = 20",
  );
  await teach(page);

  await captureMuteFirstProof(page, testInfo);

  await expectCompletedLesson(
    page,
    expectedFinalBoard(primaryFixtureValue, "cinematic"),
    8,
  );
  expect(await bridgeState(page)).toEqual({
    runnerCallCount: 1,
    calls: [
      {
        ordinal: 1,
        generation: 1,
        routingMode: "reflex",
        problemText: "x² + 8x = 20",
        baseRevision: 0,
        semanticRevision: 0,
        certificateHeadSha256: null,
        checkpointId: null,
        cornerClarified: false,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  });
  expectProviderFree(requests);
});

test("stop, ask why the corner is 16, continue, and Replay preserve the exact frontier", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto(
    "/e2e/parametric-choreography?flow=adaptive&proof=keyframes",
  );
  await waitForBridge(page);
  await teach(page);

  const lessonStage = stage(page);
  expect(await stopDuringMissingCornerCue(page)).toEqual({
    visibleCheckpointId: "missing_corner",
    committedCheckpointId: "rearrange_halves",
  });
  await expect(lessonStage).toHaveAttribute("data-phase", "interrupted", {
    timeout: 2_500,
  });
  await expect(lessonStage).toHaveAttribute(
    "data-checkpoint-id",
    "missing_corner",
    { timeout: 2_500 },
  );
  await expect(lessonStage).toHaveAttribute("data-settled-main-count", "5");
  await expect(page.getByText("scene 5", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Why is the corner 16?" }).click();
  await expect(lessonStage).toHaveAttribute("data-phase", "completed");
  await expect(lessonStage).toHaveAttribute(
    "data-visible-checkpoint-id",
    "corner_detail",
  );
  await expect(lessonStage).toHaveAttribute("data-corner-clarified", "true");
  await page.getByRole("button", { name: "Continue visually" }).click();
  const beforeReplay = await expectCompletedLesson(
    page,
    expectedFinalBoard(primaryFixtureValue, "cinematic"),
    9,
  );

  const beforeReplayCalls = await bridgeState(page);
  expect(beforeReplayCalls.runnerCallCount).toBe(3);
  expect(beforeReplayCalls.calls).toEqual([
    expect.objectContaining({
      generation: 1,
      problemText: "x² + 8x = 20",
      baseRevision: 0,
      checkpointId: null,
      cornerClarified: false,
    }),
    expect.objectContaining({
      generation: 2,
      problemText: null,
      baseRevision: 5,
      semanticRevision: 5,
      certificateHeadSha256:
        primaryFixtureValue.lanes.clarifyCorner.base.certificateHeadSha256,
      checkpointId: "missing_corner",
      cornerClarified: false,
      requestedRoute: { intent: "clarify_corner" },
    }),
    expect.objectContaining({
      generation: 3,
      problemText: null,
      baseRevision: 6,
      semanticRevision: 6,
      certificateHeadSha256:
        primaryFixtureValue.lanes.continueAfterClarification.base
          .certificateHeadSha256,
      checkpointId: "missing_corner",
      cornerClarified: true,
      requestedRoute: { intent: "advance", targetStage: "solve" },
    }),
  ]);

  await page.getByRole("button", { name: "Replay" }).click();
  await expect(lessonStage).toHaveAttribute("data-phase", "replaying");
  await expectCompletedLesson(
    page,
    {
      caption: beforeReplay.caption,
      viewBox: beforeReplay.viewBox,
      nodeIds: beforeReplay.nodeIds,
    },
    9,
  );
  expect((await bridgeState(page)).runnerCallCount).toBe(3);
  expectProviderFree(requests);
});

test("compact reset/edit runs both bounded problem fixtures from a clean board", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/parametric-choreography?layout=compact");
  await waitForBridge(page);
  const equation = page.getByLabel("Equation to teach");

  const pristine = await observeStage(page);
  await equation.fill("x² + 3x = 20");
  await teach(page);
  await expect(stage(page)).toHaveAttribute("data-phase", "declined");
  await expect(
    page.getByRole("status").filter({
      hasText:
        "This provider-free proof supports x² + 2x = 80, x² + 8x = 20, or x² + 16x = 17.",
    }),
  ).toBeVisible();
  expect(await observeStage(page)).toMatchObject({
    viewBox: pristine.viewBox,
    nodeIds: pristine.nodeIds,
    checkpointId: "none",
    settledMainCount: "0",
  });
  await expect(page.getByText("scene 0", { exact: false })).toBeVisible();
  expect(await bridgeState(page)).toEqual({
    runnerCallCount: 1,
    calls: [
      expect.objectContaining({
        generation: 1,
        problemText: "x² + 3x = 20",
        baseRevision: 0,
      }),
    ],
  });

  await page.getByRole("button", { name: "Reset board" }).click();
  await expect(stage(page)).toHaveAttribute("data-phase", "idle");
  await expect(stage(page).locator("[data-element-id]")).toHaveCount(0);

  await equation.fill("x² + 2x = 80");
  await teach(page);
  await expect(stage(page)).toHaveAttribute("data-layout", "compact");
  await expectCompletedLesson(
    page,
    expectedFinalBoard(boundaryLowFixtureValue, "compact"),
    8,
  );
  const compactBounds = await stage(page).boundingBox();
  expect(compactBounds).not.toBeNull();
  expect(compactBounds!.x).toBeGreaterThanOrEqual(0);
  expect(compactBounds!.x + compactBounds!.width).toBeLessThanOrEqual(390);
  const width = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(width.documentWidth).toBeLessThanOrEqual(width.viewportWidth);

  expect((await observeStage(page)).nodeIds.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Reset board" }).click();
  await expect(stage(page)).toHaveAttribute("data-phase", "idle");
  await expect(stage(page)).toHaveAttribute("data-checkpoint-id", "none");
  await expect(stage(page).locator("[data-element-id]")).toHaveCount(0);
  expect((await observeStage(page)).nodeIds).toEqual([]);
  await expect(equation).toBeEnabled();
  await equation.fill("x² + 16x = 17");
  await teach(page);
  await expectCompletedLesson(
    page,
    expectedFinalBoard(boundaryHighFixtureValue, "compact"),
    8,
  );
  expect((await bridgeState(page)).calls).toEqual([
    expect.objectContaining({
      generation: 1,
      problemText: "x² + 3x = 20",
      baseRevision: 0,
    }),
    expect.objectContaining({
      generation: 1,
      problemText: "x² + 2x = 80",
      baseRevision: 0,
    }),
    expect.objectContaining({
      generation: 1,
      problemText: "x² + 16x = 17",
      baseRevision: 0,
    }),
  ]);
  await attachProofScreenshot(page, testInfo, "compact-boundary-board");
  expectProviderFree(requests);
});

test("reduced motion reaches the same certified primary terminal board", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/parametric-choreography?motion=reduced");
  await waitForBridge(page);
  await teach(page);
  await expectCompletedLesson(
    page,
    expectedFinalBoard(primaryFixtureValue, "cinematic"),
    8,
  );
  expect((await bridgeState(page)).runnerCallCount).toBe(1);
  expectProviderFree(requests);
});

test("@normal-speed-capture records the primary real-time lesson", async ({
  page,
}, testInfo) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/parametric-choreography?speed=normal");
  await waitForBridge(page);
  const startedMs = await page.evaluate(() => performance.now());
  await teach(page);
  await expect(stage(page)).toHaveAttribute("data-phase", "completed", {
    timeout: 120_000,
  });
  const completedMs = await page.evaluate(() => performance.now());
  await expectCompletedLesson(
    page,
    expectedFinalBoard(primaryFixtureValue, "cinematic"),
    8,
    120_000,
  );
  const observedDurationMs = Math.round(completedMs - startedMs);
  const authoredDurationMs = fixtureCheckpoints(primaryFixtureValue).reduce(
    (total, checkpoint) =>
      total +
      checkpoint.semantic.choreography.phase.durationMs +
      checkpoint.semantic.choreography.phase.holdAfterMs,
    0,
  );
  const runner = await bridgeState(page);
  expect(runner.runnerCallCount).toBe(1);
  expect(authoredDurationMs).toBe(47_000);
  expect(observedDurationMs).toBeGreaterThanOrEqual(45_000);
  expect(observedDurationMs).toBeLessThanOrEqual(55_000);
  await attachBoardOnlyProofScreenshot(
    page,
    testInfo,
    "normal-speed-final-board",
  );
  const timingPath = path.join(
    path.dirname(testInfo.project.outputDir),
    "normal-speed-timing.json",
  );
  await fs.writeFile(
    timingPath,
    `${JSON.stringify(
      {
        v: 1,
        fixtureId: "completing-square-parametric-b8-c20",
        problemText: "x² + 8x = 20",
        playbackRate: 1,
        authoredDurationMs,
        acceptedDurationRangeMs: [45_000, 55_000],
        observedDurationMs,
        checkpointCount: 8,
        finalRevision: 8,
        runnerCallCount: runner.runnerCallCount,
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  await testInfo.attach("normal-speed-timing", {
    path: timingPath,
    contentType: "application/json",
  });
  expectProviderFree(requests);
});
