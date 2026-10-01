/**
 * Which game pages get built, decided the same way for the page and its file.
 *
 * The page and `detail.json` each ask `builtGameIds` at build time. If the
 * two answers could differ — one call losing a date the other kept — the
 * export would carry pages without their file, or files without their page,
 * and the publish workflow would refuse it. So: every slate or no slate, the
 * slates a few at a time, and one answer per export.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildDates } from "@/lib/window";

const DATES = buildDates("2026-08-02");

const slate = (games: number[]) =>
  new Response(
    JSON.stringify({
      date: "x", count: games.length, generated_at: "2026-08-02T12:00:00Z",
      model_version: "v1", freshness: [], games: games.map((game_id) => ({ game_id })),
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );

/** A fetch that answers per date: a list of ids, or a status to fail with. */
function answering(plan: (date: string) => number[] | number) {
  let inFlight = 0;
  let peak = 0;
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    const date = new URL(url).searchParams.get("date") ?? "";
    calls.push(date);
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 1));
    inFlight -= 1;
    const answer = plan(date);
    return typeof answer === "number" ? new Response("boom", { status: answer }) : slate(answer);
  });
  return { fetchMock, calls, peak: () => peak };
}

async function builtGameIds(exportMode = false) {
  vi.resetModules();
  process.env.API_RETRY_ATTEMPTS = "0";
  process.env.BUILD_DATE = "2026-08-02";
  if (exportMode) process.env.NEXT_STATIC_EXPORT = "1";
  else delete process.env.NEXT_STATIC_EXPORT;
  const { builtGameIds: decide } = await import("@/lib/games");
  return decide;
}

beforeEach(() => {
  delete process.env.NEXT_STATIC_EXPORT;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete process.env.NEXT_STATIC_EXPORT;
  delete process.env.API_RETRY_ATTEMPTS;
  delete process.env.BUILD_DATE;
});

describe("builtGameIds", () => {
  it("collects every game on every built date, once each", async () => {
    const { fetchMock } = answering((date) => [Number(date.slice(-2)), 999]);
    vi.stubGlobal("fetch", fetchMock);
    const decide = await builtGameIds();
    const ids = (await decide()).map((p) => p.id);
    expect(ids).toHaveLength(DATES.length + 1);
    expect(ids).toContain("999");
    expect(new Set(ids).size).toBe(ids.length);
    expect(fetchMock).toHaveBeenCalledTimes(DATES.length);
  });

  it("asks for the slates a few at a time, not all at once", async () => {
    const { fetchMock, peak } = answering(() => [1]);
    vi.stubGlobal("fetch", fetchMock);
    const decide = await builtGameIds();
    await decide();
    expect(peak()).toBeLessThanOrEqual(4);
    expect(peak()).toBeGreaterThan(1);
  });

  it("stops the build when one slate fails and the others answered", async () => {
    const bad = DATES[5];
    const { fetchMock } = answering((date) => (date === bad ? 500 : [1]));
    vi.stubGlobal("fetch", fetchMock);
    const decide = await builtGameIds();
    await expect(decide()).rejects.toThrow(new RegExp(`1 of ${DATES.length} slates.*${bad}`));
  });

  it("answers with no games when no slate can be read, which the publish check catches", async () => {
    const { fetchMock } = answering(() => 503);
    vi.stubGlobal("fetch", fetchMock);
    const decide = await builtGameIds();
    await expect(decide()).resolves.toEqual([]);
  });

  it("decides once per export, so the page and its file cannot disagree", async () => {
    const { fetchMock } = answering(() => [7]);
    vi.stubGlobal("fetch", fetchMock);
    const decide = await builtGameIds(true);
    const [a, b] = await Promise.all([decide(), decide()]);
    expect(a).toEqual(b);
    expect(fetchMock).toHaveBeenCalledTimes(DATES.length);
    // Outside an export each call asks afresh: `next dev` must see new games.
    const fresh = await builtGameIds(false);
    await fresh();
    await fresh();
    expect(fetchMock).toHaveBeenCalledTimes(DATES.length * 3);
  });
});
