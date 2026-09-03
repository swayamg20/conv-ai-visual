import { expect, test, type Page } from "@playwright/test";

interface SceneVisibilityProbe {
  readonly completedPatchFrameAt: Record<string, number>;
}

async function installSceneVisibilityProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const probe: SceneVisibilityProbe = { completedPatchFrameAt: {} };
    Object.defineProperty(window, "__sceneVisibilityProbe", {
      configurable: false,
      value: probe,
    });

    const states = new WeakMap<
      ReadableStreamDefaultController,
      { decoder: TextDecoder; buffer: string }
    >();
    const prototype = ReadableStreamDefaultController.prototype;
    const originalEnqueue = prototype.enqueue;
    prototype.enqueue = function enqueue(chunk?: unknown): void {
      originalEnqueue.call(this, chunk);
      if (!(chunk instanceof Uint8Array)) return;

      const state = states.get(this) ?? { decoder: new TextDecoder(), buffer: "" };
      state.buffer += state.decoder.decode(chunk, { stream: true });
      states.set(this, state);
      let boundary = state.buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = state.buffer.slice(0, boundary);
        state.buffer = state.buffer.slice(boundary + 2);
        if (frame.includes("event: scene_patch")) {
          const data = frame
            .split("\n")
            .find((line) => line.startsWith("data: "))
            ?.slice(6);
          if (data) {
            try {
              const event = JSON.parse(data) as {
                generation?: unknown;
                sequence?: unknown;
              };
              if (
                typeof event.generation === "number" &&
                event.sequence === 1 &&
                probe.completedPatchFrameAt[String(event.generation)] === undefined
              ) {
                // The runtime cannot accept the patch before the complete frame
                // exists, so this is a conservative (earlier) latency origin.
                probe.completedPatchFrameAt[String(event.generation)] = performance.now();
              }
            } catch {
              // Non-JSON stream frames are intentionally ignored by this test probe.
            }
          }
        }
        boundary = state.buffer.indexOf("\n\n");
      }
    };
  });
}

async function completeFrameToFirstVisibleMs(
  page: Page,
  generation: number
): Promise<number> {
  await page.waitForFunction((expectedGeneration) => {
    const probe = (
      window as typeof window & { __sceneVisibilityProbe?: SceneVisibilityProbe }
    ).__sceneVisibilityProbe;
    const startedAt = probe?.completedPatchFrameAt[String(expectedGeneration)];
    const title = document.getElementById(`titleG${expectedGeneration}`);
    if (startedAt === undefined || !title) return false;
    const bounds = title.getBoundingClientRect();
    const opacity = Number.parseFloat(getComputedStyle(title).opacity || "0");
    return bounds.width > 0 && bounds.height > 0 && opacity > 0;
  }, generation);

  return page.evaluate((expectedGeneration) => {
    const probe = (
      window as typeof window & { __sceneVisibilityProbe: SceneVisibilityProbe }
    ).__sceneVisibilityProbe;
    return performance.now() - probe.completedPatchFrameAt[String(expectedGeneration)];
  }, generation);
}

function percentile(samples: readonly number[], percentileValue: number): number {
  const sorted = [...samples].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * percentileValue) - 1)];
}

async function openLab(page: Page): Promise<void> {
  await page.goto("/labs/live-scene");
  await expect(page.getByRole("heading", { name: "Model-authored board" })).toBeVisible();
  await expect(page.getByText("Gate 1", { exact: true }).locator("..")).toBeVisible();
}

async function chooseScenario(page: Page, label: string): Promise<void> {
  await page
    .getByTestId("fixture-mode-picker")
    .getByText(label, { exact: true })
    .click();
  await expect(page.getByText(`Fixture · ${label}`, { exact: true })).toBeVisible();
}

async function chooseSource(page: Page, label: "Fixture" | "Azure model"): Promise<void> {
  await page
    .getByTestId("scene-source-picker")
    .getByText(label, { exact: true })
    .click();
}

test("draws accepted model patches before the stream finishes, then replays", async ({ page }) => {
  await openLab(page);
  await page.getByRole("button", { name: "Generate live" }).click();

  await expect(page.getByText("1 accepted", { exact: true })).toBeVisible();
  await expect(page.getByText("fixtureG1P1", { exact: true })).toBeVisible();
  await expect(page.getByText("fixtureG1P4", { exact: true })).not.toBeVisible();

  await expect(page.getByText("Explanation complete", { exact: true })).toBeVisible();
  await expect(page.getByText("4 accepted", { exact: true })).toBeVisible();
  await expect(page.getByText("revision 4", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Replay accepted" }).click();
  await expect(page.getByText("Replaying accepted work", { exact: true })).toBeVisible();
  await expect(page.getByText("Explanation complete", { exact: true })).toBeVisible();
  await expect(page.getByText("4 accepted", { exact: true })).toBeVisible();
});

test("sends the edited prompt to the auth-free Azure lab stream", async ({ page }) => {
  const prompt = "Show how a red-black tree rebalances after inserting 7";
  let postedBody: unknown;
  let authorization: string | undefined;
  let requestPath: string | undefined;
  await page.route("**/api/live-scenes/lab/stream", async (route) => {
    postedBody = route.request().postDataJSON();
    authorization = route.request().headers().authorization;
    requestPath = new URL(route.request().url()).pathname;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: [
        {
          type: "scene_stream_started",
          generation: 1,
          attempt: 1,
          baseRevision: 0,
        },
        {
          type: "scene_stream_failed",
          generation: 1,
          attempt: 1,
          code: "provider_error",
          message: "The test intercepted the provider stream.",
          lastAcceptedRevision: 0,
          retryable: true,
        },
      ]
        .map((event) => `data: ${JSON.stringify(event)}\n\n`)
        .join(""),
    });
  });

  await openLab(page);
  await chooseSource(page, "Azure model");
  await expect(page.getByRole("radio", { name: "Azure model" })).toBeChecked();
  await expect(page.getByText("Azure · live backend", { exact: true })).toBeVisible();
  await expect(page.getByTestId("fixture-mode-picker")).not.toBeVisible();
  await page.getByLabel("What should the board teach?").fill(prompt);
  await page.getByRole("button", { name: "Generate live" }).click();
  await expect(page.getByText("Stream stopped", { exact: true })).toBeVisible();

  expect(requestPath).toBe("/api/live-scenes/lab/stream");
  expect(authorization).toBeUndefined();
  expect(postedBody).toMatchObject({ prompt, generation: 1 });
});

test("shows the one-repair lifecycle and completes from attempt two", async ({ page }) => {
  await openLab(page);
  await chooseScenario(page, "Repair");
  await page.getByRole("button", { name: "Generate live" }).click();

  await expect(page.getByText("Explanation complete", { exact: true })).toBeVisible();
  await expect(page.getByText("4 accepted", { exact: true })).toBeVisible();
  await expect(page.getByText("generation 1 · attempt 2", { exact: true })).toBeVisible();
  await page.getByText("Stream diagnostics", { exact: true }).click();
  await expect(
    page.getByText("attempt", { exact: true }).locator("..").locator("dd")
  ).toHaveText("2");
});

test("preserves an unchanged board after both model drafts fail", async ({ page }) => {
  await openLab(page);
  await chooseScenario(page, "Failure");
  await page.getByRole("button", { name: "Generate live" }).click();

  await expect(page.getByText("Stream stopped", { exact: true })).toBeVisible();
  await expect(page.locator("p[role='alert']")).toContainText(
    "Couldn’t update the board. Your last accepted scene is safe."
  );
  await expect(page.getByText("revision 0", { exact: true })).toBeVisible();
  await expect(page.getByText("0 accepted", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeEnabled();
});

test("retains visible ink and rejects late output after interruption", async ({ page }) => {
  await openLab(page);
  await chooseScenario(page, "Late output");
  await page.getByRole("button", { name: "Generate live" }).click();

  await expect(page.locator("#titleG1")).toBeVisible();
  await page.getByRole("button", { name: "Interrupt" }).click();
  await expect(page.getByText("Interrupted safely", { exact: true })).toBeVisible();
  const acceptedAfterInterrupt = await page
    .getByText(/^[0-9]+ accepted$/)
    .textContent();

  await page.waitForTimeout(3_000);
  await expect(page.getByText("Interrupted safely", { exact: true })).toBeVisible();
  await expect(page.getByText(acceptedAfterInterrupt ?? "1 accepted", { exact: true })).toBeVisible();
  await expect(page.getByText("fixtureG1P4", { exact: true })).not.toBeVisible();
});

test("keeps complete-patch-frame to first-visible latency under 100ms p95", async ({
  page,
}) => {
  await installSceneVisibilityProbe(page);
  await openLab(page);
  const samples: number[] = [];

  for (let generation = 1; generation <= 20; generation += 1) {
    await page.getByRole("button", { name: "Generate live" }).click();
    samples.push(await completeFrameToFirstVisibleMs(page, generation));
    await page.getByRole("button", { name: "Interrupt" }).click();
    await expect(page.getByText("Interrupted safely", { exact: true })).toBeVisible();
  }

  const p95Ms = percentile(samples, 0.95);
  console.log(
    `Gate 1 complete-patch-frame to first-visible p95: ${p95Ms.toFixed(3)}ms`
  );
  expect(p95Ms).toBeLessThan(100);
});

test("keeps the prompt, controls, and board reachable at 320px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await openLab(page);

  await expect(page.getByLabel("What should the board teach?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate live" })).toBeVisible();
  await page.getByRole("button", { name: "Generate live" }).click();
  await expect(page.getByRole("button", { name: "Interrupt" })).toBeEnabled();
  await page.getByRole("button", { name: "Interrupt" }).click();

  const board = page.getByRole("region", { name: "Live visual board" });
  await board.scrollIntoViewIfNeeded();
  await expect(board).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});

test("keeps the prompt, controls, and board reachable at 375x812", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openLab(page);

  await expect(page.getByLabel("What should the board teach?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate live" })).toBeVisible();
  await page.getByRole("button", { name: "Generate live" }).click();
  await expect(page.getByRole("button", { name: "Interrupt" })).toBeEnabled();
  await page.getByRole("button", { name: "Interrupt" }).click();

  const board = page.getByRole("region", { name: "Live visual board" });
  await board.scrollIntoViewIfNeeded();
  await expect(board).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});
