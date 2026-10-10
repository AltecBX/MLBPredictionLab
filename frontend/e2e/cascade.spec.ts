import { expect, test } from "@playwright/test";

/**
 * Border colours computed by the real built stylesheet.
 *
 * For most of the product's life an unlayered `* { border-color }` overrode
 * every class and utility that set a border colour, and nothing noticed: the
 * markup was right, only the cascade was wrong, and that is visible only to a
 * browser. These place the elements the product uses on a page that renders
 * without an API and read back what the browser computed.
 */

type Page = import("@playwright/test").Page;

/** What the browser computes for a border coloured by `value`, for comparison. */
async function computed(page: Page, value: string): Promise<string> {
  return page.evaluate((v) => {
    const probe = document.createElement("span");
    probe.style.border = `1px solid ${v}`;
    document.body.appendChild(probe);
    const out = getComputedStyle(probe).borderTopColor;
    probe.remove();
    return out;
  }, value);
}

async function place(page: Page, html: string): Promise<void> {
  await page.evaluate((markup) => {
    const host = document.createElement("div");
    host.id = "cascade-probe";
    host.style.padding = "24px";
    host.innerHTML = markup;
    document.querySelector("main")?.prepend(host);
  }, html);
}

const border = (page: Page, selector: string) =>
  page.locator(selector).evaluate((el) => getComputedStyle(el).borderTopColor);

test.describe("border colours", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/methodology");
    await place(
      page,
      `<button type="button" id="chip" class="pill pill-active tap t-small px-3">Game time</button>
       <button type="button" id="plain" class="pill tap t-small px-3">Closest game</button>
       <span id="muted" class="inline-flex rounded-full border px-2 border-transparent">Final</span>
       <span id="neutral" class="inline-flex rounded-full border px-2 border-[var(--border-strong)]">Strong lean</span>
       <span id="default" class="inline-flex border px-2">plain</span>`,
    );
  });

  test("the active sort chip draws its accent border", async ({ page }) => {
    expect(await border(page, "#chip")).toBe(await computed(page, "var(--border-accent)"));
  });

  test("the badges draw the borders their tones ask for", async ({ page }) => {
    expect(await border(page, "#neutral")).toBe(await computed(page, "var(--border-strong)"));
    expect(await border(page, "#muted")).toBe(await computed(page, "transparent"));
  });

  test("an element no class colours still gets the default border", async ({ page }) => {
    expect(await border(page, "#default")).toBe(await computed(page, "var(--border)"));
  });

  test("hover strengthens a pill, and the selected chip stays selected", async ({ page }) => {
    // Pills animate their border colour over a hundred-odd milliseconds, so
    // each reading is polled until the transition has settled.
    const strong = await computed(page, "var(--border-strong)");
    const accentBorder = await computed(page, "var(--border-accent)");
    await page.hover("#plain");
    await expect.poll(() => border(page, "#plain")).toBe(strong);
    await page.hover("#chip");
    await expect.poll(() => border(page, "#chip")).toBe(accentBorder);
    const accent = await page.evaluate(() => {
      const p = document.createElement("span");
      p.style.color = "var(--accent)";
      document.body.appendChild(p);
      const c = getComputedStyle(p).color;
      p.remove();
      return c;
    });
    await expect
      .poll(() => page.locator("#chip").evaluate((el) => getComputedStyle(el).color))
      .toBe(accent);
  });
});
