import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { constants } from "node:fs";
import {
  access,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  inspectSemanticStoryboardSvgAgainstScene,
  type SemanticStoryboardDomInspection,
} from "./semantic-storyboard-dom-oracle";
import {
  STORYBOARD_PROMPTS,
  STORYBOARD_SCENARIOS,
  acceptedModelCheckpointIds,
  attachSemanticStoryboardBoardScreenshot,
  continueSemanticStoryboard,
  expectSemanticStoryboardProviderFree,
  expectSemanticStoryboardTerminal,
  installSemanticStoryboardVisibleCheckpointProbe,
  observeSemanticStoryboardNetwork,
  observeSemanticStoryboardStage,
  semanticStoryboardBridgeState,
  semanticStoryboardEventHistory,
  semanticStoryboardLane,
  semanticStoryboardSessionHistory,
  semanticStoryboardSessionObservation,
  semanticStoryboardStage,
  semanticStoryboardVisibleCheckpointHistory,
  setSemanticStoryboardPrompt,
  startSemanticStoryboard,
  waitForSemanticStoryboardBridge,
  type SemanticStoryboardSessionObservation,
} from "./semantic-storyboard-helpers";

const CAPTURE_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=real&proof=none&speed=normal";
const CHECKPOINT_ROUTE =
  "/e2e/semantic-storyboard?layout=cinematic&motion=reduced&proof=keyframes&speed=accelerated";
const VIEWPORT = Object.freeze({ width: 1_280, height: 720 });
const execFileAsync = promisify(execFile);
const FILES = Object.freeze({
  observation: "semantic-storyboard-normal-speed-observation.json",
  video: "semantic-storyboard-normal-speed.webm",
  fullPage: "semantic-storyboard-normal-speed-full.png",
  boardOnly: "semantic-storyboard-normal-speed-board.png",
  contactSheet: "semantic-storyboard-normal-speed-contact-sheet.png",
});

interface CheckpointFrame {
  readonly ordinal: number;
  readonly checkpointId: string;
  readonly fileName: string;
  readonly path: string;
  readonly png: Buffer;
}

function rounded(value: number): number {
  return Number(value.toFixed(3));
}

async function playwrightFfmpegPath(): Promise<string> {
  const executableName =
    process.platform === "darwin"
      ? "ffmpeg-mac"
      : process.platform === "win32"
        ? "ffmpeg-win64.exe"
        : "ffmpeg-linux";
  const localBrowsers = path.join(
    process.cwd(),
    "node_modules/playwright-core/.local-browsers",
  );
  const cacheBrowsers =
    process.platform === "darwin"
      ? path.join(homedir(), "Library/Caches/ms-playwright")
      : process.platform === "win32"
        ? path.join(process.env.LOCALAPPDATA ?? homedir(), "ms-playwright")
        : path.join(homedir(), ".cache/ms-playwright");
  const configuredBrowsers = process.env.PLAYWRIGHT_BROWSERS_PATH;
  const roots = [
    ...(configuredBrowsers
      ? [configuredBrowsers === "0" ? localBrowsers : configuredBrowsers]
      : []),
    localBrowsers,
    cacheBrowsers,
  ];
  for (const root of new Set(roots.map((value) => path.resolve(value)))) {
    let entries;
    try {
      entries = await readdir(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries
      .filter(
        (candidate) =>
          candidate.isDirectory() && candidate.name.startsWith("ffmpeg-"),
      )
      .sort((left, right) => right.name.localeCompare(left.name))) {
      const executable = path.join(root, entry.name, executableName);
      try {
        await access(executable, constants.X_OK);
        return executable;
      } catch {
        // Continue through the deterministic Playwright search roots.
      }
    }
  }
  throw new Error("Playwright's WebM encoder is unavailable");
}

async function cropCanonicalVideo(
  inputPath: string,
  outputPath: string,
  startMs: number,
  durationMs: number,
): Promise<void> {
  await execFileAsync(await playwrightFfmpegPath(), [
    "-y",
    "-v",
    "error",
    "-ss",
    (startMs / 1_000).toFixed(3),
    "-i",
    inputPath,
    "-t",
    (durationMs / 1_000).toFixed(3),
    "-an",
    "-c:v",
    "libvpx",
    "-deadline",
    "realtime",
    "-cpu-used",
    "8",
    outputPath,
  ]);
}

function dimensions(png: Buffer): { width: number; height: number } {
  if (
    png.length < 24 ||
    png.subarray(1, 4).toString("ascii") !== "PNG" ||
    png.subarray(12, 16).toString("ascii") !== "IHDR"
  ) {
    throw new Error("A storyboard screenshot is not a complete PNG");
  }
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
}

async function artifactDescriptor(
  fileName: string,
  filePath: string,
  image: boolean,
) {
  const bytes = await readFile(filePath);
  return {
    fileName,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    ...(image ? dimensions(bytes) : {}),
  };
}

function acceptedCheckpointTimeline(
  observations: readonly SemanticStoryboardSessionObservation[],
) {
  const timeline: Array<{
    ordinal: number;
    checkpointId: string;
    generation: number;
    observedAtMs: number;
    sceneRevision: number;
    semanticRevision: number;
  }> = [];
  let acceptedCount = 0;
  for (const observation of observations) {
    const accepted = observation.snapshot.runtime.accepted;
    while (acceptedCount < accepted.length) {
      const item = accepted[acceptedCount];
      timeline.push({
        ordinal: acceptedCount,
        checkpointId: item.event.transition.checkpoint.checkpointId,
        generation: item.event.generation,
        observedAtMs: rounded(observation.observedAtMs),
        sceneRevision: item.scene.revision,
        semanticRevision: item.semanticScene.revision,
      });
      acceptedCount += 1;
    }
  }
  return timeline;
}

function compactObservation(observation: SemanticStoryboardSessionObservation) {
  return {
    observedAtMs: rounded(observation.observedAtMs),
    status: observation.snapshot.status,
    phase: observation.snapshot.runtime.phase,
    generation: observation.snapshot.runtime.generation,
    acceptedCheckpointIds: observation.snapshot.runtime.accepted.map(
      (accepted) => accepted.event.transition.checkpoint.checkpointId,
    ),
    sceneRevision: observation.snapshot.runtime.committedScene.revision,
    semanticRevision:
      observation.snapshot.runtime.committedSemanticScene.revision,
    rendererTrusted: observation.snapshot.runtime.rendererTrusted,
  };
}

async function composeContactSheet(
  page: Page,
  frames: readonly CheckpointFrame[],
): Promise<Buffer> {
  const cells = frames.map((frame) => ({
    checkpointId: frame.checkpointId,
    dataUrl: `data:image/png;base64,${frame.png.toString("base64")}`,
    ...dimensions(frame.png),
  }));
  const first = cells[0];
  if (!first) throw new Error("The storyboard contact sheet has no frames");
  if (
    cells.some(
      (cell) => cell.width !== first.width || cell.height !== first.height,
    )
  ) {
    throw new Error("Storyboard checkpoint frames changed pixel dimensions");
  }
  const base64 = await page.evaluate(
    async ({ images, frameWidth, frameHeight }) => {
      const columns = 2;
      const rows = Math.ceil(images.length / columns);
      const canvas = document.createElement("canvas");
      canvas.width = columns * frameWidth;
      canvas.height = rows * frameHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Could not create contact-sheet canvas");
      context.fillStyle = "#050507";
      context.fillRect(0, 0, canvas.width, canvas.height);
      for (const [index, cell] of images.entries()) {
        const image = new Image();
        image.src = cell.dataUrl;
        await image.decode();
        context.drawImage(
          image,
          (index % columns) * frameWidth,
          Math.floor(index / columns) * frameHeight,
          frameWidth,
          frameHeight,
        );
      }
      return canvas.toDataURL("image/png").split(",", 2)[1] ?? "";
    },
    { images: cells, frameWidth: first.width, frameHeight: first.height },
  );
  if (!base64) throw new Error("Chromium produced an empty contact sheet");
  return Buffer.from(base64, "base64");
}

async function captureSettledCheckpoints(
  page: Page,
  testInfo: TestInfo,
  captureRoot: string,
  checkpointIds: readonly string[],
): Promise<readonly CheckpointFrame[]> {
  const frames: CheckpointFrame[] = [];
  await startSemanticStoryboard(page);
  for (const [ordinal, checkpointId] of checkpointIds.entries()) {
    if (ordinal === 5) {
      await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.continue);
      await continueSemanticStoryboard(page);
    }
    await page.waitForFunction(
      ({ expectedCount, expectedId }) => {
        const bridge = (
          window as typeof window &
            Record<
              string,
              {
                getSessionObservation(): SemanticStoryboardSessionObservation | null;
              }
            >
        ).__MURMUR_SEMANTIC_STORYBOARD_E2E__;
        const stage = document.querySelector<HTMLElement>(
          '[data-testid="semantic-storyboard-stage"]',
        );
        const snapshot = bridge?.getSessionObservation()?.snapshot;
        return (
          snapshot?.runtime.accepted.length === expectedCount &&
          stage?.dataset.visibleCheckpointId === expectedId &&
          !stage.querySelector(
            '[data-element-id$="--incoming"],[data-element-id$="--outgoing"]',
          )
        );
      },
      { expectedCount: ordinal + 1, expectedId: checkpointId },
      { polling: "raf", timeout: 30_000 },
    );
    const fileName = `semantic-storyboard-normal-speed-checkpoint-${String(
      ordinal,
    ).padStart(2, "0")}-${checkpointId}.png`;
    const filePath = path.join(captureRoot, fileName);
    const png = await attachSemanticStoryboardBoardScreenshot(
      page,
      testInfo,
      `semantic-storyboard-checkpoint-${ordinal}-${checkpointId}`,
      filePath,
    );
    frames.push({ ordinal, checkpointId, fileName, path: filePath, png });
  }
  return frames;
}

test("captures the complete provider-free normal-speed storyboard and settled contact sheet", async ({
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(145_000);
  if (!baseURL)
    throw new Error("The storyboard capture base URL is unavailable");
  const captureRoot = path.dirname(testInfo.project.outputDir);
  const rawVideoRoot = testInfo.outputPath("raw-video");
  await Promise.all([
    mkdir(captureRoot, { recursive: true }),
    mkdir(rawVideoRoot, { recursive: true }),
  ]);
  const videoPath = path.join(captureRoot, FILES.video);
  const rawVideoPath = path.join(rawVideoRoot, "semantic-storyboard-raw.webm");
  const fullPagePath = path.join(captureRoot, FILES.fullPage);
  const boardOnlyPath = path.join(captureRoot, FILES.boardOnly);
  const contactSheetPath = path.join(captureRoot, FILES.contactSheet);
  const observationPath = path.join(captureRoot, FILES.observation);
  const lane = semanticStoryboardLane(STORYBOARD_SCENARIOS.higherFirst);
  const continuation = semanticStoryboardLane(
    STORYBOARD_SCENARIOS.continuationFromHigherComplete,
  );

  const context = await browser.newContext({
    baseURL,
    viewport: VIEWPORT,
    screen: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "no-preference",
    serviceWorkers: "block",
    recordVideo: { dir: rawVideoRoot, size: VIEWPORT },
  });
  const recordingStartedAtEpochMs = Date.now();
  const page = await context.newPage();
  const video = page.video();
  if (!video) throw new Error("Playwright did not attach a storyboard video");
  const readNetwork = observeSemanticStoryboardNetwork(
    page,
    baseURL,
    CAPTURE_ROUTE,
  );
  let startedAtMs = 0;
  let stage: Awaited<ReturnType<typeof observeSemanticStoryboardStage>>;
  let terminal: Awaited<
    ReturnType<typeof semanticStoryboardSessionObservation>
  >;
  let history: Awaited<ReturnType<typeof semanticStoryboardSessionHistory>>;
  let runner: Awaited<ReturnType<typeof semanticStoryboardBridgeState>>;
  let events: Awaited<ReturnType<typeof semanticStoryboardEventHistory>>;
  let visibleHistory: Awaited<
    ReturnType<typeof semanticStoryboardVisibleCheckpointHistory>
  >;
  let dom: SemanticStoryboardDomInspection;
  let pageTimeOriginMs = 0;
  try {
    await page.goto(CAPTURE_ROUTE, { waitUntil: "domcontentloaded" });
    pageTimeOriginMs = await page.evaluate(() => performance.timeOrigin);
    await waitForSemanticStoryboardBridge(page);
    await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.higherFirst);
    await installSemanticStoryboardVisibleCheckpointProbe(page);
    startedAtMs = await page
      .getByRole("button", { name: "Make it visible" })
      .evaluate((button) => {
        const now = performance.now();
        (button as HTMLButtonElement).click();
        return now;
      });
    await expectSemanticStoryboardTerminal(page, lane, "cinematic", {
      timeout: 60_000,
    });
    await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.continue);
    await continueSemanticStoryboard(page);
    await expectSemanticStoryboardTerminal(page, continuation, "cinematic", {
      timeout: 30_000,
    });
    stage = await observeSemanticStoryboardStage(page);
    terminal = await semanticStoryboardSessionObservation(page);
    history = await semanticStoryboardSessionHistory(page);
    runner = await semanticStoryboardBridgeState(page);
    events = await semanticStoryboardEventHistory(page);
    visibleHistory = await semanticStoryboardVisibleCheckpointHistory(page);
    dom = await inspectSemanticStoryboardSvgAgainstScene(
      page,
      continuation.resultScene,
    );
    expect(dom.mismatches).toEqual([]);
    expect(acceptedModelCheckpointIds(terminal.snapshot)).toEqual([
      ...lane.checkpointIds,
      ...continuation.checkpointIds,
    ]);
    expect(runner.runnerCallCount).toBe(3);
    expectSemanticStoryboardProviderFree(readNetwork());
    await page.screenshot({
      path: fullPagePath,
      type: "png",
      fullPage: true,
      animations: "allow",
      caret: "hide",
      scale: "css",
    });
    await testInfo.attach("semantic-storyboard-normal-speed-full", {
      path: fullPagePath,
      contentType: "image/png",
    });
    await attachSemanticStoryboardBoardScreenshot(
      page,
      testInfo,
      "semantic-storyboard-normal-speed-board",
      boardOnlyPath,
    );
  } finally {
    await context.close();
    await video.saveAs(rawVideoPath);
    await video.delete();
  }

  const checkpointContext = await browser.newContext({
    baseURL,
    viewport: VIEWPORT,
    screen: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const checkpointPage = await checkpointContext.newPage();
  let frames: readonly CheckpointFrame[] = [];
  try {
    const readCheckpointNetwork = observeSemanticStoryboardNetwork(
      checkpointPage,
      baseURL,
      CHECKPOINT_ROUTE,
    );
    await checkpointPage.goto(CHECKPOINT_ROUTE, {
      waitUntil: "domcontentloaded",
    });
    await waitForSemanticStoryboardBridge(checkpointPage);
    await setSemanticStoryboardPrompt(
      checkpointPage,
      STORYBOARD_PROMPTS.higherFirst,
    );
    frames = await captureSettledCheckpoints(
      checkpointPage,
      testInfo,
      captureRoot,
      [
        "storyboard-anchor",
        ...lane.checkpointIds,
        ...continuation.checkpointIds,
      ],
    );
    await expectSemanticStoryboardTerminal(
      checkpointPage,
      continuation,
      "cinematic",
    );
    expectSemanticStoryboardProviderFree(readCheckpointNetwork());
    await writeFile(
      contactSheetPath,
      await composeContactSheet(checkpointPage, frames),
    );
    await testInfo.attach("semantic-storyboard-normal-speed-contact-sheet", {
      path: contactSheetPath,
      contentType: "image/png",
    });
  } finally {
    await checkpointContext.close();
  }

  const timeline = acceptedCheckpointTimeline(history);
  expect(timeline.map(({ checkpointId }) => checkpointId)).toEqual([
    "storyboard-anchor",
    ...lane.checkpointIds,
    ...continuation.checkpointIds,
  ]);
  const directorCall = runner.calls.find(
    (call) => call.routingMode === "director",
  );
  if (!timeline[1] || !directorCall) {
    throw new Error("The capture lacks a Director/model timing boundary");
  }
  const roundedStart = rounded(startedAtMs);
  const roundedTerminal = rounded(terminal.observedAtMs);
  const firstModel = timeline[1];
  const anchor = timeline[0];
  if (!anchor || !firstModel) {
    throw new Error("The capture lacks anchor/model post-paint observations");
  }
  const firstModelEvent = events.find(
    (event) =>
      event.generation === directorCall.generation &&
      event.checkpointId === firstModel.checkpointId,
  );
  if (!firstModelEvent) {
    throw new Error("The first model checkpoint transport event is absent");
  }
  const expectedVisibleCheckpointIds = [
    "storyboard-anchor",
    ...lane.checkpointIds,
    ...continuation.checkpointIds,
  ];
  expect(visibleHistory.map(({ checkpointId }) => checkpointId)).toEqual(
    expectedVisibleCheckpointIds,
  );
  const visibleByCheckpoint = new Map(
    visibleHistory.map((item) => [item.checkpointId, item.observedAtMs]),
  );
  const anchorVisibleAtMs = visibleByCheckpoint.get("storyboard-anchor");
  const firstModelVisibleAtMs = visibleByCheckpoint.get(
    firstModel.checkpointId,
  );
  if (anchorVisibleAtMs === undefined || firstModelVisibleAtMs === undefined) {
    throw new Error("The capture lacks anchor/model first-visible boundaries");
  }
  const directorDispatchToFirstVisibleMs = rounded(
    firstModelVisibleAtMs - directorCall.observedAtMs,
  );
  const firstModelCheckpointEventToFirstVisibleMs = rounded(
    firstModelVisibleAtMs - firstModelEvent.observedAtMs,
  );
  expect(directorDispatchToFirstVisibleMs).toBeGreaterThanOrEqual(0);
  expect(directorDispatchToFirstVisibleMs).toBeLessThan(2_000);
  expect(firstModelCheckpointEventToFirstVisibleMs).toBeGreaterThanOrEqual(0);
  const observedFiveBeatSequenceMs = rounded(
    terminal.observedAtMs - directorCall.observedAtMs,
  );
  expect(events.filter((event) => event.checkpointId)).toHaveLength(6);
  const checkpointEventToPostPaint = timeline.map((accepted) => {
    const event = events.find(
      (candidate) =>
        candidate.generation === accepted.generation &&
        candidate.checkpointId === accepted.checkpointId,
    );
    if (!event) {
      throw new Error(`No transport event exists for ${accepted.checkpointId}`);
    }
    return {
      checkpointId: accepted.checkpointId,
      eventAtMs: rounded(event.observedAtMs),
      acceptedAtMs: accepted.observedAtMs,
      durationMs: rounded(accepted.observedAtMs - event.observedAtMs),
    };
  });
  const callToStartedEventMs = runner.calls.map((call) => {
    const event = events.find(
      (candidate) =>
        candidate.generation === call.generation &&
        candidate.type === "semantic_storyboard_scene_stream_started",
    );
    if (!event) {
      throw new Error(
        `${call.routingMode} generation ${call.generation} lacks a started event`,
      );
    }
    return {
      ordinal: call.ordinal,
      generation: call.generation,
      routingMode: call.routingMode,
      durationMs: rounded(event.observedAtMs - call.observedAtMs),
    };
  });
  const postPaintToNextBeatVisibleGapsMs = timeline
    .slice(1)
    .map((next, index) => {
      const previous = timeline[index];
      const nextVisibleAtMs = visibleByCheckpoint.get(next.checkpointId);
      if (nextVisibleAtMs === undefined) {
        throw new Error(
          `No first-visible observation exists for ${next.checkpointId}`,
        );
      }
      const durationMs = rounded(nextVisibleAtMs - previous.observedAtMs);
      expect(durationMs).toBeGreaterThanOrEqual(0);
      return {
        fromCheckpointId: previous.checkpointId,
        toCheckpointId: next.checkpointId,
        postPaintAcceptedAtMs: previous.observedAtMs,
        nextVisibleAtMs: rounded(nextVisibleAtMs),
        durationMs,
      };
    });
  const checkpointEvents = events.filter(
    (event) => event.type === "semantic_storyboard_scene_checkpoint",
  );
  const transportCheckpointArrivalGapsMs = checkpointEvents
    .slice(1)
    .map((event, index) => ({
      fromCheckpointId: checkpointEvents[index].checkpointId,
      toCheckpointId: event.checkpointId,
      durationMs: rounded(
        event.observedAtMs - checkpointEvents[index].observedAtMs,
      ),
    }));
  const authoredVisualDurationMs = [
    ...lane.checkpoints,
    ...continuation.checkpoints,
  ].reduce(
    (total, checkpoint) =>
      total +
      checkpoint.transition.checkpoint.choreography.phase.durationMs +
      checkpoint.transition.checkpoint.choreography.phase.holdAfterMs,
    0,
  );
  expect(authoredVisualDurationMs).toBe(8_459);
  expect(observedFiveBeatSequenceMs).toBeGreaterThanOrEqual(
    authoredVisualDurationMs - 250,
  );
  expect(observedFiveBeatSequenceMs).toBeLessThanOrEqual(
    authoredVisualDurationMs + 2_000,
  );
  const videoLeadInMs = 100;
  const videoTailMs = 300;
  const directorDispatchAtEpochMs =
    pageTimeOriginMs + directorCall.observedAtMs;
  const videoCropStartMs = Math.max(
    0,
    directorDispatchAtEpochMs - recordingStartedAtEpochMs - videoLeadInMs,
  );
  const videoCropDurationMs =
    observedFiveBeatSequenceMs + videoLeadInMs + videoTailMs;
  await cropCanonicalVideo(
    rawVideoPath,
    videoPath,
    videoCropStartMs,
    videoCropDurationMs,
  );
  await rm(rawVideoPath, { force: true });
  expect((await stat(videoPath)).size).toBeGreaterThan(0);

  const checkpointDescriptors = await Promise.all(
    frames.map(async (frame) => ({
      ordinal: frame.ordinal,
      checkpointId: frame.checkpointId,
      ...(await artifactDescriptor(frame.fileName, frame.path, true)),
    })),
  );
  const observation = {
    schemaVersion: 1,
    route: {
      path: "/e2e/semantic-storyboard",
      query: "layout=cinematic&motion=real&proof=none&speed=normal",
    },
    viewport: VIEWPORT,
    runner,
    timeline: {
      observations: history.map(compactObservation),
      acceptedCheckpointTimeline: timeline,
    },
    terminal: { stage, snapshot: terminal.snapshot },
    timing: {
      startedAtMs: roundedStart,
      terminalAtMs: roundedTerminal,
      elapsedMs: rounded(roundedTerminal - roundedStart),
      anchorPaintMs: rounded(anchorVisibleAtMs - startedAtMs),
      anchorPostPaintAcceptanceMs: rounded(anchor.observedAtMs - startedAtMs),
      firstModelVisibleAtMs: rounded(firstModelVisibleAtMs),
      directorDispatchToFirstVisibleMs,
      firstModelCheckpointEventToFirstVisibleMs,
      anchorToFirstModelPostPaintMs: rounded(
        firstModel.observedAtMs - anchor.observedAtMs,
      ),
      directorDispatchToFirstModelPostPaintMs: rounded(
        firstModel.observedAtMs - directorCall.observedAtMs,
      ),
      authoredVisualDurationMs,
      observedFiveBeatSequenceMs,
      videoCrop: {
        recordingStartedAtEpochMs,
        pageTimeOriginMs: rounded(pageTimeOriginMs),
        directorDispatchAtEpochMs: rounded(directorDispatchAtEpochMs),
        startMs: rounded(videoCropStartMs),
        durationMs: rounded(videoCropDurationMs),
        leadInMs: videoLeadInMs,
        tailMs: videoTailMs,
      },
      callToStartedEventMs,
      checkpointEventToPostPaint,
      postPaintToNextBeatVisibleGapsMs,
      transportCheckpointArrivalGapsMs,
      unavailableMetrics: [
        "server_provider_first_byte",
        "record_complete_to_verification",
        "verification_duration",
        "post_paint_barrier_duration",
      ],
    },
    dom,
    artifacts: {
      video: await artifactDescriptor(FILES.video, videoPath, false),
      fullPage: await artifactDescriptor(FILES.fullPage, fullPagePath, true),
      boardOnly: await artifactDescriptor(FILES.boardOnly, boardOnlyPath, true),
      contactSheet: await artifactDescriptor(
        FILES.contactSheet,
        contactSheetPath,
        true,
      ),
      checkpoints: checkpointDescriptors,
    },
  };
  await writeFile(observationPath, `${JSON.stringify(observation, null, 2)}\n`);
  await testInfo.attach("semantic-storyboard-normal-speed-observation", {
    path: observationPath,
    contentType: "application/json",
  });
});
