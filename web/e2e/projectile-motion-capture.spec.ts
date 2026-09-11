import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type Request } from "@playwright/test";

import primaryFixtureValue from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json";
import {
  PROJECTILE_MAIN_CHECKPOINTS,
  attachBoardOnlyScreenshot,
  expectProjectileTerminal,
  expectStableElement,
  expectedProjectileBoard,
  observeProjectileStage,
  projectileBridgeState,
  projectileFixture,
  projectileStage,
  rememberStableElement,
  waitForActiveTrace,
  waitForProjectileBridge,
} from "./projectile-motion-helpers";
import { observeChoreographyExecution } from "./live-choreography-provenance";

const CAPTURE_ROUTE =
  "/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=normal&proof=keyframes";
const CAPTURE_VIEWPORT = Object.freeze({ width: 1_280, height: 720 });
const primaryFixture = projectileFixture(primaryFixtureValue);

interface NetworkObservation {
  readonly liveSceneRequests: readonly string[];
  readonly unexpectedRequests: readonly string[];
  readonly failedRequests: readonly string[];
}

interface ArtifactDescriptor {
  readonly path: string;
  readonly bytes: number;
  readonly sha256: string;
  readonly width?: number;
  readonly height?: number;
}

function rounded(value: number): number {
  return Number(value.toFixed(3));
}

function requestTarget(request: Request): string {
  const url = new URL(request.url());
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  return local
    ? `${request.method()} ${url.pathname}${url.search}`
    : `${request.method()} ${url.origin}${url.pathname}`;
}

function observeNetwork(
  page: Page,
  baseURL: string,
  route: string,
): () => NetworkObservation {
  const origin = new URL(baseURL).origin;
  const expectedDocument = new URL(route, baseURL);
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
    const errorText = request.failure()?.errorText ?? "unknown request failure";
    failedRequests.add(`${requestTarget(request)}: ${errorText}`);
  });

  return () => ({
    liveSceneRequests: [...liveSceneRequests],
    unexpectedRequests: [...unexpectedRequests],
    failedRequests: [...failedRequests],
  });
}

async function artifactDescriptor(
  artifactRoot: string,
  filePath: string,
  buffer?: Buffer,
): Promise<ArtifactDescriptor> {
  const bytes = buffer ?? (await readFile(filePath));
  const file = await stat(filePath);
  const relativePath = path
    .relative(artifactRoot, filePath)
    .split(path.sep)
    .join("/");
  const dimensions = filePath.endsWith(".png")
    ? {
        width: bytes.readUInt32BE(16),
        height: bytes.readUInt32BE(20),
      }
    : {};
  return Object.freeze({
    path: relativePath,
    bytes: file.size,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    ...dimensions,
  });
}

async function performanceNow(page: Page): Promise<number> {
  return page.evaluate(() => performance.now());
}

async function firstMeaningfulVisualAt(page: Page): Promise<number> {
  const result = await page.waitForFunction(
    () => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const visible = Array.from(
        stage?.querySelectorAll<SVGGraphicsElement>("[data-element-id]") ?? [],
      ).some((element) => {
        const style = getComputedStyle(element);
        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          Number(style.opacity) > 0.02
        );
      });
      return visible ? performance.now() : false;
    },
    undefined,
    { polling: "raf" },
  );
  const value = await result.jsonValue();
  if (value === false) {
    throw new Error("The capture never presented its first meaningful visual");
  }
  return value;
}

async function observeSettledMainSequence(page: Page): Promise<string[]> {
  const settled: string[] = [];
  for (const [index, checkpointId] of PROJECTILE_MAIN_CHECKPOINTS.entries()) {
    await page.waitForFunction(
      ({ count, checkpoint }) => {
        const stage = document.querySelector<HTMLElement>(
          '[data-testid="projectile-choreography-stage"]',
        );
        return (
          stage?.dataset.settledMainCount === String(count) &&
          stage.dataset.visibleCheckpointId === checkpoint
        );
      },
      { count: index + 1, checkpoint: checkpointId },
      { polling: "raf", timeout: 30_000 },
    );
    settled.push(checkpointId);
  }
  return settled;
}

test("captures the complete mute-first 1x projectile proof with recomputable evidence", async ({
  browser,
  baseURL,
}, testInfo) => {
  testInfo.setTimeout(145_000);
  if (!baseURL)
    throw new Error("The projectile capture base URL is unavailable");

  const execution = observeChoreographyExecution(testInfo.config, browser);
  const captureRoot = path.dirname(testInfo.project.outputDir);
  const artifactRoot = path.dirname(captureRoot);
  const rawVideoRoot = testInfo.outputPath("raw-video");
  const videoPath = path.join(captureRoot, "projectile-motion.webm");
  const pageScreenshotPath = path.join(
    captureRoot,
    "projectile-motion-page.png",
  );
  const boardScreenshotPath = path.join(
    captureRoot,
    "projectile-motion-board.png",
  );
  const observationsPath = path.join(captureRoot, "observations.json");
  await Promise.all([
    mkdir(captureRoot, { recursive: true }),
    mkdir(rawVideoRoot, { recursive: true }),
  ]);

  const context = await browser.newContext({
    baseURL,
    viewport: CAPTURE_VIEWPORT,
    screen: CAPTURE_VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "no-preference",
    serviceWorkers: "block",
    recordVideo: { dir: rawVideoRoot, size: CAPTURE_VIEWPORT },
  });
  const page = await context.newPage();
  const video = page.video();
  if (!video) throw new Error("Playwright did not attach a projectile video");
  const readNetwork = observeNetwork(page, baseURL, CAPTURE_ROUTE);

  let pagePng: Buffer | null = null;
  let boardPng: Buffer | null = null;
  let startedAtMs: number | null = null;
  let firstMeaningfulVisualAtMs: number | null = null;
  let completedAtMs: number | null = null;
  let settledCheckpointIds: string[] | null = null;
  let traceCueObserved = false;
  let terminal: Awaited<ReturnType<typeof observeProjectileStage>> | null =
    null;
  let bridgeCalls:
    Awaited<ReturnType<typeof projectileBridgeState>>["calls"] | null = null;
  let network: NetworkObservation | null = null;

  try {
    await page.goto(CAPTURE_ROUTE, { waitUntil: "domcontentloaded" });
    await waitForProjectileBridge(page);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-layout",
      "cinematic",
    );

    startedAtMs = await performanceNow(page);
    await page.getByRole("button", { name: "Draw this launch" }).click();
    firstMeaningfulVisualAtMs = await firstMeaningfulVisualAt(page);

    const settlementPromise = observeSettledMainSequence(page);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "1",
      { timeout: 30_000 },
    );
    await rememberStableElement(
      page,
      "projectile__ground",
      "__projectile_capture_ground__",
    );
    await rememberStableElement(
      page,
      "projectile__projectile_marker",
      "__projectile_capture_marker__",
    );

    const trace = await waitForActiveTrace(page, "trace_ascent");
    expect(trace.dashOffset).toBeGreaterThan(0);
    expect(trace.dashOffset).toBeLessThan(trace.dashArray);
    expect(trace.markerTransform).toMatch(/^translate\(/);
    traceCueObserved = true;

    settledCheckpointIds = await settlementPromise;
    const expected = expectedProjectileBoard(
      primaryFixture,
      "main",
      "cinematic",
    );
    terminal = await expectProjectileTerminal(page, expected, {
      timeout: 90_000,
    });
    completedAtMs = await performanceNow(page);
    await expectStableElement(
      page,
      "projectile__ground",
      "__projectile_capture_ground__",
    );
    await expectStableElement(
      page,
      "projectile__projectile_marker",
      "__projectile_capture_marker__",
    );
    for (const pathId of [
      "projectile__trajectory_ascent",
      "projectile__trajectory_descent",
    ]) {
      const curve = projectileStage(page).locator(
        `[data-element-id="${pathId}"] path`,
      );
      await expect(curve).toHaveCount(1);
      expect(await curve.getAttribute("d")).toMatch(/[CQ]/);
    }

    const bridge = await projectileBridgeState(page);
    expect(bridge.runnerCallCount).toBe(1);
    expect(bridge.calls).toHaveLength(1);
    bridgeCalls = bridge.calls;

    pagePng = await page.screenshot({
      path: pageScreenshotPath,
      type: "png",
      fullPage: true,
      animations: "allow",
      caret: "hide",
      scale: "css",
    });
    await testInfo.attach("projectile-motion-page", {
      path: pageScreenshotPath,
      contentType: "image/png",
    });
    boardPng = await attachBoardOnlyScreenshot(
      page,
      testInfo,
      "projectile-motion-board-caption-hidden",
      boardScreenshotPath,
    );

    network = readNetwork();
    expect(network.liveSceneRequests).toEqual([]);
    expect(network.unexpectedRequests).toEqual([]);
    expect(network.failedRequests).toEqual([]);
  } finally {
    await context.close();
    await video.saveAs(videoPath);
    await video.delete();
  }

  if (
    !pagePng ||
    !boardPng ||
    startedAtMs === null ||
    firstMeaningfulVisualAtMs === null ||
    completedAtMs === null ||
    !settledCheckpointIds ||
    !terminal ||
    !bridgeCalls ||
    !network
  ) {
    throw new Error("The projectile capture did not produce complete evidence");
  }

  const fixturePath = path.resolve(
    __dirname,
    "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json",
  );
  const fixtureBytes = await readFile(fixturePath);
  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  const evidence = {
    scenarioId: "main_solve",
    settledCheckpointIds,
    finalCheckpointId: "summary",
    finalRevision: expected.revision,
    certificateHeadSha256: expected.certificateHeadSha256,
    caption: terminal.caption,
    visibleNodeIds: terminal.nodeIds,
    stableNodeIds: ["projectile__ground", "projectile__projectile_marker"],
    traceCueObserved,
    markerId: "projectile__projectile_marker",
    pathIds: [
      "projectile__trajectory_ascent",
      "projectile__trajectory_descent",
    ],
    bridgeCalls,
  };
  const evidenceSha256 = createHash("sha256")
    .update(JSON.stringify(evidence))
    .digest("hex");
  const roundedStartedAtMs = rounded(startedAtMs);
  const roundedFirstMeaningfulVisualAtMs = rounded(firstMeaningfulVisualAtMs);
  const roundedCompletedAtMs = rounded(completedAtMs);
  const observations = {
    v: 1,
    gate: "1.7",
    execution,
    fixtureSource: {
      fixtureId: primaryFixture.fixtureId,
      compilerVersion: primaryFixture.compilerVersion,
      fixtureSha256: createHash("sha256").update(fixtureBytes).digest("hex"),
    },
    browser: {
      route: CAPTURE_ROUTE,
      viewport: CAPTURE_VIEWPORT,
      layout: "cinematic",
      reducedMotion: false,
      colorScheme: "dark",
    },
    runtime: { evidence, evidenceSha256 },
    network: {
      providerRequestCount: primaryFixture.providerRequestCount,
      liveSceneRequests: network.liveSceneRequests,
      unexpectedRequests: network.unexpectedRequests,
      failedRequests: network.failedRequests,
    },
    timing: {
      startedAtMs: roundedStartedAtMs,
      firstMeaningfulVisualAtMs: roundedFirstMeaningfulVisualAtMs,
      completedAtMs: roundedCompletedAtMs,
      visualDurationMs: rounded(
        roundedCompletedAtMs - roundedFirstMeaningfulVisualAtMs,
      ),
    },
    artifacts: {
      video: await artifactDescriptor(artifactRoot, videoPath),
      pageScreenshot: await artifactDescriptor(
        artifactRoot,
        pageScreenshotPath,
        pagePng,
      ),
      boardScreenshot: await artifactDescriptor(
        artifactRoot,
        boardScreenshotPath,
        boardPng,
      ),
    },
  };
  await writeFile(
    observationsPath,
    `${JSON.stringify(observations, null, 2)}\n`,
    "utf8",
  );
  await Promise.all([
    testInfo.attach("projectile-motion-video-mute-first", {
      path: videoPath,
      contentType: "video/webm",
    }),
    testInfo.attach("projectile-motion-observations", {
      path: observationsPath,
      contentType: "application/json",
    }),
  ]);
});
