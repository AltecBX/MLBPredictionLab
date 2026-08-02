/**
 * The site must stay exportable to files.
 *
 * It is published to GitHub Pages as static HTML because Render's free plan
 * sleeps a service after fifteen minutes and GitHub throttles the keep-warm
 * cron to roughly hourly whatever the expression says — measured here, a
 * ten-minute schedule ran at 14:28, 15:40, 16:39, 17:44 and 18:44. A ping that
 * lands hourly cannot hold open a fifteen-minute timer, so the 502 was
 * structural rather than bad luck.
 *
 * Static export forbids two things that are easy to reintroduce without
 * noticing, because both work perfectly in `next dev`:
 *
 *   * `searchParams` in a server component — a query string is not part of a
 *     file's path, so every value returns the same pre-rendered page;
 *   * `dynamic = "force-dynamic"` — there is nothing to be dynamic on.
 *
 * Neither fails the type checker. Both fail the build, minutes in, after the
 * data has been fetched.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { buildDates, isBuilt, shiftUtcIsoDate } from "@/lib/window";

const APP = path.join(process.cwd(), "app");

function pageFiles(dir: string = APP): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) return pageFiles(full);
    return entry === "page.tsx" ? [full] : [];
  });
}

describe("static export", () => {
  it("has no server component reading searchParams", () => {
    const offenders = pageFiles().filter((file) =>
      /searchParams/.test(readFileSync(file, "utf8")),
    );
    expect(
      offenders.map((f) => path.relative(APP, f)),
      "A query string cannot select a pre-rendered file. Put the value in the " +
        "path (see app/d/[date]) or read it in a client component (see TabPanels).",
    ).toEqual([]);
  });

  it("has no page forcing dynamic rendering", () => {
    const offenders = pageFiles().filter((file) =>
      /force-dynamic/.test(readFileSync(file, "utf8")),
    );
    expect(offenders.map((f) => path.relative(APP, f))).toEqual([]);
  });

  it("builds a page for every date the arrows can reach", () => {
    const dates = buildDates("2026-08-02");
    expect(dates).toContain("2026-08-02");
    // Both ends are reachable, and one step beyond each end is not — which is
    // what makes the arrows stop rather than link to a 404.
    expect(isBuilt(dates[0], "2026-08-02")).toBe(true);
    expect(isBuilt(dates[dates.length - 1], "2026-08-02")).toBe(true);
    expect(isBuilt(shiftUtcIsoDate(dates[0], -1), "2026-08-02")).toBe(false);
    expect(
      isBuilt(shiftUtcIsoDate(dates[dates.length - 1], 1), "2026-08-02"),
    ).toBe(false);
  });

  it("shifts dates across a month boundary", () => {
    expect(shiftUtcIsoDate("2026-08-01", -1)).toBe("2026-07-31");
    expect(shiftUtcIsoDate("2026-12-31", 1)).toBe("2027-01-01");
    // A leap day is the case a naive +86400000 gets wrong.
    expect(shiftUtcIsoDate("2028-02-28", 1)).toBe("2028-02-29");
  });

  it("pins today from the environment so a build cannot straddle midnight", async () => {
    const { buildToday } = await import("@/lib/window");
    expect(buildToday({ BUILD_DATE: "2026-04-01" })).toBe("2026-04-01");
    // Junk is ignored rather than trusted.
    expect(buildToday({ BUILD_DATE: "nonsense" })).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
