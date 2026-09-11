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
  firstMeaningfulProjectileVisualAt,
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
  "/e2e/projectile-motion?layout=cinematic&motion=real&flow=main&speed=normal&proof=none";
const CHECKPOINT_CAPTURE_ROUTE =
  "/e2e/projectile-motion?layout=cinematic&motion=reduced&flow=main&speed=accelerated&proof=keyframes";
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

interface CheckpointSettlement {
  readonly checkpointId: string;
  readonly settledAtMs: number;
}

interface MotionSample {
  readonly checkpointId: "trace_ascent" | "trace_descent";
  readonly pathId:
    "projectile__trajectory_ascent" | "projectile__trajectory_descent";
  readonly markerId: "projectile__projectile_marker";
  readonly dashArray: number;
  readonly dashOffset: number;
  readonly markerTransform: string;
}

interface PathGeometrySample {
  readonly pathId:
    "projectile__trajectory_ascent" | "projectile__trajectory_descent";
  readonly pathD: string;
  readonly totalLength: number;
  readonly samples: readonly {
    readonly fraction: number;
    readonly x: number;
    readonly y: number;
    readonly markerTransform: string;
  }[];
}

interface CheckpointScreenshot extends ArtifactDescriptor {
  readonly checkpointId: string;
  readonly width: number;
  readonly height: number;
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

async function observeSettledMainSequence(
  page: Page,
): Promise<CheckpointSettlement[]> {
  const settled: CheckpointSettlement[] = [];
  for (const [index, checkpointId] of PROJECTILE_MAIN_CHECKPOINTS.entries()) {
    const result = await page.waitForFunction(
      ({ count, checkpoint }) => {
        const stage = document.querySelector<HTMLElement>(
          '[data-testid="projectile-choreography-stage"]',
        );
        const accepted =
          stage?.dataset.settledMainCount === String(count) &&
          stage.dataset.visibleCheckpointId === checkpoint;
        return accepted
          ? { checkpointId: checkpoint, settledAtMs: performance.now() }
          : false;
      },
      { count: index + 1, checkpoint: checkpointId },
      { polling: "raf", timeout: 30_000 },
    );
    const value = await result.jsonValue();
    if (value === false) {
      throw new Error(`${checkpointId} did not reach a settled frame`);
    }
    settled.push({
      checkpointId: value.checkpointId,
      settledAtMs: rounded(value.settledAtMs),
    });
  }
  return settled;
}

async function observePathGeometrySamples(
  page: Page,
): Promise<PathGeometrySample[]> {
  return page.evaluate(() => {
    const pathIds = [
      "projectile__trajectory_ascent",
      "projectile__trajectory_descent",
    ] as const;
    const fractions = [0, 0.25, 0.5, 0.75, 1] as const;
    const stableNumber = (value: number): number => Number(value.toFixed(3));
    return pathIds.map((pathId) => {
      const path = document.querySelector<SVGPathElement>(
        `[data-testid="projectile-choreography-stage"] [data-element-id="${pathId}"] path`,
      );
      if (!path) throw new Error(`Missing final path ${pathId}`);
      const totalLength = path.getTotalLength();
      const pathD = path.getAttribute("d") ?? "";
      if (!pathD || !Number.isFinite(totalLength) || totalLength <= 0) {
        throw new Error(`Final path ${pathId} has invalid geometry`);
      }
      return {
        pathId,
        pathD,
        totalLength: stableNumber(totalLength),
        samples: fractions.map((fraction) => {
          const point = path.getPointAtLength(totalLength * fraction);
          const x = stableNumber(point.x);
          const y = stableNumber(point.y);
          return {
            fraction,
            x,
            y,
            markerTransform: `translate(${x} ${y})`,
          };
        }),
      };
    });
  });
}

function mergeNetwork(
  ...observations: readonly NetworkObservation[]
): NetworkObservation {
  return {
    liveSceneRequests: [
      ...new Set(observations.flatMap((value) => value.liveSceneRequests)),
    ],
    unexpectedRequests: [
      ...new Set(observations.flatMap((value) => value.unexpectedRequests)),
    ],
    failedRequests: [
      ...new Set(observations.flatMap((value) => value.failedRequests)),
    ],
  };
}

async function composeCheckpointContactSheet(
  page: Page,
  frames: readonly {
    readonly checkpointId: string;
    readonly png: Buffer;
  }[],
): Promise<Buffer> {
  const encoded = frames.map(({ checkpointId, png }) => ({
    checkpointId,
    dataUrl: `data:image/png;base64,${png.toString("base64")}`,
    width: png.readUInt32BE(16),
    height: png.readUInt32BE(20),
  }));
  const frame = encoded[0];
  if (!frame) throw new Error("The projectile contact sheet has no frames");
  if (
    encoded.some(
      ({ width, height }) => width !== frame.width || height !== frame.height,
    )
  ) {
    throw new Error("Projectile checkpoint frames must share one pixel size");
  }
  const base64 = await page.evaluate(
    async ({ cells, frameWidth, frameHeight }) => {
      if (cells.length !== 6) {
        throw new Error("The projectile contact sheet requires six frames");
      }
      const columns = 2;
      const rows = 3;
      const canvas = document.createElement("canvas");
      canvas.width = columns * frameWidth;
      canvas.height = rows * frameHeight;
      const context = canvas.getContext("2d");
      if (!context)
        throw new Error("Could not create the contact sheet canvas");
      context.fillStyle = "#050507";
      context.fillRect(0, 0, canvas.width, canvas.height);

      for (const [index, cell] of cells.entries()) {
        const image = new Image();
        image.src = cell.dataUrl;
        await image.decode();
        const column = index % columns;
        const row = Math.floor(index / columns);
        if (image.width !== frameWidth || image.height !== frameHeight) {
          throw new Error("A contact-sheet frame changed pixel dimensions");
        }
        context.drawImage(
          image,
          column * frameWidth,
          row * frameHeight,
          frameWidth,
          frameHeight,
        );
      }
      return canvas.toDataURL("image/png").split(",", 2)[1] ?? "";
    },
    { cells: encoded, frameWidth: frame.width, frameHeight: frame.height },
  );
  if (!base64) throw new Error("Chromium produced an empty contact sheet");
  return Buffer.from(base64, "base64");
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
  const checkpointRoot = path.join(captureRoot, "checkpoints");
  const contactSheetPath = path.join(
    captureRoot,
    "projectile-motion-contact-sheet.png",
  );
  const observationsPath = path.join(captureRoot, "observations.json");
  await Promise.all([
    mkdir(captureRoot, { recursive: true }),
    mkdir(checkpointRoot, { recursive: true }),
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
  let checkpointSettlements: CheckpointSettlement[] | null = null;
  let motionSamples: MotionSample[] | null = null;
  let pathGeometrySamples: PathGeometrySample[] | null = null;
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

    startedAtMs = await page
      .getByRole("button", { name: "Draw this launch" })
      .evaluate((button) => {
        const startedAt = performance.now();
        (button as HTMLButtonElement).click();
        return startedAt;
      });
    firstMeaningfulVisualAtMs = await firstMeaningfulProjectileVisualAt(page);

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

    const ascentTrace = await waitForActiveTrace(page, "trace_ascent");
    expect(ascentTrace.dashOffset).toBeGreaterThan(0);
    expect(ascentTrace.dashOffset).toBeLessThan(ascentTrace.dashArray);
    expect(ascentTrace.markerTransform).toMatch(/^translate\(/);
    const descentTrace = await waitForActiveTrace(page, "trace_descent");
    expect(descentTrace.dashOffset).toBeGreaterThan(0);
    expect(descentTrace.dashOffset).toBeLessThan(descentTrace.dashArray);
    expect(descentTrace.markerTransform).toMatch(/^translate\(/);
    motionSamples = [
      {
        checkpointId: "trace_ascent",
        pathId: "projectile__trajectory_ascent",
        markerId: "projectile__projectile_marker",
        dashArray: ascentTrace.dashArray,
        dashOffset: ascentTrace.dashOffset,
        markerTransform: ascentTrace.markerTransform,
      },
      {
        checkpointId: "trace_descent",
        pathId: "projectile__trajectory_descent",
        markerId: "projectile__projectile_marker",
        dashArray: descentTrace.dashArray,
        dashOffset: descentTrace.dashOffset,
        markerTransform: descentTrace.markerTransform,
      },
    ];
    traceCueObserved = true;

    checkpointSettlements = await settlementPromise;
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
      const geometry = await curve.evaluate((element) => {
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
      expect(geometry.totalLength).toBeGreaterThan(0);
      expect(geometry.twiceTriangleArea).toBeGreaterThan(10);
    }
    pathGeometrySamples = await observePathGeometrySamples(page);

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

    network = readNetwork();
    expect(network.liveSceneRequests).toEqual([]);
    expect(network.unexpectedRequests).toEqual([]);
    expect(network.failedRequests).toEqual([]);
  } finally {
    await context.close();
    await video.saveAs(videoPath);
    await video.delete();
  }

  if (!network) {
    throw new Error("The real-time capture did not retain network evidence");
  }
  const checkpointFrames: {
    readonly checkpointId: string;
    readonly path: string;
    readonly png: Buffer;
  }[] = [];
  let contactSheetPng: Buffer | null = null;
  let checkpointNetwork: NetworkObservation | null = null;
  const checkpointContext = await browser.newContext({
    baseURL,
    viewport: CAPTURE_VIEWPORT,
    screen: CAPTURE_VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const checkpointPage = await checkpointContext.newPage();
  const readCheckpointNetwork = observeNetwork(
    checkpointPage,
    baseURL,
    CHECKPOINT_CAPTURE_ROUTE,
  );
  try {
    await checkpointPage.goto(CHECKPOINT_CAPTURE_ROUTE, {
      waitUntil: "domcontentloaded",
    });
    await waitForProjectileBridge(checkpointPage);
    await checkpointPage
      .getByRole("button", { name: "Draw this launch" })
      .click();
    for (const [index, checkpointId] of PROJECTILE_MAIN_CHECKPOINTS.entries()) {
      await checkpointPage.waitForFunction(
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
        { polling: "raf", timeout: 15_000 },
      );
      const screenshotPath = path.join(
        checkpointRoot,
        `${String(index + 1).padStart(2, "0")}-${checkpointId}.png`,
      );
      const png = await attachBoardOnlyScreenshot(
        checkpointPage,
        testInfo,
        `projectile-checkpoint-${index + 1}-${checkpointId}-caption-hidden`,
        screenshotPath,
      );
      checkpointFrames.push({
        checkpointId,
        path: screenshotPath,
        png,
      });
    }
    await expect(projectileStage(checkpointPage)).toHaveAttribute(
      "data-phase",
      "completed",
      { timeout: 15_000 },
    );
    const summaryFrame = checkpointFrames.at(-1);
    if (!summaryFrame || summaryFrame.checkpointId !== "summary") {
      throw new Error("The summary checkpoint frame is unavailable");
    }
    boardPng = summaryFrame.png;
    await writeFile(boardScreenshotPath, boardPng);
    await testInfo.attach("projectile-motion-board-caption-hidden", {
      path: boardScreenshotPath,
      contentType: "image/png",
    });
    contactSheetPng = await composeCheckpointContactSheet(
      checkpointPage,
      checkpointFrames,
    );
    await writeFile(contactSheetPath, contactSheetPng);
    await testInfo.attach("projectile-motion-board-contact-sheet", {
      path: contactSheetPath,
      contentType: "image/png",
    });
    checkpointNetwork = readCheckpointNetwork();
    expect(checkpointNetwork.liveSceneRequests).toEqual([]);
    expect(checkpointNetwork.unexpectedRequests).toEqual([]);
    expect(checkpointNetwork.failedRequests).toEqual([]);
  } finally {
    await checkpointContext.close();
  }
  if (!checkpointNetwork || !contactSheetPng) {
    throw new Error("The checkpoint contact sheet evidence is incomplete");
  }
  network = mergeNetwork(network, checkpointNetwork);

  if (
    !pagePng ||
    !boardPng ||
    startedAtMs === null ||
    firstMeaningfulVisualAtMs === null ||
    completedAtMs === null ||
    !checkpointSettlements ||
    !motionSamples ||
    !pathGeometrySamples ||
    !terminal ||
    !bridgeCalls ||
    !network ||
    checkpointFrames.length !== PROJECTILE_MAIN_CHECKPOINTS.length ||
    !contactSheetPng
  ) {
    throw new Error("The projectile capture did not produce complete evidence");
  }
  expect(checkpointSettlements.map(({ checkpointId }) => checkpointId)).toEqual(
    PROJECTILE_MAIN_CHECKPOINTS,
  );
  expect(firstMeaningfulVisualAtMs - startedAtMs).toBeLessThan(300);
  const observedVisualDurationMs = completedAtMs - firstMeaningfulVisualAtMs;
  expect(observedVisualDurationMs).toBeGreaterThanOrEqual(35_000);
  expect(observedVisualDurationMs).toBeLessThanOrEqual(45_000);

  const fixturePath = path.resolve(
    __dirname,
    "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json",
  );
  const fixtureBytes = await readFile(fixturePath);
  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  const evidence = {
    scenarioId: "main_solve",
    settledCheckpointIds: checkpointSettlements.map(
      ({ checkpointId }) => checkpointId,
    ),
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
    motionSamples,
    pathGeometrySamples,
    bridgeCalls,
  };
  const evidenceSha256 = createHash("sha256")
    .update(JSON.stringify(evidence))
    .digest("hex");
  const roundedStartedAtMs = rounded(startedAtMs);
  const roundedFirstMeaningfulVisualAtMs = rounded(firstMeaningfulVisualAtMs);
  const roundedCompletedAtMs = rounded(completedAtMs);
  const checkpointScreenshots: CheckpointScreenshot[] = await Promise.all(
    checkpointFrames.map(async (frame) => {
      const descriptor = await artifactDescriptor(
        artifactRoot,
        frame.path,
        frame.png,
      );
      if (descriptor.width === undefined || descriptor.height === undefined) {
        throw new Error(`${frame.checkpointId} screenshot has no dimensions`);
      }
      return {
        checkpointId: frame.checkpointId,
        path: descriptor.path,
        bytes: descriptor.bytes,
        sha256: descriptor.sha256,
        width: descriptor.width,
        height: descriptor.height,
      };
    }),
  );
  const contactSheet = await artifactDescriptor(
    artifactRoot,
    contactSheetPath,
    contactSheetPng,
  );
  if (contactSheet.width === undefined || contactSheet.height === undefined) {
    throw new Error("The projectile contact sheet has no dimensions");
  }
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
      checkpointSettlements,
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
      checkpointScreenshots,
      contactSheet: {
        path: contactSheet.path,
        bytes: contactSheet.bytes,
        sha256: contactSheet.sha256,
        width: contactSheet.width,
        height: contactSheet.height,
      },
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
