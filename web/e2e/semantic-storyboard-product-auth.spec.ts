import { expect, test, type Page, type Request } from "@playwright/test";

import { createSemanticStoryboardFixtureBatch } from "../src/features/live-scene/semantic-storyboard-scene-stream-fixture";
import type { SemanticStoryboardRequestV1 } from "../src/lib/live-scene/semantic-storyboard";
import {
  STORYBOARD_PROMPTS,
  STORYBOARD_SCENARIOS,
  SEMANTIC_STORYBOARD_PARTIAL_STREAM_PROBE_KEY,
  expectSemanticStoryboardFrontierQuiet,
  expectSemanticStoryboardTerminal,
  interruptSemanticStoryboardAtSurface,
  semanticStoryboardFixture,
  semanticStoryboardLane,
  semanticStoryboardRoot,
  semanticStoryboardStage,
  setSemanticStoryboardPrompt,
  startSemanticStoryboard,
  waitForSemanticStoryboardStatus,
} from "./semantic-storyboard-helpers";
import { expectSemanticStoryboardSvgMatchesScene } from "./semantic-storyboard-dom-oracle";

const TEST_API_KEY = "test-api-key";
const TEST_ACCESS_TOKEN = "gate-1.8-storyboard-browser-auth-token";
const FIREBASE_AUTH_KEY = `firebase:authUser:${TEST_API_KEY}:[DEFAULT]`;
const IDENTITY_LOOKUP_PATH = "/v1/accounts:lookup";
const PRODUCT_STREAM_PATH = "/api/live-scenes/choreography/stream";

interface ProductRequestObservation {
  readonly authorization: string | undefined;
  readonly contentType: string | undefined;
  readonly providerHeaders: Readonly<Record<string, string>>;
  readonly method: string;
  readonly body: unknown;
}

function firebaseUser(expirationTime: number): object {
  return {
    uid: "gate-1.8-storyboard-browser-user",
    email: "gate-1.8-storyboard@example.test",
    emailVerified: true,
    isAnonymous: false,
    providerData: [],
    stsTokenManager: {
      refreshToken: "gate-1.8-storyboard-browser-refresh-token",
      accessToken: TEST_ACCESS_TOKEN,
      expirationTime,
    },
    apiKey: TEST_API_KEY,
    appName: "[DEFAULT]",
  };
}

async function seedFirebaseBrowserPersistence(page: Page): Promise<void> {
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: FIREBASE_AUTH_KEY,
      value: firebaseUser(Date.now() + 24 * 60 * 60 * 1_000),
    },
  );
}

function observeProductRequest(request: Request): ProductRequestObservation {
  const headers = request.headers();
  return {
    authorization: headers.authorization,
    contentType: headers["content-type"],
    providerHeaders: Object.fromEntries(
      Object.entries(headers).filter(([name]) =>
        /(?:api[-_]?key|azure|openai|anthropic|provider)/i.test(name),
      ),
    ),
    method: request.method(),
    body: request.postDataJSON(),
  };
}

function fixtureSse(request: SemanticStoryboardRequestV1): string {
  return createSemanticStoryboardFixtureBatch(request)
    .events.map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
}

async function mockFirebaseIdentity(page: Page): Promise<void> {
  await page
    .context()
    .route("https://identitytoolkit.googleapis.com/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          users: [
            {
              localId: "gate-1.8-storyboard-browser-user",
              email: "gate-1.8-storyboard@example.test",
              emailVerified: true,
              providerUserInfo: [],
            },
          ],
        }),
      });
    });
}

test("the signed-out product guard redirects before any storyboard model fetch", async ({
  page,
}) => {
  const requests: ProductRequestObservation[] = [];
  await page.context().route(`**${PRODUCT_STREAM_PATH}`, async (route) => {
    requests.push(observeProductRequest(route.request()));
    await route.abort("blockedbyclient");
  });

  await page.goto("/canvas/storyboard");
  await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 });
  expect(requests).toEqual([]);
});

test("the authenticated product sends a fresh bearer on both Reflex and Director calls without provider secrets", async ({
  page,
}) => {
  const identityLookups: Array<{
    apiKey: string | null;
    method: string;
    body: unknown;
  }> = [];
  const unexpectedExternalRequests: string[] = [];
  page.context().on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const expectedIdentity =
      url.hostname === "identitytoolkit.googleapis.com" &&
      url.pathname === IDENTITY_LOOKUP_PATH;
    if (!local && !expectedIdentity) {
      unexpectedExternalRequests.push(
        `${request.method()} ${url.origin}${url.pathname}`,
      );
    }
  });
  await page
    .context()
    .route("https://identitytoolkit.googleapis.com/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      identityLookups.push({
        apiKey: url.searchParams.get("key"),
        method: request.method(),
        body: request.postDataJSON(),
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          users: [
            {
              localId: "gate-1.8-storyboard-browser-user",
              email: "gate-1.8-storyboard@example.test",
              emailVerified: true,
              providerUserInfo: [],
            },
          ],
        }),
      });
    });

  const observations: ProductRequestObservation[] = [];
  await page.context().route(`**${PRODUCT_STREAM_PATH}`, async (route) => {
    const request = route.request();
    observations.push(observeProductRequest(request));
    const body = request.postDataJSON() as SemanticStoryboardRequestV1;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: fixtureSse(body),
    });
  });

  await seedFirebaseBrowserPersistence(page);
  await page.goto("/canvas/storyboard");
  await expect(page).toHaveURL(/\/canvas\/storyboard$/);
  await expect(
    page.getByRole("heading", { name: "Live storyboard" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Make it visible" }).click();
  const lane = semanticStoryboardLane(STORYBOARD_SCENARIOS.pathsFirst);
  await expectSemanticStoryboardTerminal(page, lane, "cinematic");

  expect(observations).toHaveLength(2);
  expect(
    observations.map(
      ({ method, authorization, contentType, providerHeaders }) => ({
        method,
        authorization,
        contentType,
        providerHeaders,
      }),
    ),
  ).toEqual([
    {
      method: "POST",
      authorization: `Bearer ${TEST_ACCESS_TOKEN}`,
      contentType: "application/json",
      providerHeaders: {},
    },
    {
      method: "POST",
      authorization: `Bearer ${TEST_ACCESS_TOKEN}`,
      contentType: "application/json",
      providerHeaders: {},
    },
  ]);
  const reflex = observations[0].body as SemanticStoryboardRequestV1;
  const director = observations[1].body as SemanticStoryboardRequestV1;
  expect(reflex).toEqual({
    protocol: "projectile_comparison_storyboard_v1",
    routingMode: "reflex",
    problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
    generation: 1,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  });
  expect(director).toMatchObject({
    protocol: "projectile_comparison_storyboard_v1",
    routingMode: "director",
    prompt: STORYBOARD_PROMPTS.pathsFirst,
    problemSpec: { v: 1, speedMps: 20, anglesDeg: [30, 60] },
    generation: 2,
    baseScene: { revision: 1 },
    baseSemanticScene: { revision: 1 },
  });
  expect(director.baseScene.nodes.length).toBeGreaterThan(0);
  expect(director.baseSemanticScene.components).toHaveLength(1);
  expect(director.baseSemanticScene.components[0]?.acceptedRecords).toEqual([]);
  expect(director.baseSemanticScene.certificateHeadSha256).toMatch(
    /^[0-9a-f]{64}$/,
  );
  expect(JSON.stringify(observations.map(({ body }) => body))).not.toContain(
    TEST_ACCESS_TOKEN,
  );
  expect(identityLookups.length).toBeGreaterThanOrEqual(1);
  expect(identityLookups.length).toBeLessThanOrEqual(2);
  expect(identityLookups).toEqual(
    identityLookups.map(() => ({
      apiKey: TEST_API_KEY,
      method: "POST",
      body: { idToken: TEST_ACCESS_TOKEN },
    })),
  );
  expect(unexpectedExternalRequests).toEqual([]);
});

test("the authenticated product aborts an incomplete Director SSE record without publishing it", async ({
  page,
}, testInfo) => {
  test.setTimeout(45_000);
  await mockFirebaseIdentity(page);
  await seedFirebaseBrowserPersistence(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/canvas/storyboard");
  await expect(page).toHaveURL(/\/canvas\/storyboard$/);
  await expect(
    page.getByRole("heading", { name: "Live storyboard" }),
  ).toBeVisible();
  await expect(semanticStoryboardRoot(page)).toHaveAttribute(
    "data-reduced-motion",
    "true",
  );

  const fixture = semanticStoryboardFixture();
  const lane = semanticStoryboardLane(STORYBOARD_SCENARIOS.pathsFirst);
  const started = lane.events.find(
    (event) => event.type === "semantic_storyboard_scene_stream_started",
  );
  const checkpoint = lane.events.find(
    (event) => event.type === "semantic_storyboard_scene_checkpoint",
  );
  if (!started || !checkpoint) {
    throw new Error("The partial-record fixture lacks Director events");
  }
  await page.evaluate(
    ({
      streamPath,
      anchorEvents,
      directorStarted,
      directorCheckpoint,
      probeKey,
    }) => {
      const originalFetch = window.fetch.bind(window);
      const state = {
        requestCount: 0,
        partialChunkCount: 0,
        abortCount: 0,
        activePartialOpen: false,
        calls: [] as Array<{
          routingMode: string;
          authorization: string | null;
        }>,
      };
      (window as typeof window & Record<string, unknown>)[probeKey] = state;
      window.fetch = async (input, init) => {
        const request = new Request(input, init);
        const url = new URL(request.url);
        if (url.pathname !== streamPath) return originalFetch(input, init);
        const body = JSON.parse(await request.clone().text()) as {
          routingMode?: string;
          generation?: number;
        };
        const routingMode = body.routingMode ?? "unknown";
        if (!Number.isInteger(body.generation)) {
          throw new Error("The storyboard request generation is missing");
        }
        const generation = body.generation as number;
        const sse = (events: readonly object[]) =>
          events
            .map(
              (event) =>
                `data: ${JSON.stringify({ ...event, generation })}\n\n`,
            )
            .join("");
        state.requestCount += 1;
        state.calls.push({
          routingMode,
          authorization: request.headers.get("authorization"),
        });
        if (routingMode === "reflex") {
          state.activePartialOpen = false;
          return new Response(sse(anchorEvents), {
            status: 200,
            headers: { "content-type": "text/event-stream; charset=utf-8" },
          });
        }
        const startedSse = sse([directorStarted]);
        const checkpointRecord = sse([directorCheckpoint]);
        const partialSse = `${startedSse}${checkpointRecord.slice(
          0,
          Math.floor(checkpointRecord.length / 2),
        )}`;
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              state.activePartialOpen = true;
              state.partialChunkCount += 1;
              controller.enqueue(new TextEncoder().encode(partialSse));
              const abort = () => {
                if (!state.activePartialOpen) return;
                state.activePartialOpen = false;
                state.abortCount += 1;
                controller.error(new DOMException("Aborted", "AbortError"));
              };
              if (request.signal.aborted) abort();
              else
                request.signal.addEventListener("abort", abort, { once: true });
            },
          }),
          {
            status: 200,
            headers: { "content-type": "text/event-stream; charset=utf-8" },
          },
        );
      };
    },
    {
      streamPath: PRODUCT_STREAM_PATH,
      anchorEvents: fixture.anchor.events,
      directorStarted: started,
      directorCheckpoint: checkpoint,
      probeKey: SEMANTIC_STORYBOARD_PARTIAL_STREAM_PROBE_KEY,
    },
  );

  const observations: Array<{
    trial: number;
    requestedAtMs: number;
    settledAtMs: number;
    latencyMs: number;
  }> = [];
  for (let trial = 0; trial < 4; trial += 1) {
    if (trial > 0) {
      await page.getByRole("button", { name: "Reset" }).click();
      await waitForSemanticStoryboardStatus(page, "ready");
    }
    await setSemanticStoryboardPrompt(page, STORYBOARD_PROMPTS.pathsFirst);
    await startSemanticStoryboard(page);
    const interruption = await interruptSemanticStoryboardAtSurface(page, {
      surface: "partial_record",
    });
    observations.push({
      trial: trial + 1,
      requestedAtMs: interruption.requestedAtMs,
      settledAtMs: interruption.settledAtMs,
      latencyMs: interruption.latencyMs,
    });
    await expect(semanticStoryboardRoot(page)).toHaveAttribute(
      "data-scene-revision",
      "1",
    );
    await expect(semanticStoryboardRoot(page)).toHaveAttribute(
      "data-semantic-revision",
      "1",
    );
    await expect(semanticStoryboardStage(page)).toHaveAttribute(
      "data-settled-beat-count",
      "0",
    );
    await expectSemanticStoryboardSvgMatchesScene(
      page,
      fixture.anchor.resultScene,
    );
    const probe = await page.evaluate((probeKey) => {
      return (window as typeof window & Record<string, unknown>)[probeKey] as {
        requestCount: number;
        partialChunkCount: number;
        abortCount: number;
        activePartialOpen: boolean;
        calls: Array<{
          routingMode: string;
          authorization: string | null;
        }>;
      };
    }, SEMANTIC_STORYBOARD_PARTIAL_STREAM_PROBE_KEY);
    expect(probe).toMatchObject({
      requestCount: (trial + 1) * 2,
      partialChunkCount: trial + 1,
      abortCount: trial + 1,
      activePartialOpen: false,
    });
    expect(probe.calls.slice(-2)).toEqual([
      { routingMode: "reflex", authorization: `Bearer ${TEST_ACCESS_TOKEN}` },
      {
        routingMode: "director",
        authorization: `Bearer ${TEST_ACCESS_TOKEN}`,
      },
    ]);
    await expectSemanticStoryboardFrontierQuiet(page);
  }
  const latencies = observations.map(({ latencyMs }) => latencyMs);
  const p95Ms = Math.max(...latencies);
  const maxMs = Math.max(...latencies);
  const finalProbe = await page.evaluate((probeKey) => {
    return (window as typeof window & Record<string, unknown>)[probeKey] as {
      requestCount: number;
      partialChunkCount: number;
      abortCount: number;
      activePartialOpen: boolean;
    };
  }, SEMANTIC_STORYBOARD_PARTIAL_STREAM_PROBE_KEY);
  expect(p95Ms).toBeLessThan(150);
  expect(finalProbe.abortCount).toBe(observations.length);
  await testInfo.attach(
    "semantic-storyboard-partial-record-interruption-observation",
    {
      body: JSON.stringify(
        {
          schemaVersion: 1,
          clock: "browser_performance_now",
          thresholdMs: 150,
          results: [
            {
              surface: "partial_record",
              trials: observations.length,
              observations: observations.map((observation) => ({
                ...observation,
                requestedAtMs: Number(observation.requestedAtMs.toFixed(3)),
                settledAtMs: Number(observation.settledAtMs.toFixed(3)),
                latencyMs: Number(observation.latencyMs.toFixed(3)),
              })),
              latenciesMs: latencies.map((value) => Number(value.toFixed(3))),
              p95Ms: Number(p95Ms.toFixed(3)),
              maxMs: Number(maxMs.toFixed(3)),
              abortCount: finalProbe.abortCount,
              requestCount: finalProbe.requestCount,
              partialChunkCount: finalProbe.partialChunkCount,
            },
          ],
          unavailableSurfaces: [],
        },
        null,
        2,
      ),
      contentType: "application/json",
    },
  );
});
