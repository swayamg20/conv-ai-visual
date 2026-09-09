import { expect, test, type Page, type Request } from "@playwright/test";

const TEST_API_KEY = "test-api-key";
const TEST_ACCESS_TOKEN = "gate-1.6-browser-auth-token";
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
    uid: "gate-1.6-browser-user",
    email: "gate-1.6@example.test",
    emailVerified: true,
    isAnonymous: false,
    providerData: [],
    stsTokenManager: {
      refreshToken: "gate-1.6-browser-refresh-token",
      accessToken: TEST_ACCESS_TOKEN,
      expirationTime,
    },
    apiKey: TEST_API_KEY,
    appName: "[DEFAULT]",
  };
}

async function seedFirebaseBrowserPersistence(page: Page): Promise<void> {
  await page.goto("/login");
  const expirationTime = Date.now() + 24 * 60 * 60 * 1_000;
  const user = firebaseUser(expirationTime);
  const seeded = await page.evaluate(
    async ({ key, value }) => {
      localStorage.setItem(key, JSON.stringify(value));
      const database = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open("firebaseLocalStorageDb", 1);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains("firebaseLocalStorage")) {
            request.result.createObjectStore("firebaseLocalStorage", {
              keyPath: "fbase_key",
            });
          }
        };
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
      });
      await new Promise<void>((resolve, reject) => {
        const transaction = database.transaction(
          "firebaseLocalStorage",
          "readwrite",
        );
        transaction.objectStore("firebaseLocalStorage").put({
          fbase_key: key,
          value,
        });
        transaction.onabort = () => reject(transaction.error);
        transaction.onerror = () => reject(transaction.error);
        transaction.oncomplete = () => resolve();
      });
      database.close();
      return localStorage.getItem(key);
    },
    { key: FIREBASE_AUTH_KEY, value: user },
  );
  expect(JSON.parse(seeded ?? "null")).toEqual(user);
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
      type: "parametric_choreography_scene_stream_declined",
      generation: 1,
      attempt: 1,
      finalRevision: 0,
      reasonCode: "problem_unsupported",
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

test("@authenticated-product-smoke sends the exact authenticated V3 Reflex request", async ({
  page,
}) => {
  await seedFirebaseBrowserPersistence(page);

  const identityLookups: IdentityLookupObservation[] = [];
  const unexpectedExternalRequests: string[] = [];
  page.on("request", (request) => {
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
  await page.route("https://identitytoolkit.googleapis.com/**", async (route) => {
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
            localId: "gate-1.6-browser-user",
            email: "gate-1.6@example.test",
            emailVerified: true,
            providerUserInfo: [],
          },
        ],
      }),
    });
  });

  const observations: ProductRequestObservation[] = [];
  await page.route("**/api/live-scenes/choreography/stream", async (route) => {
    observations.push(observeProductRequest(route.request()));
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: declinedSse(),
    });
  });

  await page.goto("/canvas/generate");
  await expect(page).toHaveURL(/\/canvas\/generate$/);
  await expect(page.getByRole("heading", { name: "Live equation studio" })).toBeVisible();
  const stage = page.getByTestId("live-choreography-stage");
  const board = page.getByTestId("live-choreography-board");
  const canvas = board.locator("svg").first();
  const initialViewBox = await canvas.getAttribute("viewBox");
  await expect(stage.locator("[data-element-id]")).toHaveCount(0);

  await page.getByRole("button", { name: "Teach this equation" }).click();
  await expect(stage).toHaveAttribute("data-phase", "declined");
  await expect(
    page.getByRole("status").filter({
      hasText: "This smoke endpoint leaves the authenticated board unchanged.",
    }),
  ).toBeVisible();
  await expect(page.getByText("scene 0", { exact: false })).toBeVisible();
  await expect(stage.locator("[data-element-id]")).toHaveCount(0);
  expect(await canvas.getAttribute("viewBox")).toBe(initialViewBox);
  await expect(page).toHaveURL(/\/canvas\/generate$/);

  expect(observations).toEqual([
    {
      method: "POST",
      authorization: `Bearer ${TEST_ACCESS_TOKEN}`,
      contentType: "application/json",
      body: {
        protocol: "parametric_choreography_v3",
        routingMode: "reflex",
        problemText: "x² + 8x = 20",
        generation: 1,
        baseScene: { revision: 0, nodes: [] },
        baseSemanticScene: { revision: 0, components: [] },
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    },
  ]);
  expect(identityLookups).toEqual([
    {
      apiKey: TEST_API_KEY,
      method: "POST",
      body: { idToken: TEST_ACCESS_TOKEN },
    },
  ]);
  expect(unexpectedExternalRequests).toEqual([]);
});
