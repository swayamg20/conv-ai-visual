import { expect, test, type Page } from "@playwright/test";

import primaryFixtureValue from "../src/features/live-scene/fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json";
import {
  PROJECTILE_CLARIFICATION_CASES,
  PROJECTILE_MAIN_CHECKPOINTS,
  attachBoardOnlyScreenshot,
  expectNoHorizontalOverflow,
  expectProjectileTerminal,
  expectProviderFree,
  expectStableElement,
  expectedProjectileBoard,
  fixtureCheckpoints,
  observeProjectileStage,
  observeProviderFreeRequests,
  projectileBoard,
  projectileBridgeState,
  projectileFixture,
  projectileStage,
  rememberStableElement,
  selectProjectileProblem,
  stopAtVisibleCheckpoint,
  stopDuringActiveTrace,
  waitForActiveTrace,
  waitForProjectileBridge,
} from "./projectile-motion-helpers";

const primaryFixture = projectileFixture(primaryFixtureValue);
const primaryProblem = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 45,
} as const);
const retargetProblem = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 60,
} as const);

async function drawLaunch(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Draw this launch" }).click();
}

async function waitForPresentedElement(
  page: Page,
  checkpointId: string,
  elementId: string,
): Promise<void> {
  await page.waitForFunction(
    ({ checkpoint, id }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const element = stage?.querySelector<SVGGraphicsElement>(
        `[data-element-id="${id}"]`,
      );
      if (stage?.dataset.visibleCheckpointId !== checkpoint || !element) {
        return false;
      }
      const style = getComputedStyle(element);
      return (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity) > 0.02
      );
    },
    { checkpoint: checkpointId, id: elementId },
    { polling: "raf" },
  );
}

async function stopDuringFocus(page: Page): Promise<string> {
  const result = await page.waitForFunction(
    () => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const target = stage?.querySelector<SVGGraphicsElement>(
        '[data-element-id="projectile__velocity_resultant"]',
      );
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const match = target?.style.filter.match(/brightness\(([^)]+)\)/);
      const brightness = match ? Number(match[1]) : 1;
      if (
        stage?.dataset.visibleCheckpointId !== "setup" ||
        !Number.isFinite(brightness) ||
        brightness <= 1.005 ||
        !stop
      ) {
        return false;
      }
      const filter = target?.style.filter ?? "";
      stop.click();
      return filter;
    },
    undefined,
    { polling: "raf" },
  );
  const filter = await result.jsonValue();
  if (filter === false) throw new Error("The setup focus cue never animated");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return filter;
}

async function stopDuringHold(page: Page): Promise<{
  readonly dashArray: number;
  readonly dashOffset: number;
}> {
  const result = await page.waitForFunction(
    () => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const target = stage?.querySelector<SVGGraphicsElement>(
        '[data-element-id="projectile__velocity_resultant"]',
      );
      const path = target?.querySelector("path");
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const dashArray = Number(path?.getAttribute("stroke-dasharray"));
      const dashOffset = Number(path?.getAttribute("stroke-dashoffset"));
      if (
        stage?.dataset.visibleCheckpointId !== "setup" ||
        stage.dataset.settledMainCount !== "0" ||
        !Number.isFinite(dashArray) ||
        !Number.isFinite(dashOffset) ||
        dashArray <= 0 ||
        Math.abs(dashOffset) > dashArray * 0.005 ||
        !stop
      ) {
        return false;
      }
      stop.click();
      return { dashArray, dashOffset };
    },
    undefined,
    { polling: "raf", timeout: 15_000 },
  );
  const value = await result.jsonValue();
  if (value === false) throw new Error("The setup hold was not observable");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return value;
}

async function stopDuringTransformAndTrace(page: Page): Promise<{
  readonly beforePath: string;
  readonly interruptedPath: string;
  readonly traceDashArray: number;
  readonly traceDashOffset: number;
}> {
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  const beforePath =
    (await projectileBoard(page)
      .locator('[data-element-id="projectile__vertical_state"] path')
      .getAttribute("d")) ?? "";
  expect(beforePath).not.toBe("");

  const result = await page.waitForFunction(
    ({ initialPath }) => {
      const stage = document.querySelector<HTMLElement>(
        '[data-testid="projectile-choreography-stage"]',
      );
      const transformed = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__vertical_state"]',
      );
      const trace = stage?.querySelector<SVGGElement>(
        '[data-element-id="projectile__trajectory_descent"]',
      );
      const transformedPath = transformed?.querySelector("path");
      const tracePath = trace?.querySelector("path");
      const stop = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          candidate.textContent?.trim() === "Stop at this moment" &&
          !candidate.disabled,
      );
      const currentPath = transformedPath?.getAttribute("d") ?? "";
      const traceDashArray = Number(
        tracePath?.getAttribute("stroke-dasharray"),
      );
      const traceDashOffset = Number(
        tracePath?.getAttribute("stroke-dashoffset"),
      );
      if (
        stage?.dataset.visibleCheckpointId !== "trace_descent" ||
        !currentPath ||
        currentPath === initialPath ||
        !Number.isFinite(traceDashArray) ||
        !Number.isFinite(traceDashOffset) ||
        traceDashArray <= 0 ||
        traceDashOffset <= 0 ||
        traceDashOffset >= traceDashArray ||
        !stop
      ) {
        return false;
      }
      stop.click();
      return {
        beforePath: initialPath,
        interruptedPath: currentPath,
        traceDashArray,
        traceDashOffset,
      };
    },
    { initialPath: beforePath },
    { polling: "raf", timeout: 20_000 },
  );
  const value = await result.jsonValue();
  if (value === false) {
    throw new Error("The descent transform and trace never overlapped");
  }
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "interrupted",
  );
  return value;
}

test("the certified main flow draws a curved trace with one stable projectile marker and no provider", async ({
  page,
}, testInfo) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?proof=keyframes");
  await waitForProjectileBridge(page);

  await expect(
    page.getByRole("heading", { name: "Projectile motion studio" }),
  ).toBeVisible();
  for (const speed of [20, 25, 30]) {
    await expect(
      page.getByRole("button", {
        name: `Set launch speed to ${speed} metres per second`,
      }),
    ).toHaveAttribute("aria-pressed", String(speed === 20));
  }
  for (const angle of [30, 45, 60]) {
    await expect(
      page.getByRole("button", {
        name: `Set launch angle to ${angle} degrees`,
      }),
    ).toHaveAttribute("aria-pressed", String(angle === 45));
  }

  await drawLaunch(page);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "1",
  );
  await rememberStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );

  const trace = await waitForActiveTrace(page, "trace_ascent");
  expect(trace.dashOffset).toBeGreaterThan(0);
  expect(trace.dashOffset).toBeLessThan(trace.dashArray);
  expect(trace.markerTransform).toMatch(/^translate\(/);
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );
  const curvePath = projectileBoard(page).locator(
    '[data-element-id="projectile__trajectory_ascent"] path',
  );
  await expect(curvePath).toHaveCount(1);
  expect(await curvePath.getAttribute("d")).toMatch(/[CQ]/);

  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  await expectProjectileTerminal(page, expected);
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_identity__",
  );
  await attachBoardOnlyScreenshot(
    page,
    testInfo,
    "main-terminal-board-mute-first",
  );

  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 1,
    calls: [
      {
        ordinal: 1,
        generation: 1,
        routingMode: "reflex",
        problemSpec: primaryProblem,
        baseRevision: 0,
        semanticRevision: 0,
        certificateHeadSha256: null,
        checkpointId: null,
        clarifiedTopics: [],
        activeClarification: null,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  });
  expectProviderFree(requests);
});

test.describe("real certified clarification lanes", () => {
  for (const clarification of PROJECTILE_CLARIFICATION_CASES) {
    test(`${clarification.topic} stops at its prerequisite and adds only its one-shot detail`, async ({
      page,
    }) => {
      const requests = observeProviderFreeRequests(page);
      await page.goto("/e2e/projectile-motion?flow=main&proof=keyframes");
      await waitForProjectileBridge(page);
      await drawLaunch(page);

      if (clarification.prerequisite === "trace_descent") {
        await stopDuringActiveTrace(page, "trace_descent");
      } else {
        const evidenceElement =
          clarification.prerequisite === "decompose_velocity"
            ? "projectile__equation_x"
            : "projectile__apex_marker";
        await waitForPresentedElement(
          page,
          clarification.prerequisite,
          evidenceElement,
        );
        await stopAtVisibleCheckpoint(page, clarification.prerequisite);
      }

      await expect(projectileStage(page)).toHaveAttribute(
        "data-settled-main-count",
        String(clarification.settledMainCount),
      );
      await page.getByRole("button", { name: clarification.button }).click();

      const expected = expectedProjectileBoard(
        primaryFixture,
        clarification.lane,
        "cinematic",
      );
      await expectProjectileTerminal(page, expected, {
        settledMainCount: clarification.settledMainCount,
      });
      await expect(page.getByText(clarification.settledLabel)).toBeVisible();
      await expect(
        page.getByRole("button", { name: clarification.button }),
      ).toHaveCount(0);

      const frontier = fixtureCheckpoints(primaryFixture, "main")[
        clarification.settledMainCount - 1
      ];
      if (!frontier) throw new Error("Missing clarification prerequisite");
      expect(await projectileBridgeState(page)).toEqual({
        runnerCallCount: 2,
        calls: [
          expect.objectContaining({
            ordinal: 1,
            generation: 1,
            baseRevision: 0,
            requestedRoute: { intent: "advance", targetStage: "solve" },
          }),
          {
            ordinal: 2,
            generation: 2,
            routingMode: "reflex",
            problemSpec: primaryProblem,
            baseRevision: clarification.settledMainCount,
            semanticRevision: clarification.settledMainCount,
            certificateHeadSha256:
              frontier.semantic.semanticResultCertificateSha256,
            checkpointId: clarification.prerequisite,
            clarifiedTopics: [],
            activeClarification: null,
            requestedRoute: {
              intent: "clarify",
              topic: clarification.topic,
            },
          },
        ],
      });
      expectProviderFree(requests);
    });
  }
});

test("Stop settles one exact trace frontier and Continue consumes only the real remaining suffix", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?proof=keyframes");
  await waitForProjectileBridge(page);
  await drawLaunch(page);

  await stopDuringActiveTrace(page, "trace_ascent");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "3",
  );
  const traceFrontier = fixtureCheckpoints(primaryFixture, "main")[2];
  if (!traceFrontier) throw new Error("Missing trace-ascent fixture frontier");
  await page.getByRole("button", { name: "Continue the flight" }).click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "main", "cinematic"),
  );

  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 2,
    calls: [
      expect.objectContaining({
        ordinal: 1,
        generation: 1,
        baseRevision: 0,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      {
        ordinal: 2,
        generation: 2,
        routingMode: "reflex",
        problemSpec: primaryProblem,
        baseRevision: 3,
        semanticRevision: 3,
        certificateHeadSha256:
          traceFrontier.semantic.semanticResultCertificateSha256,
        checkpointId: "trace_ascent",
        clarifiedTopics: [],
        activeClarification: null,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      },
    ],
  });
  expectProviderFree(requests);
});

test("apex clarification continues to summary, retargets in place, and Replay makes zero requests", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?flow=adaptive&proof=keyframes");
  await waitForProjectileBridge(page);
  await drawLaunch(page);

  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    "apex_state",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  await stopAtVisibleCheckpoint(page, "apex_state");
  await page
    .getByRole("button", {
      name: "At the apex, why is acceleration still down?",
    })
    .click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "clarifyApex", "cinematic"),
    { settledMainCount: 4 },
  );

  await page.getByRole("button", { name: "Continue the flight" }).click();
  const summary = await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "continueAfterClarification",
      "cinematic",
    ),
  );
  await rememberStableElement(
    page,
    "projectile__ground",
    "__projectile_ground_before_retarget__",
  );
  await rememberStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_before_retarget__",
  );

  const callsBeforeSelection = await projectileBridgeState(page);
  await page
    .getByRole("button", { name: "Set launch angle to 60 degrees" })
    .click();
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "45",
  );
  expect(await observeProjectileStage(page)).toEqual(summary);
  expect(await projectileBridgeState(page)).toEqual(callsBeforeSelection);
  await expect(
    page.getByRole("button", { name: "Morph to 20 m/s · 60°" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Morph to 20 m/s · 60°" }).click();
  const retargeted = await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "retargetAfterSummary",
      "cinematic",
    ),
  );
  await expectStableElement(
    page,
    "projectile__ground",
    "__projectile_ground_before_retarget__",
  );
  await expectStableElement(
    page,
    "projectile__projectile_marker",
    "__projectile_marker_before_retarget__",
  );
  await expect(page.getByText("apex acceleration explained")).toBeVisible();

  const beforeReplay = await projectileBridgeState(page);
  await page.getByRole("button", { name: "Replay" }).click();
  await expect(projectileStage(page)).toHaveAttribute(
    "data-phase",
    "replaying",
  );
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(
      primaryFixture,
      "retargetAfterSummary",
      "cinematic",
    ),
  );
  expect(await observeProjectileStage(page)).toEqual(retargeted);
  expect(await projectileBridgeState(page)).toEqual(beforeReplay);

  expect(beforeReplay).toEqual({
    runnerCallCount: 4,
    calls: [
      expect.objectContaining({
        ordinal: 1,
        generation: 1,
        baseRevision: 0,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      expect.objectContaining({
        ordinal: 2,
        generation: 2,
        baseRevision: 4,
        checkpointId: "apex_state",
        requestedRoute: { intent: "clarify", topic: "apex_acceleration" },
      }),
      expect.objectContaining({
        ordinal: 3,
        generation: 3,
        baseRevision: 5,
        checkpointId: "apex_state",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: "apex_acceleration",
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
      expect.objectContaining({
        ordinal: 4,
        generation: 4,
        problemSpec: primaryProblem,
        baseRevision: 7,
        checkpointId: "summary",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: null,
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: retargetProblem,
        },
      }),
    ],
  });
  expectProviderFree(requests);
});

test("the apex frontier can retarget directly instead of continuing", async ({
  page,
}) => {
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?flow=adaptive");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-settled-main-count",
    "4",
  );
  await stopAtVisibleCheckpoint(page, "apex_state");
  await page
    .getByRole("button", {
      name: "At the apex, why is acceleration still down?",
    })
    .click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "clarifyApex", "cinematic"),
    { settledMainCount: 4 },
  );
  await selectProjectileProblem(page, retargetProblem);
  await page.getByRole("button", { name: "Morph to 20 m/s · 60°" }).click();
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "retargetAtApex", "cinematic"),
    { settledMainCount: 4 },
  );
  expect(await projectileBridgeState(page)).toEqual({
    runnerCallCount: 3,
    calls: [
      expect.objectContaining({ generation: 1, baseRevision: 0 }),
      expect.objectContaining({
        generation: 2,
        baseRevision: 4,
        requestedRoute: { intent: "clarify", topic: "apex_acceleration" },
      }),
      expect.objectContaining({
        generation: 3,
        problemSpec: primaryProblem,
        baseRevision: 5,
        checkpointId: "apex_state",
        clarifiedTopics: ["apex_acceleration"],
        activeClarification: "apex_acceleration",
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: retargetProblem,
        },
      }),
    ],
  });
  expectProviderFree(requests);
});

test.describe("interruption phase boundaries", () => {
  test("trace_path interruption settles the exact ascent checkpoint", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    const trace = await stopDuringActiveTrace(page, "trace_ascent");
    expect(trace.markerTransform).toMatch(/^translate\(/);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "3",
    );
    await expect(page.getByText("scene 3", { exact: false })).toBeVisible();
    expectProviderFree(requests);
  });

  test("transform and trace interruption preserve the descent target identities", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "2",
    );
    await rememberStableElement(
      page,
      "projectile__vertical_state",
      "__vertical_state_before_transform__",
    );
    const interrupted = await stopDuringTransformAndTrace(page);
    expect(interrupted.interruptedPath).not.toBe(interrupted.beforePath);
    expect(interrupted.traceDashOffset).toBeGreaterThan(0);
    expect(interrupted.traceDashOffset).toBeLessThan(
      interrupted.traceDashArray,
    );
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "5",
    );
    await expectStableElement(
      page,
      "projectile__vertical_state",
      "__vertical_state_before_transform__",
    );
    expectProviderFree(requests);
  });

  test("focus interruption settles setup after the first painted cue", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    expect(await stopDuringFocus(page)).toMatch(/^brightness\(/);
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "1",
    );
    await expect(page.getByText("scene 1", { exact: false })).toBeVisible();
    expectProviderFree(requests);
  });

  test("hold interruption settles setup after motion has visually finished", async ({
    page,
  }) => {
    const requests = observeProviderFreeRequests(page);
    await page.goto("/e2e/projectile-motion?proof=keyframes&speed=normal");
    await waitForProjectileBridge(page);
    await drawLaunch(page);
    const hold = await stopDuringHold(page);
    expect(Math.abs(hold.dashOffset)).toBeLessThanOrEqual(
      hold.dashArray * 0.005,
    );
    await expect(projectileStage(page)).toHaveAttribute(
      "data-settled-main-count",
      "1",
    );
    await expect(page.getByText("scene 1", { exact: false })).toBeVisible();
    expectProviderFree(requests);
  });
});

test("closed query controls reject invalid values and UI selection cannot mutate the accepted board", async ({
  page,
}) => {
  const invalid = await page.goto(
    "/e2e/projectile-motion?layout=wide&speed=turbo",
  );
  expect(invalid?.status()).toBe(404);
  const repeated = await page.goto(
    "/e2e/projectile-motion?layout=compact&layout=cinematic",
  );
  expect(repeated?.status()).toBe(404);

  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?motion=reduced");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  await expectProjectileTerminal(
    page,
    expectedProjectileBoard(primaryFixture, "main", "cinematic"),
  );
  const before = await observeProjectileStage(page);
  const beforeCalls = await projectileBridgeState(page);
  await page
    .getByRole("button", { name: "Set launch angle to 60 degrees" })
    .click();
  expect(await observeProjectileStage(page)).toEqual(before);
  expect(await projectileBridgeState(page)).toEqual(beforeCalls);
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "45",
  );

  await page.getByRole("button", { name: "Reset board" }).click();
  await expect(projectileStage(page)).toHaveAttribute("data-phase", "idle");
  await expect(projectileStage(page)).toHaveAttribute(
    "data-visible-checkpoint-id",
    "none",
  );
  await expect(projectileStage(page)).toHaveAttribute(
    "data-accepted-angle",
    "none",
  );
  await expect(projectileBoard(page).locator("[data-element-id]")).toHaveCount(
    0,
  );
  expect(await projectileBridgeState(page)).toEqual(beforeCalls);
  expectProviderFree(requests);
});

test.describe("responsive compact proof", () => {
  for (const viewport of [
    { width: 375, height: 812 },
    { width: 320, height: 568 },
  ] as const) {
    test(`${viewport.width}x${viewport.height} keeps controls, board, and caption contained`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize(viewport);
      const requests = observeProviderFreeRequests(page);
      await page.goto("/e2e/projectile-motion?layout=compact&motion=reduced");
      await waitForProjectileBridge(page);
      await drawLaunch(page);
      await expectProjectileTerminal(
        page,
        expectedProjectileBoard(primaryFixture, "main", "compact"),
      );
      await expect(projectileStage(page)).toHaveAttribute(
        "data-layout",
        "compact",
      );
      await expectNoHorizontalOverflow(page);
      const productBounds = await page
        .getByTestId("projectile-choreography-product")
        .boundingBox();
      const stageBounds = await projectileStage(page).boundingBox();
      expect(productBounds).not.toBeNull();
      expect(stageBounds).not.toBeNull();
      expect(productBounds!.x).toBeGreaterThanOrEqual(0);
      expect(productBounds!.x + productBounds!.width).toBeLessThanOrEqual(
        viewport.width,
      );
      expect(stageBounds!.x).toBeGreaterThanOrEqual(0);
      expect(stageBounds!.x + stageBounds!.width).toBeLessThanOrEqual(
        viewport.width,
      );
      if (viewport.width === 320) {
        await attachBoardOnlyScreenshot(
          page,
          testInfo,
          "compact-320x568-terminal-mute-first",
        );
      }
      expectProviderFree(requests);
    });
  }
});

test("reduced motion materializes the same six-checkpoint cinematic terminal", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const requests = observeProviderFreeRequests(page);
  await page.goto("/e2e/projectile-motion?motion=reduced");
  await waitForProjectileBridge(page);
  await drawLaunch(page);
  const expected = expectedProjectileBoard(primaryFixture, "main", "cinematic");
  const actual = await expectProjectileTerminal(page, expected);
  expect(actual.nodeIds).toEqual(expected.nodeIds);
  expect(actual.caption).toBe(expected.caption);
  expect(actual.viewBox).toBe(expected.viewBox);
  expect(PROJECTILE_MAIN_CHECKPOINTS).toHaveLength(6);
  expect((await projectileBridgeState(page)).runnerCallCount).toBe(1);
  expectProviderFree(requests);
});
