import { expect, test, type Locator, type Page } from "@playwright/test";

import semanticTranscript from "../src/features/live-scene/fixtures/pythagorean-area-identity.v1.json";

interface SceneVisibilityProbe {
  readonly completedPatchFrameAt: Record<string, number>;
}

interface SemanticLateTrace {
  emitted: number;
  requests: number;
}

interface SemanticLateWindow extends Window {
  readonly __semanticLateTrace?: SemanticLateTrace;
  readonly __releaseLateSemanticFrames?: () => void;
}

const IDENTITY_SEMANTIC_NODE_IDS = [
  "areas__triangle",
  "areas__square_a",
  "areas__label_a2",
  "areas__square_b",
  "areas__label_b2",
  "areas__square_c",
  "areas__label_c2",
  "areas__identity",
] as const;

const PROOF_SEMANTIC_NODE_IDS = [
  "areas__altitude",
  "areas__partition",
  "areas__region_a",
  "areas__region_a_label",
  "areas__region_b",
  "areas__region_b_label",
  "areas__projection_identity",
  "areas__proof_conclusion",
] as const;

const SEMANTIC_NODE_IDS = [
  ...IDENTITY_SEMANTIC_NODE_IDS,
  ...PROOF_SEMANTIC_NODE_IDS,
] as const;

function semanticSse(events: readonly object[]): string {
  return events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
}

function semanticStarted(generation: number, baseRevision: number): object {
  return {
    type: "scene_stream_started",
    generation,
    attempt: 1,
    baseRevision,
  };
}

function semanticFixtureAtom(
  atomIndex: number,
  generation: number,
  sequence: number
): object {
  return {
    ...semanticTranscript.events[atomIndex],
    generation,
    attempt: 1,
    sequence,
  };
}

async function installLateSemanticFetch(
  page: Page,
  events: readonly object[]
): Promise<void> {
  await page.addInitScript((streamEvents) => {
    const originalFetch = window.fetch;
    const trace: SemanticLateTrace = { emitted: 0, requests: 0 };
    let releaseLateFrames = (): void => {};
    const lateFramesReleased = new Promise<void>((resolve) => {
      releaseLateFrames = resolve;
    });
    Object.defineProperty(window, "__semanticLateTrace", {
      configurable: false,
      value: trace,
    });
    Object.defineProperty(window, "__releaseLateSemanticFrames", {
      configurable: false,
      value: releaseLateFrames,
    });
    window.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : String(input);
      if (!url.endsWith("/api/live-scenes/lab/semantic/stream")) {
        return originalFetch(input, init);
      }

      trace.requests += 1;
      const encoder = new TextEncoder();
      let cancelled = false;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          void (async () => {
            for (const [index, event] of streamEvents.entries()) {
              if (index === 2) await lateFramesReleased;
              const delayMs = index < 2 ? 20 : 5;
              await new Promise((resolve) => globalThis.setTimeout(resolve, delayMs));
              if (cancelled) return;
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify(event)}\n\n`)
              );
              trace.emitted += 1;
            }
            if (!cancelled) controller.close();
          })();
        },
        cancel() {
          cancelled = true;
        },
      });
      // Deliberately ignore init.signal. This simulates a provider/transport
      // that races cancellation and proves the runtime rejects its stale token.
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    };
  }, events);
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

async function canonicalSvgMarkup(svg: Locator): Promise<string> {
  return svg.evaluate((root) => {
    const serialize = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return JSON.stringify(node.nodeValue ?? "");
      }
      if (!(node instanceof Element)) return "";
      const attributes = Array.from(node.attributes)
        .map(({ name, value }) => [name, value] as const)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([name, value]) => `${name}=${JSON.stringify(value)}`)
        .join(" ");
      const opening = attributes
        ? `<${node.namespaceURI}:${node.localName} ${attributes}>`
        : `<${node.namespaceURI}:${node.localName}>`;
      return `${opening}${Array.from(node.childNodes).map(serialize).join("")}</${node.namespaceURI}:${node.localName}>`;
    };
    return serialize(root);
  });
}

async function openLab(page: Page): Promise<void> {
  await page.goto("/labs/live-scene");
  await expect(page.getByRole("heading", { name: "Verified-act board" })).toBeVisible();
  await expect(page.getByText("Verified", { exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Verified acts" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "Fixture · $0" })).toBeChecked();
  await expect(page.getByText("Verified fixture · $0", { exact: true })).toBeVisible();
}

async function chooseRawBaseline(page: Page): Promise<void> {
  await page
    .getByTestId("authoring-mode-picker")
    .getByText("Raw coordinates", { exact: true })
    .click();
  await expect(page.getByRole("radio", { name: "Raw coordinates" })).toBeChecked();
  await expect(page.getByRole("heading", { name: "Model-authored board" })).toBeVisible();
  await expect(page.getByText("Gate 1", { exact: true }).locator("..")).toBeVisible();
}

async function chooseScenario(page: Page, label: string): Promise<void> {
  await page
    .getByTestId("fixture-mode-picker")
    .getByText(label, { exact: true })
    .click();
  await expect(
    page.getByText(`Raw fixture · ${label} · $0`, { exact: true })
  ).toBeVisible();
}

async function chooseSource(
  page: Page,
  label: "Fixture · $0" | "Azure · paid"
): Promise<void> {
  await page
    .getByTestId("scene-source-picker")
    .getByText(label, { exact: true })
    .click();
}

async function expectSemanticBoard(
  page: Page,
  nodeIds: readonly string[] = SEMANTIC_NODE_IDS
): Promise<void> {
  for (const nodeId of nodeIds) {
    const node = page.locator(`#${nodeId}`);
    if (nodeId === "areas__proof_conclusion") {
      // A horizontal SVG path has a zero-height geometric bounding box even
      // though its stroke is painted. Assert the rendered path itself.
      await expect(node).toBeAttached();
      await expect(node.locator("path")).toHaveAttribute("d", /\S/);
    } else {
      await expect(node).toBeVisible();
    }
  }
  await expect(
    page.locator("[id$='--incoming'], [id$='--outgoing']")
  ).toHaveCount(0);
}

async function expectMobileCanvasControlsBelowBoard(board: Locator): Promise<void> {
  const layout = await board.evaluate((root) => {
    const svg = root.querySelector('svg[viewBox="0 0 800 600"]');
    const reset = root.querySelector<HTMLButtonElement>('button[title="Reset zoom"]');
    if (!svg || !reset) return null;
    const svgBounds = svg.getBoundingClientRect();
    const resetBounds = reset.getBoundingClientRect();
    return { svgBottom: svgBounds.bottom, resetTop: resetBounds.top };
  });
  expect(layout).not.toBeNull();
  expect(layout!.resetTop).toBeGreaterThanOrEqual(layout!.svgBottom);
}

test("stops the zero-network fixture at identity, then appends the proof", async ({
  page,
}) => {
  const liveSceneRequests: string[] = [];
  await page.route("**/api/live-scenes/**", async (route) => {
    liveSceneRequests.push(route.request().url());
    await route.abort("blockedbyclient");
  });

  await openLab(page);
  await expect(page.getByTestId("fixture-mode-picker")).not.toBeVisible();
  await expect(page.getByText(/no sign-in, network request, or Azure spend/i)).toBeVisible();
  await page.getByRole("button", { name: "Begin verified lesson" }).click();

  await expect(page.getByText("1 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__triangle")).toBeVisible();
  await expect(page.locator("#areas__identity")).not.toBeVisible();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();
  await expect(page.getByText("scene 8", { exact: true })).toBeVisible();
  await expect(page.getByText("semantic 8", { exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Presented visual acts" }).locator("li")).toHaveCount(8);
  await expect(page.getByLabel("Verified act trust boundary")).toContainText(
    "does not re-run cryptography or geometry"
  );
  await expectSemanticBoard(page, IDENTITY_SEMANTIC_NODE_IDS);

  await page
    .getByLabel("What should the board teach?")
    .fill("I do not understand why the areas are equal; dissect the large square");
  await page.getByRole("button", { name: "Begin verified lesson" }).click();

  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible();
  await expect(page.getByText("scene 16", { exact: true })).toBeVisible();
  await expect(page.getByText("semantic 16", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Presented visual acts" }).locator("li")
  ).toHaveCount(16);
  await expectSemanticBoard(page);
  await expect(page.locator("#areas__projection_identity")).toContainText(
    "AH = a²/c · HB = b²/c"
  );
  await page.screenshot({
    path: "../var/scene-e2e/gate14-adaptive-proof.png",
    fullPage: true,
  });
  expect(liveSceneRequests).toEqual([]);
});

test("interrupts at one presented semantic act, resumes the exact suffix, and replays it", async ({
  page,
}) => {
  const liveSceneRequests: string[] = [];
  await page.route("**/api/live-scenes/**", async (route) => {
    liveSceneRequests.push(route.request().url());
    await route.abort("blockedbyclient");
  });

  await openLab(page);
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.locator("#areas__triangle")).toBeVisible();
  await page.getByRole("button", { name: "Stop drawing" }).click();

  await expect(page.getByText("Stopped at presented frontier", { exact: true })).toBeVisible();
  await expect(page.getByText("1 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__triangle")).toBeVisible();
  await expect(page.locator("#areas__square_a")).not.toBeVisible();
  await expect(
    page.locator("[id$='--incoming'], [id$='--outgoing']")
  ).toHaveCount(0);

  await page.getByText("Stream diagnostics", { exact: true }).click();
  await expect(
    page.getByText("frontier", { exact: true }).locator("..").locator("dd")
  ).toHaveText("areas__atom_triangle");

  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();
  await expectSemanticBoard(page, IDENTITY_SEMANTIC_NODE_IDS);

  await page.getByRole("button", { name: "Replay presented" }).click();
  await expect(page.getByText("Replaying presented acts", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();
  await expectSemanticBoard(page, IDENTITY_SEMANTIC_NODE_IDS);
  await expect(
    page.getByText("frontier", { exact: true }).locator("..").locator("dd")
  ).toHaveText("areas__atom_identity");
  expect(liveSceneRequests).toEqual([]);
});

test("stops mid-proof at the settled partition and resumes only the proof suffix", async ({
  page,
}) => {
  const liveSceneRequests: string[] = [];
  await page.route("**/api/live-scenes/**", async (route) => {
    liveSceneRequests.push(route.request().url());
    await route.abort("blockedbyclient");
  });

  await openLab(page);
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();

  await page
    .getByLabel("What should the board teach?")
    .fill("I do not understand why the areas are equal; dissect the large square");
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("9 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__altitude")).toBeVisible();
  await page.getByRole("button", { name: "Stop drawing" }).click();

  await expect(page.getByText("Stopped at presented frontier", { exact: true })).toBeVisible();
  await expect(page.getByText("10 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__altitude")).toBeVisible();
  await expect(page.locator("#areas__partition")).toBeVisible();
  await expect(page.locator("#areas__region_a")).not.toBeVisible();
  await page.getByText("Stream diagnostics", { exact: true }).click();
  await expect(
    page.getByText("frontier", { exact: true }).locator("..").locator("dd")
  ).toHaveText("areas__atom_partition");

  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible();
  await expectSemanticBoard(page);
  await expect(
    page.getByText("frontier", { exact: true }).locator("..").locator("dd")
  ).toHaveText("areas__atom_proof_conclusion");
  expect(liveSceneRequests).toEqual([]);
});

test("retains the semantic frontier and rejects deliberately late atoms", async ({ page }) => {
  const generation = 1;
  const atoms = semanticTranscript.events.slice(0, 8).map((_event, index) =>
    semanticFixtureAtom(index, generation, index + 1)
  );
  const events = [
    semanticStarted(generation, 0),
    ...atoms,
    {
      type: "scene_stream_completed",
      generation,
      finalRevision: atoms.length,
      patchCount: atoms.length,
      firstPatchMs: 20,
      totalMs: 200,
      repaired: false,
    },
  ];
  await installLateSemanticFetch(page, events);

  await openLab(page);
  await chooseSource(page, "Azure · paid");
  await page.getByRole("button", { name: "Run paid Azure lesson" }).click();
  await expect(page.locator("#areas__triangle")).toBeVisible();
  expect(await page.locator("#areas__square_a").count()).toBe(0);

  await page.getByRole("button", { name: "Stop drawing" }).click();
  await expect(page.getByText("Stopped at presented frontier", { exact: true })).toBeVisible();
  const canvas = page
    .getByRole("region", { name: "Live visual board" })
    .locator('svg[viewBox="0 0 800 600"]');
  const interruptedMarkup = await canvas.evaluate((svg) => svg.innerHTML);

  await page.evaluate(() => {
    (window as SemanticLateWindow).__releaseLateSemanticFrames?.();
  });

  await page.waitForFunction((eventCount) => {
    const trace = (window as SemanticLateWindow).__semanticLateTrace;
    return trace?.emitted === eventCount;
  }, events.length);

  await expect(page.getByText("Stopped at presented frontier", { exact: true })).toBeVisible();
  await expect(page.getByText("1 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__square_a")).not.toBeVisible();
  await expect(page.locator("#areas__identity")).not.toBeVisible();
  expect(await canvas.evaluate((svg) => svg.innerHTML)).toBe(interruptedMarkup);
  expect(
    await page.evaluate(() => {
      return (window as SemanticLateWindow).__semanticLateTrace?.requests;
    })
  ).toBe(1);
});

test("posts the identity frontier for proof and replays with no semantic request", async ({
  page,
}) => {
  const identityPrompt = "Teach the Pythagorean area identity through verified areas";
  const proofPrompt =
    "I do not understand why the areas are equal; dissect the large square";
  const postedBodies: Array<Record<string, unknown>> = [];
  let authorization: string | undefined;
  let requestPath: string | undefined;
  let rawRouteCalls = 0;
  await page.route("**/api/live-scenes/lab/stream", async (route) => {
    rawRouteCalls += 1;
    await route.abort("blockedbyclient");
  });
  await page.route("**/api/live-scenes/lab/semantic/stream", async (route) => {
    const postedBody = route.request().postDataJSON() as Record<string, unknown>;
    postedBodies.push(postedBody);
    authorization = route.request().headers().authorization;
    requestPath = new URL(route.request().url()).pathname;
    const generation = postedBody.generation as number;
    const baseRevision = (postedBody.baseScene as { revision: number }).revision;
    const turnEndRevision = baseRevision < 8 ? 8 : 16;
    const atoms = semanticTranscript.events
      .slice(baseRevision, turnEndRevision)
      .map((_event, index) =>
        semanticFixtureAtom(baseRevision + index, generation, index + 1)
      );
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: semanticSse([
        semanticStarted(generation, baseRevision),
        ...atoms,
        {
          type: "scene_stream_completed",
          generation,
          finalRevision: turnEndRevision,
          patchCount: atoms.length,
          firstPatchMs: 20,
          totalMs: 60,
          repaired: false,
        },
      ]),
    });
  });

  await openLab(page);
  await chooseSource(page, "Azure · paid");
  await expect(page.getByRole("radio", { name: "Azure · paid" })).toBeChecked();
  await expect(page.getByText("Verified acts · Azure paid", { exact: true })).toBeVisible();
  await expect(page.getByText(/consumes paid Azure quota/i)).toBeVisible();
  await page.getByLabel("What should the board teach?").fill(identityPrompt);
  await page.getByRole("button", { name: "Run paid Azure lesson" }).click();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();

  await page.getByLabel("What should the board teach?").fill(proofPrompt);
  await page.getByRole("button", { name: "Run paid Azure lesson" }).click();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible();
  await expectSemanticBoard(page);
  await page.getByText("Stream diagnostics", { exact: true }).click();

  const canvas = page
    .getByRole("region", { name: "Live visual board" })
    .locator('svg[viewBox="0 0 800 600"]');
  const markupBeforeReplay = await canonicalSvgMarkup(canvas);
  const frontierValue = page
    .getByText("frontier", { exact: true })
    .locator("..")
    .locator("dd");
  const frontierBeforeReplay = await frontierValue.textContent();
  const requestCountBeforeReplay = postedBodies.length;
  expect(requestCountBeforeReplay).toBe(2);

  await page.getByRole("button", { name: "Replay presented" }).click();
  await expect(page.getByText("Replaying presented acts", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible({
    timeout: 30_000,
  });

  expect(requestPath).toBe("/api/live-scenes/lab/semantic/stream");
  expect(authorization).toBeUndefined();
  expect(rawRouteCalls).toBe(0);
  expect(postedBodies).toHaveLength(requestCountBeforeReplay);
  expect(postedBodies).toHaveLength(2);
  expect(postedBodies[0]).toMatchObject({
    prompt: identityPrompt,
    generation: 1,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  });
  expect(postedBodies[1]).toMatchObject({
    prompt: proofPrompt,
    generation: 2,
    baseScene: {
      revision: 8,
      nodes: IDENTITY_SEMANTIC_NODE_IDS.map((id) => ({ id })),
    },
    baseSemanticScene: {
      revision: 8,
      certificateHeadSha256:
        semanticTranscript.events[7].semantic.certificate.certificateSha256,
      components: [
        {
          id: "areas",
          revealedRoles: IDENTITY_SEMANTIC_NODE_IDS.map((id) =>
            id.replace("areas__", "")
          ),
        },
      ],
    },
  });
  expect(await canonicalSvgMarkup(canvas)).toBe(markupBeforeReplay);
  await expect(frontierValue).toHaveText(frontierBeforeReplay ?? "");
  expect(frontierBeforeReplay).toBe("areas__atom_proof_conclusion");
});

test("keeps a declined semantic frontier unchanged and accepts the next request", async ({
  page,
}) => {
  const postedBodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/live-scenes/lab/semantic/stream", async (route) => {
    const request = route.request().postDataJSON() as Record<string, unknown>;
    postedBodies.push(request);
    const generation = request.generation as number;
    const baseScene = request.baseScene as { revision: number };
    const events =
      postedBodies.length === 1
        ? [
            semanticStarted(generation, baseScene.revision),
            {
              type: "semantic_scene_stream_declined",
              generation,
              attempt: 1,
              finalRevision: baseScene.revision,
              reasonCode: "unsupported_intent",
              message: "This request does not have a supported visual yet.",
            },
          ]
        : [
            semanticStarted(generation, 0),
            semanticFixtureAtom(0, generation, 1),
            {
              type: "scene_stream_completed",
              generation,
              finalRevision: 1,
              patchCount: 1,
              firstPatchMs: 20,
              totalMs: 40,
              repaired: false,
            },
          ];
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: semanticSse(events),
    });
  });

  await openLab(page);
  await chooseSource(page, "Azure · paid");
  const board = page.getByRole("region", { name: "Live visual board" });
  const canvas = board.locator('svg[viewBox="0 0 800 600"]');
  const before = await canvas.evaluate((svg) => svg.innerHTML);

  await page.getByLabel("What should the board teach?").fill("Draw a weather map");
  await page.getByRole("button", { name: "Run paid Azure lesson" }).click();

  await expect(page.getByText("No visual change", { exact: true })).toBeVisible();
  await expect(page.getByText("0 presented", { exact: true })).toBeVisible();
  await expect(page.getByText("scene 0", { exact: true })).toBeVisible();
  await expect(page.getByText("semantic 0", { exact: true })).toBeVisible();
  expect(await canvas.evaluate((svg) => svg.innerHTML)).toBe(before);
  await expect(page.getByRole("button", { name: "Run paid Azure lesson" })).toBeEnabled();

  await page
    .getByLabel("What should the board teach?")
    .fill("Start with the right triangle");
  await page.getByRole("button", { name: "Run paid Azure lesson" }).click();

  await expect(page.getByText("Verified acts presented", { exact: true })).toBeVisible();
  await expect(page.getByText("1 presented", { exact: true })).toBeVisible();
  await expect(page.locator("#areas__triangle")).toBeVisible();
  expect(postedBodies).toHaveLength(2);
  expect(postedBodies[1]).toMatchObject({
    generation: 2,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  });
});

test("draws accepted model patches before the stream finishes, then replays", async ({ page }) => {
  await openLab(page);
  await chooseRawBaseline(page);
  await page.getByRole("button", { name: "Run raw fixture" }).click();

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

test("sends the edited prompt to the intercepted raw Azure baseline", async ({ page }) => {
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
  await chooseRawBaseline(page);
  await chooseSource(page, "Azure · paid");
  await expect(page.getByRole("radio", { name: "Azure · paid" })).toBeChecked();
  await expect(page.getByText("Raw baseline · Azure paid", { exact: true })).toBeVisible();
  await expect(page.getByTestId("fixture-mode-picker")).not.toBeVisible();
  await page.getByLabel("What should the board teach?").fill(prompt);
  await page.getByRole("button", { name: "Run paid Azure baseline" }).click();
  await expect(page.getByText("Stream stopped", { exact: true })).toBeVisible();

  expect(requestPath).toBe("/api/live-scenes/lab/stream");
  expect(authorization).toBeUndefined();
  expect(postedBody).toMatchObject({ prompt, generation: 1 });
});

test("settles a draw animation that uses a theme-token fill", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const events = [
    {
      type: "scene_stream_started",
      generation: 1,
      attempt: 1,
      baseRevision: 0,
    },
    {
      type: "scene_patch",
      generation: 1,
      attempt: 1,
      sequence: 1,
      baseRevision: 0,
      resultRevision: 1,
      patch: {
        v: 1,
        patchId: "filled-path",
        narration: "Draw a filled triangle.",
        operations: [
          {
            op: "put",
            node: {
              id: "filled-triangle",
              kind: "path",
              presentation: { enter: "draw", exit: "fade" },
              points: [
                [180, 420],
                [400, 120],
                [620, 420],
              ],
              closed: true,
              style: {
                stroke: "hsl(var(--chalk))",
                strokeWidth: 4,
                fill: "hsl(var(--amber))",
                opacity: 1,
                roughness: 1,
              },
            },
          },
        ],
      },
    },
    {
      type: "scene_stream_completed",
      generation: 1,
      finalRevision: 1,
      patchCount: 1,
      firstPatchMs: 1,
      totalMs: 2,
      repaired: false,
    },
  ];
  await page.route("**/api/live-scenes/lab/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    });
  });

  await openLab(page);
  await chooseRawBaseline(page);
  await chooseSource(page, "Azure · paid");
  await page.getByLabel("What should the board teach?").fill("Draw a filled triangle");
  await page.getByRole("button", { name: "Run paid Azure baseline" }).click();

  await expect(page.getByText("Explanation complete", { exact: true })).toBeVisible();
  await expect(page.getByText("revision 1", { exact: true })).toBeVisible();
  await expect(page.getByText("1 accepted", { exact: true })).toBeVisible();
  await expect(page.locator("#filled-triangle")).toBeVisible();
  await expect(page.locator("#filled-triangle path")).toHaveAttribute(
    "fill",
    "hsl(var(--amber))"
  );
  expect(pageErrors).toEqual([]);
});

test("shows the one-repair lifecycle and completes from attempt two", async ({ page }) => {
  await openLab(page);
  await chooseRawBaseline(page);
  await chooseScenario(page, "Repair");
  await page.getByRole("button", { name: "Run raw fixture" }).click();

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
  await chooseRawBaseline(page);
  await chooseScenario(page, "Failure");
  await page.getByRole("button", { name: "Run raw fixture" }).click();

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
  await chooseRawBaseline(page);
  await chooseScenario(page, "Late output");
  await page.getByRole("button", { name: "Run raw fixture" }).click();

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
  await chooseRawBaseline(page);
  const samples: number[] = [];

  for (let generation = 1; generation <= 20; generation += 1) {
    await page.getByRole("button", { name: "Run raw fixture" }).click();
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
  await expect(page.getByRole("button", { name: "Begin verified lesson" })).toBeVisible();
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByRole("button", { name: "Stop drawing" })).toBeEnabled();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();
  await page
    .getByLabel("What should the board teach?")
    .fill("I do not understand why the areas are equal; dissect the large square");
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible();

  const board = page.getByRole("region", { name: "Live visual board" });
  await board.scrollIntoViewIfNeeded();
  await expect(board).toBeVisible();
  await expectSemanticBoard(page);
  await expectMobileCanvasControlsBelowBoard(board);
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});

test("keeps the prompt, controls, and board reachable at 375x812", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openLab(page);

  await expect(page.getByLabel("What should the board teach?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Begin verified lesson" })).toBeVisible();
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByRole("button", { name: "Stop drawing" })).toBeEnabled();
  await expect(page.getByText("8 presented", { exact: true })).toBeVisible();
  await page
    .getByLabel("What should the board teach?")
    .fill("I do not understand why the areas are equal; dissect the large square");
  await page.getByRole("button", { name: "Begin verified lesson" }).click();
  await expect(page.getByText("16 presented", { exact: true })).toBeVisible();

  const board = page.getByRole("region", { name: "Live visual board" });
  await board.scrollIntoViewIfNeeded();
  await expect(board).toBeVisible();
  await expectSemanticBoard(page);
  await expectMobileCanvasControlsBelowBoard(board);
  await board.screenshot({
    path: "../var/scene-e2e/gate14-adaptive-proof-375.png",
  });
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});
