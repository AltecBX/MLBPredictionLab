import { expect, test } from "@playwright/test";

/**
 * iPhone contract.
 *
 * This is the primary device for the product, so the properties below are
 * assertions rather than aspirations: the page never scrolls sideways, every
 * control clears the 44pt touch floor, the sticky layers do not bury each
 * other, and the app is installable to the home screen.
 *
 * 375x667 is the iPhone SE — the narrowest current iPhone, and the one that
 * breaks first.
 */

const IPHONE_SE = { width: 375, height: 667 };
const IPHONE_14 = { width: 390, height: 844 };

type Page = import("@playwright/test").Page;

test.use({
  viewport: IPHONE_14,
  isMobile: true,
  hasTouch: true,
  deviceScaleFactor: 3,
  userAgent:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
});

/** Page-level horizontal overflow, the failure this whole file exists to catch. */
async function horizontalOverflow(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/**
 * Controls whose effective hit area is under 44pt.
 *
 * "Effective" matters: an inline 14px info icon expands its target with an
 * absolutely positioned ::after rather than by growing and shoving the text
 * around it. The browser hit-tests the pseudo-element, so the audit counts it.
 */
async function undersizedTargets(page: Page) {
  return page.evaluate(() => {
    const bad: { text: string; h: number; w: number }[] = [];
    for (const el of document.querySelectorAll<HTMLElement>(
      "a, button, [role=button], input, summary",
    )) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue; // not rendered
      if (getComputedStyle(el).visibility === "hidden") continue;
      if (el.classList.contains("sr-only")) continue; // skip-link, until focused

      let { height, width } = r;
      const after = getComputedStyle(el, "::after");
      if (after.content !== "none" && after.position === "absolute") {
        const grow = (v: string) => Math.max(0, -parseFloat(v || "0") * 2);
        height += grow(after.top);
        width += grow(after.left);
      }

      if (height < 44 || width < 24) {
        bad.push({
          text: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40),
          h: Math.round(height),
          w: Math.round(width),
        });
      }
    }
    return bad;
  });
}

/** Routes that render regardless of whether a backend or a slate exists. */
const ALWAYS = ["/?date=2026-08-01", "/backtest", "/diagnostics", "/methodology"];

/**
 * The first game's URL, or null when there is no seeded slate.
 *
 * Deliberately not a `test.skip`. CI runs this suite against an empty database,
 * where every screen renders an explicit unavailable state — and a layout that
 * breaks on a phone breaks there too. Skipping the whole test because no game
 * exists would silently retire the assertion this file exists for.
 */
async function firstGameUrl(page: Page, date = "2026-08-01"): Promise<string | null> {
  await page.goto(`/?date=${date}`);
  const link = page.getByRole("link", { name: /Full breakdown/ }).first();
  try {
    await link.waitFor({ state: "visible", timeout: 10_000 });
  } catch {
    return null;
  }
  return await link.getAttribute("href");
}

/** Game routes when a slate is seeded, nothing when it is not. */
function gameRoutes(game: string | null, tabs: string[]): string[] {
  return game ? tabs.map((tab) => `${game}?tab=${tab}`) : [];
}

test.describe("iPhone layout", () => {
  test("no route scrolls the page sideways", async ({ page }) => {
    const game = await firstGameUrl(page);
    const routes = [
      ...ALWAYS,
      ...gameRoutes(game, ["prediction", "explanation", "pitchers", "history"]),
    ];

    for (const route of routes) {
      await page.goto(route);
      expect(await horizontalOverflow(page), `sideways scroll on ${route}`).toBeLessThanOrEqual(1);
    }
  });

  test("the narrowest iPhone does not scroll sideways either", async ({ page }) => {
    await page.setViewportSize(IPHONE_SE);
    const game = await firstGameUrl(page);
    for (const route of [...ALWAYS, ...gameRoutes(game, ["prediction"])]) {
      await page.goto(route);
      expect(await horizontalOverflow(page), `sideways scroll on ${route}`).toBeLessThanOrEqual(1);
    }
  });

  test("every control clears the 44pt touch floor", async ({ page }) => {
    const game = await firstGameUrl(page);
    for (const route of [...ALWAYS, ...gameRoutes(game, ["prediction"])]) {
      await page.goto(route);
      expect(await undersizedTargets(page), `small touch targets on ${route}`).toEqual([]);
    }
  });

  test("primary navigation is a bottom bar within thumb reach", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav).toBeVisible();

    const box = (await nav.boundingBox())!;
    const viewport = page.viewportSize()!;
    // Anchored to the bottom edge, not merely somewhere down the page.
    expect(box.y + box.height).toBeGreaterThan(viewport.height - 2);

    await page.getByRole("link", { name: "Backtest" }).click();
    await page.waitForURL(/\/backtest/);
    await expect(page.getByRole("heading", { name: "Walk-forward backtest" })).toBeVisible();
  });

  test("the header collapses to one row and the date bar sticks below it", async ({
    page,
  }) => {
    await page.goto("/?date=2026-08-01");

    const header = page.locator("body > header");
    const headerBox = (await header.boundingBox())!;
    // One row: brand plus the theme control, nothing wrapped.
    expect(headerBox.height).toBeLessThanOrEqual(72);

    const dateNav = page.getByRole("navigation", { name: "Date" });
    const before = (await dateNav.boundingBox())!;

    await page.mouse.wheel(0, 1200);
    await page.waitForTimeout(200);
    const after = (await dateNav.boundingBox())!;

    // Still on screen after scrolling, and clear of the header rather than
    // hidden under it.
    expect(after.y).toBeGreaterThanOrEqual(headerBox.height - 2);
    expect(after.y).toBeLessThan(before.y + 8);
    await expect(page.getByRole("link", { name: "← Prev" })).toBeVisible();
  });

  test("the game tab strip stays reachable and scrolls the active tab into view", async ({
    page,
  }) => {
    const game = await firstGameUrl(page);
    test.skip(!game, "No seeded slate; the tab strip only exists on a game page.");
    await page.goto(`${game}?tab=backtest`);

    const strip = page.getByRole("navigation", { name: "Game sections" });
    await expect(strip).toBeVisible();

    // The active tab is last of ten; it must be visible without the reader
    // hunting for it.
    const active = page.locator('nav[aria-label="Game sections"] a[aria-current="page"]');
    await expect(active).toBeInViewport();

    // And the strip stays pinned under the header while the panel scrolls.
    const headerH = (await page.locator("body > header").boundingBox())!.height;
    await page.mouse.wheel(0, 1500);
    await page.waitForTimeout(200);
    const stripBox = (await strip.boundingBox())!;
    expect(stripBox.y).toBeGreaterThanOrEqual(headerH - 2);
    expect(stripBox.y).toBeLessThanOrEqual(headerH + 4);
  });

  test("a tooltip opens on tap and stays inside the viewport", async ({ page }) => {
    await page.goto("/?date=2026-08-01");
    // Scoped to the freshness strip: the "Largest model edge" chip is
    // aria-disabled, so its tooltip is deliberately not operable.
    const trigger = page
      .getByLabel("Data freshness")
      .getByRole("button", { name: "More information" })
      .first();
    // waitFor, not isVisible: the page streams, and a bare visibility read can
    // resolve before the strip has rendered — which skips a test that should
    // have run.
    try {
      await trigger.waitFor({ state: "visible", timeout: 10_000 });
    } catch {
      test.skip(true, "No backend; the freshness strip has no tooltips to tap.");
    }
    // tap(), not click(): click() dispatches mouse events even under touch
    // emulation, so it would exercise the hover path and prove nothing about
    // the phone behaviour this test is named for.
    await trigger.tap();

    const tip = page.getByRole("tooltip").first();
    await expect(tip).toBeVisible();

    const box = (await tip.boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);

    // Tapping again dismisses it — there is no hover-out on a phone.
    await trigger.tap();
    await expect(tip).toBeHidden();
  });

  test("the app is installable to the home screen", async ({ page }) => {
    const res = await page.request.get("/manifest.webmanifest");
    expect(res.ok()).toBeTruthy();
    const manifest = await res.json();

    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(manifest.short_name.length).toBeLessThanOrEqual(12); // iOS truncates
    expect(manifest.icons.some((i: { sizes: string }) => i.sizes === "512x512")).toBe(true);
    expect(
      manifest.icons.some((i: { purpose?: string }) => i.purpose === "maskable"),
    ).toBe(true);

    for (const icon of manifest.icons) {
      const asset = await page.request.get(icon.src);
      expect(asset.ok(), `${icon.src} is missing`).toBeTruthy();
    }

    await page.goto("/");
    await expect(page.locator('link[rel="manifest"]')).toHaveCount(1);
    // Both spellings: iOS 18+ reads the unprefixed name, earlier versions only
    // the apple-prefixed one, and the app should install fullscreen on both.
    for (const name of ["mobile-web-app-capable", "apple-mobile-web-app-capable"]) {
      await expect(page.locator(`meta[name="${name}"]`)).toHaveAttribute("content", "yes");
    }
    // viewport-fit=cover is what lets the safe-area insets mean anything.
    await expect(page.locator('meta[name="viewport"]')).toHaveAttribute(
      "content",
      /viewport-fit=cover/,
    );
  });

  test("pinch zoom is not disabled", async ({ page }) => {
    await page.goto("/");
    const content =
      (await page.locator('meta[name="viewport"]').getAttribute("content")) ?? "";
    expect(content).not.toContain("user-scalable=no");
    const max = /maximum-scale=([\d.]+)/.exec(content);
    if (max) expect(Number(max[1])).toBeGreaterThanOrEqual(2);
  });

  test("the bottom bar never covers the last card", async ({ page }) => {
    // networkidle matters here: the slate streams in, so scrolling to the
    // bottom of a partially rendered document lands nowhere near the footer.
    await page.goto("/?date=2026-08-01", { waitUntil: "networkidle" });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(300);

    const navBox = (await page.getByRole("navigation", { name: "Primary" }).boundingBox())!;
    // The footer box intentionally extends under the bar — that padding is what
    // creates the clearance. Its text is what must stay readable.
    const textBox = (await page.locator("body > footer p").boundingBox())!;

    expect(textBox.y + textBox.height).toBeLessThanOrEqual(navBox.y + 1);
  });
});
