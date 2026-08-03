/**
 * The installed-app (PWA) contract, pinned as source assertions.
 *
 * The app ships with `viewport-fit=cover` and a translucent status bar, so on
 * a home-screen install the canvas extends under the iPhone status bar. Two
 * regressions actually shipped and were caught by a reader instead of a test:
 * the header drew its wordmark beneath the clock (no top safe-area inset),
 * and a fifth nav tab wrapped the bottom bar onto a second row (hardcoded
 * column count). These are string-level guards — layout engines cannot run
 * here, but the constructs whose absence caused both bugs can be asserted.
 * The geometric halves live in e2e/mobile.spec.ts.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(path.join(process.cwd(), p), "utf8");

describe("status bar safe area", () => {
  it("the header height token carries the top inset", () => {
    const css = read("app/globals.css");
    expect(css).toMatch(
      /--header-h:\s*calc\(var\(--header-row\)\s*\+\s*env\(safe-area-inset-top/,
    );
  });

  it("the header element pads by the top inset", () => {
    const layout = read("app/layout.tsx");
    expect(layout).toContain('paddingTop: "env(safe-area-inset-top, 0px)"');
    // And the visible row is sized by the row token, not the inset-inclusive
    // one — otherwise the row itself would grow by the inset.
    expect(layout).toContain("h-[calc(var(--header-row)-1px)]");
  });

  it("sticky layers offset by the inset-inclusive token", () => {
    for (const file of [
      "components/GameCenter.tsx",
      "components/Tabs.tsx",
    ]) {
      expect(read(file)).toContain("var(--header-h)");
    }
  });
});

describe("bottom navigation", () => {
  it("derives its columns from the destination list, never a hardcoded count", () => {
    const nav = read("components/BottomNav.tsx");
    expect(nav).toContain("repeat(${ITEMS.length}, minmax(0, 1fr))");
    expect(nav).not.toMatch(/grid-cols-\d/);
  });

  it("keeps the bottom inset as padding so the bar reaches the screen edge", () => {
    const nav = read("components/BottomNav.tsx");
    expect(nav).toContain("env(safe-area-inset-bottom");
  });
});
