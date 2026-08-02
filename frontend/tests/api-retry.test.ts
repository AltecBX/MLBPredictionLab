/**
 * The API client retries a waking service.
 *
 * The deployment sleeps after fifteen minutes idle and takes about a minute to
 * wake, so the first request after any quiet spell fails with a 502. Without
 * retries that renders identically to "the backend is down", which is what a
 * reader actually saw: an empty page on a perfectly healthy deployment.
 *
 * These tests exist to stop that regressing, and to stop the retry loop growing
 * to cover statuses that are answers rather than outages.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { looksLikeColdStart } from "@/lib/api";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function callGames() {
  const { api } = await import("@/lib/api");
  return api.games("2026-08-02");
}

describe("cold-start classification", () => {
  it("treats a gateway failure as a service still coming up", () => {
    expect(looksLikeColdStart(502)).toBe(true);
    expect(looksLikeColdStart(503)).toBe(true);
    expect(looksLikeColdStart(504)).toBe(true);
    expect(looksLikeColdStart(0)).toBe(true);
  });

  it("does not treat an answer as a cold start", () => {
    // A 404 is the API telling us something true. Retrying it would be waiting
    // for a different answer to a settled question.
    expect(looksLikeColdStart(404)).toBe(false);
    expect(looksLikeColdStart(200)).toBe(false);
  });
});

describe("request retries", () => {
  it("recovers when the service answers on a later attempt", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("Bad Gateway", { status: 502 }))
      .mockResolvedValueOnce(new Response("Bad Gateway", { status: 502 }))
      .mockResolvedValueOnce(json({ date: "2026-08-02", count: 0, games: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = callGames();
    await vi.runAllTimersAsync();
    const result = await pending;

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.ok).toBe(true);
  });

  it("gives up eventually rather than retrying forever", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("Bad Gateway", { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = callGames();
    await vi.runAllTimersAsync();
    const result = await pending;

    // One initial attempt plus the four backoff delays, and no more.
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.status).toBe(502);
  });

  it("does not retry a 404, which is an answer", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("Not Found", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await callGames();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(false);
  });

  it("retries a network error, which is what a sleeping host looks like", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("ECONNREFUSED"))
      .mockResolvedValueOnce(json({ date: "2026-08-02", count: 0, games: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = callGames();
    await vi.runAllTimersAsync();
    const result = await pending;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.ok).toBe(true);
  });

  it("does not retry a request that succeeded", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(json({ date: "2026-08-02", count: 0, games: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await callGames();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
