import fs from "node:fs/promises";
import path from "node:path";

import {
  expect,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

import {
  decodeProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneCheckpointEventV1,
} from "../src/lib/live-scene/projectile-choreography-stream";
import type {
  ProjectileMotionCheckpointId,
  ProjectileMotionClarificationTopic,
  ProjectileMotionProblemSpecV1,
  ProjectileMotionRouteV1,
} from "../src/lib/live-scene/projectile-motion";
import { orderSceneNodesForSvgPaint } from "../src/features/live-scene/svg-node-reconciler";
import type { SceneNode } from "../src/lib/live-scene";

export const PROJECTILE_MOTION_E2E_BRIDGE_KEY =
  "__MURMUR_PROJECTILE_MOTION_E2E__" as const;

export const PROJECTILE_MAIN_CHECKPOINTS = [
  "setup",
  "decompose_velocity",
  "trace_ascent",
  "apex_state",
  "trace_descent",
  "summary",
] as const;

export const PROJECTILE_CLARIFICATION_CASES = [
  {
    topic: "horizontal_velocity",
    prerequisite: "decompose_velocity",
    settledMainCount: 2,
    button: "Why does horizontal speed stay constant?",
    detailCheckpoint: "horizontal_velocity_detail",
    settledLabel: "horizontal speed explained",
    lane: "clarifyHorizontal",
  },
  {
    topic: "apex_acceleration",
    prerequisite: "apex_state",
    settledMainCount: 4,
    button: "At the apex, why is acceleration still down?",
    detailCheckpoint: "apex_acceleration_detail",
    settledLabel: "apex acceleration explained",
    lane: "clarifyApex",
  },
  {
    topic: "flight_symmetry",
    prerequisite: "trace_descent",
    settledMainCount: 5,
    button: "Why do rise and fall take the same time?",
    detailCheckpoint: "flight_symmetry_detail",
    settledLabel: "flight symmetry explained",
    lane: "clarifySymmetry",
  },
] as const satisfies readonly {
  readonly topic: ProjectileMotionClarificationTopic;
  readonly prerequisite: ProjectileMotionCheckpointId;
  readonly settledMainCount: number;
  readonly button: string;
  readonly detailCheckpoint: ProjectileMotionCheckpointId;
  readonly settledLabel: string;
  readonly lane: ProjectileFixtureLaneName;
}[];

export interface ProjectileRunnerCallObservation {
  readonly ordinal: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly checkpointId: ProjectileMotionCheckpointId | null;
  readonly clarifiedTopics: readonly ProjectileMotionClarificationTopic[];
  readonly activeClarification: ProjectileMotionClarificationTopic | null;
  readonly requestedRoute: ProjectileMotionRouteV1 | null;
}

export interface ProjectileE2EBridgeState {
  readonly runnerCallCount: number;
  readonly calls: readonly ProjectileRunnerCallObservation[];
}

export interface ProjectileStageObservation {
  readonly phase: string;
  readonly visibleCheckpointId: string;
  readonly settledMainCount: string;
  readonly acceptedSpeed: string;
  readonly acceptedAngle: string;
  readonly rendererTrusted: string;
  readonly layout: string;
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
}

export interface ExpectedProjectileBoard {
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
  readonly revision: number;
  readonly certificateHeadSha256: string;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
}

export interface ProjectileProviderFreeObservation {
  readonly liveSceneRequests: readonly string[];
  readonly unexpectedRequests: readonly string[];
}

export type ProjectileFixtureLaneName =
  | "main"
  | "clarifyHorizontal"
  | "clarifyApex"
  | "clarifySymmetry"
  | "continueAfterClarification"
  | "retargetAtApex"
  | "retargetAfterSummary";

interface ProjectileFixtureLane {
  readonly events: readonly unknown[];
  readonly expectedTerminal: {
    readonly certificateSha256: string;
    readonly scene: {
      readonly revision: number;
      readonly nodes: readonly unknown[];
    };
    readonly frontier: {
      readonly problemSpec: ProjectileMotionProblemSpecV1;
    };
  };
}

export interface ProjectileFixtureValue {
  readonly fixtureId: string;
  readonly compilerVersion: string;
  readonly providerRequestCount: number;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly lanes: Readonly<
    Record<ProjectileFixtureLaneName, ProjectileFixtureLane>
  >;
}

export function projectileFixture(value: unknown): ProjectileFixtureValue {
  return value as ProjectileFixtureValue;
}

export function projectileStage(page: Page): Locator {
  return page.getByTestId("projectile-choreography-stage");
}

export function projectileBoard(page: Page): Locator {
  return projectileStage(page).getByTestId("live-choreography-board");
}

export function fixtureCheckpoints(
  fixture: ProjectileFixtureValue,
  laneName: ProjectileFixtureLaneName,
): readonly ProjectileChoreographySceneCheckpointEventV1[] {
  return fixture.lanes[laneName].events
    .map(decodeProjectileChoreographySceneStreamEventV1)
    .filter(
      (event): event is ProjectileChoreographySceneCheckpointEventV1 =>
        event.type === "projectile_choreography_scene_checkpoint",
    );
}

export function expectedProjectileBoard(
  fixture: ProjectileFixtureValue,
  laneName: ProjectileFixtureLaneName,
  layout: "cinematic" | "compact",
): ExpectedProjectileBoard {
  const lane = fixture.lanes[laneName];
  const checkpoint = fixtureCheckpoints(fixture, laneName).at(-1);
  if (!checkpoint) throw new Error(`${laneName} has no projectile checkpoint`);
  const viewport = checkpoint.semantic.presentation.resultViewports[layout];
  return Object.freeze({
    checkpointId: checkpoint.semantic.checkpointId,
    caption: checkpoint.patch.narration,
    viewBox: `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`,
    nodeIds: Object.freeze(
      orderSceneNodesForSvgPaint(
        lane.expectedTerminal.scene.nodes as readonly SceneNode[],
      ).map((node) => node.id),
    ),
    revision: lane.expectedTerminal.scene.revision,
    certificateHeadSha256: lane.expectedTerminal.certificateSha256,
    problemSpec: lane.expectedTerminal.frontier.problemSpec,
  });
}

export async function waitForProjectileBridge(page: Page): Promise<void> {
  await page.waitForFunction(
    (key) => Boolean((window as typeof window & Record<string, unknown>)[key]),
    PROJECTILE_MOTION_E2E_BRIDGE_KEY,
  );
}

export async function projectileBridgeState(
  page: Page,
): Promise<ProjectileE2EBridgeState> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      | { readonly version: number; getState(): ProjectileE2EBridgeState }
      | undefined;
    if (!bridge || bridge.version !== 1) {
      throw new Error("The projectile-motion e2e bridge is unavailable");
    }
    return bridge.getState();
  }, PROJECTILE_MOTION_E2E_BRIDGE_KEY);
}

export function observeProviderFreeRequests(
  page: Page,
): ProjectileProviderFreeObservation {
  const liveSceneRequests: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const target = local
      ? `${request.method()} ${url.pathname}${url.search}`
      : `${request.method()} ${url.origin}${url.pathname}`;
    if (url.pathname.startsWith("/api/live-scenes")) {
      liveSceneRequests.push(target);
    }
    const allowed =
      local &&
      request.method() === "GET" &&
      (url.pathname === "/e2e/projectile-motion" ||
        url.pathname.startsWith("/_next/") ||
        url.pathname.startsWith("/__nextjs_font/") ||
        url.pathname === "/favicon.ico");
    if (!allowed) unexpectedRequests.push(target);
  });
  return { liveSceneRequests, unexpectedRequests };
}

export function expectProviderFree(
  observation: ProjectileProviderFreeObservation,
): void {
  expect(observation.liveSceneRequests).toEqual([]);
  expect(observation.unexpectedRequests).toEqual([]);
}

export async function observeProjectileStage(
  page: Page,
): Promise<ProjectileStageObservation> {
  return projectileStage(page).evaluate((element) => {
    const svg = element.querySelector("svg");
    const caption = element.querySelector("figcaption p");
    if (!svg || !caption) throw new Error("The projectile stage is incomplete");
    return {
      phase: element.dataset.phase ?? "",
      visibleCheckpointId: element.dataset.visibleCheckpointId ?? "",
      settledMainCount: element.dataset.settledMainCount ?? "",
      acceptedSpeed: element.dataset.acceptedSpeed ?? "",
      acceptedAngle: element.dataset.acceptedAngle ?? "",
      rendererTrusted: element.dataset.rendererTrusted ?? "",
      layout: element.dataset.layout ?? "",
      caption: caption.textContent?.trim() ?? "",
      viewBox: svg.getAttribute("viewBox") ?? "",
      nodeIds: Array.from(
        svg.querySelectorAll<SVGGraphicsElement>("[data-element-id]"),
        (node) => node.dataset.elementId ?? "",
      ),
    };
  });
}

export async function expectCaptionContained(page: Page): Promise<void> {
  const bounds = await projectileStage(page).evaluate((element) => {
    const caption = element.querySelector("figcaption p");
    const board = element.querySelector(
      '[data-testid="live-choreography-board"]',
    );
    if (!caption || !board) {
      throw new Error("The projectile caption or board is unavailable");
    }
    const stageRect = element.getBoundingClientRect();
    const captionRect = caption.getBoundingClientRect();
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

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const width = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(width.documentWidth).toBeLessThanOrEqual(width.viewportWidth);
}

export async function expectProjectileTerminal(
  page: Page,
  expected: ExpectedProjectileBoard,
  options: {
    readonly phase?: "completed" | "interrupted";
    readonly settledMainCount?: number;
    readonly timeout?: number;
  } = {},
): Promise<ProjectileStageObservation> {
  const stage = projectileStage(page);
  await expect(stage).toHaveAttribute(
    "data-phase",
    options.phase ?? "completed",
    { timeout: options.timeout ?? 20_000 },
  );
  await expect(stage).toHaveAttribute(
    "data-visible-checkpoint-id",
    expected.checkpointId,
  );
  await expect(stage).toHaveAttribute(
    "data-settled-main-count",
    String(options.settledMainCount ?? 6),
  );
  await expect(stage).toHaveAttribute("data-renderer-trusted", "true");
  await expect(stage).toHaveAttribute(
    "data-accepted-speed",
    String(expected.problemSpec.speedMps),
  );
  await expect(stage).toHaveAttribute(
    "data-accepted-angle",
    String(expected.problemSpec.angleDeg),
  );
  await expect(
    page.getByText(`scene ${expected.revision}`, { exact: false }),
  ).toBeVisible();
  await expectCaptionContained(page);
  const observation = await observeProjectileStage(page);
  expect(observation.caption).toBe(expected.caption);
  expect(observation.viewBox).toBe(expected.viewBox);
  expect(observation.nodeIds).toEqual(expected.nodeIds);
  expect(new Set(observation.nodeIds).size).toBe(observation.nodeIds.length);
  expect(
    observation.nodeIds.every(
      (id) =>
        id.startsWith("projectile__") &&
        !id.endsWith("--incoming") &&
        !id.endsWith("--outgoing"),
    ),
  ).toBe(true);
  return observation;
}

export async function selectProjectileProblem(
  page: Page,
  problemSpec: Pick<ProjectileMotionProblemSpecV1, "speedMps" | "angleDeg">,
): Promise<void> {
  await page
    .getByRole("button", {
      name: `Set launch speed to ${problemSpec.speedMps} metres per second`,
    })
    .click();
  await page
    .getByRole("button", {
      name: `Set launch angle to ${problemSpec.angleDeg} degrees`,
    })
    .click();
}

export async function attachBoardOnlyScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  explicitPath?: string,
): Promise<Buffer> {
  const stage = projectileStage(page);
  const caption = stage.locator("figcaption");
  const board = projectileBoard(page);
  const screenshotPath =
    explicitPath ??
    path.join(
      path.dirname(testInfo.project.outputDir),
      "screenshots",
      `${name}.png`,
    );
  await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
  const previousDisplay = await caption.evaluate(
    (element) => element.style.display,
  );
  await caption.evaluate((element) => {
    element.style.display = "none";
  });
  let png: Buffer;
  try {
    png = await board.screenshot({ path: screenshotPath, type: "png" });
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
  return png;
}

export async function rememberStableElement(
  page: Page,
  elementId: string,
  memoryKey: string,
): Promise<void> {
  const locator = projectileBoard(page).locator(
    `[data-element-id="${elementId}"]`,
  );
  await expect(locator).toHaveCount(1);
  await locator.evaluate((element, key) => {
    (window as typeof window & Record<string, unknown>)[key] = element;
  }, memoryKey);
}

export async function expectStableElement(
  page: Page,
  elementId: string,
  memoryKey: string,
): Promise<void> {
  const locator = projectileBoard(page).locator(
    `[data-element-id="${elementId}"]`,
  );
  await expect(locator).toHaveCount(1);
  expect(
    await locator.evaluate(
      (element, key) =>
        (window as typeof window & Record<string, unknown>)[key] === element,
      memoryKey,
    ),
  ).toBe(true);
}

export async function waitForActiveTrace(
  page: Page,
  checkpointId: "trace_ascent" | "trace_descent",
): Promise<{
  readonly dashArray: number;
  readonly dashOffset: number;
  readonly markerTransform: string;
}> {
  const pathId = `projectile__trajectory_${
    checkpointId === "trace_ascent" ? "ascent" : "descent"
  }`;
  const result = await page.waitForFunction(
    ({ checkpoint, path }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const pathGroup = stage?.querySelector<SVGGElement>(
        `[data-element-id="${path}"]`,
      );
      const marker = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__projectile_marker"]',
      );
      const curve = pathGroup?.querySelector("path");
      if (
        stage?.dataset.visibleCheckpointId !== checkpoint ||
        !curve ||
        !marker
      ) {
        return false;
      }
      const dashArray = Number(curve.getAttribute("stroke-dasharray"));
      const dashOffset = Number(curve.getAttribute("stroke-dashoffset"));
      const markerTransform = marker.getAttribute("transform") ?? "";
      if (
        !Number.isFinite(dashArray) ||
        !Number.isFinite(dashOffset) ||
        dashArray <= 0 ||
        dashOffset <= 0 ||
        dashOffset >= dashArray ||
        !markerTransform.startsWith("translate(")
      ) {
        return false;
      }
      return { dashArray, dashOffset, markerTransform };
    },
    { checkpoint: checkpointId, path: pathId },
    { polling: "raf" },
  );
  const value = await result.jsonValue();
  if (value === false) throw new Error(`${checkpointId} never began tracing`);
  return value;
}

export async function stopDuringActiveTrace(
  page: Page,
  checkpointId: "trace_ascent" | "trace_descent",
): Promise<{
  readonly dashArray: number;
  readonly dashOffset: number;
  readonly markerTransform: string;
}> {
  const pathId = `projectile__trajectory_${
    checkpointId === "trace_ascent" ? "ascent" : "descent"
  }`;
  const result = await page.waitForFunction(
    ({ checkpoint, path }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const pathGroup = stage?.querySelector<SVGGElement>(
        `[data-element-id="${path}"]`,
      );
      const marker = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__projectile_marker"]',
      );
      const curve = pathGroup?.querySelector("path");
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      if (
        stage?.dataset.visibleCheckpointId !== checkpoint ||
        !curve ||
        !marker ||
        !stop
      ) {
        return false;
      }
      const dashArray = Number(curve.getAttribute("stroke-dasharray"));
      const dashOffset = Number(curve.getAttribute("stroke-dashoffset"));
      const markerTransform = marker.getAttribute("transform") ?? "";
      if (
        !Number.isFinite(dashArray) ||
        !Number.isFinite(dashOffset) ||
        dashArray <= 0 ||
        dashOffset <= 0 ||
        dashOffset >= dashArray ||
        !markerTransform.startsWith("translate(")
      ) {
        return false;
      }
      stop.click();
      return { dashArray, dashOffset, markerTransform };
    },
    { checkpoint: checkpointId, path: pathId },
    { polling: "raf" },
  );
  const value = await result.jsonValue();
  if (value === false) throw new Error(`${checkpointId} never began tracing`);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    checkpointId,
  );
  return value;
}

export async function stopAtVisibleCheckpoint(
  page: Page,
  checkpointId: ProjectileMotionCheckpointId,
): Promise<void> {
  await page.waitForFunction(
    (checkpoint) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const button = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      if (stage?.dataset.visibleCheckpointId !== checkpoint || !button) {
        return false;
      }
      button.click();
      return true;
    },
    checkpointId,
    { polling: "raf" },
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    checkpointId,
  );
}
