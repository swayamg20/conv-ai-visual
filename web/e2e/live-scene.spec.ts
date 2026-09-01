import { expect, test, type Page } from "@playwright/test";

async function openLab(page: Page): Promise<void> {
  await page.goto("/labs/live-scene");
  await expect(page.getByRole("heading", { name: "Model-authored board" })).toBeVisible();
  await expect(page.getByText("Gate 1", { exact: true })).toBeVisible();
}

async function chooseScenario(page: Page, label: string): Promise<void> {
  await page
    .getByTestId("fixture-mode-picker")
    .getByText(label, { exact: true })
    .click();
  await expect(page.getByText(`Fixture · ${label}`, { exact: true })).toBeVisible();
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
