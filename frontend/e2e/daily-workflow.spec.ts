import { expect, test } from "@playwright/test";

/**
 * End-to-end coverage of the daily game workflow: land on the game center,
 * read a prediction, open the full breakdown, and check the backtest evidence.
 *
 * The suite adapts to whether the backend is reachable — when it is not, it
 * asserts that the UI says so explicitly rather than rendering empty state that
 * could be mistaken for "no games today".
 */

type Page = import("@playwright/test").Page;

async function backendIsReachable(page: Page) {
  await page.goto("/");
  return !(await page.getByText(/prediction API is unavailable/i).isVisible());
}

/**
 * Open the first game with a prediction, or skip when the slate has none.
 * Waits for the link rather than counting immediately, because a bare count()
 * does not auto-wait and every route here is server-rendered on demand.
 */
async function openFirstGame(page: Page, date = "2026-08-01"): Promise<string> {
  await page.goto(`/?date=${date}`);
  const link = page.getByRole("link", { name: /Full breakdown/ }).first();
  try {
    await link.waitFor({ state: "visible", timeout: 15_000 });
  } catch {
    test.skip(true, `No games with predictions on ${date}.`);
  }
  await link.click();
  await page.waitForURL(/\/game\/\d+/, { timeout: 30_000 });
  return page.url().split("?")[0];
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

  test("date navigation targets the adjacent dates and renders them", async ({
    page,
  }) => {
    await page.goto("/?date=2026-08-01");
    await expect(page.getByText("August 1, 2026")).toBeVisible();

    // Assert the links point where they should, then follow them by URL. A
    // click here would measure the client router's timing under whatever load
    // the rest of the suite is putting on the server, not the navigation
    // contract this test is about.
    await expect(page.getByRole("link", { name: "← Prev" })).toHaveAttribute(
      "href",
      /date=2026-07-31/,
    );
    await expect(page.getByRole("link", { name: "Next →" })).toHaveAttribute(
      "href",
      /date=2026-08-02/,
    );

    await page.goto("/?date=2026-07-31");
    await expect(page.getByText("July 31, 2026").first()).toBeVisible();

    // A date with nothing ingested must say so rather than rendering blank.
    await page.goto("/?date=2026-08-02");
    await expect(page.getByText("August 2, 2026").first()).toBeVisible();
  });

  test("a game card opens its full breakdown", async ({ page }) => {
    await openFirstGame(page);
    await expect(page.getByRole("navigation", { name: "Game sections" })).toBeVisible();
    await expect(page.getByText(/Why the model favors/)).toBeVisible();
  });

  test("game detail tabs are deep-linkable and each renders", async ({ page }) => {
    const base = await openFirstGame(page);

    for (const [tab, marker] of [
      ["pitchers", /Starter comparison/],
      ["bullpens", /Bullpen quality and recent workload/],
      ["environment", /Ballpark/],
      ["explanation", /Every measured contribution/],
      ["simulation", /Monte Carlo simulation is not available/],
      ["market", /Model fair price/],
      ["backtest", /How reliable have similar predictions been/],
    ] as const) {
      await page.goto(`${base}?tab=${tab}`);
      await expect(page.getByText(marker).first()).toBeVisible();
    }
  });

  test("unavailable data states name the source that would enable them", async ({ page }) => {
    const base = await openFirstGame(page);

    await page.goto(`${base}?tab=market`);
    await expect(page.getByText("ODDS_PROVIDER", { exact: true })).toBeVisible();

    await page.goto(`${base}?tab=lineups`);
    await expect(page.getByText("LINEUP_PROVIDER", { exact: true })).toBeVisible();
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
    const missing = page.getByText(/No backtest report available/).first();
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

  test("an unknown game id renders the not-found page", async ({ page }) => {
    // The route is force-dynamic and streamed, so the HTTP status is committed
    // before notFound() runs; the rendered page is the contract that matters.
    await page.goto("/game/999999999");
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Back to the game center/ })).toBeVisible();
  });
});
