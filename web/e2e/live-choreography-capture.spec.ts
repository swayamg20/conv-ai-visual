import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  expect,
  test,
  type BrowserContext,
  type Page,
  type Request,
} from "@playwright/test";

import fixtureValue from "../src/features/live-scene/fixtures/completing-the-square.v1.json";
import type { ChoreographyEvidenceTraceEvent } from "../src/features/live-scene/choreography-playback";
import {
  CINEMATIC_CHECKPOINTS,
  MAIN_CHOREOGRAPHY_EVIDENCE,
  acknowledgeCheckpoint,
  assertRetainedDomIdentity,
  choreographyStage,
  observeSettledCheckpoint,
  readCaptureBridgeState,
  waitForCaptureEvidence,
  type CaptureBridgeState,
  type SettledCheckpointObservation,
} from "./live-choreography-helpers";
import {
  observeChoreographyExecution,
  type ChoreographyEnvironmentObservation,
  type ChoreographySourceObservation,
} from "./live-choreography-provenance";

const ARTIFACT_ROOT = path.resolve(
  process.env.CHOREOGRAPHY_E2E_ARTIFACT_DIR ?? "../var/live-choreography",
);
const CAPTURE_ROOT = path.join(ARTIFACT_ROOT, "capture");
const CHECKPOINT_ROOT = path.join(CAPTURE_ROOT, "checkpoints");
const VIDEO_PATH = path.join(CAPTURE_ROOT, "live-choreography.webm");
const CONTACT_SHEET_PATH = path.join(
  CAPTURE_ROOT,
  "live-choreography-contact-sheet.png",
);
const OBSERVATIONS_PATH = path.join(CAPTURE_ROOT, "observations.json");

const VIDEO_VIEWPORT = Object.freeze({ width: 1_280, height: 720 });
const CONTACT_COLUMNS = 2;
const CONTACT_ROWS = 4;
const CONTACT_FRAME = Object.freeze({ width: 640, height: 360 });
const CONTACT_LABEL_HEIGHT = 34;
const CONTACT_CELL = Object.freeze({
  width: CONTACT_FRAME.width,
  height: CONTACT_FRAME.height + CONTACT_LABEL_HEIGHT,
});
const MAX_DIAGNOSTIC_SAMPLES = 500;

interface StageSettlement {
  readonly ordinal: number;
  readonly checkpointId: string;
  readonly phase: string;
  readonly atMs: number;
}

interface StagePhaseTransition {
  readonly phase: string;
  readonly checkpointId: string;
  readonly settledMainCount: number;
  readonly atMs: number;
}

interface PerformanceSample {
  readonly startTimeMs: number;
  readonly durationMs: number;
}

interface CapturePerformanceProbe {
  readonly version: 1;
  readonly installedAtMs: number;
  firstMeaningfulVisualAtMs: number | null;
  stoppedAtMs: number | null;
  readonly settlements: StageSettlement[];
  readonly phaseTransitions: StagePhaseTransition[];
  readonly longFrames: PerformanceSample[];
  readonly longTasks: PerformanceSample[];
  stop(): void;
}

type CapturePerformanceSnapshot = Omit<CapturePerformanceProbe, "stop">;

interface NetworkRequestObservation {
  readonly method: string;
  readonly resourceType: string;
  readonly target: string;
}

interface NetworkFailureObservation extends NetworkRequestObservation {
  readonly errorText: string;
}

interface NetworkObservation {
  readonly requestCount: number;
  readonly resourceTypeCounts: Readonly<Record<string, number>>;
  readonly liveSceneRequests: readonly NetworkRequestObservation[];
  readonly unexpectedRequests: readonly NetworkRequestObservation[];
  readonly failedRequests: readonly NetworkFailureObservation[];
}

interface ArtifactObservation {
  readonly path: string;
  readonly bytes: number;
  readonly sha256: string;
  readonly width?: number;
  readonly height?: number;
}

interface RealTimeCaptureObservation {
  readonly route: string;
  readonly layout: "cinematic";
  readonly viewport: typeof VIDEO_VIEWPORT;
  readonly fixtureAuthoredDurationMs: number;
  readonly firstMeaningfulVisualAtMs: number;
  readonly completedAtMs: number;
  readonly visualDurationMs: number;
  readonly settlements: readonly StageSettlement[];
  readonly phaseTransitions: readonly StagePhaseTransition[];
  readonly longFrames: readonly PerformanceSample[];
  readonly longTasks: readonly PerformanceSample[];
  readonly finalCheckpoint: SettledCheckpointObservation;
  readonly runtimeEvidence: readonly ChoreographyEvidenceTraceEvent[];
  readonly network: NetworkObservation;
  readonly video: ArtifactObservation;
}

interface CheckpointCaptureObservation {
  readonly route: string;
  readonly layout: "cinematic";
  readonly viewport: typeof VIDEO_VIEWPORT;
  readonly network: NetworkObservation;
  readonly runtimeEvidence: readonly ChoreographyEvidenceTraceEvent[];
  readonly checkpoints: readonly (SettledCheckpointObservation & {
    readonly gateOpenedAtMs: number;
    readonly capturedAtMs: number;
    readonly gateToCaptureMs: number;
    readonly cues: readonly string[];
    readonly baseViewport: string;
    readonly resultViewport: string;
    readonly screenshot: ArtifactObservation;
  })[];
  readonly contactSheet: ArtifactObservation;
}

interface CaptureObservations {
  readonly v: 1;
  readonly gate: "1.5";
  source: ChoreographySourceObservation | null;
  environment: ChoreographyEnvironmentObservation | null;
  readonly fixtureId: string;
  readonly compilerVersion: string;
  readonly generatedAt: string;
  realTime: RealTimeCaptureObservation | null;
  checkpointCapture: CheckpointCaptureObservation | null;
}

const observations: CaptureObservations = {
  v: 1,
  gate: "1.5",
  source: null,
  environment: null,
  fixtureId: fixtureValue.fixtureId,
  compilerVersion: fixtureValue.compilerVersion,
  generatedAt: new Date().toISOString(),
  realTime: null,
  checkpointCapture: null,
};

function rounded(value: number): number {
  return Number(value.toFixed(3));
}

function viewportString(viewport: {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}): string {
  return `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;
}

function artifactRelativePath(filePath: string): string {
  return path.relative(ARTIFACT_ROOT, filePath).split(path.sep).join("/");
}

async function artifactObservation(
  filePath: string,
  buffer?: Buffer,
): Promise<ArtifactObservation> {
  const bytes = buffer ?? (await readFile(filePath));
  const file = await stat(filePath);
  const dimensions = filePath.endsWith(".png")
    ? {
        width: bytes.readUInt32BE(16),
        height: bytes.readUInt32BE(20),
      }
    : {};
  return Object.freeze({
    path: artifactRelativePath(filePath),
    bytes: file.size,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    ...dimensions,
  });
}

async function persistObservations(): Promise<void> {
  await mkdir(CAPTURE_ROOT, { recursive: true });
  await writeFile(
    OBSERVATIONS_PATH,
    `${JSON.stringify(observations, null, 2)}\n`,
    "utf8",
  );
}

function normalizeRequest(
  request: Request,
  baseURL: string,
): NetworkRequestObservation {
  const url = new URL(request.url());
  const baseOrigin = new URL(baseURL).origin;
  return Object.freeze({
    method: request.method(),
    resourceType: request.resourceType(),
    target:
      url.origin === baseOrigin
        ? `${url.pathname}${url.search}`
        : `${url.origin}${url.pathname}`,
  });
}

function observeNetwork(
  page: Page,
  baseURL: string,
  documentTarget: string,
): () => NetworkObservation {
  const requests: NetworkRequestObservation[] = [];
  const failures: NetworkFailureObservation[] = [];

  page.on("request", (request) => {
    requests.push(normalizeRequest(request, baseURL));
  });
  page.on("requestfailed", (request) => {
    failures.push(
      Object.freeze({
        ...normalizeRequest(request, baseURL),
        errorText: request.failure()?.errorText ?? "unknown",
      }),
    );
  });

  return () => {
    const resourceTypeCounts: Record<string, number> = {};
    for (const request of requests) {
      resourceTypeCounts[request.resourceType] =
        (resourceTypeCounts[request.resourceType] ?? 0) + 1;
    }
    const allowedRequest = (request: NetworkRequestObservation): boolean =>
      request.method === "GET" &&
      (request.target === documentTarget ||
        request.target.startsWith("/_next/") ||
        request.target === "/favicon.ico");
    return Object.freeze({
      requestCount: requests.length,
      resourceTypeCounts: Object.freeze(resourceTypeCounts),
      liveSceneRequests: Object.freeze(
        requests.filter((request) =>
          /^\/api\/live-scenes(?:[/?]|$)/.test(request.target),
        ),
      ),
      unexpectedRequests: Object.freeze(
        requests.filter((request) => !allowedRequest(request)),
      ),
      failedRequests: Object.freeze([...failures]),
    });
  };
}

async function installPerformanceProbe(context: BrowserContext): Promise<void> {
  await context.addInitScript((maximumSamples) => {
    const key = "__MURMUR_CHOREOGRAPHY_PERFORMANCE__";
    const owner = window as typeof window & Record<string, unknown>;
    const settlements: StageSettlement[] = [];
    const phaseTransitions: StagePhaseTransition[] = [];
    const longFrames: PerformanceSample[] = [];
    const longTasks: PerformanceSample[] = [];
    const seenSettlements = new Set<number>();
    let lastPhaseSignature = "";
    let animationFrame = 0;
    let lastFrameAt = performance.now();
    let stopped = false;

    const probe: CapturePerformanceProbe = {
      version: 1,
      installedAtMs: performance.now(),
      firstMeaningfulVisualAtMs: null,
      stoppedAtMs: null,
      settlements,
      phaseTransitions,
      longFrames,
      longTasks,
      stop() {
        if (stopped) return;
        stopped = true;
        probe.stoppedAtMs = performance.now();
        cancelAnimationFrame(animationFrame);
        observer.disconnect();
        longTaskObserver?.disconnect();
      },
    };

    const sampleStage = (): void => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="live-choreography-stage"]',
      );
      if (!stage) return;
      const ordinal = Number(stage.dataset.settledMainCount ?? "0");
      const checkpointId = stage.dataset.checkpointId ?? "none";
      const phase = stage.dataset.phase ?? "unknown";
      const now = performance.now();

      if (
        Number.isInteger(ordinal) &&
        ordinal > 0 &&
        !seenSettlements.has(ordinal)
      ) {
        seenSettlements.add(ordinal);
        settlements.push({ ordinal, checkpointId, phase, atMs: now });
      }

      const signature = `${phase}:${checkpointId}:${ordinal}`;
      if (signature !== lastPhaseSignature) {
        lastPhaseSignature = signature;
        phaseTransitions.push({
          phase,
          checkpointId,
          settledMainCount: ordinal,
          atMs: now,
        });
      }
    };

    const observer = new MutationObserver(sampleStage);
    observer.observe(document, {
      attributes: true,
      attributeFilter: [
        "data-checkpoint-id",
        "data-phase",
        "data-settled-main-count",
      ],
      childList: true,
      subtree: true,
    });

    let longTaskObserver: PerformanceObserver | null = null;
    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      longTaskObserver = new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries()) {
          if (longTasks.length >= maximumSamples) break;
          longTasks.push({
            startTimeMs: entry.startTime,
            durationMs: entry.duration,
          });
        }
      });
      longTaskObserver.observe({ type: "longtask", buffered: true });
    }

    const tick = (now: number): void => {
      const duration = now - lastFrameAt;
      if (duration > 50 && longFrames.length < maximumSamples) {
        longFrames.push({ startTimeMs: lastFrameAt, durationMs: duration });
      }
      lastFrameAt = now;

      if (probe.firstMeaningfulVisualAtMs === null) {
        const nodes = Array.from(
          document.querySelectorAll<SVGGraphicsElement>(
            '[data-testid="live-choreography-stage"] svg > [data-element-id]',
          ),
        );
        const meaningful = nodes.some((node) => {
          const bounds = node.getBoundingClientRect();
          const opacity = Number.parseFloat(
            getComputedStyle(node).opacity || "0",
          );
          return bounds.width > 0 && bounds.height > 0 && opacity > 0;
        });
        if (meaningful) probe.firstMeaningfulVisualAtMs = now;
      }

      sampleStage();
      if (!stopped) animationFrame = requestAnimationFrame(tick);
    };

    Object.defineProperty(owner, key, {
      configurable: false,
      enumerable: false,
      writable: false,
      value: probe,
    });
    animationFrame = requestAnimationFrame(tick);
  }, MAX_DIAGNOSTIC_SAMPLES);
}

async function readPerformanceProbe(
  page: Page,
): Promise<CapturePerformanceSnapshot> {
  return page.evaluate(() => {
    const owner = window as typeof window & Record<string, unknown>;
    const probe = owner["__MURMUR_CHOREOGRAPHY_PERFORMANCE__"] as
      CapturePerformanceProbe | undefined;
    if (!probe || probe.version !== 1) {
      throw new Error("The choreography performance probe is unavailable");
    }
    probe.stop();
    return {
      version: probe.version,
      installedAtMs: probe.installedAtMs,
      firstMeaningfulVisualAtMs: probe.firstMeaningfulVisualAtMs,
      stoppedAtMs: probe.stoppedAtMs,
      settlements: probe.settlements.map((entry) => ({ ...entry })),
      phaseTransitions: probe.phaseTransitions.map((entry) => ({ ...entry })),
      longFrames: probe.longFrames.map((entry) => ({ ...entry })),
      longTasks: probe.longTasks.map((entry) => ({ ...entry })),
    };
  });
}

async function waitForPendingCheckpoint(
  page: Page,
  ordinal: number,
  checkpointId: string,
): Promise<NonNullable<CaptureBridgeState["waitingFor"]>> {
  await expect
    .poll(
      async () => {
        try {
          return (await readCaptureBridgeState(page)).waitingFor;
        } catch {
          // The bridge may be absent during React Strict Mode's same-tick probe.
          return null;
        }
      },
      {
        message: `checkpoint ${ordinal} (${checkpointId}) did not enter the capture gate`,
      },
    )
    .toMatchObject({ generation: 1, sequence: ordinal, checkpointId });
  const waiting = (await readCaptureBridgeState(page)).waitingFor;
  if (!waiting) throw new Error("The capture gate closed before observation");
  return waiting;
}

async function composeContactSheet(
  page: Page,
  screenshots: readonly { readonly label: string; readonly png: Buffer }[],
): Promise<Buffer> {
  const encoded = screenshots.map(({ label, png }) => ({
    label,
    dataUrl: `data:image/png;base64,${png.toString("base64")}`,
  }));
  const result = await page.evaluate(
    async ({
      cells,
      columns,
      rows,
      cellWidth,
      cellHeight,
      frameHeight,
      labelHeight,
    }) => {
      if (cells.length !== columns * rows) {
        throw new Error("The contact sheet requires exactly two by four cells");
      }
      const canvas = document.createElement("canvas");
      canvas.width = columns * cellWidth;
      canvas.height = rows * cellHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("The browser could not create a 2D canvas");
      context.fillStyle = "#060608";
      context.fillRect(0, 0, canvas.width, canvas.height);

      for (const [index, cell] of cells.entries()) {
        const image = new Image();
        image.src = cell.dataUrl;
        await image.decode();
        const x = (index % columns) * cellWidth;
        const y = Math.floor(index / columns) * cellHeight;
        context.fillStyle = "#0b0b0f";
        context.fillRect(x, y, cellWidth, labelHeight);
        context.fillStyle = "#f4b63f";
        context.font = "600 17px ui-monospace, SFMono-Regular, monospace";
        context.textBaseline = "middle";
        context.fillText(cell.label, x + 14, y + labelHeight / 2);
        context.drawImage(image, x, y + labelHeight, cellWidth, frameHeight);
        context.strokeStyle = "rgba(244, 182, 63, 0.28)";
        context.lineWidth = 2;
        context.strokeRect(x + 1, y + 1, cellWidth - 2, cellHeight - 2);
      }

      return canvas.toDataURL("image/png").split(",", 2)[1] ?? "";
    },
    {
      cells: encoded,
      columns: CONTACT_COLUMNS,
      rows: CONTACT_ROWS,
      cellWidth: CONTACT_CELL.width,
      cellHeight: CONTACT_CELL.height,
      frameHeight: CONTACT_FRAME.height,
      labelHeight: CONTACT_LABEL_HEIGHT,
    },
  );
  if (!result) throw new Error("The browser produced an empty contact sheet");
  return Buffer.from(result, "base64");
}

test.beforeAll(async ({ browser }, testInfo) => {
  const execution = observeChoreographyExecution(testInfo.config, browser);
  observations.source = execution.source;
  observations.environment = execution.environment;
  await mkdir(CHECKPOINT_ROOT, { recursive: true });
});

test.afterAll(async () => {
  await persistObservations();
});

test("records the complete 1x cinematic choreography", async ({
  browser,
  baseURL,
}, testInfo) => {
  testInfo.setTimeout(140_000);
  if (!baseURL) throw new Error("The choreography base URL is unavailable");

  const rawVideoRoot = testInfo.outputPath("raw-video");
  const context = await browser.newContext({
    baseURL,
    viewport: VIDEO_VIEWPORT,
    screen: VIDEO_VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "no-preference",
    recordVideo: { dir: rawVideoRoot, size: VIDEO_VIEWPORT },
  });
  await installPerformanceProbe(context);
  const page = await context.newPage();
  const route =
    "/e2e/choreography?layout=cinematic&motion=real&pace=auto&timing=real";
  const readNetwork = observeNetwork(page, baseURL, route);
  const video = page.video();
  if (!video) throw new Error("Playwright did not attach a video recorder");

  let performance: CapturePerformanceSnapshot | null = null;
  let finalCheckpoint: SettledCheckpointObservation | null = null;
  let runtimeEvidence: readonly ChoreographyEvidenceTraceEvent[] | null = null;
  try {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    const stage = choreographyStage(page);
    await expect(stage).toHaveAttribute("data-layout", "cinematic");
    await expect(stage).toHaveAttribute("data-phase", "completed", {
      timeout: 90_000,
    });
    finalCheckpoint = await observeSettledCheckpoint(
      page,
      CINEMATIC_CHECKPOINTS.at(-1)!,
    );
    await page.waitForTimeout(2_000);
    performance = await readPerformanceProbe(page);
    runtimeEvidence = await waitForCaptureEvidence(
      page,
      MAIN_CHOREOGRAPHY_EVIDENCE,
    );
  } finally {
    await context.close();
    await video.saveAs(VIDEO_PATH);
    await video.delete();
  }

  if (!performance || !finalCheckpoint || !runtimeEvidence) {
    throw new Error("The real-time capture did not reach its final checkpoint");
  }
  if (performance.firstMeaningfulVisualAtMs === null) {
    throw new Error("The performance probe never observed a meaningful visual");
  }
  expect(performance.settlements).toHaveLength(CINEMATIC_CHECKPOINTS.length);
  expect(performance.settlements.map((entry) => entry.checkpointId)).toEqual(
    CINEMATIC_CHECKPOINTS.map((checkpoint) => checkpoint.checkpointId),
  );
  const completedAtMs = performance.settlements.at(-1)!.atMs;
  const visualDurationMs =
    completedAtMs - performance.firstMeaningfulVisualAtMs;
  expect(visualDurationMs).toBeGreaterThanOrEqual(60_000);
  expect(visualDurationMs).toBeLessThanOrEqual(90_000);
  const network = readNetwork();
  expect(network.liveSceneRequests).toEqual([]);
  expect(network.unexpectedRequests).toEqual([]);

  observations.realTime = Object.freeze({
    route,
    layout: "cinematic",
    viewport: VIDEO_VIEWPORT,
    fixtureAuthoredDurationMs: fixtureValue.transcript.authoredDurationMs,
    firstMeaningfulVisualAtMs: rounded(performance.firstMeaningfulVisualAtMs),
    completedAtMs: rounded(completedAtMs),
    visualDurationMs: rounded(visualDurationMs),
    settlements: Object.freeze(
      performance.settlements.map((entry) => ({
        ...entry,
        atMs: rounded(entry.atMs),
      })),
    ),
    phaseTransitions: Object.freeze(
      performance.phaseTransitions.map((entry) => ({
        ...entry,
        atMs: rounded(entry.atMs),
      })),
    ),
    longFrames: Object.freeze(
      performance.longFrames.map((entry) => ({
        startTimeMs: rounded(entry.startTimeMs),
        durationMs: rounded(entry.durationMs),
      })),
    ),
    longTasks: Object.freeze(
      performance.longTasks.map((entry) => ({
        startTimeMs: rounded(entry.startTimeMs),
        durationMs: rounded(entry.durationMs),
      })),
    ),
    finalCheckpoint,
    runtimeEvidence,
    network,
    video: await artifactObservation(VIDEO_PATH),
  });
  await persistObservations();
});

test("captures all eight quiescent checkpoints and a two-by-four contact sheet", async ({
  page,
  baseURL,
}) => {
  if (!baseURL) throw new Error("The choreography base URL is unavailable");
  const route =
    "/e2e/choreography?layout=cinematic&motion=real&pace=step&timing=accelerated";
  const readNetwork = observeNetwork(page, baseURL, route);
  await page.goto(route, { waitUntil: "domcontentloaded" });
  await expect(choreographyStage(page)).toHaveAttribute(
    "data-layout",
    "cinematic",
  );

  const checkpointObservations: NonNullable<
    CheckpointCaptureObservation["checkpoints"]
  >[number][] = [];
  const screenshots: { label: string; png: Buffer }[] = [];
  let previous: SettledCheckpointObservation | null = null;
  let runtimeEvidence: readonly ChoreographyEvidenceTraceEvent[] = [];

  for (const checkpoint of CINEMATIC_CHECKPOINTS) {
    const waiting = await waitForPendingCheckpoint(
      page,
      checkpoint.ordinal,
      checkpoint.checkpointId,
    );
    const settled = await observeSettledCheckpoint(page, checkpoint);
    if (previous) assertRetainedDomIdentity(previous, settled);
    const expectedEvidence = MAIN_CHOREOGRAPHY_EVIDENCE.filter(
      (event) => event.sequence <= checkpoint.ordinal,
    );
    runtimeEvidence = await waitForCaptureEvidence(page, expectedEvidence);
    const checkpointEvidence = runtimeEvidence.filter(
      (event) => event.sequence === checkpoint.ordinal,
    );

    const screenshotPath = path.join(
      CHECKPOINT_ROOT,
      `${String(checkpoint.ordinal).padStart(2, "0")}-${checkpoint.checkpointId}.png`,
    );
    const png = await choreographyStage(page).screenshot({
      path: screenshotPath,
      type: "png",
      animations: "allow",
      caret: "hide",
      scale: "css",
    });
    const capturedAtMs = await page.evaluate(() => performance.now());
    checkpointObservations.push(
      Object.freeze({
        ...settled,
        gateOpenedAtMs: rounded(waiting.openedAtMs),
        capturedAtMs: rounded(capturedAtMs),
        gateToCaptureMs: rounded(capturedAtMs - waiting.openedAtMs),
        cues: checkpointEvidence
          .filter((event) => event.type === "cueStarted")
          .map((event) => event.cue),
        baseViewport: viewportString(checkpoint.baseViewport),
        resultViewport: viewportString(checkpoint.resultViewport),
        screenshot: await artifactObservation(screenshotPath, png),
      }),
    );
    screenshots.push({
      label: `${checkpoint.ordinal}. ${checkpoint.checkpointId.replaceAll("_", " ")}`,
      png,
    });
    previous = settled;
    await acknowledgeCheckpoint(page, checkpoint);
  }

  await expect(choreographyStage(page)).toHaveAttribute(
    "data-phase",
    "completed",
  );
  const contactSheet = await composeContactSheet(page, screenshots);
  await writeFile(CONTACT_SHEET_PATH, contactSheet);
  const network = readNetwork();
  expect(network.liveSceneRequests).toEqual([]);
  expect(network.unexpectedRequests).toEqual([]);
  expect(checkpointObservations).toHaveLength(8);

  observations.checkpointCapture = Object.freeze({
    route,
    layout: "cinematic",
    viewport: VIDEO_VIEWPORT,
    network,
    runtimeEvidence,
    checkpoints: Object.freeze(checkpointObservations),
    contactSheet: await artifactObservation(CONTACT_SHEET_PATH, contactSheet),
  });
  await persistObservations();
});
