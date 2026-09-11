import { expect, test, type Page, type Request } from "@playwright/test";

const TEST_API_KEY = "test-api-key";
const TEST_ACCESS_TOKEN = "gate-1.7-projectile-browser-auth-token";
const FIREBASE_AUTH_KEY = `firebase:authUser:${TEST_API_KEY}:[DEFAULT]`;
const IDENTITY_LOOKUP_PATH = "/v1/accounts:lookup";

interface ProductRequestObservation {
  readonly authorization: string | undefined;
  readonly contentType: string | undefined;
  readonly method: string;
  readonly body: unknown;
}

interface IdentityLookupObservation {
  readonly apiKey: string | null;
  readonly method: string;
  readonly body: unknown;
}

function firebaseUser(expirationTime: number): object {
  return {
    uid: "gate-1.7-projectile-browser-user",
    email: "gate-1.7-projectile@example.test",
    emailVerified: true,
    isAnonymous: false,
    providerData: [],
    stsTokenManager: {
      refreshToken: "gate-1.7-projectile-browser-refresh-token",
      accessToken: TEST_ACCESS_TOKEN,
      expirationTime,
    },
    apiKey: TEST_API_KEY,
    appName: "[DEFAULT]",
  };
}

async function seedFirebaseBrowserPersistence(page: Page): Promise<void> {
  const expirationTime = Date.now() + 24 * 60 * 60 * 1_000;
  await page.addInitScript(
    ({ key, value }) => {
      localStorage.setItem(key, JSON.stringify(value));
    },
    {
      key: FIREBASE_AUTH_KEY,
      value: firebaseUser(expirationTime),
    },
  );
}

function declinedSse(): string {
  return [
    {
      type: "scene_stream_started",
      generation: 1,
      attempt: 1,
      baseRevision: 0,
    },
    {
      type: "projectile_choreography_scene_stream_declined",
      generation: 1,
      attempt: 1,
      finalRevision: 0,
      reasonCode: "unsupported_intent",
      message: "This smoke endpoint leaves the authenticated board unchanged.",
    },
  ]
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
}

function observeProductRequest(request: Request): ProductRequestObservation {
  return {
    authorization: request.headers().authorization,
    contentType: request.headers()["content-type"],
    method: request.method(),
    body: request.postDataJSON(),
  };
}

test("the signed-out product guard redirects before any projectile model fetch", async ({
  page,
}) => {
  const productRequests: ProductRequestObservation[] = [];
  await page
    .context()
    .route("**/api/live-scenes/choreography/stream", async (route) => {
      productRequests.push(observeProductRequest(route.request()));
      await route.abort("blockedbyclient");
    });

  await page.goto("/canvas/projectile");
  await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 });
  expect(productRequests).toEqual([]);
});

test("the authenticated product sends the exact fresh Firebase bearer and projectile Reflex body", async ({
  page,
}) => {
  const identityLookups: IdentityLookupObservation[] = [];
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
              localId: "gate-1.7-projectile-browser-user",
              email: "gate-1.7-projectile@example.test",
              emailVerified: true,
              providerUserInfo: [],
            },
          ],
        }),
      });
    });

  const observations: ProductRequestObservation[] = [];
  await page
    .context()
    .route("**/api/live-scenes/choreography/stream", async (route) => {
      observations.push(observeProductRequest(route.request()));
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream; charset=utf-8",
        body: declinedSse(),
      });
    });

  await seedFirebaseBrowserPersistence(page);
  await page.goto("/canvas/projectile");
  await expect(page).toHaveURL(/\/canvas\/projectile$/);
  await expect(
    page.getByRole("heading", { name: "Projectile motion studio" }),
  ).toBeVisible();
  const stage = page.getByTestId("projectile-choreography-stage");
  const canvas = stage.getByTestId("live-choreography-board").locator("svg");
  const initialViewBox = await canvas.getAttribute("viewBox");
  await expect(stage.locator("[data-element-id]")).toHaveCount(0);

  await page.getByRole("button", { name: "Draw this launch" }).click();
  await expect(stage).toHaveAttribute("data-phase", "declined");
  await expect(
    page.getByRole("status").filter({
      hasText: "This smoke endpoint leaves the authenticated board unchanged.",
    }),
  ).toBeVisible();
  await expect(page.getByText("scene 0", { exact: false })).toBeVisible();
  await expect(stage.locator("[data-element-id]")).toHaveCount(0);
  expect(await canvas.getAttribute("viewBox")).toBe(initialViewBox);

  expect(observations).toEqual([
    {
      method: "POST",
      authorization: `Bearer ${TEST_ACCESS_TOKEN}`,
      contentType: "application/json",
      body: {
        protocol: "projectile_choreography_v1",
        routingMode: "reflex",
        problemSpec: { v: 1, speedMps: 20, angleDeg: 45 },
        generation: 1,
        baseScene: { revision: 0, nodes: [] },
        baseSemanticScene: { revision: 0, components: [] },
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    },
  ]);
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
