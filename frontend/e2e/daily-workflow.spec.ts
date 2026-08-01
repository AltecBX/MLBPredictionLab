import { expect, test } from "@playwright/test";

/**
 * End-to-end coverage of the daily game workflow: land on the game center,
 * read a prediction, open the full breakdown, and check the backtest evidence.
 *
 * The suite adapts to whether the backend is reachable — when it is not, it
 * asserts that the UI says so explicitly rather than rendering empty state that
 * could be mistaken for "no games today".
 */

async function backendIsReachable(page: import("@playwright/test").Page) {
  await page.goto("/");
  return !(await page.getByText(/prediction API is unavailable/i).isVisible());
}

test.describe("daily game workflow", () => {
  test("game center renders with freshness and sorting", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Daily Game Center" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Date" })).toBeVisible();

    if (!(await backendIsReachable(page))) {
      await expect(page.getByText(/prediction API is unavailable/i)).toBeVisible();
      return;
    }

    await expect(page.getByLabel("Data freshness")).toBeVisible();
    await expect(page.getByRole("link", { name: "Highest win probability" })).toBeVisible();
  });

  test("date navigation changes the queried date", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    await expect(page.getByText("August 1, 2026")).toBeVisible();
    await page.getByRole("link", { name: "← Prev" }).click();
    await expect(page).toHaveURL(/date=2026-07-31/);
  });

  test("a game card opens its full breakdown", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    const link = page.getByRole("link", { name: /Full breakdown/ }).first();
    if ((await link.count()) === 0) {
      test.skip(true, "No games with predictions on this date.");
    }
    await link.click();
    await expect(page).toHaveURL(/\/game\/\d+/);
    await expect(page.getByRole("navigation", { name: "Game sections" })).toBeVisible();
  });

  test("game detail tabs are deep-linkable and each renders", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    const link = page.getByRole("link", { name: /Full breakdown/ }).first();
    if ((await link.count()) === 0) {
      test.skip(true, "No games with predictions on this date.");
    }
    await link.click();
    const url = page.url();

    for (const [tab, marker] of [
      ["pitchers", /Starter comparison/],
      ["bullpens", /Bullpen quality and recent workload/],
      ["environment", /Ballpark/],
      ["explanation", /Every measured contribution/],
      ["simulation", /Monte Carlo simulation is not available/],
      ["market", /Model fair price/],
      ["backtest", /How reliable have similar predictions been/],
    ] as const) {
      await page.goto(`${url.split("?")[0]}?tab=${tab}`);
      await expect(page.getByText(marker)).toBeVisible();
    }
  });

  test("unavailable data states name the source that would enable them", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    const link = page.getByRole("link", { name: /Full breakdown/ }).first();
    if ((await link.count()) === 0) {
      test.skip(true, "No games with predictions on this date.");
    }
    await link.click();
    const base = page.url().split("?")[0];

    await page.goto(`${base}?tab=market`);
    await expect(page.getByText("ODDS_PROVIDER")).toBeVisible();

    await page.goto(`${base}?tab=lineups`);
    await expect(page.getByText("LINEUP_PROVIDER")).toBeVisible();
  });

  test("no page promises a guaranteed outcome", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    const body = (await page.textContent("body")) ?? "";
    for (const phrase of ["guaranteed win", "sure thing", "lock of the day", "can't lose"]) {
      expect(body.toLowerCase()).not.toContain(phrase);
    }
  });

  test("backtest page shows calibration and metrics or says why not", async ({ page }) => {
    await page.goto("/backtest");
    await expect(page.getByRole("heading", { name: "Walk-forward backtest" })).toBeVisible();
    const missing = page.getByText(/No backtest report available/);
    if (await missing.isVisible()) return;
    await expect(page.getByText("Log loss").first()).toBeVisible();
    await expect(page.getByRole("img", { name: /Calibration chart/ })).toBeVisible();
  });

  test("diagnostics page lists source health", async ({ page }) => {
    await page.goto("/diagnostics");
    await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible();
  });

  test("methodology page documents active and deferred inputs", async ({ page }) => {
    await page.goto("/methodology");
    await expect(page.getByRole("heading", { name: "Methodology" })).toBeVisible();
    await expect(page.getByText(/How a prediction is built/)).toBeVisible();
  });

  test("theme toggle switches to dark and persists", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("layout is usable on a narrow viewport without horizontal page scroll", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/?date=2026-08-01");
    await expect(page.getByRole("heading", { name: "Daily Game Center" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("an unknown game id returns the not-found page", async ({ page }) => {
    const response = await page.goto("/game/999999999");
    expect(response?.status()).toBe(404);
    await expect(page.getByText("Not found")).toBeVisible();
  });
});
